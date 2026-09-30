"""SQLAlchemy LangGraph saver for MySQL; typed serialization, no pickle fallback."""
import asyncio
import base64
import hashlib
import json

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from sqlalchemy import Integer, String, Text, select
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, Session, mapped_column

from .store_base import Base


class CheckpointRow(Base):
    __tablename__ = "graph_checkpoints"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), index=True)
    namespace: Mapped[str] = mapped_column(Text)
    checkpoint_id: Mapped[str] = mapped_column(String(80), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    metadata_json: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))


class WriteRow(Base):
    __tablename__ = "graph_writes"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_key: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(80))
    index: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))


def key_for(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class SQLCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.lock = asyncio.Lock()

    def encode(self, value):
        kind, data = self.serde.dumps_typed(value)
        return json.dumps([kind, base64.b64encode(data).decode()])

    def decode(self, value):
        kind, data = json.loads(value)
        return self.serde.loads_typed((kind, base64.b64decode(data)))

    def get_tuple(self, config):
        c = config["configurable"]
        namespace = c.get("checkpoint_ns", "")
        with Session(self.engine) as session:
            query = select(CheckpointRow).where(CheckpointRow.thread_id == c["thread_id"],
                                                 CheckpointRow.namespace == namespace)
            if c.get("checkpoint_id"):
                query = query.where(CheckpointRow.checkpoint_id == c["checkpoint_id"])
            row = session.scalar(query.order_by(CheckpointRow.checkpoint_id.desc()).limit(1))
            if not row:
                return None
            saved = {"configurable": {"thread_id": row.thread_id, "checkpoint_ns": namespace,
                                       "checkpoint_id": row.checkpoint_id}}
            parent = {"configurable": {**saved["configurable"], "checkpoint_id": row.parent_id}} if row.parent_id else None
            writes = session.scalars(select(WriteRow).where(WriteRow.checkpoint_key == row.key)
                                     .order_by(WriteRow.task_id, WriteRow.index))
            return CheckpointTuple(saved, self.decode(row.content), json.loads(row.metadata_json), parent,
                                   [(w.task_id, w.channel, self.decode(w.content)) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        c = config["configurable"]
        namespace = c.get("checkpoint_ns", "")
        with Session(self.engine) as session:
            session.merge(CheckpointRow(key=key_for(c["thread_id"], namespace, checkpoint["id"]),
                thread_id=c["thread_id"], namespace=namespace, checkpoint_id=checkpoint["id"],
                parent_id=c.get("checkpoint_id"), content=self.encode(checkpoint),
                metadata_json=json.dumps(get_checkpoint_metadata(config, metadata))))
            session.commit()
        return {"configurable": {"thread_id": c["thread_id"], "checkpoint_ns": namespace,
                                  "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        c = config["configurable"]
        checkpoint_key = key_for(c["thread_id"], c.get("checkpoint_ns", ""), c["checkpoint_id"])
        with Session(self.engine) as session:
            for index, (channel, value) in enumerate(writes):
                position = WRITES_IDX_MAP.get(channel, index)
                key = key_for(checkpoint_key, task_id, position)
                if position >= 0 and session.get(WriteRow, key):
                    continue
                session.merge(WriteRow(key=key, checkpoint_key=checkpoint_key, task_id=task_id,
                                       index=position, channel=channel, content=self.encode(value)))
            session.commit()

    async def aget_tuple(self, config):
        async with self.lock:
            return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self.lock:
            return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self.lock:
            await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        with Session(self.engine) as session:
            query = select(CheckpointRow).order_by(CheckpointRow.checkpoint_id.desc())
            if config:
                c = config["configurable"]
                query = query.where(CheckpointRow.thread_id == c["thread_id"],
                                     CheckpointRow.namespace == c.get("checkpoint_ns", ""))
            if before:
                query = query.where(CheckpointRow.checkpoint_id < before["configurable"]["checkpoint_id"])
            configs = [{"configurable": {"thread_id": r.thread_id, "checkpoint_ns": r.namespace,
                        "checkpoint_id": r.checkpoint_id}} for r in session.scalars(query)]
        count = 0
        for saved in configs:
            item = await self.aget_tuple(saved)
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            count += 1
            yield item
