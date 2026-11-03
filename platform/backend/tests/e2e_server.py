"""브라우저 시험 전용 API·Worker 실행. GitHub 응답만 fixture이며 DB는 실제 PostgreSQL이다."""

import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
import uvicorn
from alembic import command
from alembic.config import Config
from build_fixture import BrowserBuilder
from support import configure_env
from test_builds import config as build_config

import delta_platform.api.app as app_module
from delta_platform.api.app import create_app
from delta_platform.core.database import engine_for

if __name__ == "__main__":
    configure_env()
    config = build_config()
    app_module.CodeBuildRunner = BrowserBuilder
    engine = engine_for(config)
    assert engine.url.database == "platform_test" and engine.url.host == "127.0.0.1"
    command.upgrade(Config("alembic.ini"), "head")
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE sessions, auth_rate_limits, run_events, runs, services"))
    child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("e2e_worker.py"))])
    try:
        uvicorn.run(
            create_app(config, engine),
            host="127.0.0.1",
            port=8088,
            proxy_headers=False,
            access_log=False,
        )
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        engine.dispose()
