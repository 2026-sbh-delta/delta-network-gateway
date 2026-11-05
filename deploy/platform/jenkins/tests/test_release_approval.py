"""진짜 Ed25519 서명·임시 SQLite로 승인과 중복 접수를 검증한다."""

import base64
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .delivery_support import canonical_hash

NOW = 1_800_000_000


@pytest.fixture
def signer(release_request):
    # 아래 키는 매 시험 생성하는 공개 fixture다. 기존 키 파일/환경을 읽지 않는다.
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign(request=None, **changes):
        req = request or release_request
        claims = {
            "deployment_id": req["deployment_id"], "commit_sha": req["commit_sha"],
            "bundle_sha256": req["bundle"]["bundle_sha256"],
            "target_id": req["target"]["target_id"], "environment": req["target"]["environment"],
            "config_sha256": req["config_sha256"], "exp": NOW + 600, "nonce": "nonce-a",
            **changes,
        }
        payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        signature = base64.urlsafe_b64encode(key.sign(payload)).decode().rstrip("=")
        return {"claims": claims, "signature": signature}

    return public, sign


def sqlite_rows(path):
    assert path.exists(), "접수 기록이 실제 SQLite에 보존되지 않았다"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if not row[0].startswith("sqlite_")]
        return {table: db.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall()
                for table in tables}


def reserve(module, request, token, public, db):
    return module.verify_and_reserve(request, token, public, db, now=NOW)


def test_valid_approval_persists_and_same_deployment_reuses_reservation(release_module, release_request, signer, tmp_path):
    public, sign = signer
    db = tmp_path / "release.sqlite3"
    first = reserve(release_module, release_request, sign(), public, db)
    before = sqlite_rows(db)
    second = reserve(release_module, release_request, sign(), public, db)
    assert first["deployment_id"] == second["deployment_id"] == "deployment-a"
    assert first["state"] == second["state"] == "reserved"
    assert sqlite_rows(db) == before, "동일 deployment_id 재조회가 nonce/작업을 추가 생성했다"
    assert "nonce-a" in repr(before) and "deployment-a" in repr(before)


@pytest.mark.parametrize("change", [
    {"target_id": "other-target"}, {"environment": "staging"}, {"deployment_id": "other-id"},
    {"commit_sha": "d" * 40}, {"bundle_sha256": "f" * 64}, {"config_sha256": "e" * 64},
    {"exp": NOW}, {"exp": NOW - 1}, {"nonce": ""},
])
def test_signed_but_wrong_or_expired_claims_are_rejected(release_module, release_request, signer, tmp_path, change):
    public, sign = signer
    db = tmp_path / "release.sqlite3"
    with pytest.raises(ValueError):
        reserve(release_module, release_request, sign(**change), public, db)
    if db.exists():
        assert all(not rows for rows in sqlite_rows(db).values())


def test_forged_signature_is_rejected_before_reserving(release_module, release_request, signer, tmp_path):
    public, sign = signer
    token = sign()
    raw = bytearray(base64.urlsafe_b64decode(token["signature"] + "=="))
    raw[0] ^= 1
    token["signature"] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with pytest.raises(ValueError):
        reserve(release_module, release_request, token, public, tmp_path / "release.sqlite3")


def test_same_nonce_cannot_authorize_new_deployment(release_module, release_request, signer, tmp_path):
    public, sign = signer
    db = tmp_path / "release.sqlite3"
    reserve(release_module, release_request, sign(), public, db)
    before = sqlite_rows(db)
    other = {**release_request, "deployment_id": "deployment-b"}
    with pytest.raises(ValueError):
        reserve(release_module, other, sign(other), public, db)
    assert sqlite_rows(db) == before


def test_same_deployment_cannot_change_backend_even_with_new_signed_token(release_module, release_request, signer, tmp_path):
    public, sign = signer
    db = tmp_path / "release.sqlite3"
    reserve(release_module, release_request, sign(), public, db)
    before = sqlite_rows(db)
    other = copy.deepcopy(release_request)
    other["bundle"]["image_digests"]["backend"] = "sha256:" + "d" * 64
    other["bundle"]["images"]["backend"] = "registry.invalid/app/backend@sha256:" + "d" * 64
    other["bundle"]["bundle_sha256"] = canonical_hash({key: other["bundle"][key]
        for key in ("schema", "commit", "platform", "image_digests")})
    with pytest.raises(ValueError):
        reserve(release_module, other, sign(other, nonce="nonce-b"), public, db)
    assert sqlite_rows(db) == before


def test_bundle_ref_suffix_is_checked_not_just_signed_hash(release_module, release_request, signer, tmp_path):
    public, sign = signer
    request = copy.deepcopy(release_request)
    request["bundle"]["images"]["backend"] = "registry.invalid/app/backend@sha256:" + "e" * 64
    with pytest.raises(ValueError):
        reserve(release_module, request, sign(request), public, tmp_path / "release.sqlite3")


def test_nonce_reservation_is_atomic_under_concurrent_different_ids(release_module, release_request, signer, tmp_path):
    public, sign = signer
    db = tmp_path / "release.sqlite3"
    requests = [{**release_request, "deployment_id": "deployment-" + str(i)} for i in range(2)]
    tokens = [sign(request) for request in requests]

    def attempt(pair):
        request, token = pair
        try:
            reserve(release_module, request, token, public, db)
            return "reserved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, zip(requests, tokens)))
    assert sorted(outcomes) == ["rejected", "reserved"], outcomes
    rows = sqlite_rows(db)
    assert "nonce-a" in repr(rows)
