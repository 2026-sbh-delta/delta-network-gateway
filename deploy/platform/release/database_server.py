"""Provision a service-owned PostgreSQL 18 server separately from its logical application database.

동규님 platform/deploy/workloads/database_server.py 이식. state는 우리 S3 backend(state_backend.py),
AWS 생성은 대상 계정 위임(provider assume_role), GCP 관리자 비밀번호는 write-only로만 전달한다.
"""

import json
import os
import secrets
from uuid import UUID

import state_backend


def provision(data, profile, config, execute, selected):
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
    params["region"] = data["target"]["region"]
    directory = state_backend.workdir(config, environment, "database", cloud)
    environment_variables = state_backend.env(config)
    if cloud == "aws":
        params.update(
            service_id=service,
            environment_id=environment,
            assume_role_arn=selected["assume_role_arn"],
            external_id=selected["external_id"],
        )
    else:
        params.update(purpose="customer-app", ca_pem=profile["ca_pem"])
        # Write-only 값이라 최초 apply에만 쓰인다. 재시도에도 같은 값을 쓰도록 영속 작업 디렉터리에 둔다.
        password = directory / "admin-password"
        if not password.exists():
            descriptor = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(secrets.token_urlsafe(36))
        environment_variables["TF_VAR_admin_password"] = password.read_text()
    variables = directory / "server.auto.tfvars.json"
    variables.write_text(json.dumps(params))
    variables.chmod(0o600)

    def run(args, **kwargs):
        return execute(args, cwd=directory, env=environment_variables, **kwargs)

    run(state_backend.init_command(config, environment, "database"))
    run(["terraform", "plan", "-input=false", "-out=server.tfplan"])
    plan = json.loads(run(["terraform", "show", "-json", "server.tfplan"]))
    for change in plan.get("resource_changes", []):
        if any(
            a not in {"create", "read", "no-op"} for a in change["change"]["actions"]
        ):
            raise ValueError(
                "Database provisioning must not update or delete an existing server"
            )
    run(["terraform", "apply", "-input=false", "server.tfplan"], timeout=3000)
    outputs = {
        k: v["value"]
        for k, v in json.loads(run(["terraform", "output", "-json"])).items()
    }
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
