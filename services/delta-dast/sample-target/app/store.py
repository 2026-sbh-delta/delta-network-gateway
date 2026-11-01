"""메모리 저장소. 예제 앱이라 재시작하면 지워진다."""

from uuid import uuid4

posts: dict[str, dict] = {}


def create_post(title: str, body: str) -> str:
    post_id = uuid4().hex
    posts[post_id] = {"title": title, "body": body, "comments": []}
    return post_id


def get_post(post_id: str) -> dict | None:
    return posts.get(post_id)
