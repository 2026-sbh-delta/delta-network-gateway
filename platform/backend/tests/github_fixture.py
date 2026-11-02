import httpx

SHA = "a" * 40
TREE = "b" * 40
BLOB = "c" * 40


def handler(request):
    assert request.url.host == "api.github.com"
    path = request.url.path
    if path.endswith("/repos/example/orders"):
        return httpx.Response(200, json={"private": False})
    if "/commits/" in path:
        return httpx.Response(200, json={"sha": SHA, "commit": {"tree": {"sha": TREE}}})
    if "/git/trees/" in path:
        return httpx.Response(
            200,
            json={
                "truncated": False,
                "tree": [
                    {
                        "path": "Dockerfile",
                        "sha": BLOB,
                        "type": "blob",
                        "mode": "100644",
                        "size": 100,
                    }
                ],
            },
        )
    return httpx.Response(404)
