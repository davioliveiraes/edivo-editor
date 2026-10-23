#!/usr/bin/env python3
"""Real Edivo PostgreSQL outage during a dedicated synthetic render attempt.

Run only in a coordinated idle window, with the main application worker already
stopped. This harness temporarily stops/starts ONLY the existing Edivo db service
and always attempts restoration in finally. It never changes the main worker,
ObraControl, volumes, existing projects or artifacts. Only labeled, owned one-off
worker containers may be signaled by inherited cleanup. A new synthetic project
and its immutable original/history remain available after the test.

Requires the rebuilt R05 image and the existing worker-control harness. This
script is intentionally not part of default pytest or automatic app startup.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r05/evidence"
HELPER_PATH = Path(__file__).with_name("acceptance-r05-worker-control.py")
SPEC = importlib.util.spec_from_file_location(
    "edivo_r05_worker_control_qa", HELPER_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("worker_control_qa_helper_unavailable")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
CheckFailed = HELPER.CheckFailed

STATE_SOURCE = r"""
import hashlib,json,sys
from uuid import UUID
from sqlalchemy import func,select
from app.core.config import Settings
from app.db.session import Database
from app.models.render_job import RenderJob
database=Database(Settings())
try:
 with database.sessions() as session:
  job=session.scalar(select(RenderJob).where(RenderJob.project_id==UUID(sys.argv[1]),
                                            RenderJob.id==UUID(sys.argv[2])))
  assert job is not None
  now=session.scalar(select(func.clock_timestamp()))
  token=hashlib.sha256(str(job.claim_token).encode()).hexdigest() if job.claim_token else None
  print(json.dumps({'id':str(job.id),'status':job.status,'stage':job.stage,
   'attempts':job.attempts,'claim_token_sha256':token,
   'lease_expired':job.lease_expires_at is not None and job.lease_expires_at<=now,
   'cancel_requested':job.cancel_requested_at is not None,
   'published':job.artifact_storage_key is not None,'error_code':job.error_code}))
finally: database.close()
"""

UNPUBLISHED_SOURCE = r"""
import json,sys
from uuid import UUID
from app.core.config import Settings
project,job=UUID(sys.argv[1]),UUID(sys.argv[2])
root=Settings().storage_root
canonical=root/'renders'/str(project)/str(job)/'final.mp4'
temporary=root/'temp'
workspaces=list(temporary.glob('render-'+str(job)+'-*')) if temporary.is_dir() else []
print(json.dumps({'project_id':str(project),'job_id':str(job),
 'canonical_exists':canonical.exists() or canonical.is_symlink(),
 'owned_attempt_temporary_entries':len(workspaces)}))
