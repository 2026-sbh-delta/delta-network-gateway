#!/usr/bin/env python3
"""Validate compiled mock rule groups with AWS DryRun=true; never create them."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--expected-account-id", required=True)
    parser.add_argument("--profile", default="sbh-delta-security")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--aws-bin", default="aws")
    args = parser.parse_args()
    env = dict(os.environ, AWS_PROFILE=args.profile, AWS_PAGER="", AWS_MAX_ATTEMPTS="1")

    def aws(*command):
        response = subprocess.run(
            [args.aws_bin, *command, "--region", args.region, "--output", "json", "--no-cli-pager"],
            env=env, text=True, capture_output=True, check=True,
        )
        return json.loads(response.stdout)

    identity = aws("sts", "get-caller-identity")
    if identity["Account"] != args.expected_account_id:
        raise SystemExit("Refusing dry run: authenticated account differs from --expected-account-id")

    selected = {}
    for line in args.events.read_text().splitlines():
        event = json.loads(line)
        name = event.get("@testrun")
        if event.get("type") != "test_plan" or name not in ("central_only", "tcp_udp_icmp_and_signature"):
            continue
        for change in event["test_plan"]["resource_changes"]:
            if change.get("address") == "aws_networkfirewall_rule_group.central":
                selected[name] = change["change"]["after"]
    if len(selected) != 2:
        raise SystemExit("Need both central_only and tcp_udp_icmp_and_signature mock plans")

    for name, resource in selected.items():
        group = resource["rule_group"][0]
        variables = group["rule_variables"][0]
        request = {
            "DryRun": True,
            "RuleGroupName": "delta-lab-configuration-dryrun",
            "Type": "STATEFUL",
            "Capacity": resource["capacity"],
            "RuleGroup": {
                "StatefulRuleOptions": {"RuleOrder": "STRICT_ORDER"},
                "RuleVariables": {"IPSets": {
                    item["key"]: {"Definition": item["ip_set"][0]["definition"]}
                    for item in variables["ip_sets"]
                }},
                "RulesSource": {"RulesString": group["rules_source"][0]["rules_string"]},
            },
        }
        # The request is encoded directly as an argv value, never through shell
        # interpolation. DryRun is fixed here and reinforced by --dry-run.
        result = aws("network-firewall", "create-rule-group", "--cli-input-json", json.dumps(request), "--dry-run")
        capacity = result.get("RuleGroupResponse", {}).get("ConsumedCapacity")
        print(f"AWS DryRun accepted: {name}; consumed capacity={capacity}; resources created=0")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip()) from error
