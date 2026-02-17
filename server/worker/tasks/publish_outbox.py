import logging
from datetime import timezone, datetime

from worker.celery_app import celery_app
from config import TASK_PUBLISH_OUTBOX, TASK_PLAN_JOB

from sqlalchemy import select

from db.models import Outbox
from db.db import Session
from log.utils import log_event

logger = logging.getLogger('publish_outbox')


@celery_app.task(name=TASK_PUBLISH_OUTBOX)
def publish_outbox(batch_size: int = 200) -> None:
    job_ids: list[str] = []

    with Session.begin() as session:
        events = (
            session.execute(
                select(Outbox)
                .where(Outbox.status == Outbox.Status.NEW)
                .order_by(Outbox.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        for event in events:
            try:
                job_id = event.payload["job_id"]
                job_ids.append(str(job_id))

                event.status = Outbox.Status.SENT
                event.sent_at = datetime.now(timezone.utc)
                log_event(logger, 'outbox event sent', job_id=job_id)
            except Exception as e:
                event.attempts += 1
                event.status = Outbox.Status.FAILED if event.attempts >= 10 else Outbox.Status.NEW

    for jid in list(dict.fromkeys(job_ids)):
        celery_app.send_task(TASK_PLAN_JOB, args=[jid])
        log_event(logger, 'outbox event send task plan job', job_id=job_id)
