import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[1] / "sast" / "aws"


def module(name):
    spec = importlib.util.spec_from_file_location("sast_" + name, ROOT / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"boto3": object()}):
        spec.loader.exec_module(value)
    return value


class SastRuntimeTest(unittest.TestCase):
    def test_merge_preserves_credentials_and_other_configuration(self):
        connect = module("connect")
        original = {
            "db_password": "keep-db",
            "app_env": {
                "PLATFORM_LOGIN_ID": "keep-login",
                "PLATFORM_JENKINS_TOKEN": "keep-jenkins",
            },
        }
        fields = {
            "PLATFORM_SAST_URL": "https://172.31.25.180",
            "PLATFORM_SAST_TOKEN": "new-sast",
            "PLATFORM_SAST_CA_BASE64": "cert",
        }
        merged = connect.merge(original, fields)
        self.assertEqual(merged["db_password"], original["db_password"])
        for key, value in original["app_env"].items():
            self.assertEqual(merged["app_env"][key], value)
        self.assertNotIn("PLATFORM_SAST_URL", original["app_env"])
        with self.assertRaises(ValueError):
            connect.merge(original, {**fields, "PLATFORM_LOGIN_ID": "overwrite"})

    def test_replacement_preserves_image_model_and_mounts(self):
        install = module("install")
        old = {
            "Image": "sha256:immutable",
            "Config": {
                "Image": "registry/tag",
                "User": "10001",
                "Cmd": ["uvicorn", "delta_sast.api:app"],
                "Env": [
                    "DELTA_SAST_TOKEN=keep",
                    "SAST_LLM_BACKEND=api",
                    "ANTHROPIC_IDENTITY_TOKEN_FILE=/run/token",
                ],
            },
            "HostConfig": {
                "NetworkMode": "host",
                "Binds": ["/state:/var/lib/delta-sast", "/wif:/run:ro"],
                "Memory": 1,
            },
        }
        new = install.replacement(old, "https://github.com/docker/")
        self.assertEqual(new["Image"], old["Image"])
        self.assertEqual(new["HostConfig"], old["HostConfig"])
        self.assertEqual(new["Cmd"], old["Config"]["Cmd"])
        self.assertTrue(set(old["Config"]["Env"]) <= set(new["Env"]))
        self.assertNotIn(
            "SAST_REPO_PREFIXES=https://github.com/docker/", old["Config"]["Env"]
        )

    def test_nginx_rejects_config_injection_and_limits_private_api(self):
        install = module("install")
        with self.assertRaises(ValueError):
            install.nginx_config("172.31.25.180; listen 80")
        for prefix in (
            "http://github.com/docker/",
            "https://example.com/",
            "https://github.com/docker/\nX=1",
        ):
            with self.assertRaises(ValueError):
                install.prefix_setting(prefix)
        text = install.nginx_config("172.31.25.180")
        self.assertIn("listen 172.31.25.180:443 ssl;", text)
        self.assertIn("location / { return 404; }", text)
        self.assertNotIn("dast-results", text)

    def test_failed_new_container_start_restores_old_container(self):
        install = module("install")
        original = {
            "Image": "sha256:immutable",
            "Config": {"Env": ["SAST_REPO_PREFIXES=https://github.com/old/"]},
            "HostConfig": {"NetworkMode": "host"},
        }
        calls = []

        def docker(method, path, body=None):
            calls.append((method, path))
            if method == "GET":
                return original
            if (
                path == "/containers/delta-sast/start"
                and calls.count((method, path)) == 1
            ):
                raise RuntimeError("startup failed")

        with (
            patch.object(install, "docker", side_effect=docker),
            patch.object(install.json, "load", return_value=[]),
            patch.object(install.urllib.request, "urlopen"),
            self.assertRaises(RuntimeError),
        ):
            install.source_policy("https://github.com/docker/", "secret")
        self.assertEqual(
            calls[-2:],
            [
                (
                    "POST",
                    "/containers/delta-sast-before-platform/rename?name=delta-sast",
                ),
                ("POST", "/containers/delta-sast/start"),
            ],
        )

    def test_active_scan_blocks_replacement(self):
        install = module("install")
        original = {"Config": {"Env": []}}
        with (
            patch.object(install, "docker", return_value=original) as docker,
            patch.object(install.json, "load", return_value=[{"state": "running"}]),
            patch.object(install.urllib.request, "urlopen"),
            self.assertRaises(RuntimeError),
        ):
            install.source_policy("https://github.com/docker/", "secret")
        docker.assert_called_once_with("GET", "/containers/delta-sast/json")
