from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass
