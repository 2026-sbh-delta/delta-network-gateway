import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from test_onprem_release import evidence, module

ROOT = Path(__file__).resolve().parents[1] / "instrumentation"
sys.path.insert(0, str(ROOT))
from profiles import INIT, MOUNT, NODE_IMAGE, VOLUME, endpoint, environment, inject


def pod():
    return {
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
        "containers": [
            {
                "name": "app",
                "image": "customer@sha256:" + "a" * 64,
                "command": ["node"],
                "args": ["server.mjs"],
                "env": [{"name": "NODE_OPTIONS", "value": "--max-old-space-size=192"}],
                "securityContext": {"readOnlyRootFilesystem": True},
            }
        ],
    }


class InstrumentationTests(unittest.TestCase):
    def test_injection_preserves_customer_image_command_and_existing_node_options(self):
        original = pod()
        result = inject(original, "http://10.40.0.10:4318", "app", "a" * 40)
        self.assertEqual(original, pod())
        app = result["containers"][0]
        for key in ("image", "command", "args", "securityContext"):
            self.assertEqual(app[key], original["containers"][0][key])
        self.assertTrue(app["volumeMounts"][0]["readOnly"])
        env = {v["name"]: v["value"] for v in app["env"]}
        self.assertTrue(env["NODE_OPTIONS"].startswith("--max-old-space-size=192 "))
        self.assertIn("build/src/register.js", env["NODE_OPTIONS"])
        self.assertEqual(result["initContainers"][0]["image"], NODE_IMAGE)
        self.assertTrue(result["initContainers"][0]["securityContext"]["runAsNonRoot"])

    def test_rejects_public_metadata_and_credential_endpoints(self):
        for value in (
            "http://1.1.1.1:4318",
            "http://169.254.169.254:4318",
            "http://user:password@10.40.0.10:4318",
            "http://10.40.0.10:4318/path",
            "http://0.0.0.0:4318",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                endpoint(value)

    def test_duplicate_and_hidden_instrumentation_are_rejected(self):
        for env in (
            {"OTEL_SERVICE_NAME": "existing"},
            {"NODE_OPTIONS": "--require @opentelemetry/register"},
        ):
            with self.assertRaises(ValueError):
                environment("http://10.40.0.10:4318", "app", "a" * 40, existing=env)
        spec = pod()
        spec["containers"][0]["env"] = [
            {
                "name": "NODE_OPTIONS",
                "valueFrom": {"secretKeyRef": {"name": "hidden", "key": "value"}},
            }
        ]
        with self.assertRaises(ValueError):
            inject(spec, "http://10.40.0.10:4318", "app", "a" * 40)

    def test_off_render_removes_injection_and_revokes_the_egress_grant(self):
        render = module("render-release").render
        on = render(
            evidence(),
            preview=True,
            instrumentation="nodejs",
            collector_endpoint="http://delta-otel-preview.delta-notes.svc.cluster.local:4318",
        )
        off = render(evidence(), preview=True)
        for mode, objects in ((True, on), (False, off)):
            for obj in objects:
                if obj["kind"] == "Deployment":
                    spec = obj["spec"]["template"]["spec"]
                    self.assertEqual(
                        any(v["name"] == VOLUME for v in spec.get("volumes", [])), mode
                    )
                    self.assertEqual(
                        any(c["name"] == INIT for c in spec.get("initContainers", [])),
                        mode,
                    )
                if obj["metadata"]["name"] == "app-to-collector":
                    self.assertEqual(bool(obj["spec"]["egress"]), mode)
        backend = next(
            o
            for o in on
            if o["kind"] == "Deployment" and o["metadata"]["name"] == "backend"
        )
        values = {
            v["name"]: v["value"]
            for v in backend["spec"]["template"]["spec"]["containers"][0]["env"]
        }
        self.assertEqual(values["OTEL_METRICS_EXPORTER"], "none")

    def test_vm_render_keeps_original_image_and_does_not_require_rebuild(self):
        spec = importlib.util.spec_from_file_location(
            "compose_instrumentation", ROOT / "render-compose.py"
        )
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        source = {
            "services": {
                "app": {
                    "image": "customer@sha256:" + "a" * 64,
                    "environment": {"PORT": "3000"},
                }
            }
        }
        before = copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "delta-agent.json").write_text(json.dumps({"image": NODE_IMAGE}))
            value = renderer.render(
                source, "app", tmp, "http://10.40.0.10:4318", "a" * 40
            )
        self.assertEqual(source, before)
        self.assertEqual(
            value["services"]["app"]["image"], source["services"]["app"]["image"]
        )
        self.assertEqual(value["services"]["app"]["volumes"][0]["target"], MOUNT)
        self.assertTrue(value["services"]["app"]["volumes"][0]["read_only"])
