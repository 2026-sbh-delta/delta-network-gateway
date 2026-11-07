"""Read action names through the instance profile's Terraform policy graph.

This is configured policy evidence, not effective IAM permissions. Resource scopes
are not translated. Unresolved attachments, boundaries, Deny and conditional
policies keep the identity unknown rather than returning a partial Allow list.
No policy document, resource ARN, condition value or user data leaves this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

ACTION = re.compile(r"(?:\*|[A-Za-z0-9-]+:[A-Za-z0-9*?]+)\Z")
MAX_ACTIONS = 256


def _name(reference: Any) -> str | None:
    if not isinstance(reference, str) or not reference:
        return None
    return reference.rsplit("/", 1)[-1]


def _one(values: list[dict], reference: Any) -> dict | None:
    name = _name(reference)
    if isinstance(reference, str) and reference.startswith("arn:"):
        found = [v for v in values if v.get("arn") == reference]
    else:
        found = [v for v in values if name and name in {_name(v.get(k)) for k in ("name", "id", "arn")}]
    return found[0] if len(found) == 1 else None


def _policy_actions(document: Any) -> set[str] | None:
    try:
        policy = json.loads(document) if isinstance(document, str) else document
        if not isinstance(policy, dict) or "Statement" not in policy:
            return None
        statements = policy["Statement"]
        if isinstance(statements, dict):
            statements = [statements]
        if not isinstance(statements, list):
            return None
        actions: set[str] = set()
        for statement in statements:
            if (not isinstance(statement, dict) or statement.get("Effect") != "Allow"
                    or set(statement) - {"Sid", "Effect", "Action", "Resource"}):
                return None
            resources = statement.get("Resource")
            resources = [resources] if isinstance(resources, str) else resources
            if not isinstance(resources, list) or not resources or not all(isinstance(r, str) and r for r in resources):
                return None
            items = statement.get("Action")
            items = [items] if isinstance(items, str) else items
            if not isinstance(items, list) or not items or not all(isinstance(a, str) and ACTION.fullmatch(a) for a in items):
                return None
            actions.update(items)
            if len(actions) > MAX_ACTIONS:
                return None
        return actions
    except (ValueError, TypeError):
        return None


def profile_actions(resources: Sequence[dict[str, Any]], profile_reference: str) -> list[str] | None:
    def of(type_: str) -> list[dict]:
        return [r["values"] for r in resources if r["type"] == type_]

    profile = _one(of("aws_iam_instance_profile"), profile_reference)
    role = _one(of("aws_iam_role"), profile.get("role")) if profile else None
    if role is None or role.get("permissions_boundary"):
        return None
    role_name = _name(role.get("name") or role.get("id"))
    if not role_name:
        return None

    inline = role.get("inline_policy") or ()
    if not isinstance(inline, (list, tuple)) or not all(isinstance(p, dict) for p in inline):
        return None
    documents = [p.get("policy") for p in inline]
    documents.extend(p.get("policy") for p in of("aws_iam_role_policy") if _name(p.get("role")) == role_name)
    attached = set(role.get("managed_policy_arns") or ())
    attached.update(p.get("policy_arn") for p in of("aws_iam_role_policy_attachment") if _name(p.get("role")) == role_name)
    attached.update(p.get("policy_arn") for p in of("aws_iam_policy_attachment")
                    if any(_name(r) == role_name for r in p.get("roles") or ()))
    for arn in attached:
        # Match full ARNs: a policy with the same basename in another account is not evidence.
        matches = [p for p in of("aws_iam_policy") if arn and p.get("arn") == arn]
        if len(matches) != 1:
            return None
        documents.append(matches[0].get("policy"))
    if not documents:
        return None
    actions: set[str] = set()
    for document in documents:
        parsed = _policy_actions(document)
        if parsed is None:
            return None
        actions.update(parsed)
        if len(actions) > MAX_ACTIONS:
            return None
    return sorted(actions)
