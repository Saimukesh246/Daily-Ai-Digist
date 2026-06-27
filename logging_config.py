import json
import logging
import sys
from datetime import datetime


class JsonFormatter(logging.Formatter):
    """Renders log records as single-line JSON so they're greppable/parseable in
    Render's log viewer (or any log aggregator) instead of free-form text."""

    def format(self, record):
        payload = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level=logging.INFO):
    """Configures the root logger once, before any module-level `logging.basicConfig`
    calls elsewhere in the codebase can install a conflicting plain-text handler."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
