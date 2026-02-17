import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import insert, select, update, func

from pydantic import BaseModel

from db.db import Session
from db.models import Host, Job, Execution, Outbox, HostCommandBlock, ExecutionLogs
from log.utils import log_event

from config import REQUIRES_APPROVAL

logger = logging.getLogger("api")


class JobBody(BaseModel):
    external_id: str
    command_type: str
    selector: dict
    payload: dict


router = APIRouter(tags=['jobs'])


@router.post("/webhook/jobs/")
async def create_job(job_body: JobBody):
    log_event(logger, "webhook_received", service="api",
              external_id=job_body.external_id, command_type=job_body.command_type)
    created_new = False
    with Session.begin() as session:
        job_id = session.execute(select(Job.uid).where(Job.external_id == job_body.external_id)).scalar_one_or_none()

        if job_id is None:
            job_approval_state = Job.ApprovalState.WAIT_APPROVAL if job_body.command_type in REQUIRES_APPROVAL else None
            stmt = (
                insert(Job).values(
                    **job_body.model_dump() | {'approval_state': job_approval_state}
                ).returning(Job.uid)
            )
            job_id = session.execute(stmt).scalar_one()
            created_new = True
            log_event(logger, "job_create", service="api",
                      external_id=job_body.external_id, command_type=job_body.command_type)
        if created_new:
            if job_body.selector.get('all'):
                host_ids = session.execute(select(Host.uid)).scalars().all()
            else:
                hostnames = job_body.selector.get('hostnames')
                stmt = select(Host.hostname, Host.uid).where(Host.hostname.in_(hostnames))
                result = session.execute(stmt).all()

                existing_hostname = [h for h, _ in result]
                missing = [hostname for hostname in hostnames if hostname not in existing_hostname]

                if missing:
                    raise HTTPException(status_code=404, detail=f"Missing hosts: {','.join(missing)}")

                host_ids = [host_id for _, host_id in result]

            host_block_ids = session.execute(
                select(HostCommandBlock.host_id).where(HostCommandBlock.command_type == job_body.command_type)
            ).scalars().all()

            rows_execution = [
                {
                    'job_id': job_id,
                    "host_id": host_id,
                    "status": Execution.Status.NEW if host_id not in host_block_ids else Execution.Status.BLOCKED,
                }
                for host_id in host_ids
            ]

            session.execute(insert(Execution), rows_execution)
            log_event(logger, "executions_create", service="api", job_id=str(job_id))
            if job_body.command_type not in REQUIRES_APPROVAL:
                session.execute(insert(Outbox).values(
                    payload={"job_id": str(job_id)},
                ))
                log_event(logger, "outbox_event_create", service="api", job_id=str(job_id))

    return {'job_id': job_id}


@router.post("/jobs/{job_id}/approve/")
async def approve_job(job_id: str):
    with Session.begin() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "job not found")

        if job.approval_state == Job.ApprovalState.APPROVED:
            return {"job_id": str(job.uid), "approval_state": "APPROVED", "enqueued": False}

        if job.approval_state != Job.ApprovalState.WAIT_APPROVAL:
            raise HTTPException(409, f"job not waiting approval (state={job.approval_state})")

        job.approval_state = Job.ApprovalState.APPROVED
        log_event(logger, "job_approved", service="api", job_id=job_id)
        log_event(logger, "", service="api", job_id=job_id)
        session.add(Outbox(
            event_type=Outbox.Event.PLAN_JOB,
            payload={"job_id": str(job.uid)},
            status=Outbox.Status.NEW,
        ))
        log_event(logger, "outbox_event create", service="api", job_id=job_id)

        return {"job_id": str(job.uid), "approval_state": "APPROVED", "enqueued": True}


@router.post("/jobs/{job_id}/reject/")
async def reject_job(job_id: str):
    with Session.begin() as session:
        job = session.execute(
            select(Job).where(Job.uid == job_id)
        ).scalars().one_or_none()

        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        if job.approval_state == Job.ApprovalState.REJECTED:
            return dict(
                job_id=str(job.uid),
                approval_state=job.approval_state.value,
                status=job.status.value,
                cancelled_executions=0,
            )

        if job.approval_state != Job.ApprovalState.WAIT_APPROVAL:
            raise HTTPException(
                status_code=409,
                detail=f"job is not waiting for approval (approval_state={job.approval_state})",
            )

        job.approval_state = Job.ApprovalState.REJECTED
        job.status = Job.Status.FAILED

        session.execute(
            update(Execution)
            .where(
                Execution.job_id == job.uid,
                Execution.status.in_([Execution.Status.NEW, Execution.Status.QUEUED]),
            )
            .values(status=Execution.Status.CANCELLED)
        )
        log_event(logger, "job rejected", service="api", job_id=job_id)
    return dict(
        job_id=str(job.uid),
        approval_state=job.approval_state.value,
        status=job.status.value
    )


