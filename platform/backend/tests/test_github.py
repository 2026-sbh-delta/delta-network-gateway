import httpx
import pytest
from github_fixture import SHA, handler

from delta_platform.adapters.github import GitHubReader, SourceError


def inspect(data, transport):
    snapshot = data.model_dump() | {"repo_owner": "example", "repo_name": "orders"}
    return GitHubReader(httpx.Client(transport=httpx.MockTransport(transport))).inspect(
        snapshot, None, lambda *args: None
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("120000", "unsupported_path_type"),
        ("160000", "unsupported_path_type"),
        ("040000", "unsupported_path_type"),
    ],
)
def test_reject_link_submodule_directory(data, mode, code):
    def transport(request):
        response = handler(request)
        if "/git/trees/" in request.url.path:
            payload = response.json()
            payload["tree"][0]["mode"] = mode
            return httpx.Response(200, json=payload)
        return response

    with pytest.raises(SourceError) as exc:
        inspect(data, transport)
    assert exc.value.payload["code"] == code


@pytest.mark.parametrize(
    "status,headers,code,retry",
    [
        (301, {"Location": "http://127.0.0.1/secret"}, "repository_unavailable", False),
        (404, {}, "repository_unavailable", False),
        (429, {}, "github_rate_limited", False),
        (
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"},
            "github_rate_limited",
            False,
        ),
        (503, {}, "github_unavailable", True),
    ],
)
def test_http_errors_do_not_follow_redirects(data, status, headers, code, retry):
    visited = []

    def transport(request):
        visited.append(request.url)
        return httpx.Response(status, headers=headers)

    with pytest.raises(SourceError) as exc:
        inspect(data, transport)
    assert exc.value.payload["code"] == code and exc.value.retry is retry and len(visited) == 1


def test_timeout_is_retryable(data):
    def transport(request):
        raise httpx.ReadTimeout("hidden-internal-message", request=request)

    with pytest.raises(SourceError) as exc:
        inspect(data, transport)
    assert exc.value.retry and "hidden" not in str(exc.value)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("truncated", "source_limit_exceeded"),
        ("missing", "path_not_found"),
        ("large", "source_limit_exceeded"),
        ("malformed", "github_unavailable"),
    ],
)
def test_tree_errors(data, mutation, code):
    def transport(request):
        response = handler(request)
        if "/git/trees/" not in request.url.path:
            return response
        payload = response.json()
        if mutation == "truncated":
            payload["truncated"] = True
        if mutation == "missing":
            payload["tree"] = []
        if mutation == "large":
            payload["tree"][0]["size"] = 300000
        if mutation == "malformed":
            payload["tree"][0]["sha"] = "bad"
        return httpx.Response(200, json=payload)

    with pytest.raises(SourceError) as exc:
        inspect(data, transport)
    assert exc.value.payload["code"] == code


def test_response_limit_and_private_repository(data):
    with pytest.raises(SourceError) as exc:
        inspect(data, lambda _: httpx.Response(200, content=b" " * (4 * 1024 * 1024 + 1)))
    assert exc.value.payload["code"] == "source_limit_exceeded"
    with pytest.raises(SourceError) as exc:
        inspect(data, lambda _: httpx.Response(200, json={"private": True}))
    assert exc.value.payload["code"] == "repository_unavailable"


def test_success_pins_before_file_access(data):
    steps = []

    def transport(request):
        if "/git/trees/" in request.url.path:
            assert any(len(s) == 3 and s[2] == SHA for s in steps)
        return handler(request)

    snapshot = data.model_dump() | {"repo_owner": "example", "repo_name": "orders"}
    result = GitHubReader(httpx.Client(transport=httpx.MockTransport(transport))).inspect(
        snapshot, None, lambda *s: steps.append(s)
    )
    assert result["resolved_commit_sha"] == SHA


@pytest.mark.live
def test_real_public_repository_pipeline(store, data):
    real = data.model_copy(
        update={
            "repository_url": "https://github.com/docker/welcome-to-docker",
            "source_ref": "68c1b9f87c41fb3fef2667e27149865c2f42d1eb",
        }
    )
    service, _ = store.register(real, "live-registration")
    run, _ = store.enqueue(service["id"], "live-run")
    from delta_platform.worker.main import process_one

    reader = GitHubReader()
    try:
        process_one(store, reader)
    finally:
        reader.close()
    result = store.run(run["id"])
    assert result["status"] == "succeeded", result
    assert result["resolved_commit_sha"] == real.source_ref
