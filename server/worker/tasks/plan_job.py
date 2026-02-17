import logging
from datetime import datetime, timezone

from worker.celery_app import celery_app
from config import TASK_PLAN_JOB

from sqlalchemy import select, update
from db.db import Session
from db.models import Job, Execution

from config import TASK_RUN_EXECUTION
from log.utils import log_event

logger = logging.getLogger('worker plan_job')


@celery_app.task(name=TASK_PLAN_JOB)
def plan_job(job_id: str, batch_size: int = 200) -> None:
    with Session.begin() as session:
        job = session.execute(
            select(Job).where(
                Job.uid == job_id,
                Job.status == Job.Status.NEW,
                (Job.approval_state.is_(None) | (Job.approval_state == Job.ApprovalState.APPROVED)),
            )
        ).scalars().one_or_none()

        if job is None:
            log_event(logger, 'job not found')
            return

        session.execute(
            update(Job)
            .where(Job.uid == job_id, Job.status == Job.Status.NEW)
            .values(status=Job.Status.QUEUED)
        )
        log_event(logger, 'job queued', job_id=job_id, command_type=job.command_type)
    while True:
        with Session.begin() as session:
            ids = (
                session.execute(
                    select(Execution.uid)
                    .where(
                        Execution.job_id == job_id,
                        Execution.status == Execution.Status.NEW,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(batch_size)
                )
                .scalars()
                .all()
            )

            if not ids:
                break

            session.execute(
                update(Execution)
                .where(
                    Execution.uid.in_(ids),
                    Execution.status == Execution.Status.NEW,
                )
                .values(status=Execution.Status.QUEUED)
            )
            execution_ids = [str(x) for x in ids]
            log_event(logger, 'executions queued', job_id=job_id)
        for execution_id in execution_ids:
            celery_app.send_task(TASK_RUN_EXECUTION, args=[execution_id])
            log_event(logger, 'send task_run_execution', job_id=job_id, execution_id=execution_id)