@router.get("/jobs/")
async def get_all_jobs(limit: int = 50, offset: int = 0):
    with Session.begin() as session:
        jobs = session.execute(
            select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()
        result = [
            {
                "job_id": str(j.uid),
                "external_id": j.external_id,
                "command_type": j.command_type.value,
                "approval_state": j.approval_state.value if j.approval_state else None,
            }
            for j in jobs
        ]

    return result


@router.get("/jobs/{job_id}/")
async def get_job(job_id: uuid.UUID):
    with Session.begin() as session:
        job = session.execute(select(Job).where(Job.uid == job_id)).scalars().one_or_none()

        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        rows = session.execute(
            select(Execution.status, func.count(Execution.uid))
            .where(Execution.job_id == job_id)
            .group_by(Execution.status)
        ).all()

        counts = {status.value: cnt for status, cnt in rows}
        total = sum(counts.values())

        done = counts.get(Execution.Status.SUCCESS, 0) \
               + counts.get(Execution.Status.FAILED, 0) \
               + counts.get(Execution.Status.CANCELLED, 0) \
               + counts.get(Execution.Status.TIMEOUT, 0) \
               + counts.get(Execution.Status.BLOCKED, 0)

        running = counts.get(Execution.Status.RUNNING, 0)

        if total == 0:
            summary = "EMPTY"
        elif done == total and counts.get(Execution.Status.FAILED, 0) == 0 \
                and counts.get(Execution.Status.BLOCKED, 0) == 0 \
                and counts.get(Execution.Status.TIMEOUT, 0) == 0:
            summary = "SUCCESS"
        elif done == total and counts.get(Execution.Status.SUCCESS, 0) == 0:
            summary = "FAILED"
        elif done == total:
            summary = "PARTIAL"
        else:
            if counts.get(Execution.Status.QUEUED, 0) != 0:
                summary = "QUEUED"
            else:
                summary = "RUNNING" if (running > 0) else "NEW"

        return {
            "job_id": str(job.uid),
            "external_id": job.external_id,
            "command_type": job.command_type.value,
            "status": job.status.value,
            "approval_state": job.approval_state.value if job.approval_state else None,
            "executions_total": total,
            "executions_by_status": counts,
            "summary": summary,
        }


@router.get("/jobs/{job_id}/executions")
async def get_job_executions(
        job_id: uuid.UUID,
        status: Optional[Execution.Status] = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
):
    with Session.begin() as session:
        exists = session.execute(select(Job.uid).where(Job.uid == job_id)).scalar_one_or_none()
        if not exists:
            raise HTTPException(status_code=404, detail="job not found")

        stmt = (
            select(Execution, Host.hostname)
            .join(Host, Host.uid == Execution.host_id)
            .where(Execution.job_id == job_id)
        )

        if status is not None:
            stmt = stmt.where(Execution.status == status)

        rows = session.execute(
            stmt.order_by(Host.hostname.asc()).limit(limit).offset(offset)
        ).all()

        return [
            {
                "execution_id": str(ex.uid),
                "host_id": str(ex.host_id),
                "hostname": hostname,
                "attempts": ex.attempts,
                "status": ex.status.value,
            }
            for (ex, hostname) in rows
        ]


@router.get('/jobs/executions/{execution_id}/logs')
async def get_job_execution_logs(execution_id: uuid.UUID):
    with Session.begin() as session:
        execution_logs = session.execute(
            select(ExecutionLogs).where(ExecutionLogs.execution_id == execution_id).order_by(ExecutionLogs.ts.asc())
        ).scalars().all()

        return [
            {
                "execution_id": str(execution_log.execution_id),
                "ts": execution_log.ts,
                "line": execution_log.line
            }
            for execution_log in execution_logs
        ]
