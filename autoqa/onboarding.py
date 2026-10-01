"""User-owned first-use progress, independent of the agent and provider gateway."""
from fastapi import HTTPException
from sqlalchemy import select, update

from .product_models import MemberRow, OnboardingRow, ProviderConfigRow
from .store_base import now
from .studio_models import ProjectRow, RunRow, TaskMetadataRow


def snapshot(row):
    if row is None:
        return None  # Pre-onboarding accounts keep their existing Dashboard flow.
    return {key: getattr(row, key) for key in (
        "workspace_id", "project_id", "step", "model_configured", "project_created",
        "first_test_completed", "onboarding_completed")}


def progress(session, user_id):
    row = session.get(OnboardingRow, user_id)
    if row is None:
        return None
    # The original workspace remains the progress scope when the user switches workspaces.
    if session.get(MemberRow, (row.workspace_id, user_id)) is None:
        raise HTTPException(404, "首次使用工作空间不存在")
    if not row.model_configured and session.get(ProviderConfigRow, row.workspace_id):
        row.model_configured = True
    if not row.project_created and session.scalar(select(ProjectRow.id).where(
            ProjectRow.workspace_id == row.workspace_id).limit(1)):
        row.project_created = True
    if not row.first_test_completed and session.scalar(select(RunRow.id)
            .join(TaskMetadataRow, TaskMetadataRow.task_id == RunRow.task_id)
            .join(ProjectRow, ProjectRow.id == TaskMetadataRow.project_id)
            .where(ProjectRow.workspace_id == row.workspace_id, RunRow.status.in_(["passed", "failed"]),
                   RunRow.finished_at.is_not(None)).limit(1)):
        row.first_test_completed = True
    session.commit()
    return snapshot(row)


def owned_progress(session, identity):
    row = session.get(OnboardingRow, identity.user_id)
    if row is None:
        raise HTTPException(409, "此账号无需首次使用引导")
    if row.workspace_id != identity.workspace_id:
        raise HTTPException(404, "首次使用工作空间不存在")
    return row


def advance(session, identity, action):
    row = owned_progress(session, identity)
    expected, following = {"start": (1, 2), "skip_model": (2, 3), "complete": (4, 4)}[action]
    if row.step < expected:
        raise HTTPException(409, "请先完成当前引导步骤")
    # Conditional updates make retries monotonic and cannot reset a completed flow.
    values = {"step": following, "updated_at": now()}
    if action == "complete":
        values["onboarding_completed"] = True
    session.execute(update(OnboardingRow).where(OnboardingRow.user_id == identity.user_id,
        OnboardingRow.step == expected, OnboardingRow.onboarding_completed.is_(False)).values(**values),
        execution_options={"synchronize_session": False})
    session.commit()
    session.expire_all()
    return snapshot(session.get(OnboardingRow, identity.user_id))
