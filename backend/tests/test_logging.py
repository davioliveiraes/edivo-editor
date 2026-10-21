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


def test_preview_fields_are_allowlisted_without_snapshot_or_exception_payload():
    record = logging.LogRecord(
        "edivo.worker", logging.INFO, "internal.py", 1, "preview_job_failed", (), None
    )
    record.job_id = "11111111-1111-4111-8111-111111111111"
    record.project_id = "22222222-2222-4222-8222-222222222222"
    record.stage = "failed"
    record.attempts = 2
    record.error_code = "preview_timeout"
    record.spec_json = {"text": "private user text"}
    record.argv = ["ffmpeg", "/home/private/input.mp4"]
    record.raw_exception = "password=private-secret"
    payload = json.loads(SafeJsonFormatter().format(record))
    assert payload["job_id"] == record.job_id
    assert payload["project_id"] == record.project_id
    assert payload["stage"] == "failed"
    assert payload["attempts"] == 2
    assert payload["error_code"] == "preview_timeout"
    assert not {"spec_json", "argv", "raw_exception"} & payload.keys()
    assert "private" not in json.dumps(payload)
    record.job_id = "/home/private/asset"
    record.error_code = "password=hidden-credential"
    output = SafeJsonFormatter().format(record)
    assert "/home/private" not in output
    assert "hidden-credential" not in output
