import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "onprem"


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / (name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def evidence():
    images = {
        c: "111122223333.dkr.ecr.ap-northeast-2.amazonaws.com/app@sha256:" + letter * 64
        for c, letter in [("frontend", "a"), ("backend", "b")]
    }
    release = {
        "commit_sha": "c" * 40,
        **images,
        "frontend_env": {
            "API_HOST": "sandbox-backend",
            "API_PORT": "8080",
            "API_PROTOCOL": "http",
        },
    }
    return {
        "runtime_check": "passed",
        "cleanup": "stopped",
        "security_check": "not_run",
        "commit_sha": "c" * 40,
        "components": {c: {"image": i} for c, i in images.items()},
        "release_sha256": hashlib.sha256(
            json.dumps(release, sort_keys=True).encode()
        ).hexdigest(),
    }


class ReleaseTests(unittest.TestCase):
    def test_pending_security_requires_explicit_preview_and_is_never_marked_passed(
        self,
    ):
        render = module("render-release").render
        with self.assertRaises(ValueError):
            render(evidence())
        items = render(evidence(), preview=True)
        self.assertTrue(
            all(
                x["metadata"]["annotations"]["delta-platform/security-status"]
                == "pending"
                for x in items
            )
        )

    def test_image_changed_after_scan_is_rejected(self):
        value = evidence()
        value["components"]["backend"]["image"] = value["components"]["frontend"][
            "image"
        ]
        with self.assertRaises(ValueError):
            module("render-release").render(value, preview=True)

    def test_state_collection_does_not_forward_env_secrets_or_annotations(self):
        raw = {
            "kind": "Deployment",
            "metadata": {"name": "backend", "annotations": {"last-applied": "secret"}},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "backend",
                                "image": "image",
                                "env": [
                                    {"name": "PASSWORD", "value": "must-never-leak"}
                                ],
                            }
                        ]
                    }
                }
            },
        }
        result = module("collect-state").sanitize(raw)
        self.assertNotIn("must-never-leak", json.dumps(result))
        self.assertNotIn("annotations", result)
        self.assertEqual(result["workload"]["containers"][0]["image"], "image")

    def test_wider_or_cross_namespace_policy_candidate_is_rejected(self):
        policy = module("policy-change")
        candidate = copy.deepcopy(policy.expected())
        candidate["spec"]["ingress"][0]["from"] = [{"ipBlock": {"cidr": "0.0.0.0/0"}}]
        with self.assertRaises(ValueError):
            policy.validate(candidate)
        candidate = policy.expected()
        candidate["metadata"]["namespace"] = "demo"
        with self.assertRaises(ValueError):
            policy.validate(candidate)
