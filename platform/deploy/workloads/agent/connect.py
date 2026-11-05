"""Read on the Jenkins controller; only encrypted credentials leave SSM output."""

import argparse
import base64
import http.cookiejar
import json
import os
import ssl
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipient", required=True)
    args = parser.parse_args()
    root = Path("/etc/delta-jenkins")
    cached = root / "deployment-connection.json"
    if not cached.exists():
        config = json.loads((root / "controller-config.json").read_text())
        context = ssl.create_default_context(cafile=str(root / "ca.crt"))

        def client(user, password):
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            )
            auth = base64.b64encode(f"{user}:{password}".encode()).decode()
            opener.addheaders = [("Authorization", "Basic " + auth)]
            return opener

        api = client("platform-deployer", config["deployment_password"])
        crumb = json.load(api.open(config["url"] + "/crumbIssuer/api/json", timeout=20))
        request = urllib.request.Request(
            config["url"]
            + "/user/platform-deployer/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
            data=urllib.parse.urlencode({"newTokenName": "delta-deployment"}).encode(),
            headers={crumb["crumbRequestField"]: crumb["crumb"]},
        )
        token = json.load(api.open(request, timeout=20))["data"]["tokenValue"]
        admin = client("admin", config["admin_password"])
        secret = (
            ET.fromstring(
                admin.open(
                    config["url"] + "/computer/deployer/jenkins-agent.jnlp", timeout=20
                ).read()
            )
            .find(".//argument")
            .text
        )
        if not token or not secret or len(secret) < 32:
            raise ValueError("Missing deployment credentials")
        descriptor = os.open(cached, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"token": token, "agent_secret": secret}, stream)
    encrypted = subprocess.check_output(
        [
            "openssl",
            "pkeyutl",
            "-encrypt",
            "-pubin",
            "-inkey",
            args.recipient,
            "-pkeyopt",
            "rsa_padding_mode:oaep",
            "-pkeyopt",
            "rsa_oaep_md:sha256",
        ],
        input=cached.read_bytes(),
    )
    print(base64.b64encode(encrypted).decode())


if __name__ == "__main__":
    main()
