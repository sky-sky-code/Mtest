import logging


def log_event(log: logging.Logger, event: str, msg: str = "", **fields):
    log.info(msg or event, extra={"event": event, **fields})
