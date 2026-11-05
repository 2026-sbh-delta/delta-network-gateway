"""R02/R07: 실제 graph가 발급한 Ed25519 승인과 설정 결합·누락 시 차단."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from delivery_fakes import (TARGET, TEST_RELEASE_CONFIG, TEST_RELEASE_PUBLIC_KEY_HEX,
                            DeliveryOptions, Harness, bundle)
from fakes import COMMIT, REPO
from test_delivery_release import _await_release

NOW = 1_800_000_000


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify(signature, message):
    public = bytes.fromhex(TEST_RELEASE_PUBLIC_KEY_HEX)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ModuleNotFoundError:
        # pipeline에 없는 라이브러리 때문에 수집/RED를 깨뜨리지 않는다.
        # 이미 설치된 staging interpreter를 독립 서명 검증기로만 사용한다.
        interpreter = Path(__file__).resolve().parents[2] / "delta-staging/.venv/bin/python"
        result = subprocess.run(
            [str(interpreter), "-c",
             "import json,sys; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey; "
             "p,s,m=json.load(sys.stdin); Ed25519PublicKey.from_public_bytes(bytes.fromhex(p)).verify(bytes.fromhex(s),bytes.fromhex(m))"],
            input=json.dumps([public.hex(), signature.hex(), message.hex()]), text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, "독립 Ed25519 검증기가 서명을 거부했다"
    else:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)


def test_signature_verifier_accepts_the_public_rfc8032_vector_and_rejects_tampering():
    signature = bytes.fromhex("e5564300c360ac729086e2cc806e828a"
                              "84877f1eb8e5d974d873e06522490155"
                              "5fb8821590a33bacc61e39701cf9b46bd"
                              "25bf5f0595bbe24655141438e7a100b")
    _verify(signature, b"")
    # InvalidSignature는 cryptography가 없는 interpreter에서도 import하지 않는다.
    with pytest.raises(Exception):
        _verify(signature, b"tampered")


def test_harness_supplies_explicit_synthetic_keys_only_for_the_configured_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    h = Harness(target, options=DeliveryOptions(REPO, COMMIT, release_target=TARGET))
    assert h.deps.release_signing_key.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert h.deps.release_config == TEST_RELEASE_CONFIG
    no_target = tmp_path / "no-target"
    no_target.mkdir()
    plain = Harness(no_target)
    assert plain.deps.release_signing_key is None and plain.deps.release_config is None


def test_approval_has_a_real_ed25519_signature_and_binds_all_required_claims(tmp_path):
    h = Harness(tmp_path, options=DeliveryOptions(REPO, COMMIT, release_target=TARGET), now=lambda: NOW)
    h.drive()
    _await_release(h)
    assert h.release.params == [], "사람 승인 전에는 토큰을 Jenkins에 전달할 수 없다"
    h.drive("approve")
    assert len(h.release.params) == 1
    approval = h.release.params[0].get("APPROVAL")
    assert isinstance(approval, str) and approval, "서명된 APPROVAL JSON이 없다"
    token = json.loads(approval)
    claims = token.get("claims")
    assert isinstance(claims, dict)
    assert {"deployment_id", "commit_sha", "bundle_sha256", "target_id", "environment",
            "config_sha256", "exp", "nonce"} <= claims.keys()
    assert isinstance(claims["deployment_id"], str) and claims["deployment_id"]
    assert claims["commit_sha"] == COMMIT
    assert claims["bundle_sha256"] == bundle()["bundle_sha256"]
    assert claims["target_id"] == TARGET and claims["environment"] == "production"
    assert claims["config_sha256"] == hashlib.sha256(_canonical(h.deps.release_config)).hexdigest()
    assert type(claims["exp"]) is int and claims["exp"] == NOW + 600
    assert isinstance(claims["nonce"], str) and claims["nonce"]
    encoded = token.get("signature")
    assert isinstance(encoded, str) and encoded
    signature = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    assert len(signature) == 64
    _verify(signature, _canonical(claims))
    tampered = {**claims, "bundle_sha256": "0" * 64}
    with pytest.raises(Exception):
        _verify(signature, _canonical(tampered))


@pytest.mark.parametrize("missing", ["release_signing_key", "release_config"])
def test_missing_signing_key_or_target_config_fails_closed(tmp_path, missing):
    h = Harness(tmp_path, options=DeliveryOptions(REPO, COMMIT, release_target=TARGET), **{missing: None})
    assert getattr(h.deps, missing) is None  # fixture가 누락 값을 몰래 복원하지 않는다.
    h.drive()
    if h.pending():
        _await_release(h)
        h.drive("approve")
    assert h.release.params == [], "키/설정 누락을 자동 허용해 운영 job을 호출했다"
    assert h.snapshot()["outcome"] == "stopped", "서명할 수 없는 운영 요청을 성공 처리했다"


def test_target_config_mismatch_fails_closed_before_release(tmp_path):
    config = deepcopy(TEST_RELEASE_CONFIG)
    config["target_id"] = "different-target"
    h = Harness(tmp_path, options=DeliveryOptions(REPO, COMMIT, release_target=TARGET), release_config=config)
    h.drive()
    if h.pending():
        _await_release(h)
        h.drive("approve")
    assert h.release.params == [] and h.snapshot()["outcome"] == "stopped"
