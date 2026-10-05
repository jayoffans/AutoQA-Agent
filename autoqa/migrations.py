"""Run explicit, versioned migrations against the already selected database."""
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(engine):
    root = Path(__file__).resolve().parent.parent
    if not (root / "alembic.ini").is_file():
        root = Path(sys.prefix)  # Wheel data-files install migration sources alongside the runtime.
    config = Config(str(root / "alembic.ini"))
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # Alembic's batch recreation must preserve referenced legacy project rows.
            # SQLite requires this pragma outside a transaction.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Foreign key integrity check failed after migration")
        finally:
            if sqlite:
                connection.commit()
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
