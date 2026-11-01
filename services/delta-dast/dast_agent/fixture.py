"""의도적으로 취약한 로컬 전용 게시판. 운영에 배포하지 않는다."""

import html
from pathlib import Path
from string import Template
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .contracts import SCENARIOS, Scenario

DEMO = Path(__file__).with_name("demo")


def create_fixture_app(port: int = 8766) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    posts: dict[str, dict] = {s["id"]: {} for s in SCENARIOS}
    app.state.posts = posts

    def reset():
        # 시연마다 같은 출발점에서 시작하도록 이전 검사 글을 지우고 깨끗한 글 하나만 둔다.
        for scenario in posts:
            posts[scenario].clear()
            posts[scenario][uuid4().hex] = {
                "title": "[공지] 서비스 점검 안내", "comments": [], "pinned": True,
                "body": "11월 정기 점검이 예정되어 있습니다. 문의는 댓글로 남겨 주세요.",
            }

    app.state.reset = reset
    reset()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return JSONResponse({"detail": "로컬 호스트만 허용합니다."}, status_code=403)
        if request.method == "POST" and request.headers.get("origin") != f"http://{host}":
            return JSONResponse({"detail": "다른 출처의 저장 요청을 거부했습니다."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # Only the vulnerable modes deliberately permit the registered inline SVG event.
        scripts = "'none'" if request.url.path.startswith("/csp/") else "'unsafe-inline'"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'none'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        return response

    def render(scenario: str, title: str, template: str, **values) -> HTMLResponse:
        content = Template((DEMO / template).read_text()).substitute(scenario=scenario, **values)
        page = Template((DEMO / "layout.html").read_text()).substitute(
            scenario=scenario, title=html.escape(title), content=content,
            mode=next(s["label"] for s in SCENARIOS if s["id"] == scenario),
        )
        return HTMLResponse(page)

    @app.get("/demo/style.css")
    async def style():
        return FileResponse(DEMO / "style.css", media_type="text/css")

    @app.get("/{scenario}/board", response_class=HTMLResponse)
    async def board(scenario: Scenario):
        # 공지 글은 항상 맨 위에 두고 나머지는 최신순으로 보인다.
        ordered = sorted(reversed(posts[scenario].items()), key=lambda item: not item[1].get("pinned"))
        rows = "".join(
            f'<tr><td><a href="/{scenario}/posts/{key}">{html.escape(post["title"])}</a></td>'
            f'<td>{key[:8]}</td></tr>' for key, post in ordered
        ) or '<tr><td colspan="2">등록된 게시글이 없습니다.</td></tr>'
        return render(scenario, "게시글 목록", "board.html", rows=rows)

    @app.get("/{scenario}/new", response_class=HTMLResponse)
    async def new(scenario: Scenario):
        return render(scenario, "새 게시글 작성", "new.html")

    @app.post("/{scenario}/posts", response_class=HTMLResponse)
    async def save(scenario: Scenario, request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "폼 입력만 저장할 수 있습니다.")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 32 * 1024:
                raise HTTPException(413, "폼 입력이 너무 큽니다.")
        try:
            fields = parse_qs(raw.decode("utf-8"), strict_parsing=True, max_num_fields=2)
            if set(fields) != {"title", "body"} or any(len(v) != 1 for v in fields.values()):
                raise ValueError
            title, body = fields["title"][0], fields["body"][0]
            if not title.strip() or len(title) > 160 or not body or len(body.encode()) > 8192:
                raise ValueError
        except (ValueError, UnicodeError):
            raise HTTPException(422, "제목 또는 본문 입력을 확인하세요.") from None
        if len(posts[scenario]) >= 1000:
            raise HTTPException(507, "실습 저장 공간이 찼습니다. 서버를 재시작하세요.")
        post_id = uuid4().hex
        posts[scenario][post_id] = {"title": title, "body": body, "comments": []}
        # Never interpolate body in the save response: only a new visit can execute it.
        return render(scenario, "저장 완료", "saved.html", post_id=post_id)

    @app.get("/{scenario}/posts/{post_id}", response_class=HTMLResponse)
    async def detail(scenario: Scenario, post_id: str):
        post = posts[scenario].get(post_id)
        if post is None:
            raise HTTPException(404, "게시글을 찾을 수 없습니다.")
        escape = html.escape if scenario == "fixed" else (lambda value: value)
        comments = "".join(f"<li>{escape(item)}</li>" for item in post["comments"]) or (
            '<li class="empty">아직 댓글이 없습니다.</li>')
        return render(scenario, "게시글 상세", "detail.html", post_id=post_id,
                      post_title=html.escape(post["title"]), body=escape(post["body"]),
                      comments=comments)

    @app.post("/{scenario}/posts/{post_id}/comments", response_class=HTMLResponse)
    async def comment(scenario: Scenario, post_id: str, request: Request):
        post = posts[scenario].get(post_id)
        if post is None:
            raise HTTPException(404, "게시글을 찾을 수 없습니다.")
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "폼 입력만 저장할 수 있습니다.")
        raw = await request.body()
        try:
            if len(raw) > 32 * 1024:
                raise ValueError
            fields = parse_qs(raw.decode("utf-8"), strict_parsing=True, max_num_fields=1)
            if set(fields) != {"comment"} or len(fields["comment"]) != 1:
                raise ValueError
            text = fields["comment"][0]
            if not text.strip() or len(text.encode()) > 8192 or len(post["comments"]) >= 200:
                raise ValueError
        except (ValueError, UnicodeError):
            raise HTTPException(422, "댓글 입력을 확인하세요.") from None
        post["comments"].append(text)
        # 게시글 저장과 같이 확인 화면에서는 댓글을 출력하지 않는다. 재방문해야만 실행된다.
        return render(scenario, "댓글 등록 완료", "comment_saved.html", post_id=post_id)

    return app
