import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from support import LOGIN_ID, PASSWORD, configure_env, settings

from delta_platform.api.app import create_app
from delta_platform.core.database import engine_for
from delta_platform.core.inputs import ServiceInput
from delta_platform.core.store import Store


@pytest.fixture(scope="session")
def engine():
    configure_env()
    command.upgrade(Config("alembic.ini"), "head")
    engine = engine_for(settings())
    assert engine.url.database == "platform_test" and engine.url.host == "127.0.0.1"
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE sessions, auth_rate_limits, run_events, workflow_executions, workflows, deployment_plans, runs, service_environments, services"
            )
        )


@pytest.fixture
def store(engine):
    return Store(engine)


@pytest.fixture
def data():
    return ServiceInput(
        name="orders-api",
        repository_url="https://github.com/example/orders",
        source_ref="main",
        build_context=".",
        dockerfile_path="Dockerfile",
        container_port=8088,
        healthcheck_path="/health",
    )


@pytest.fixture
def client(engine):
    with TestClient(create_app(settings(), engine), base_url="http://127.0.0.1:8088") as client:
        client.headers.update({"Origin": "http://127.0.0.1:8088", "X-Platform-Request": "1"})
        yield client


@pytest.fixture
def signed(client):
    response = client.post("/api/v1/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
