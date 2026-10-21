from __future__ import annotations

import logging


class QuietStatusAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method = args[1]
        path = args[2]
        status_code = args[4]
        if method != "GET" or status_code != 200 or not isinstance(path, str):
            return True
        path_only = path.split("?", 1)[0]
        if path_only.endswith("/status"):
            return False
        return True


def configure_access_log_filters() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(
        isinstance(existing, QuietStatusAccessFilter)
        for existing in access_logger.filters
    ):
        return
    access_logger.addFilter(QuietStatusAccessFilter())
