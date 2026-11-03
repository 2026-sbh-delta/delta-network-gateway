"""게시글 목록·작성·상세."""

from html import escape

from flask import Blueprint, abort, request

from .comments import render_comments
from .store import create_post, get_post, posts

board = Blueprint("board", __name__)

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{title}</title></head>
<body><header><a href="/board">게시판</a></header><main>{content}</main></body></html>"""


def page(title: str, content: str) -> str:
    return PAGE.format(title=escape(title), content=content)


@board.get("/board")
def list_posts():
    rows = "".join(
        f'<li><a href="/posts/{post_id}">{escape(post["title"])}</a></li>'
        for post_id, post in reversed(posts.items())
    )
    return page("게시글 목록", f'<a href="/new">글 작성</a><ul>{rows}</ul>')


@board.get("/new")
def new_post():
    form = ('<form method="post" action="/posts"><input name="title" required>'
            '<textarea name="body" required></textarea><button>게시글 저장</button></form>')
    return page("새 게시글 작성", form)


@board.post("/posts")
def save_post():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "")
    if not title or not body:
        abort(422)
    # 저장 확인 화면에서 상세로 이동한다. DAST는 이 링크로 저장된 글을 찾는다.
    post_id = create_post(title, body)
    return page("저장 완료", f'<p>저장했습니다.</p><a id="saved-post" href="/posts/{post_id}">저장한 글 보기</a>')


@board.get("/posts/<post_id>")
def post_detail(post_id: str):
    post = get_post(post_id)
    if post is None:
        abort(404)
    # 본문을 그대로 HTML에 넣는다.
    body = post["body"]
    content = (f'<article><h2>{escape(post["title"])}</h2><div id="post-body">{body}</div></article>'
               f'{render_comments(post_id, post["comments"])}')
    return page("게시글 상세", content)
