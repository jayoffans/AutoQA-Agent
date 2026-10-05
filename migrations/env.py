from alembic import context
from sqlalchemy import create_engine

from autoqa.config import Settings


def migrate(connection):
    if connection.dialect.name == "mysql":
        # MySQL DATETIME does not carry a timezone. Match application's UTC timestamps
        # even when the database host itself runs in another timezone.
        connection.exec_driver_sql("SET SESSION time_zone = '+00:00'")
    context.configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


if context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    engine = create_engine(Settings().database_url)
    with engine.begin() as connection:
        migrate(connection)

