"""코드에 등록된 대상·경로·페이로드만 실행 가능하게 만든다."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PACKAGE = Path(__file__).resolve().parent
TARGETS_DIR = PACKAGE / "targets"
PAYLOADS_DIR = PACKAGE / "payloads"
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class RegistryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RouteDefinition(RegistryModel):
    id: str = Field(pattern=ID_PATTERN)
    kind: Literal["stored_xss"]
    board_path: str
    create_path: str
    create_link_selector: str = Field(min_length=1, max_length=300)
    field: str = Field(pattern=ID_PATTERN)
    input_selector: str = Field(min_length=1, max_length=300)
    title_selector: str | None = Field(default=None, max_length=300)
    title_value: str = Field(default="DAST 검증 게시글", min_length=1, max_length=120)
    submit_selector: str = Field(min_length=1, max_length=300)
    submit_path: str
    saved_detail_link_selector: str = Field(min_length=1, max_length=300)
    detail_path_regex: str = Field(min_length=3, max_length=500)
    output_selector: str = Field(min_length=1, max_length=300)
    asset_paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("board_path", "create_path", "submit_path")
    @classmethod
    def exact_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("등록 경로는 query·fragment 없는 절대 경로여야 합니다.")
        return value

    @field_validator("asset_paths")
    @classmethod
    def asset_paths_are_exact(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(
            not item.startswith("/") or "?" in item or "#" in item for item in values
        ):
            raise ValueError("asset_paths는 중복 없는 절대 경로여야 합니다.")
        return values


class RepoDefinition(RegistryModel):
    """수정안·PR 대상 저장소. path는 dast-agent 기준 상대 경로 또는 절대 경로, remote는 비워 두면 PR 없이 로컬 커밋까지만 한다."""
    path: str = Field(min_length=1, max_length=500)
    remote: str = Field(default="", max_length=300)
    base_branch: str = Field(default="main", pattern=r"^[A-Za-z0-9._/-]{1,100}$")


class TargetDefinition(RegistryModel):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    origin: str = Field(min_length=1, max_length=300)
    scenarios: list[str] = Field(default_factory=list, max_length=20)
    # 에이전트 모드는 셀렉터 없이 시작 경로와 허용 경로 범위만 사용한다.
    entry_path: str = "/"
    allowed_path_prefixes: list[str] = Field(default_factory=lambda: ["/"], min_length=1, max_length=20)
    routes: list[RouteDefinition] = Field(default_factory=list, max_length=30)
    repo: RepoDefinition | None = None

    @field_validator("entry_path")
    @classmethod
    def entry_is_absolute(cls, value: str) -> str:
        if not value.startswith("/") or "#" in value:
            raise ValueError("entry_path는 fragment 없는 절대 경로여야 합니다.")
        return value

    @field_validator("allowed_path_prefixes")
    @classmethod
    def prefixes_are_absolute(cls, values: list[str]) -> list[str]:
        if any(not item.startswith("/") or "?" in item or "#" in item for item in values):
            raise ValueError("allowed_path_prefixes는 절대 경로여야 합니다.")
        return values

    @model_validator(mode="after")
    def unique_routes(self):
        if len({route.id for route in self.routes}) != len(self.routes):
            raise ValueError("route id는 target 안에서 중복될 수 없습니다.")
        return self


@dataclass(frozen=True)
class ResolvedTarget:
    target_id: str
    target_label: str
    origin: str
    entry_path: str
    allowed_path_prefixes: tuple[str, ...]

    def permits(self, path: str) -> bool:
        return any(path == prefix.rstrip("/") or path.startswith(prefix if prefix.endswith("/") else prefix + "/")
                   or prefix == "/" for prefix in self.allowed_path_prefixes)


@dataclass(frozen=True)
class ResolvedRoute:
    target_id: str
    target_label: str
    origin: str
    route_id: str
    kind: str
    board_path: str
    create_path: str
    create_link_selector: str
    field: str
    input_selector: str
    title_selector: str | None
    title_value: str
    submit_selector: str
    submit_path: str
    saved_detail_link_selector: str
    detail_path_regex: re.Pattern[str]
    output_selector: str
    asset_paths: tuple[str, ...]

    def permits(self, method: str, path: str) -> bool:
        if method == "GET":
            return path in {self.board_path, self.create_path, *self.asset_paths} or bool(
                self.detail_path_regex.fullmatch(path)
            )
        return method == "POST" and path == self.submit_path


def _render(value: str, scenario: str) -> str:
    rendered = value.replace("{scenario}", scenario)
    if "{scenario}" in rendered:
        raise ValueError("scenario 경로를 해석하지 못했습니다.")
    return rendered


def _origin(value: str, target_port: int) -> str:
    rendered = value.replace("{target_port}", str(target_port))
    if "{" in rendered or "}" in rendered:
        raise ValueError("origin에는 {target_port} 외의 변수를 사용할 수 없습니다.")
    parsed = urlsplit(rendered)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("origin은 경로·인증정보가 없는 명시적 http(s) origin이어야 합니다.")
    return rendered.rstrip("/")


class RegistryCatalog:
    def __init__(self, targets: dict[str, TargetDefinition], payloads: dict[str, str]):
        self.targets = targets
        self.payloads = payloads

    @classmethod
    def load(cls) -> "RegistryCatalog":
        targets: dict[str, TargetDefinition] = {}
        for path in sorted(TARGETS_DIR.glob("*.json")):
            if path.is_symlink():
                raise ValueError("target registry에는 symlink를 사용할 수 없습니다.")
            target = TargetDefinition.model_validate_json(path.read_text())
            if path.stem != target.id or target.id in targets:
                raise ValueError("target 파일명과 id가 일치하고 중복되지 않아야 합니다.")
            targets[target.id] = target
        payloads: dict[str, str] = {}
        for path in sorted(PAYLOADS_DIR.glob("*.html")):
            if path.is_symlink() or not re.fullmatch(ID_PATTERN, path.stem):
                raise ValueError("payload 파일명은 안전한 id여야 하며 symlink일 수 없습니다.")
            template = path.read_text().strip()
            if not template or len(template.encode()) > 8192 or "{{DAST_NONCE}}" not in template:
                raise ValueError("payload 템플릿은 8KB 이하이며 DAST_NONCE를 포함해야 합니다.")
            payloads[path.stem] = template
        if not targets or not payloads:
            raise ValueError("target과 payload registry가 비어 있습니다.")
        return cls(targets, payloads)

    def option_sets(self) -> dict[str, list[str]]:
        routes = sorted({route.id for target in self.targets.values() for route in target.routes})
        fields = sorted({route.field for target in self.targets.values() for route in target.routes})
        return {
            "target_ids": sorted(self.targets),
            "route_ids": routes,
            "fields": fields,
            "payload_ids": sorted(self.payloads),
        }

    def combinations(self) -> list[dict[str, str]]:
        return [
            {
                "target_id": target.id,
                "route_id": route.id,
                "field": route.field,
                "payload_id": payload_id,
            }
            for target in self.targets.values()
            for route in target.routes
            for payload_id in self.payloads
        ]

    def match_route(self, target_id: str, scenario: str, method: str, path: str,
                    field: str | None, stored: bool) -> RouteDefinition | None:
        """SAST가 알려준 입력 라우트·칸과 정확히 맞는 등록 route. 없거나 여러 개면 None."""
        target = self.targets.get(target_id)
        if target is None or not stored or method != "POST":
            return None
        matches = [route for route in target.routes
                   if route.kind == "stored_xss" and _render(route.submit_path, scenario) == path
                   and (field is None or route.field == field)]
        return matches[0] if len(matches) == 1 else None

    def resolve_target(self, target_id: str, scenario: str, target_port: int) -> ResolvedTarget:
        target = self.targets.get(target_id)
        if target is None:
            raise ValueError("등록되지 않은 target입니다.")
        if target.scenarios and scenario not in target.scenarios:
            raise ValueError("target에 등록되지 않은 scenario입니다.")
        return ResolvedTarget(
            target_id=target.id,
            target_label=target.label,
            origin=_origin(target.origin, target_port),
            entry_path=_render(target.entry_path, scenario),
            allowed_path_prefixes=tuple(_render(item, scenario) for item in target.allowed_path_prefixes),
        )

    def resolve(self, target_id: str, route_id: str, field: str, payload_id: str,
                scenario: str, target_port: int) -> tuple[ResolvedRoute, str]:
        target = self.targets.get(target_id)
        if target is None:
            raise ValueError("등록되지 않은 target입니다.")
        route = next((item for item in target.routes if item.id == route_id), None)
        if route is None or route.field != field:
            raise ValueError("등록되지 않은 route·field 조합입니다.")
        if payload_id not in self.payloads:
            raise ValueError("등록되지 않은 payload입니다.")
        if target.scenarios and scenario not in target.scenarios:
            raise ValueError("target에 등록되지 않은 scenario입니다.")
        origin = _origin(target.origin, target_port)
        regex = re.compile(_render(route.detail_path_regex, scenario))
        resolved = ResolvedRoute(
            target_id=target.id,
            target_label=target.label,
            origin=origin,
            route_id=route.id,
            kind=route.kind,
            board_path=_render(route.board_path, scenario),
            create_path=_render(route.create_path, scenario),
            create_link_selector=_render(route.create_link_selector, scenario),
            field=route.field,
            input_selector=route.input_selector,
            title_selector=route.title_selector,
            title_value=route.title_value,
            submit_selector=route.submit_selector,
            submit_path=_render(route.submit_path, scenario),
            saved_detail_link_selector=route.saved_detail_link_selector,
            detail_path_regex=regex,
            output_selector=route.output_selector,
            asset_paths=tuple(_render(item, scenario) for item in route.asset_paths),
        )
        return resolved, self.payloads[payload_id]


def load_catalog() -> RegistryCatalog:
    return RegistryCatalog.load()
