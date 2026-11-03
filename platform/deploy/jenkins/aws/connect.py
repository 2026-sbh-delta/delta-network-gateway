"""Run on the controller via SSM. Output is secret and must stay in operator memory."""

import base64
import http.cookiejar
import json
import os
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    root = Path("/etc/delta-jenkins")
    cached = root / "connection.json"
    if cached.exists():
        print(cached.read_text())
        return
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

    api = client("platform", config["platform_password"])
    crumb = json.load(api.open(config["url"] + "/crumbIssuer/api/json", timeout=20))
    request = urllib.request.Request(
        config["url"]
        + "/user/platform/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
        data=urllib.parse.urlencode({"newTokenName": "delta-platform-aws"}).encode(),
        headers={crumb["crumbRequestField"]: crumb["crumb"]},
    )
    token = json.load(api.open(request, timeout=20))["data"]["tokenValue"]
    admin = client("admin", config["admin_password"])
    secret = (
        ET.fromstring(
            admin.open(
                config["url"] + "/computer/image-builder/jenkins-agent.jnlp", timeout=20
            ).read()
        )
        .find(".//argument")
        .text
    )
    if not secret or len(secret) < 32 or not token:
        raise ValueError("Missing controller connection credentials")
    result = json.dumps(
        {
            "token": token,
            "agent_secret": secret,
            "ca_base64": config["ca_base64"],
            "url": config["url"],
        }
    )
    fd = os.open(cached, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(result)
    print(result)


if __name__ == "__main__":
    main()
