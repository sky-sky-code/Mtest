import os

from celery_app import celery_app


def main() -> int:
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    celery_app.worker_main([
        "worker",
        "--loglevel=DEBUG",
        "-P", "solo",
        "--concurrency=1",
        "-Q", "default",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
