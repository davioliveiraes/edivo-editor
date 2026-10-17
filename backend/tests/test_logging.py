import json
import logging

from app.core.logging import SafeJsonFormatter


def test_logging_excludes_credentials_paths_and_tracebacks():
    record = logging.LogRecord(
        "edivo.test",
        logging.ERROR,
        "/home/private/source.py",
        1,
        "failed postgresql+psycopg://user:secret@db/edivo password=secret /home/private/file",
        (),
        (RuntimeError, RuntimeError("secret"), None),
    )
    output = SafeJsonFormatter().format(record)
    payload = json.loads(output)
    assert "secret" not in output
    assert "/home/private" not in output
    assert "postgresql" not in output
    assert "exc_info" not in payload
    assert "Traceback" not in output
