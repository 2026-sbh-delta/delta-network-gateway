import getpass
import hashlib
import ipaddress
import os
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from argon2 import PasswordHasher, extract_parameters
from argon2.low_level import Type
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PASSWORD_HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLATFORM_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    database_url: SecretStr
    login_id: str
    password_hash: SecretStr
    public_origin: str
    local_mode: bool = False
    session_hours: int = 8
    web_dist: Path | None = None
    build_provider: Literal["jenkins", "codebuild"] = "jenkins"
    build_project: str = ""
    build_repository: str = ""
    build_region: str = "ap-northeast-2"
    jenkins_url: str = ""
    jenkins_job: str = "delta-platform-image"
    jenkins_user: str = ""
    jenkins_token: SecretStr = SecretStr("")
    jenkins_ca_base64: str = ""
    sandbox_target: str = ""
    sast_url: str = ""
    sast_token: SecretStr = SecretStr("")
    sast_ca_base64: str = ""
    dast_url: str = ""
    dast_token: SecretStr = SecretStr("")
    dast_ca_base64: str = ""
    observability_region: str = "ap-northeast-2"

    @model_validator(mode="after")
    def validate_settings(self):
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL psycopg 연결이 필요합니다.")
        if not 1 <= len(self.login_id) <= 128 or not 1 <= self.session_hours <= 24:
            raise ValueError("로그인 ID 또는 세션 만료 설정이 올바르지 않습니다.")
        try:
            params = extract_parameters(self.password_hash.get_secret_value())
        except Exception:
            raise ValueError("유효한 Argon2id 해시가 필요합니다.") from None
        if (
            params.type != Type.ID
            or not 19456 <= params.memory_cost <= 262144
            or not 2 <= params.time_cost <= 10
            or not 1 <= params.parallelism <= 8
        ):
            raise ValueError("Argon2id 비용 설정이 허용 범위를 벗어났습니다.")
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or self.public_origin != f"{origin.scheme}://{origin.netloc}"
        ):
            raise ValueError("PUBLIC_ORIGIN은 경로 없는 웹 origin이어야 합니다.")
        if self.local_mode:
            try:
                loopback = ipaddress.ip_address(origin.hostname).is_loopback
            except ValueError:
                loopback = origin.hostname == "localhost"
            if not loopback:
                raise ValueError("로컬 모드는 loopback 주소만 허용합니다.")
        elif origin.scheme != "https":
            raise ValueError("운영 origin은 HTTPS가 필요합니다.")
        return self

    @property
    def credential_version(self):
        return hashlib.sha256(
            (self.login_id + "\0" + self.password_hash.get_secret_value()).encode()
        ).hexdigest()


def initialize():
    """Create ignored local config without printing secrets."""
    backend = Path(__file__).resolve().parents[2]
    platform = backend.parent
    destinations = (backend / ".env", platform / ".env")
    if any(p.exists() for p in destinations):
        raise SystemExit("기존 .env가 있어 중단합니다. 설정을 직접 수정하세요.")
    login = input("고정 로그인 ID: ").strip()
    password = getpass.getpass("고정 비밀번호: ")
    if not 1 <= len(login) <= 128 or not 1 <= len(password) <= 1024:
        raise SystemExit("ID와 비밀번호를 입력하세요.")
    if password != getpass.getpass("비밀번호 확인: "):
        raise SystemExit("비밀번호가 일치하지 않습니다.")
    if any(c in login for c in "\r\n'\\"):
        raise SystemExit("ID에 줄바꿈·따옴표·역슬래시를 사용할 수 없습니다.")
    db_password = secrets.token_urlsafe(32)
    content = (
        f"PLATFORM_DATABASE_URL='postgresql+psycopg://platform:{db_password}@127.0.0.1:55432/platform'\n"
        f"PLATFORM_LOGIN_ID='{login}'\n"
        f"PLATFORM_PASSWORD_HASH='{PASSWORD_HASHER.hash(password)}'\n"
        "PLATFORM_PUBLIC_ORIGIN=http://127.0.0.1:8088\nPLATFORM_LOCAL_MODE=true\n"
    )
    for path, value in zip(
        destinations, (content, f"PLATFORM_DB_PASSWORD={db_password}\n"), strict=True
    ):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(value)
    print("로컬 .env 두 개를 생성했습니다. 비밀번호 원문은 저장하지 않았습니다.")
