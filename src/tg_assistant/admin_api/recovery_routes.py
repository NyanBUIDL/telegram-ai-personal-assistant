"""Recovery handlers composed with the existing owner/session/CSRF boundary."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from sqlalchemy import select

from ..db.models import AppSetting, AuditLog, BackgroundJob
from ..services.vector_recovery import VectorRecoveryService, plan_key


def recovery_service(context):
    return VectorRecoveryService(
        context.database,
        settings_getter=context.settings_getter,
        vectors_getter=context.vectors_getter,
        owner_getter=lambda: context.owner_id,
    )


async def create_recovery_preview(context, pending, chat_id, *, action_json):
    service = recovery_service(context)
    try:
        plan = await service.preview(chat_id, context.settings_getter().embedding_profile.store_id)
        async with context.database.session() as session:
            private = await session.get(AppSetting, plan_key(plan.plan_id))
            coverage = private.value["coverage"]
            action = await pending.create(
                session,
                action_type="recover_source_index",
                requested_by=context.owner_id,
                chat_id=chat_id,
                payload={"plan_id": plan.plan_id},
                preview=f"Khôi phục {coverage['missing_count']} tham chiếu thiếu của nguồn {chat_id}; "
                f"kiểm chứng {plan.expected_count} tham chiếu hợp lệ.",
                reason="Chỉ chuyển kho sau khi xây dựng và truy xuất đã được kiểm chứng.",
            )
            session.add(
                AuditLog(
                    occurred_at=datetime.now(UTC),
                    actor_id=context.owner_id,
                    action="vector_recovery_preview_created",
                    target_type="knowledge_source",
                    target_id=str(chat_id),
                    outcome="pending",
                    correlation_id=action.action_id,
                    details_redacted={
                        "plan_id": plan.plan_id,
                        "action_id": action.action_id,
                        "expected_count": plan.expected_count,
                    },
                )
            )
    except PermissionError:
        raise HTTPException(status_code=403, detail="recovery_not_authorized") from None
    except ValueError:
        raise HTTPException(status_code=409, detail="recovery_preview_unavailable") from None
    return {
        "pending_action": action_json(action),
        "recovery_plan": plan.model_dump(mode="json"),
        "coverage": coverage,
    }


def install_recovery_routes(app, context, require_read_session, require_write_session):
    @app.get("/api/v1/recovery-jobs/{operation_id}")
    async def recovery_status(operation_id: str, _session=Depends(require_read_session)):
        async with context.database.session() as session:
            job = await session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.id == operation_id, BackgroundJob.job_type == "vector_recovery"
                )
            )
            if job is None or (job.payload or {}).get("owner_id") != context.owner_id:
                raise HTTPException(status_code=404, detail="recovery_operation_unavailable")
            state = (
                "running" if job.status in {"pause_requested", "cancel_requested"} else job.status
            )
            return VectorRecoveryService.result(
                job.id, state, job.last_error or "recovery_status", job.payload.get("progress")
            ).model_dump(mode="json")

    @app.post("/api/v1/recovery-jobs/{operation_id}/{operation}")
    async def recovery_control(
        operation_id: str, operation: str, _session=Depends(require_write_session)
    ):
        try:
            result = await recovery_service(context).control(
                operation_id, operation, context.owner_id
            )
        except PermissionError:
            raise HTTPException(status_code=404, detail="recovery_operation_unavailable") from None
        except ValueError:
            raise HTTPException(status_code=409, detail="recovery_control_refused") from None
        return result.model_dump(mode="json")
