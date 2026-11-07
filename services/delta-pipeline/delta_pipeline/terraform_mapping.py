"""Select one GCP workload VM from the verified Diff result."""

from delta_pipeline.diff_client import DiffInput, DiffResult


def workload_mapping(result: DiffResult, request: DiffInput, target: dict) -> dict:
    selectors = target["terraform_mapping"]
    if (
        not isinstance(selectors, dict)
        or set(selectors) != {"source_compute", "source_subnet"}
        or any(not isinstance(value, str) or not value for value in selectors.values())
        or target["cloud"] != "gcp"
        or target["database"]["mode"] != "existing"
    ):
        raise ValueError("차분 배포 대상은 기존 DB를 쓰는 GCP VM 하나여야 한다")

    mapping = result.assessment.mapping
    if mapping is None or mapping.network_mode != "reuse" or mapping.region != target["region"]:
        raise ValueError("차분 결과에 대상 리전의 기존 서브넷 매핑이 없다")

    def selected(resource: str, rule: str, source: str) -> str:
        rows = [row for row in mapping.rows if row.resource == resource and row.source == source]
        if (len(rows) != 1 or rows[0].rule_id != rule
                or rows[0].grade not in ("exact", "approximate") or not rows[0].target):
            raise ValueError("배포에 필요한 컴퓨트 또는 서브넷 매핑을 확정할 수 없다")
        return rows[0].target

    profile = selected("compute", "MAP-CMP-001", selectors["source_compute"])
    subnet = selected("subnet", "MAP-NET-001", selectors["source_subnet"])
    candidates = [
        item for item in request.target.existing_subnets or ()
        if item.tier == "private" and f"{item.name} ({item.cidr})" == subnet
    ]
    if profile not in ("e2-micro", "e2-small") or len(candidates) != 1:
        raise ValueError("지원되는 GCP VM 사양과 등록된 사설 서브넷이 필요하다")
    return {
        # The verified two-container release runtime requires at least 2 GiB.
        "profile_id": "e2-small",
        "mapped_profile_id": profile,
        "subnet_name": candidates[0].name,
        "subnet_cidr": str(candidates[0].cidr),
        "state_serial": result.source.serial,
        "diff_request_sha256": request.fingerprint,
    }
