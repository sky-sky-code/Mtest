import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        for k in (
                "event", 'service',
                "request_id",
                "job_id",
                "execution_id",
                "host_id",
                "hostnames",
                "command_type",
                "status",
                "attempt", "celery_retries",
                "duration_ms", "backoff_sec",
                "error_type", "error_msg",
        ):
            v = getattr(record, k, None)
            if v is not None:
                data[k] = v

        if record.exc_info:
            data["exc_type"] = record.exc_info[0].__name__
            data["exc"] = self.formatException(record.exc_info)

        return json.dumps(data, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root.addHandler(h)