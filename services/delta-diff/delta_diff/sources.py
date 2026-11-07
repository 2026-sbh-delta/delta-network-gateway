"""허용한 S3 위치의 Terraform state 만 읽는다. state 원본은 메모리에서만 다루고 남기지 않는다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_MAX_STATE_BYTES = 20_000_000
STATE_SUFFIX = ".tfstate"
MISSING_CODES = {"NoSuchKey", "404", "NotFound"}


class StateError(Exception):
    """state 를 읽지 못했다. 메시지에 state 내용은 넣지 않는다."""


class StateNotAllowed(StateError):
    pass


class StateNotFound(StateError):
    pass


class StateTooLarge(StateError):
    pass


class StateUnreadable(StateError):
    pass


@dataclass(frozen=True)
class StateLocation:
    bucket: str
    key: str


@dataclass(frozen=True)
class StateSnapshot:
    state: dict[str, Any]
    version_id: str | None


@dataclass(frozen=True)
class StateAllowlist:
    """`버킷/접두어` 목록. 접두어 아래의 `.tfstate` 만 허용한다."""

    entries: tuple[tuple[str, str], ...]

    @classmethod
    def parse(cls, text: str) -> StateAllowlist:
        entries = []
        for item in filter(None, (part.strip() for part in text.split(","))):
            bucket, _, prefix = item.partition("/")
            if not bucket or not prefix:
                raise ValueError(f"허용 목록 항목은 '버킷/접두어' 여야 한다: {item}")
            entries.append((bucket, prefix))
        if not entries:
            raise ValueError("허용할 state 위치가 하나 이상 필요하다")
        return cls(tuple(entries))

    def allows(self, location: StateLocation) -> bool:
        key = location.key
        if not key.endswith(STATE_SUFFIX) or ".." in key.split("/"):
            return False
        return any(
            location.bucket == bucket and key.startswith(prefix)
            for bucket, prefix in self.entries
        )


class StateReader(Protocol):
    def read(self, location: StateLocation) -> StateSnapshot: ...


class S3StateReader:
    def __init__(self, client: Any, max_bytes: int = DEFAULT_MAX_STATE_BYTES) -> None:
        self._client = client
        self._max_bytes = max_bytes

    def read(self, location: StateLocation) -> StateSnapshot:
        try:
            response = self._client.get_object(Bucket=location.bucket, Key=location.key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in MISSING_CODES:
                raise StateNotFound(
                    f"state 가 없다: s3://{location.bucket}/{location.key}"
                ) from exc
            raise StateUnreadable(
                f"state 를 읽지 못했다({code}): s3://{location.bucket}/{location.key}"
            ) from exc
        except BotoCoreError as exc:
            raise StateUnreadable(f"S3 에 닿지 못했다: {type(exc).__name__}") from exc
        if response.get("ContentLength", 0) > self._max_bytes:
            raise StateTooLarge(f"state 가 {self._max_bytes} 바이트를 넘는다")
        body = response["Body"].read(self._max_bytes + 1)
        if len(body) > self._max_bytes:
            raise StateTooLarge(f"state 가 {self._max_bytes} 바이트를 넘는다")
        try:
            state = json.loads(body)
        except ValueError as exc:
            raise StateUnreadable("state 가 JSON 이 아니다") from exc
        if not isinstance(state, dict):
            raise StateUnreadable("state 가 JSON 객체가 아니다")
        return StateSnapshot(state=state, version_id=response.get("VersionId"))


class AssumedRoleStateReader:
    """운영자가 지정한 조회 역할만 위임받는다. 역할·ExternalId는 요청에서 받지 않는다.

    호출마다 새 단기 세션을 받아 장시간 실행 후 만료된 자격증명을 재사용하지 않는다.
    대상 역할에는 별도로 정확한 state 객체의 GetObject 권한이 필요하다.
    """

    def __init__(self, sts: Any, role_arn: str, external_id: str, *, region: str | None = None,
                 client_factory: Any = None) -> None:
        if not re.fullmatch(r"arn:aws:iam::\d{12}:role/[A-Za-z0-9_+=,.@/-]+", role_arn) or not external_id:
            raise ValueError("state 조회 역할 ARN과 ExternalId가 필요하다")
        if client_factory is None:
            import boto3
            client_factory = boto3.client
        self._sts, self._role, self._external_id = sts, role_arn, external_id
        self._region, self._client_factory = region, client_factory

    def read(self, location: StateLocation) -> StateSnapshot:
        try:
            credentials = self._sts.assume_role(
                RoleArn=self._role, ExternalId=self._external_id,
                RoleSessionName="delta-diff-state", DurationSeconds=900,
            )["Credentials"]
        except (ClientError, BotoCoreError) as exc:
            raise StateUnreadable("state 조회 역할을 위임받지 못했다") from exc
        client = self._client_factory(
            "s3", region_name=self._region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
        )
        try:
            return S3StateReader(client).read(location)
        finally:
            client.close()
