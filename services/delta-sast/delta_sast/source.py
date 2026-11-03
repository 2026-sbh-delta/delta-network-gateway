"""점검할 소스 가져오기. 허용 목록 밖의 주소·경로는 거절한다.

- 원격: https 주소가 허용 접두어로 시작해야 하고 commit이 필요하다. 그 커밋 하나만 얕게 받는다.
- 로컬: 허용 루트 아래 경로만. commit이 있으면 그 커밋을 git archive로 풀어서 점검한다.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT = 120


class SourceRejected(Exception):
    """요청한 저장소가 허용 목록 밖이거나 형식이 맞지 않는다."""


class SourceError(Exception):
    """허용된 저장소지만 가져오지 못했다."""


@dataclass(frozen=True)
class SourcePolicy:
    repo_prefixes: tuple[str, ...]
    local_roots: tuple[Path, ...]

    @classmethod
    def from_env(cls) -> "SourcePolicy":
        prefixes = os.environ.get("SAST_REPO_PREFIXES", "https://github.com/2026-sbh-delta/")
        roots = os.environ.get("SAST_LOCAL_ROOTS", "")
        return cls(tuple(p for p in prefixes.split(",") if p),
                   tuple(Path(r).resolve() for r in roots.split(os.pathsep) if r))


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT,
                              check=False, env=env)
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git {args[0]}이 {GIT_TIMEOUT}초 안에 끝나지 않았다") from exc
    if done.returncode != 0:
        # 원문에 주소·자격 정보가 섞일 수 있어 첫 줄만 짧게 남긴다
        first = (done.stderr.strip().splitlines() or ["알 수 없는 오류"])[0][:200]
        raise SourceError(f"git {args[0]} 실패: {first}")
    return done.stdout


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def fetch(repo: str, commit: str | None, dest: Path, policy: SourcePolicy) -> tuple[Path, bool]:
    """소스를 준비한다. (점검할 경로, 끝나고 지워야 하는지)를 돌려준다."""
    if repo.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        if not repo.startswith("https://") or not any(repo.startswith(p) for p in policy.repo_prefixes):
            raise SourceRejected("허용된 https 저장소 주소가 아니다")
        if "@" in repo.split("://", 1)[1].split("/", 1)[0]:
            raise SourceRejected("주소에 자격 정보를 넣지 않는다")
        if not commit:
            raise SourceRejected("원격 저장소는 commit이 필요하다")
        dest.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", cwd=dest)
        _git("fetch", "-q", "--depth", "1", repo, commit, cwd=dest)
        _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
        # .git은 담당자 확인(커밋 작성자)에 쓰고, 점검이 끝나면 작업 폴더와 함께 지운다
        return dest, True

    path = Path(repo).resolve()
    if not policy.local_roots or not _inside(path, policy.local_roots):
        raise SourceRejected("허용된 로컬 경로가 아니다")
    if not path.is_dir():
        raise SourceRejected("로컬 경로가 없다")
    if commit is None:
        return path, False
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest.with_suffix(".tar")
    try:
        _git("-C", str(path), "archive", "--format=tar", "-o", str(archive), commit)
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")
    finally:
        archive.unlink(missing_ok=True)
    return dest, True


def scan_root(base: Path, subdir: str | None) -> Path:
    if not subdir:
        return base
    root = (base / subdir).resolve()
    if base.resolve() not in root.parents and root != base.resolve():
        raise SourceRejected("subdir가 저장소 밖을 가리킨다")
    if not root.is_dir():
        raise SourceRejected("subdir가 없다")
    return root
