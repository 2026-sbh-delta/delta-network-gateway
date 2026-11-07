"""Render and bind a GCP VM plan to the signed mapping and the Jenkins approval."""

import hashlib
import ipaddress
import json
import re
from pathlib import Path

from errors import DeployError

OVERRIDE = "mapping_override.tf.json"
SUBNET_DATA = "mapping_subnet.tf.json"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def render(data, params):
    mapping = data["terraform_mapping"]
    subnet = re.fullmatch(
        r"(?:https://www\.googleapis\.com/compute/v1/)?"
        r"projects/([a-z][a-z0-9-]{0,62})/regions/([a-z0-9-]+)/subnetworks/([a-z][a-z0-9-]{0,62})",
        params["subnetwork"],
    )
    machine = mapping["profile_id"]
    if (data["target"]["cloud"] != "gcp" or data["database"]["mode"] != "existing"
            or machine != "e2-small" or machine != params["profile_id"]
            or not subnet or subnet.group(2, 3) != (params["region"], mapping["subnet_name"])):
        raise DeployError("provision_vm", "mapping_target_mismatch")
    cidr = str(ipaddress.IPv4Network(mapping["subnet_cidr"]))
    project, region, name = subnet.groups()
    return {
        OVERRIDE: {"resource": {"google_compute_instance": {"app": {
            "machine_type": machine,
            "network_interface": {"subnetwork": "${data.google_compute_subnetwork.mapped.self_link}"},
        }}}},
        SUBNET_DATA: {"data": {"google_compute_subnetwork": {"mapped": {
            "project": project, "region": region, "name": name,
            "lifecycle": {"postcondition": [{
                "condition": f'${{self.ip_cidr_range == "{cidr}"}}',
                "error_message": "GCP subnet CIDR does not match the approved mapping.",
            }]},
        }}}},
    }


def validate_plan(plan):
    if plan.get("errored") or plan.get("complete") is not True:
        raise DeployError("provision_vm", "terraform_plan_incomplete")
    changes = []
    for change in plan["resource_changes"]:
        address, actions = change["address"], change["change"]["actions"]
        if not (
            (address == "google_compute_instance.app" and actions in (["no-op"], ["create"]))
            or (address == "data.google_compute_subnetwork.mapped" and actions in (["no-op"], ["read"]))
        ):
            raise DeployError("provision_vm", "plan_not_create_only")
        changes.append({"address": address, "actions": actions})
    if sum(item["address"] == "google_compute_instance.app" for item in changes) != 1:
        raise DeployError("provision_vm", "mapped_vm_missing")
    return changes


def binding(data, config, rendered):
    return digest({"deployment": data, "config": config, "terraform": rendered})


def plan_digest(path: Path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
