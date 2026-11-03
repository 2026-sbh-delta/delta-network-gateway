from sqlalchemy import create_engine, text

SCHEMA_VERSION = "0003"


def engine_for(settings):
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


def ready(engine):
    with engine.connect() as conn:
        return (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == SCHEMA_VERSION
        )
