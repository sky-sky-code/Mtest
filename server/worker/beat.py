# scripts/run_worker_inproc.py
from __future__ import annotations

import os
from celery_app import celery_app


def main() -> int:
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    celery_app.start([
        "beat",
        "--loglevel=DEBUG",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
