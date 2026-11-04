import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


class ClientError(Exception):
    pass


class MissingProvider(Exception):
    pass


class IdentityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.sdk = MagicMock()
        self.sts, self.s3, self.iam = MagicMock(), MagicMock(), MagicMock()
        self.sdk.Session.return_value.client.side_effect = {
            "sts": self.sts,
            "s3": self.s3,
            "iam": self.iam,
        }.get
        self.sts.get_caller_identity.return_value = {"Account": "123456789012"}
        self.iam.exceptions.NoSuchEntityException = MissingProvider
        self.iam.get_open_id_connect_provider.return_value = {"ClientIDList": ["sts.amazonaws.com"]}
        spec = importlib.util.spec_from_file_location(
            "prepare_identity",
            Path(__file__).resolve().parents[1] / "scripts/prepare-identity.py",
        )
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(
            sys.modules,
            {
                "boto3": self.sdk,
                "botocore.exceptions": MagicMock(ClientError=ClientError),
            },
        ):
            spec.loader.exec_module(self.module)

    def prepare(self, owns_provider=False, subject_default=True, extra_resources=()):
        platform = {
            "outputs": {
                "account_id": {"value": "123456789012"},
                "region": {"value": "ap-northeast-2"},
            },
            "resources": [
                {
                    "mode": "data" if resource_type == "aws_ec2_managed_prefix_list" else "managed",
                    "type": resource_type,
                    "name": name,
                    "instances": [{"attributes": {"id": resource_id}}],
                }
                for resource_type, name, resource_id in [
                    ("aws_vpc", "platform", "vpc-test"),
                    ("aws_subnet", "public", "subnet-public"),
                    ("aws_subnet", "private", "subnet-private"),
                    ("aws_cloudfront_origin_request_policy", "platform", "policy-test"),
                    ("aws_ec2_managed_prefix_list", "cloudfront", "pl-cloudfront"),
                ]
            ],
        }
        platform["resources"].extend(extra_resources)
        identity = {
            "resources": [
                {
                    "type": "aws_iam_openid_connect_provider",
                    "name": "github",
                    "instances": [{"attributes": {"id": "provider"}}],
                }
            ]
            if owns_provider
            else []
        }
        self.s3.get_object.side_effect = [
            {"Body": io.BytesIO(json.dumps(state).encode())} for state in (platform, identity)
        ]
        oidc = {
            "use_default": subject_default,
            "use_immutable_subject": True,
            "sub_claim_prefix": "repo:team@123/repo@456",
        }
        with (
            patch.object(sys, "argv", ["prepare-identity.py", "--account-id", "123456789012"]),
            patch.object(self.module.subprocess, "check_output", return_value=json.dumps(oidc)),
            patch.object(self.module, "write_private") as write,
            redirect_stdout(io.StringIO()),
        ):
            self.module.main()
        self.s3.put_object.assert_not_called()
        self.iam.create_open_id_connect_provider.assert_not_called()
        self.iam.create_role.assert_not_called()
        return json.loads(write.call_args_list[0].args[1])

    def test_wrong_account_stops_before_reading_or_writing(self):
        self.sts.get_caller_identity.return_value = {"Account": "999999999999"}
        with self.assertRaisesRegex(SystemExit, "AWS 계정"):
            self.prepare()
        self.s3.get_object.assert_not_called()
        self.iam.get_open_id_connect_provider.assert_not_called()

    def test_existing_external_provider_is_reused(self):
        values = self.prepare()
        self.assertFalse(values["create_oidc_provider"])
        self.assertEqual(values["platform_subnet_ids"], ["subnet-private", "subnet-public"])

    def test_all_owned_service_instances_are_included_and_other_subnets_excluded(self):
        values = self.prepare(
            extra_resources=[
                {
                    "mode": "managed",
                    "type": "aws_subnet",
                    "name": "service",
                    "instances": [
                        {"index_key": "ai", "attributes": {"id": "subnet-ai"}},
                        {
                            "index_key": "jenkins_controller",
                            "attributes": {"id": "subnet-controller"},
                        },
                    ],
                },
                {
                    "mode": "data",
                    "type": "aws_subnet",
                    "name": "service",
                    "instances": [
                        {"attributes": {"id": "subnet-external"}},
                    ],
                },
                {
                    "mode": "managed",
                    "type": "aws_subnet",
                    "name": "unrelated",
                    "instances": [
                        {"attributes": {"id": "subnet-unrelated"}},
                    ],
                },
            ]
        )
        self.assertEqual(
            set(values["platform_subnet_ids"]),
            {
                "subnet-private",
                "subnet-public",
                "subnet-ai",
                "subnet-controller",
            },
        )

    def test_owned_provider_remains_managed_on_rerun(self):
        self.assertTrue(self.prepare(owns_provider=True)["create_oidc_provider"])

    def test_cloudfront_prefix_list_is_reused_from_platform_state(self):
        self.assertEqual(self.prepare()["cloudfront_prefix_list_id"], "pl-cloudfront")

    def test_missing_provider_is_selected_for_creation(self):
        self.iam.get_open_id_connect_provider.side_effect = MissingProvider
        self.assertTrue(self.prepare()["create_oidc_provider"])

    def test_wrong_audience_stops(self):
        self.iam.get_open_id_connect_provider.return_value = {"ClientIDList": ["unrelated"]}
        with self.assertRaisesRegex(SystemExit, "audience"):
            self.prepare()

    def test_unexpected_subject_mode_stops(self):
        with self.assertRaisesRegex(SystemExit, "immutable"):
            self.prepare(subject_default=False)


if __name__ == "__main__":
    unittest.main()
