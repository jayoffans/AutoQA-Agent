"""Persistent quotas; proxy client addresses require an explicit trusted socket range."""
import hashlib
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network

from sqlalchemy import ForeignKey, Integer, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .store_base import Base


class DemoQuotaRow(Base):
    __tablename__ = "demo_quotas"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    day: Mapped[str] = mapped_column(String(10))
    count: Mapped[int] = mapped_column(Integer, default=0)


class DemoOwnerRow(Base):
    __tablename__ = "demo_owners"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))


class DemoLimitError(ValueError):
    pass


def identity(value):
    return hashlib.sha256(value.encode()).hexdigest()


def source_address(request, settings):
    peer = request.client.host if request.client else "unknown"
    try:
        address = ip_address(peer)
        trusted = any(address in ip_network(cidr.strip(), strict=False)
                      for cidr in settings.trusted_proxy_cidrs.split(",") if cidr.strip())
        values = request.headers.getlist("x-real-ip")
        if trusted and len(values) == 1:
            return str(ip_address(values[0].strip()))
    except ValueError:
        pass
    return peer


def reserve(session, source, settings):
    # UPDATE obtains a write lock on SQLite and an exclusive row lock on MySQL.
    # This lock serializes all reservations, including IP-row insertion and task creation.
    session.execute(update(DemoQuotaRow).where(DemoQuotaRow.id == "global").values(count=DemoQuotaRow.count))
    global_row = session.get(DemoQuotaRow, "global", populate_existing=True)
    day = datetime.now(timezone.utc).date().isoformat()
    if global_row.day != day:
        global_row.day, global_row.count = day, 0
        for row in session.scalars(select(DemoQuotaRow).where(DemoQuotaRow.id != "global")):
            session.delete(row)
        session.flush()
    key = "ip:" + identity(source)
    source_row = session.get(DemoQuotaRow, key)
    if not source_row:
        source_row = DemoQuotaRow(id=key, day=day, count=0)
        session.add(source_row)
    if global_row.count >= settings.demo_daily_limit or source_row.count >= settings.demo_ip_limit:
        raise DemoLimitError("今日 Demo 任务额度已用完，请明天再试。你仍可浏览示例 Workflow、Findings 和 Report。")
    global_row.count += 1
    source_row.count += 1
