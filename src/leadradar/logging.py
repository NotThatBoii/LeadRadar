import json
import logging
import sys

from leadradar.models import utc_now


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "timestamp": utc_now(),
                "level": record.levelname,
                "event": record.getMessage(),
                "details": getattr(record, "details", {}),
            }
        )


class CurrentStderrHandler(logging.StreamHandler):
    def emit(self, record):
        # Embedded CLIs can replace/close stderr between invocations.
        self.stream = sys.stderr
        super().emit(record)


def configure_logging():
    handler = CurrentStderrHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("leadradar")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