"""


class DatabaseOutageHarness(HELPER.Harness):
    def __init__(self, output: Path, port: int):
        super().__init__(output, port)
        self.database_container: dict | None = None
        self.restore_database_needed = False
        self.report.update(
            scenario="postgresql_unavailable_during_encoding",
            database_lifecycle_performed=False,
            application_worker_lifecycle_performed=False,
            existing_project_data_mutated=False,
        )
        self.report["limitations"] += [
            "Stops/starts only existing Edivo PostgreSQL in a coordinated idle window.",
            "One local database outage; not a network partition or multi-host deployment test.",
            "QA lease/heartbeat are 10/0.5 seconds; production defaults remain 30/5 seconds.",
        ]

    def establish_maintenance_window(self):
        workers = self.command(
            ["docker", "compose", "ps", "--all", "--quiet", "worker"]
        ).stdout.split()
        states = [self.inspect(identifier) for identifier in workers]
        self.check(
            "main_edivo_worker_exists_and_is_already_stopped",
            len(states) == 1
            and states[0] is not None
            and not states[0]["running"]
            and states[0]["labels"].get("com.docker.compose.project") == "edivo"
            and states[0]["labels"].get("com.docker.compose.service") == "worker"
            and str(states[0]["labels"].get("com.docker.compose.oneoff", "")).lower()
            == "false",
        )
        self.report["main_worker_container_id"] = states[0]["id"]
        active = self.database("active")
        self.check(
            "all_queues_initially_empty", not any(active.values()), counts=active
        )
        containers = self.command(
            ["docker", "compose", "ps", "--quiet", "db"]
        ).stdout.split()
        self.check("one_existing_database_container", len(containers) == 1)
        state = self.inspect(containers[0])
        self.check(
            "existing_database_is_edivo_non_oneoff_and_running",
            state is not None
            and state["running"]
            and state["labels"].get("com.docker.compose.project") == "edivo"
            and state["labels"].get("com.docker.compose.service") == "db"
            and str(state["labels"].get("com.docker.compose.oneoff", "")).lower()
            == "false",
        )
        self.database_container = state
        self.report["database_container_id"] = state["id"]

    def verify_database_identity(self, *, running=None):
        self.check(
            "database_identity_was_recorded", self.database_container is not None
        )
        state = self.inspect(self.database_container["id"])
        self.check(
            "database_identity_still_matches_before_lifecycle",
            state is not None
            and state["id"] == self.database_container["id"]
            and state["name"] == self.database_container["name"]
            and state["labels"].get("com.docker.compose.project") == "edivo"
            and state["labels"].get("com.docker.compose.service") == "db"
            and (running is None or state["running"] is running),
        )

    def stop_database(self):
        self.verify_database_identity(running=True)
        # Set before invoking Docker: even an uncertain stop result must trigger
        # restoration, never leave the database stopped on a failed check.
        self.restore_database_needed = True
        self.report["database_lifecycle_performed"] = True
        self.report["database_stop_requested_at"] = datetime.now(UTC).isoformat()
        self.command(["docker", "compose", "stop", "--timeout", "5", "db"], timeout=30)
        self.verify_database_identity(running=False)

    def restore_database(self):
        if not self.restore_database_needed:
            return
        self.verify_database_identity()
        self.command(["docker", "compose", "start", "db"], timeout=30)
        deadline = monotonic() + 90
        while monotonic() < deadline:
            health = self.command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State.Health.Status}}",
                    self.database_container["id"],
                ]
            ).stdout.strip()
            if health == '"healthy"':
                status, _ = self.request("GET", "/api/v1/system/ready")
                if status == 200:
                    self.verify_database_identity(running=True)
                    self.restore_database_needed = False
                    self.report["database_restored_at"] = datetime.now(UTC).isoformat()
                    self.check(
                        "same_database_container_restored_healthy_and_ready", True
                    )
                    return
            sleep(0.5)
        raise CheckFailed("database_health_not_restored_before_deadline")

    def prepare_project(self):
        fixture = self.python(HELPER.FIXTURE_SOURCE)
        content = base64.b64decode(fixture["data"], validate=True)
        status, project = self.request(
            "POST",
            "/api/v1/projects",
            {"name": f"QA R05 database outage {self.run_id[:12]}"},
        )
        self.check("new_synthetic_project_created", status == 201)
        project_id = str(UUID(project["id"]))
        self.report["project_id"] = project_id
        base = f"/api/v1/projects/{project_id}"
        boundary = "edivo-r05-db-" + self.run_id
        multipart = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                'filename="database-outage.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        status, media = self.request(
            "POST",
            base + "/media",
            binary=multipart,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        self.check(
            "synthetic_mp4_uploaded_with_exact_sha256",
            status == 201
            and media["checksum_sha256"] == hashlib.sha256(content).hexdigest(),
        )
        self.report["original_before"] = self.database("snapshot", project_id)
        status, revision = self.request(
            "PUT",
            base + "/edit-spec",
            {
                "expected_revision": 0,
                "schema_version": 1,
                "source_media_id": media["id"],
                "trim": {"start_ms": 1000, "end_ms": 13000},
                "canvas": {
                    "preset": "9:16",
                    "fit": "cover",
                    "focal_x": 0.5,
                    "focal_y": 0.5,
                },
            },
        )
        self.check("saved_immutable_v1_twelve_second_trim", status == 201)
        self.report["revision_id"] = revision["id"]
        return project_id, base, revision

    def wait_measured_encoding(self, worker, project_id, job):
        claim = self.wait_encoding(worker, project_id, job)
        deadline = monotonic() + 30
        while monotonic() < deadline:
            status, current = self.request("GET", job["status_url"])
            if (
                status == 200
                and current["status"] == "running"
                and current["stage"] == "encoding"
                and (current["progress_encoded_ms"] or 0) > 0
            ):
                self.check(
                    "actual_ffmpeg_positive_encoding_progress_before_database_outage",
                    current["attempts"] == 1,
                    progress_encoded_ms=current["progress_encoded_ms"],
                )
                return claim
            if status != 200 or current["status"] in {
                "completed",
                "failed",
                "canceled",
            }:
                raise CheckFailed("render_finished_before_database_outage_boundary")
            sleep(0.05)
        raise CheckFailed("positive_encoding_progress_not_observed")

    def run(self):
        self.establish_maintenance_window()
        project_id, base, revision = self.prepare_project()
        job = self.create_job(base, revision)
        first_worker = self.start_worker("database-outage", project_id)
        first = self.wait_measured_encoding(first_worker, project_id, job)
        first_token = hashlib.sha256(first["claim_token"].encode()).hexdigest()
        self.report["first_claim_token_sha256"] = first_token
        self.stop_database()
        status, health = self.request("GET", "/api/v1/system/health")
        self.check(
            "database_offline_liveness_remains_200", status == 200, response=health
        )
        status, ready = self.request("GET", "/api/v1/system/ready")
        safe = json.dumps(ready).lower()
        self.check(
            "database_offline_readiness_503_is_sanitized",
            status == 503
            and not any(
                value in safe
                for value in (
                    "traceback",
                    "postgresql://",
                    "postgresql+psycopg://",
                    "/data/storage",
                )
            ),
            response=ready,
        )
        log = self.wait_container(first_worker, timeout=30)
        stderr = Path(f"{first_worker['log_stem']}.stderr.log").read_text()
        self.check(
            "database_loss_fences_attempt_and_worker_exits_after_child_stops",
            "render_lease_lost" in stderr
            and '"qa_event": "run_one_finished"' in log
            and '"processed": true' in log,
        )
        unpublished = self.python(UNPUBLISHED_SOURCE, project_id, job["id"])
        self.check(
            "no_canonical_or_owned_staging_after_database_loss",
            not unpublished["canonical_exists"]
            and unpublished["owned_attempt_temporary_entries"] == 0,
            **unpublished,
        )
        self.restore_database()
        deadline = monotonic() + HELPER.QA_LEASE_SECONDS + 10
        while monotonic() < deadline:
            state = self.python(STATE_SOURCE, project_id, job["id"])
            if state["lease_expired"]:
                break
            sleep(0.2)
        else:
            raise CheckFailed("original_attempt_lease_did_not_expire")
        self.check(
            "original_attempt_remains_running_expired_without_cancel_or_publication",
            state["status"] == "running"
            and state["attempts"] == 1
            and state["claim_token_sha256"] == first_token
            and state["lease_expired"]
            and not state["cancel_requested"]
            and not state["published"],
            state=state,
        )
        retry = self.start_worker("database-recovery", project_id)
        second = self.wait_encoding(retry, project_id, job)
        self.check(
            "database_recovery_claims_only_attempt_two_with_new_fencing_token",
            second["attempts"] == 2 and second["claim_token"] != first["claim_token"],
        )
        self.wait_container(retry)
        self.completed(project_id, job, 2)
        completed = self.report["jobs"][-1]["result"]
        artifact = self.report["jobs"][-1]["artifact"]
        self.check(
            "recovered_full_export_is_twelve_seconds_final_resolution_and_private",
            abs(artifact["duration_seconds"] - 12) <= 0.035
            and (completed["width"], completed["height"]) == (1080, 1920)
            and completed["fps_num"] == 30
            and completed["fps_den"] == 1
            and artifact["mode"] == "0o400",
        )
        after = self.database("snapshot", project_id)
        self.report["original_after"] = after
        self.check(
            "original_sha256_mode0400_size_and_mtime_unchanged",
            after == self.report["original_before"],
        )
        active = self.database("active")
        self.check("all_queues_finally_empty", not any(active.values()), counts=active)
        self.report["status"] = "passed"

    def cleanup(self):
        # Restore database before inherited scoped cancellation/cleanup needs SQL.
        # A second finally attempt is made by main even if cleanup itself fails.
        try:
            self.restore_database()
        finally:
            super().cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New docs/r05/evidence/db-outage-attempt-N/report.json (exclusive logs).",
    )
    parser.add_argument("--api-port", type=int)
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        output.parent.parent != EVIDENCE.resolve()
        or not output.parent.name.startswith("db-outage-attempt-")
        or output.parent.exists()
        or output.name != "report.json"
    ):
        parser.error(
            "--output must be report.json in a new R05 db-outage-attempt-N directory"
        )
    port = args.api_port if args.api_port is not None else HELPER.configured_port()
    if not 1 <= port <= 65535:
        parser.error("--api-port must be between 1 and 65535")
    output.parent.mkdir(mode=0o700, parents=True)
    harness = DatabaseOutageHarness(output, port)
    try:
        harness.run()
    except (
        RuntimeError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AssertionError,
        subprocess.SubprocessError,
        http.client.HTTPException,
    ) as error:
        harness.report["status"] = "failed"
        harness.report["failure"] = (
            str(error) if isinstance(error, CheckFailed) else type(error).__name__
        )
    finally:
        try:
            harness.cleanup()
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            harness.report["cleanup_failure"] = type(error).__name__
            harness.report["status"] = "failed"
        finally:
            try:
                harness.restore_database()
            except (
                RuntimeError,
                OSError,
                ValueError,
                subprocess.SubprocessError,
            ) as error:
                harness.report["database_restoration_failure"] = type(error).__name__
                harness.report["status"] = "failed"
        output.write_text(
            json.dumps(harness.report, ensure_ascii=False, indent=2) + "\n"
        )
    print(
        json.dumps(
            {
                "status": harness.report["status"],
                "checks": len(harness.report["checks"]),
                "report": str(output.relative_to(ROOT)),
                "database_restoration_pending": harness.restore_database_needed,
            }
        )
    )
    return 0 if harness.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
