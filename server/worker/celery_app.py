
from celery import Celery
from kombu import Queue

from config import REDIS_URL, TASK_PUBLISH_OUTBOX

from log.conf import setup_logging
setup_logging()

celery_app = Celery(
    "orchestrator",
    broker=REDIS_URL,
    include=[
        "worker.tasks.publish_outbox",
        "worker.tasks.run_execution",
        "worker.tasks.plan_job",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 60 * 60,
        "retry_on_timeout": True,
    },
)

celery_app.conf.beat_schedule = {
    "publish-outbox-every-2s": {
        "task": TASK_PUBLISH_OUTBOX,
        "schedule": 2.0,
    }
}

celery_app.conf.task_queues = (Queue("default"),)
celery_app.conf.task_default_queue = "default"
