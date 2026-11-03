"""비밀값 3단계 판정. 원문은 SARIF에도 LLM 요청에도 들어가면 안 된다."""

import asyncio
import shutil

import pytest

from delta_sast.classify import RuleCache
from delta_sast.judge import FakeClient
from delta_sast.scan import run_fast
from delta_sast.secret_judge import features

needs_opengrep = pytest.mark.skipif(shutil.which("opengrep") is None, reason="opengrep 없음")
REAL_LIKE = "Wq7" + "r2Lm9" + "xTz4!"  # 소스에 통째로 남지 않게 나눠 쓴다


@pytest.mark.parametrize("value, name, expected", [
    ("test", "password", "자리표시 단어"),
    ("test1234", "password", "자리표시 단어"),
    ("changeme", "DB_PASSWORD", "자리표시 단어"),
    ("AKIA" + "IOSFODNN7EXAMPLE", "aws_key", "공식 문서의 예시 키"),
    ("your_api_key_here", "API_KEY", "자리표시 조각 포함"),
    ("abc", "token", "8자 미만"),
    ("aaaaaaaaaa", "token", "같은 문자 반복"),
    (REAL_LIKE, "DB_PASSWORD", None),
])
def test_placeholder_features(value, name, expected):
    f = features(value, name, "assignment")
    assert f["placeholder"] == expected
    assert f["length"] == len(value) and "classes" in f and "entropy" in f
    assert value not in str(f)  # 특징에 원문이 남지 않는다


def write_app(root):
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "__init__.py").write_text("")
    (root / "svc" / "app.py").write_text(
        "import os\n"
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        'PASSWORD = "test"\n'
        f'DB_PASSWORD = "{REAL_LIKE}"\n'
        'API_TOKEN = os.environ.get("API_TOKEN", "fallback-token-value-99")\n'
        f'GH = "{"ghp_" + "Zx81Kq0LmT4vR7wE2yU9iO3pA6sD5fG1hJ8k"}"\n',
        encoding="utf-8")
    (root / "svc" / "fixtures").mkdir()
    (root / "svc" / "fixtures" / "__init__.py").write_text("")
    (root / "svc" / "fixtures" / "seed.py").write_text(f'ADMIN_PASSWORD = "{REAL_LIKE}0"\n', encoding="utf-8")


def scan(root, client):
    return asyncio.run(run_fast(root, client=client, cache=RuleCache()))


def by_name(result):
    return {s.name or f"{s.rule_id}@{s.loc.line}": s for s in result.secrets}


def result_secrets(named):
    return list(named.values())


@needs_opengrep
def test_rule_stage(tmp_path):
    write_app(tmp_path / "app")
    secrets = by_name(scan(tmp_path / "app", None))
    assert secrets["PASSWORD"].verdict == "placeholder"
    assert secrets["DB_PASSWORD"].verdict == "needs_review"
    assert secrets["API_TOKEN"].verdict == "env_default"
    assert [s.features["token_format"] for s in result_secrets(secrets) if s.verdict == "known_token"] == ["github_token"]
    assert secrets["ADMIN_PASSWORD"].verdict == "test_context"
    assert all(s.by == "rule" for s in secrets.values())


@needs_opengrep
def test_llm_stage_sees_masked_value_only(tmp_path):
    write_app(tmp_path / "app")
    client = FakeClient({})

    async def answer(system, user, schema):
        client.prompts.append(user)
        return {"usage": "test", "reason": "테스트 설정이다.", "cited": [{"path": "svc/app.py", "line": 5}]}

    client.ask = answer
    result = scan(tmp_path / "app", client)
    sent = "\n".join(client.prompts)
    assert client.prompts, "needs_review가 LLM 단계로 가야 한다"
    for raw in (REAL_LIKE, "fallback-token-value-99", "Zx81Kq0LmT4vR7wE2yU9iO3pA6sD5fG1hJ8k"):
        assert raw not in sent  # 판단 중인 값뿐 아니라 같은 파일의 다른 비밀값도 보내지 않는다
    assert f"값 특징: 길이 {len(REAL_LIKE)}" in sent and "<가림: 길이 40" in sent
    secret = by_name(result)["DB_PASSWORD"]
    assert (secret.verdict, secret.by) == ("test_context", "llm")


@needs_opengrep
def test_llm_stage_bad_citation_keeps_human_review(tmp_path):
    write_app(tmp_path / "app")
    client = FakeClient({})

    async def answer(system, user, schema):
        return {"usage": "test", "reason": "테스트 값", "cited": [{"path": "svc/app.py", "line": 999}]}

    client.ask = answer
    secret = by_name(scan(tmp_path / "app", client))["DB_PASSWORD"]
    assert (secret.verdict, secret.by) == ("needs_review", "rule")


@needs_opengrep
def test_sarif_secret_levels_and_no_raw_value(tmp_path):
    write_app(tmp_path / "app")
    sarif = scan(tmp_path / "app", None).sarif(run_id="s1")
    results = [r for r in sarif["runs"][0]["results"] if r["ruleId"] == "delta.secret"]
    kinds = {r["properties"]["secret"]["name"]: (r["kind"], r["properties"]["judgment"]["verdict"]) for r in results}
    assert kinds["PASSWORD"] == ("informational", "safe")
    assert kinds["DB_PASSWORD"] == ("review", "uncertain")
    assert kinds["API_TOKEN"] == ("fail", "candidate")
    assert kinds["ADMIN_PASSWORD"] == ("informational", "safe")
    text = str(sarif)
    assert REAL_LIKE not in text and "fallback-token-value-99" not in text


@needs_opengrep
def test_llm_runtime_answer_asks_owner_and_rotation(tmp_path):
    import subprocess

    root = tmp_path / "app"
    write_app(root)
    git = ["git", "-C", str(root), "-c", "user.name=담당자A", "-c", "user.email=a@example.com"]
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    client = FakeClient({})

    async def answer(system, user, schema):
        return {"usage": "runtime", "reason": "DB 연결에 그대로 쓰인다.", "cited": [{"path": "svc/app.py", "line": 5}]}

    client.ask = answer
    result = scan(root, client)
    secret = by_name(result)["DB_PASSWORD"]
    assert (secret.verdict, secret.by) == ("runtime_secret", "llm")
    assert secret.owner == {"name": "담당자A", "commit": secret.owner["commit"], "source": "blame"}
    assert any("담당자에게 알린다" in a for a in secret.actions)
    assert any(a.startswith("키 로테이션") for a in secret.actions)

    sarif = result.sarif(run_id="s2")
    res = next(r for r in sarif["runs"][0]["results"] if r["properties"].get("secret", {}).get("name") == "DB_PASSWORD")
    assert (res["kind"], res["level"], res["properties"]["judgment"]["verdict"]) == ("fail", "error", "vulnerable")
    assert res["properties"]["secret"]["notify_owner"] is True
    assert "키 로테이션" in res["message"]["text"]
    assert REAL_LIKE not in str(sarif)


@needs_opengrep
def test_known_token_gets_rotation_guide_and_placeholder_gets_none(tmp_path):
    write_app(tmp_path / "app")
    secrets = scan(tmp_path / "app", None).secrets
    token = next(s for s in secrets if s.verdict == "known_token")
    assert any("GitHub 설정에서 토큰을 폐기" in a for a in token.actions)
    assert token.owner is None  # git 저장소가 아니면 담당자는 비워 둔다
    placeholder = next(s for s in secrets if s.verdict == "placeholder")
    assert placeholder.actions == []
