from alembic import context
from sqlalchemy import create_engine

from autoqa.config import Settings


def migrate(connection):
    context.configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    engine = create_engine(Settings().database_url)
    with engine.begin() as connection:
        migrate(connection)

