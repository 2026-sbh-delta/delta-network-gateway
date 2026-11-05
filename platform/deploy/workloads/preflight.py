"""Read-only checks before creating VMs or copying images."""

import ipaddress
import socket


class DeploymentPreflightError(RuntimeError):
    pass


def check_prepared_database(data, config):
    if not data.get("database"):
        return
    profile = config["databases"][data["database"]["profile_id"]]
    if profile.get("server_mode", "prepared") != "prepared":
        return
    if (
        data.get("target", {}).get("cloud") == "gcp"
        and config.get("connections", {}).get("gcp", {}).get("management_transport")
        == "iap"
    ):
        # The destination VM supplies DB network access. Validate the explicit
        # operator endpoint now; SQL/TLS checks run through IAP after VM creation.
        endpoint = profile.get("iap_endpoint")
        if not endpoint or endpoint.get("port") != 5432:
            raise DeploymentPreflightError(
                "GCP IAP database endpoint is not configured"
            )
        return
    # Operator supplied, never a destination supplied by an application user.
    endpoint = profile.get("preflight_endpoint")
    if not endpoint:
        return
    address = str(ipaddress.ip_address(endpoint["address"]))
    port = int(endpoint["port"])
    try:
        with socket.create_connection((address, port), timeout=5):
            pass
    except OSError:
        raise DeploymentPreflightError(
            "Target database is unreachable from the deployment worker. "
            "Connect the environment network and check VPN routes/firewall rules "
            "before retrying. No destination VM was created by this attempt."
        ) from None
