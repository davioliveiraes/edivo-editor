from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryFile
from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "unavailable"]


class DependencyChecks(BaseModel):
    database: CheckStatus
    storage: CheckStatus


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: DependencyChecks


def check_storage(root: Path) -> bool:
    """Check real write/read permissions without altering existing files."""
    try:
        if not root.is_dir():
            return False
        with TemporaryFile(dir=root) as probe:
            probe.write(b"edivo-readiness")
            probe.flush()
            probe.seek(0)
            return probe.read() == b"edivo-readiness"
    except OSError:
        return False


def check_dependencies(database_check: Callable[[], bool], storage_root: Path) -> ReadinessReport:
    database_ok = database_check()
    storage_ok = check_storage(storage_root)
    return ReadinessReport(
        status="ready" if database_ok and storage_ok else "not_ready",
        checks=DependencyChecks(
            database="ok" if database_ok else "unavailable",
            storage="ok" if storage_ok else "unavailable",
        ),
    )
