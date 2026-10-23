"""Independent serial preview/export worker with diagnostics and graceful signals."""

import logging
import signal
from threading import Event

from pydantic import ValidationError
from pydantic_settings import SettingsError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.integrations.ffmpeg.capabilities import CapabilityDetector
from app.services.readiness import check_dependencies
from app.worker.processor import PreviewProcessor
from app.worker.render_processor import RenderProcessor
from app.worker.scheduler import WorkerScheduler

logger = logging.getLogger("edivo.worker")


def run() -> int:
    configure_logging("INFO")
    try:
        settings = get_settings()
    except (ValidationError, SettingsError):
        logger.error("worker_configuration_invalid")
        return 1
    configure_logging(settings.log_level)
    stopped = Event()

    def stop(signum: int, _frame: object) -> None:
        logger.info("worker_stop_requested", extra={"signal": signal.Signals(signum).name})
        stopped.set()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    database = Database(settings)
    try:
        readiness = check_dependencies(database.check, settings.storage_root)
        capabilities = CapabilityDetector(settings, runtime="worker").get()
        logger.info(
            "worker_startup_diagnostic",
            extra={
                "diagnostic": {
                    "dependencies": readiness.model_dump(),
                    "capabilities": capabilities.model_dump(),
                }
            },
        )
        if readiness.status != "ready" or capabilities.status != "available":
            logger.error("worker_startup_failed_dependencies_unavailable")
            return 1
        logger.info("worker_ready_idle")
        preview_processor = PreviewProcessor(settings, database.sessions, stopped)
        render_processor = RenderProcessor(settings, database.sessions, stopped)
        WorkerScheduler(
            settings, database.sessions, stopped, preview_processor, render_processor
        ).run()
        return 0
    except Exception:
        logger.error("worker_startup_failed")
        return 1
    finally:
        database.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        logger.info("worker_stopped")


if __name__ == "__main__":
    raise SystemExit(run())
