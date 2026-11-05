"""compose-delivery-20261106: 기존 fake의 호출 기록을 유지하는 schema2 시험 자료.

Options/Deps 확장은 아직 없는 설정을 기존 공개 그래프 경계에 전달하기 위한
시험 전용 입력이다. 새 production 모듈이나 함수의 존재를 RED 조건으로 삼지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fakes import COMMIT, DIGEST, FIX_COMMIT, IMAGE_REPO, REPO, FakeDast, FakeJenkins, FakeSast, FakeStaging
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from delta_pipeline.graph import build_graph
from delta_pipeline.model import Board, Options
from delta_pipeline.steps import Deps, Steps

TARGET = "gcp-production-demo"
# RFC 8032 section 7.1 test 1의 공개 시험 벡터. 실계정 키가 아니다.
_TEST_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
TEST_RELEASE_SIGNING_KEY = (b"-----BEGIN PRIVATE KEY-----\n"
                            + base64.b64encode(bytes.fromhex("302e020100300506032b657004220420") + _TEST_SEED)
                            + b"\n-----END PRIVATE KEY-----\n")
TEST_RELEASE_PUBLIC_KEY_HEX = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
TEST_RELEASE_CONFIG = {"target_id": TARGET, "cloud": "gcp", "environment": "production",
                       "project": "synthetic-project", "zone": "asia-northeast3-a",
                       "vm_name": "synthetic-vm", "compose_project": "synthetic-app",
                       "install_dir": "/opt/synthetic-app", "frontend_url": "https://production.example"}
_DEFAULT_RELEASE = object()


def bundle_hash(result):
    value = {key: result[key] for key in ("schema", "commit", "platform", "image_digests")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def bundle(commit=COMMIT):
    digests = {"frontend": DIGEST, "backend": "sha256:" + "b" * 64}
    if commit == FIX_COMMIT:
        digests = {"frontend": "sha256:" + "c" * 64, "backend": "sha256:" + "d" * 64}
    value = {"schema": 2, "commit": commit, "platform": "linux/amd64", "image_digests": digests,
             "images": {name: f"{IMAGE_REPO}-{name}@{digest}" for name, digest in digests.items()},
             "image_ref": f"{IMAGE_REPO}-frontend@{digests['frontend']}", "image_digest": digests["frontend"]}
    return {**value, "bundle_sha256": bundle_hash(value)}


def stage_result(value=None):
    value = deepcopy(value or bundle())
    return {**value, "state": "ready", "url": "http://staging.example", "revision": "revision-2",
            "scanner_ip": "192.0.2.10", "data_reset_remaining": 0, "database_version_num": 180003,
            "steps": [{"name": "image_digest", "detail": value["image_digest"]},
                      {"name": "data_reset", "detail": "reset-job: 남은 행 0"}]}


def release_result(value=None):
    return {**deepcopy(value or bundle()), "state": "verified", "target_id": TARGET,
            "environment": "production", "database_version_num": 180003}


class RoundJenkins(FakeJenkins):
    """기존 FakeJenkins와 달리 오류 시험의 commit을 요청값으로 덮어쓰지 않는다."""

    def __init__(self, factory, *, final="SUCCESS"):
        super().__init__({}, final=final)
        self.factory = factory

    def file(self, build, name):
        if name != "result.json":
            return None
        return deepcopy(self.factory(self.params[build - 1]["COMMIT"]))


class CloudDast(FakeDast):
    def __init__(self, *args, cloud="gcp", **kwargs):
        super().__init__(*args, **kwargs)
        self.cloud = cloud

    def submit(self, run_id, binding, report, reverify_of=None):
        # 부모 fake의 GCP 고정 assertion만 클라우드 매개변수로 확장한다.
        # 원본 binding은 그대로 보관하고 부모의 idempotency·report 검사를 유지한다.
        compatible = {**binding, "execution_cloud": "gcp"}
        if run_id in self.bindings:
            assert self.bindings[run_id] == binding
            return self.view(run_id)
        result = super().submit(run_id, compatible, report, reverify_of)
        self.bindings[run_id] = deepcopy(binding)
        return result


@dataclass(frozen=True)
class DeliveryOptions(Options):
    staging_cloud: str = "gcp"
    release_target: str | None = None


@dataclass(frozen=True)
class DeliveryDeps(Deps):
    release_jenkins: Any = None
    release_signing_key: bytes | None = None
    release_config: dict | None = None


class Harness:
    """실제 Steps·StateGraph·interrupt를 실행하고 외부 시스템만 fake로 대체한다."""

    def __init__(self, tmp_path, *, options=None, build=None, deploy=None, dast=None, sast=None,
                 release=None, sleep=None, release_signing_key=_DEFAULT_RELEASE,
                 release_config=_DEFAULT_RELEASE, **extra):
        self.options = options or DeliveryOptions(REPO, COMMIT)
        # 정상 운영 시험은 키·설정을 명시적으로 제공한다. 명시한 None은 복원하지 않는다.
        if release_signing_key is _DEFAULT_RELEASE:
            release_signing_key = TEST_RELEASE_SIGNING_KEY if self.options.release_target == TARGET else None
        if release_config is _DEFAULT_RELEASE:
            release_config = deepcopy(TEST_RELEASE_CONFIG) if self.options.release_target == TARGET else None
        self.build = build or RoundJenkins(bundle)
        self.deploy = deploy or RoundJenkins(lambda commit: stage_result(bundle(commit)))
        self.dast = dast or CloudDast(cloud=self.options.staging_cloud)
        self.staging = FakeStaging()
        self.release = release or RoundJenkins(lambda commit: release_result(bundle(commit)))
        self.board = Board(tmp_path / "board.json", self.options, "delivery-test")
        self.deps = DeliveryDeps(run_id="delivery-test", options=self.options, board=self.board,
                                 sast=sast or FakeSast(), dast=self.dast, build_jenkins=self.build,
                                 deploy_jenkins=self.deploy, staging=self.staging, image_repo=IMAGE_REPO,
                                 sleep=sleep or (lambda _: None), release_jenkins=self.release,
                                 release_signing_key=release_signing_key, release_config=release_config, **extra)
        self.steps = Steps(self.deps)
        self.graph = build_graph(self.deps, InMemorySaver())
        self.config = {"configurable": {"thread_id": "delivery-test"}}
        self.visited = []

    def drive(self, answer=None):
        command = {} if answer is None else Command(resume=answer)
        for update in self.graph.stream(command, self.config, durability="sync"):
            self.visited.extend(name for name in update if name != "__interrupt__")
        return self.graph.get_state(self.config).values

    def pending(self):
        return [item.value for task in self.graph.get_state(self.config).tasks for item in task.interrupts]

    def snapshot(self):
        return self.board.snapshot()
