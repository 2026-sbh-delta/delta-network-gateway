import os

from delta_platform.config import PASSWORD_HASHER, Settings

DATABASE_URL = "postgresql+psycopg://platform_test:local-test-only@127.0.0.1:55433/platform_test"
LOGIN_ID = "test-team"
PASSWORD = "local-integration-test-only"
HASH = PASSWORD_HASHER.hash(PASSWORD)


def settings():
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        login_id=LOGIN_ID,
        password_hash=HASH,
        public_origin="http://127.0.0.1:8088",
        local_mode=True,
    )


def configure_env():
    config = settings()
    for field in ("database_url", "password_hash", "login_id", "public_origin", "local_mode"):
        value = getattr(config, field)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        os.environ["PLATFORM_" + field.upper()] = str(value)
    return config
