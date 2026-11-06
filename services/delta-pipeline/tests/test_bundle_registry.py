"""빌드 묶음의 클라우드별 사본: AWS 운영·스테이징이 쓸 ECR 사본을 같은 digest로 담는다."""

from __future__ import annotations

import pytest
from delivery_fakes import DeliveryOptions, Harness, RoundJenkins, bundle
from fakes import COMMIT, REPO

from delta_pipeline.delivery import validate_bundle

ECR = "616723161368.dkr.ecr.ap-northeast-2.amazonaws.com/delta-lab-staging"


def test_build_copies_to_ecr_only_when_aws_uses_the_bundle(tmp_path):
    for name in ("gcp", "aws"):
        (tmp_path / name).mkdir()
    build = RoundJenkins(bundle)
    Harness(tmp_path / "gcp", build=build, ecr_image_repo=ECR).drive()
    assert "ECR_IMAGE_REPO" not in build.params[0], "GCP 만 쓰는 실행에서 ECR 복사 시간을 쓴다"
    build = RoundJenkins(bundle)
    Harness(tmp_path / "aws", build=build, ecr_image_repo=ECR,
            options=DeliveryOptions(REPO, COMMIT, staging_cloud="aws")).drive()
    assert build.params[0]["ECR_IMAGE_REPO"] == ECR


def test_registry_copies_must_keep_the_bundle_digests():
    value = bundle(COMMIT)
    digests = value["image_digests"]
    value["registry_images"] = {"gcp": value["images"],
                                "aws": {s: f"{ECR}@{digests[s]}" for s in ("frontend", "backend")}}
    assert validate_bundle(value, COMMIT)["registry_images"]["aws"]["backend"].endswith(digests["backend"])
    value["registry_images"]["aws"]["backend"] = f"{ECR}@sha256:{'f' * 64}"
    with pytest.raises(ValueError):
        validate_bundle(value, COMMIT)


def test_release_uses_the_target_cloud_copy_of_the_bundle():
    """AWS 운영은 ECR 사본, GCP 운영은 AR 원본을 넘기고 같은 사본으로 실행 결과를 대조한다."""
    from delta_pipeline.steps import Steps

    value = validate_bundle(bundle(COMMIT), COMMIT)
    digests = value["image_digests"]
    aws = {s: f"{ECR}@{digests[s]}" for s in ("frontend", "backend")}
    value["registry_images"] = {"gcp": value["images"], "aws": aws}
    assert Steps._target_bundle(value, "aws")["images"] == aws
    assert Steps._target_bundle(value, "aws")["image_ref"] == aws["frontend"]
    assert Steps._target_bundle(value, "gcp")["images"] == value["images"]
