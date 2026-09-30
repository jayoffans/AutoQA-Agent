"""Same runtime round-trip suite on SQLite and an explicitly supplied disposable MySQL database."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.checkpoints import SQLCheckpointSaver
from autoqa.config import Settings
from autoqa.demo_guard import DemoLimitError, DemoQuotaRow
from autoqa.schemas import ProjectCreate, TaskCreate
from autoqa.seed import SEED_TASK_ID
from autoqa.store import EventRow, Store, TaskRow
from autoqa.studio_models import ProjectRow


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
def test_database_runtime_roundtrips_transactions_and_seed(backend, tmp_path):
    url = f"sqlite:///{tmp_path / 'compatibility.db'}"
    if backend == "mysql":
        url = os.getenv("AUTOQA_TEST_MYSQL_URL")
        if not url:
            pytest.skip("No disposable MySQL service supplied; runtime compatibility remains unverified locally")
    settings = Settings(_env_file=None, env="production", database_url=url, data_dir=tmp_path / "data",
                        artifact_dir=tmp_path / "artifacts", demo_daily_limit=2, demo_ip_limit=2)
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert client.get("/health").json() == {"status": "ok"}
        seed = client.get(f"/api/tasks/{SEED_TASK_ID}").json()
        assert seed["run"]["passed"] == 5 and seed["run"]["coverage"] == 100
    store = Store(settings.database_url, settings.artifact_dir)
    project = store.create_project(ProjectCreate(name="数据库验证 " + str(uuid4())[:8],
        description="JSON / Boolean / UTC / FK roundtrip", url="https://qa.example"))
    request = TaskCreate(project_id=project["id"], target_url="https://qa.example", goal="Schema roundtrip")
    task_id = store.create(request)
    store.event(task_id, "roundtrip", {"unicode": "中文", "nested": {"flag": True}, "large": "x" * 100000})
    assert store.events(task_id)[-1]["payload"]["nested"]["flag"] is True
    assert len(store.events(task_id)[-1]["payload"]["large"]) == 100000
    with Session(store.engine) as session:
        row = session.get(ProjectRow, project["id"])
        assert row.builtin is False and row.archived is False and row.created_at is not None
        row.description = "uncommitted"
        session.flush()
        session.rollback()
        assert session.get(ProjectRow, project["id"]).description != "uncommitted"
        with pytest.raises(IntegrityError):
            session.add(EventRow(task_id=str(uuid4()), kind="invalid-fk", payload="{}"))
            session.commit()
        session.rollback()
        quota = session.get(DemoQuotaRow, "global")
        quota.day, quota.count = "", 0
        session.commit()
    def submit(index):
        try:
            return store.create(request, source=str(index), owner=str(index), settings=settings)
        except DemoLimitError:
            return None
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(value is not None for value in executor.map(submit, range(8))) == 2
    with Session(store.engine) as session:
        assert session.get(DemoQuotaRow, "global").count == 2
        assert session.scalar(select(TaskRow.id).where(TaskRow.id == SEED_TASK_ID))
    async def verify_checkpoint():
        def build():
            graph = StateGraph(dict)
            graph.add_node("review", lambda state: {"approved": interrupt("review")})
            graph.add_edge(START, "review")
            graph.add_edge("review", END)
            return graph.compile(checkpointer=SQLCheckpointSaver(store.engine))
        config = {"configurable": {"thread_id": str(uuid4())}}
        first = await build().ainvoke({"input": "contract"}, config)
        assert first["__interrupt__"]
        assert (await build().ainvoke(Command(resume=True), config))["approved"] is True
    asyncio.run(verify_checkpoint())
    store.engine.dispose()
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert client.get(f"/api/tasks/{SEED_TASK_ID}").json()["run"]["failed"] == 1
