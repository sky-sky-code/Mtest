import random
import time
import zlib
from datetime import datetime, timezone

from sqlalchemy import select, update, text, insert

from worker.celery_app import celery_app
from db.db import Session
from db.models import Execution, ExecutionLogs, Job, HostCommandBlock

from config import TASK_RUN_EXECUTION, MAX_BACKOFF, MAX_RETRIES, BASE_BACKOFF


def _backoff_seconds(retries_done: int) -> float:
    return min(MAX_BACKOFF, BASE_BACKOFF * (2 ** retries_done)) + random.uniform(0, 1.0)


def _simulate_agent_call() -> dict:
    p = random.random()
    if p > 0.5:
        time.sleep(0.5)
        raise TimeoutError("agent timeout")
    if p < 0.15:
        raise RuntimeError("agent error")
    time.sleep(random.uniform(0.1, 1.5))
    return {"exit_code": 0, "stdout": "ok", "stderr": ""}


def _host_lock_key(host_id: str) -> int:
    return zlib.crc32(host_id.encode("utf-8"))


def _try_lock_host(session, host_id: str) -> bool:
    return session.execute(
        text("SELECT pg_try_advisory_lock(:k)"),
        {"k": _host_lock_key(host_id)},
    ).scalar_one()


def _unlock_host(session, host_id: str) -> None:
    session.execute(
        text("SELECT pg_advisory_unlock(:k)"),
        {"k": _host_lock_key(host_id)},
    )


def _retry_or_finish(task, execution_id: str, err: str, is_timeout: bool) -> None:
    retries_done = task.request.retries
    if retries_done < MAX_RETRIES:
        with Session.begin() as session:
            session.execute(
                update(Execution).where(Execution.uid == execution_id)
                .values(
                    status=Execution.Status.QUEUED
                )
            )

            session.execute(
                insert(ExecutionLogs).values(execution_id=execution_id, line=err)
            )
        raise task.retry(countdown=_backoff_seconds(retries_done), exc=RuntimeError(err))

    final_status = Execution.Status.TIMEOUT if is_timeout else Execution.Status.FAILED
    with Session.begin() as session:
        session.execute(
            update(Execution)
            .where(Execution.uid == execution_id)
            .values(
                status=final_status,
                finished_at=datetime.now(timezone.utc),
            )
        )

        session.execute(
            insert(ExecutionLogs).values(execution_id=execution_id, line=err)
        )


@celery_app.task(
    name=TASK_RUN_EXECUTION,
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_execution(self, execution_id: str) -> None:
    now = datetime.now(timezone.utc)

    session = Session()
    host_id_str: str | None = None
    lock_taken = False

    try:
        exec_obj = session.execute(
            select(Execution).where(Execution.uid == execution_id)
        ).scalars().one_or_none()

        if exec_obj is None:
            return

        if exec_obj.status in {
            Execution.Status.SUCCESS,
            Execution.Status.FAILED,
            Execution.Status.TIMEOUT,
            Execution.Status.CANCELLED,
            Execution.Status.BLOCKED,
        }:
            return

        if exec_obj.status != Execution.Status.QUEUED:
            return

        host_id_str = str(exec_obj.host_id)

        job_cmd = session.execute(
            select(Job.command_type).where(Job.uid == exec_obj.job_id)
        ).scalar_one()

        blocked = session.execute(
            select(HostCommandBlock.uid).where(
                HostCommandBlock.host_id == exec_obj.host_id,
                HostCommandBlock.command_type == job_cmd,
            )
        ).first()

        if blocked:
            session.execute(
                update(Execution)
                .where(Execution.uid == execution_id, Execution.status == Execution.Status.QUEUED)
                .values(status=Execution.Status.BLOCKED, finished_at=now)
            )
            session.execute(
                insert(ExecutionLogs).values(
                    execution_id=execution_id,
                    line='blocked by host policy'
                )
            )

            session.commit()
            return

        if not _try_lock_host(session, host_id_str):
            session.rollback()
            session.execute(
                insert(ExecutionLogs).values(
                    execution_id=execution_id,
                    line='host locked'
                )
            )
            session.commit()
            raise self.retry(
                countdown=_backoff_seconds(self.request.retries),
                exc=RuntimeError("host locked"),
            )
        lock_taken = True

        updated = session.execute(
            update(Execution)
            .where(Execution.uid == execution_id, Execution.status == Execution.Status.QUEUED)
            .values(status=Execution.Status.RUNNING, started_at=now, attempts=Execution.attempts + 1)
        ).rowcount

        if updated == 0:
            session.commit()
            return

        session.execute(
            update(Job)
            .where(Job.uid == exec_obj.job_id, Job.status == Job.Status.QUEUED)
            .values(status=Job.Status.RUNNING)
        )
        session.commit()

        result = _simulate_agent_call()
        finished = datetime.now(timezone.utc)

        session.execute(
            update(Execution)
            .where(Execution.uid == execution_id, Execution.status == Execution.Status.RUNNING)
            .values(status=Execution.Status.SUCCESS, finished_at=finished)
        )
        session.execute(
            insert(ExecutionLogs).values(
                execution_id=execution_id,
                line=str(result)
            )
        )
        session.commit()
        return

    except TimeoutError as e:
        session.rollback()
        _retry_or_finish(self, execution_id, str(e), is_timeout=True)

    except Exception as e:
        session.rollback()
        _retry_or_finish(self, execution_id, str(e), is_timeout=False)

    finally:
        if lock_taken and host_id_str is not None:
            try:
                _unlock_host(session, host_id_str)
                session.commit()
            except Exception as e:
                session.rollback()
        session.close()
