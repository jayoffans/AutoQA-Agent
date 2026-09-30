import asyncio
import logging

from langgraph.types import Command

from .schemas import TERMINAL

log = logging.getLogger(__name__)


class Runner:
    def __init__(self, workflow, store, concurrency):
        self.workflow, self.store = workflow, store
        self.semaphore = asyncio.Semaphore(concurrency)
        self.jobs: dict[str, asyncio.Task] = {}

    def submit(self, task_id: str, resume: bool | None = None):
        if task_id in self.jobs and not self.jobs[task_id].done():
            raise ValueError("Task is already running")
        self.jobs[task_id] = asyncio.create_task(self.run(task_id, resume))

    async def run(self, task_id: str, resume: bool | None):
        try:
            async with self.semaphore:
                task = self.store.get(task_id, private=True)
                if task["status"] in TERMINAL:
                    return
                self.store.update(task_id, status="running", approval=None)
                config = {"configurable": {"thread_id": task_id}, "recursion_limit": 300}
                initial = (Command(resume=resume) if resume is not None else
                           {"task_id": task_id, "request": task["request"], "cursor": 0, "attempt": 0})
                with self.workflow.telemetry.span("autoqa-task", {"task_id": task_id}):
                    async with asyncio.timeout(task["request"]["task_timeout_seconds"]):
                        result = await self.workflow.graph.ainvoke(initial, config=config)
                if result.get("__interrupt__"):
                    approval = result["__interrupt__"][0].value
                    self.store.update(task_id, status="awaiting_approval", approval=approval)
                    self.store.event(task_id, "approval_required", approval)
                    # At a pre-plan pause, release the unused browser. A step-level pause
                    # retains the live page until approval, cancellation or shutdown.
                    if approval.get("scope") == "plan":
                        await self.workflow.close_session(task_id)
        except asyncio.CancelledError:
            self.store.update(task_id, status="cancelled", approval=None)
            self.store.event(task_id, "cancelled", {"message": "执行已取消"})
        except Exception as error:
            category = "task_timeout" if isinstance(error, TimeoutError) else "agent_error"
            diagnosis = {"category": category, "summary": type(error).__name__,
                         "suggestion": str(error)[:1500], "root_cause_confirmed": False}
            self.store.update(task_id, status="failed", diagnosis=diagnosis, approval=None)
            self.store.event(task_id, "error", diagnosis)
            log.warning("Task %s failed: %s", task_id, type(error).__name__)
        finally:
            task = self.store.get(task_id)
            if task and task["status"] != "awaiting_approval":
                try:
                    await self.workflow.close_session(task_id)
                except Exception:
                    log.warning("Browser cleanup failed for %s", task_id, exc_info=False)

    async def cancel(self, task_id: str):
        job = self.jobs.get(task_id)
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        else:
            self.store.update(task_id, status="cancelled", approval=None)
            self.store.event(task_id, "cancelled", {"message": "任务已取消"})
            await self.workflow.close_session(task_id)

    async def shutdown(self):
        for job in self.jobs.values():
            if not job.done():
                job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for task_id in list(self.workflow.sessions):
            await self.workflow.close_session(task_id)
        self.workflow.telemetry.flush()
