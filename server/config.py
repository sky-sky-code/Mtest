import os

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://dev:dev@127.0.0.1:5432/mtest")
REQUIRES_APPROVAL = {"RESTART_SERVICE", "DEPLOY", "RUN_SCRIPT"}

MAX_RETRIES = int(os.getenv("EXEC_MAX_RETRIES", "3"))
BASE_BACKOFF = float(os.getenv("EXEC_BASE_BACKOFF_SEC", "2"))
MAX_BACKOFF = float(os.getenv("EXEC_MAX_BACKOFF_SEC", "30"))


TASK_PLAN_JOB = "worker.tasks.plan_job.plan_job"
TASK_PUBLISH_OUTBOX = "worker.tasks.publish_outbox.publish_outbox"
TASK_RUN_EXECUTION = 'worker.tasks.run_execution.run_execution'

