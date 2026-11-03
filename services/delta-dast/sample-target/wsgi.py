"""스테이징 실행 진입점. 앱 코드(SAST·DAST 예제와 같은 코드)는 그대로 두고, 헬스 체크용 / 리다이렉트만 붙인다."""

from flask import redirect

from app import create_app

app = create_app()


@app.get("/")
def root():
    return redirect("/board", code=302)
