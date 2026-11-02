from alembic import context

from delta_platform.config import Settings
from delta_platform.core.database import engine_for
from delta_platform.core.schema import metadata

engine = engine_for(Settings())
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
