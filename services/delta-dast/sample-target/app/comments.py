"""게시글 댓글 등록·표시."""

from flask import Blueprint, abort, redirect, request

from .store import get_post

comments = Blueprint("comments", __name__)


def render_comments(post_id: str, items: list[str]) -> str:
    # 저장된 댓글을 그대로 목록에 넣는다.
    rows = "".join(f"<li>{text}</li>" for text in items) or "<li>아직 댓글이 없습니다.</li>"
    form = (f'<form method="post" action="/posts/{post_id}/comments">'
            '<textarea name="comment" required></textarea><button>댓글 등록</button></form>')
    return f'<section class="comments"><ul id="comment-list">{rows}</ul>{form}</section>'


@comments.post("/posts/<post_id>/comments")
def add_comment(post_id: str):
    post = get_post(post_id)
    text = request.form.get("comment", "")
    if post is None:
        abort(404)
    if not text.strip():
        abort(422)
    post["comments"].append(text)
    return redirect(f"/posts/{post_id}", code=303)
