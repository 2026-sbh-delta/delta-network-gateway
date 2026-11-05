"""Retain Jenkins credentials while rotating addresses and TLS for a subnet move."""

import argparse
import copy
import importlib.util
import json
from pathlib import Path

import boto3

CONTROLLER = "/delta-platform/jenkins/controller"
AGENT = "/delta-platform/jenkins/agent"
PLATFORM = "/delta-platform/runtime"


def replacements(controller, agent, platform, url, ca, keystore):
    if url not in ("https://10.80.1.20", "https://10.80.10.20"):
        raise ValueError("Only the current or isolated controller address is allowed")
    if not all(
        controller.get(key)
        for key in ("admin_password", "platform_password", "keystore_password")
    ):
        raise ValueError("Existing controller credentials are required")
    if not agent.get("agent_secret") or not platform.get("app_env", {}).get(
        "PLATFORM_JENKINS_TOKEN"
    ):
        raise ValueError("Existing client credentials are required")
    c, a, p = copy.deepcopy((controller, agent, platform))
    c.update(url=url, ca_base64=ca, keystore_base64=keystore)
    a.update(url=url, ca_base64=ca)
    p["app_env"].update(PLATFORM_JENKINS_URL=url, PLATFORM_JENKINS_CA_BASE64=ca)
    return {CONTROLLER: c, AGENT: a, PLATFORM: p}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--url", default="https://10.80.10.20")
    args = parser.parse_args()
    if args.url not in ("https://10.80.1.20", "https://10.80.10.20"):
        raise ValueError("Invalid controller address")
    session = boto3.Session(profile_name=args.profile, region_name="ap-northeast-2")
    if session.client("sts").get_caller_identity()["Account"] != args.account_id:
        raise ValueError("Account mismatch")
    ssm = session.client("ssm")
    originals = {
        name: ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]
        for name in (CONTROLLER, AGENT, PLATFORM)
    }
    if any(p["Type"] != "SecureString" for p in originals.values()):
        raise ValueError("Existing encrypted runtime parameters required")
    c, a, p = (
        json.loads(originals[name]["Value"]) for name in (CONTROLLER, AGENT, PLATFORM)
    )
    if c["url"] != args.url:
        spec = importlib.util.spec_from_file_location(
            "jenkins_bootstrap", Path(__file__).with_name("bootstrap-runtime.py")
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        ca, keystore = helper.certificate(
            args.url.removeprefix("https://"), c["keystore_password"]
        )
    else:
        ca, keystore = c["ca_base64"], c["keystore_base64"]
    values = replacements(c, a, p, args.url, ca, keystore)
    for name, value in values.items():
        if value == json.loads(originals[name]["Value"]):
            continue
        if (
            ssm.get_parameter(Name=name)["Parameter"]["Version"]
            != originals[name]["Version"]
        ):
            raise RuntimeError(
                f"Concurrent runtime update: {name}. Re-read and retry before deployment."
            )
        metadata = ssm.describe_parameters(
            ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}]
        )["Parameters"][0]
        result = ssm.put_parameter(
            Name=name,
            Value=json.dumps(value),
            Type="SecureString",
            KeyId=metadata["KeyId"],
            Tier="Advanced"
            if len(json.dumps(value).encode()) > 4096
            else metadata["Tier"],
            Overwrite=True,
        )
        print(
            name,
            "previous version",
            originals[name]["Version"],
            "new version",
            result["Version"],
        )
    print(
        "Addresses and CA prepared; tokens, passwords, database and other providers retained. Redeploy clients after controller readiness."
    )


if __name__ == "__main__":
    main()
