"""Provision a service-owned server separately from its logical application database."""

import json
import os
import secrets
import shutil
from pathlib import Path
from uuid import UUID


def provision(data, profile, config, execute):
    cloud = data["target"]["cloud"]
    if cloud not in {"aws", "gcp"} or data["database"]["mode"] != "new":
        raise ValueError(
            "A new database server requires a supported cloud and a new database"
        )
    params = dict(profile["terraform"])
    identity_key = "account_id" if cloud == "aws" else "gcp_project"
    parameter_key = "account_id" if cloud == "aws" else "project_id"
    if params[parameter_key] != data["connection"][identity_key]:
        raise ValueError("Database server must belong to the selected cloud account")
    environment = str(UUID(data["environment_id"]))
    service = str(UUID(data["service_id"]))
    params["name"] = "delta-platform-db-" + UUID(environment).hex[:24]
    directory = Path(config["state_root"]) / "databases" / environment
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    module = Path(__file__).resolve().parents[1] / "databases" / cloud
    for file in [*module.glob("*.tf"), module / ".terraform.lock.hcl"]:
        shutil.copyfile(file, directory / file.name)
    environment_variables = None
    if cloud == "aws":
        params.update(service_id=service, environment_id=environment)
    else:
        params.update(purpose="customer-app", ca_pem=profile["ca_pem"])
        password = directory / "admin-password"
        if not password.exists():
            descriptor = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_urlsafe(36))
        environment_variables = {
            **os.environ,
            "TF_VAR_admin_password": password.read_text(),
        }
    variables = directory / "server.auto.tfvars.json"
    variables.write_text(json.dumps(params))
    variables.chmod(0o600)

    def run(args, **kwargs):
        return execute(
            ["terraform", *args], cwd=directory, env=environment_variables, **kwargs
        )

    run(["init", "-input=false", "-lockfile=readonly"])
    run(["plan", "-input=false", "-out=server.tfplan"])
    plan = json.loads(run(["show", "-json", "server.tfplan"]))
    for change in plan.get("resource_changes", []):
        if any(
            a not in {"create", "read", "no-op"} for a in change["change"]["actions"]
        ):
            raise ValueError(
                "Database provisioning must not update or delete an existing server"
            )
    run(["apply", "-input=false", "server.tfplan"], timeout=3000)
    outputs = {k: v["value"] for k, v in json.loads(run(["output", "-json"])).items()}
    # Only references and public connection metadata leave the provisioning boundary.
    result = {
        **profile,
        "admin_secret_id": outputs["admin_secret_id"],
        "secret_provider": cloud,
        "connection": {
            "host": outputs["host"],
            "port": 5432,
            "dbname": "postgres",
            "ca_pem": profile["ca_pem"],
        },
    }
    if cloud == "gcp":
        result["iap_endpoint"] = {"address": outputs["private_ip"], "port": 5432}
    return result
