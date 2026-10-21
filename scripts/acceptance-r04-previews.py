#!/usr/bin/env python3
"""Real R04 HTTP/worker/Range/persistence QA on a new synthetic project only.

Briefly stops only Edivo's worker when the queue is empty. It always starts it
again, preserves all sources/results, and logically deletes only its own project.
No simulation of completed jobs or fabricated render artifacts is used.
"""

import argparse
import hashlib
import http.client
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r02", ROOT / "scripts/acceptance-r02.py")
assert spec and spec.loader
r02 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r02)
foundation = r02.foundation


def raw_request(method, path, headers=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", foundation.configured_port("EDIVO_API_PORT", 8000), timeout=30
    )
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def snapshot(pid):
    return json.loads(
        foundation.execute_python(
            "api",
            f"""
import hashlib,json,stat
from uuid import UUID
from sqlalchemy import select
from app.core.config import Settings
from app.db.session import Database
from app.models.media_asset import MediaAsset
from app.models.audio_asset import AudioAsset
from app.models.preview_job import PreviewJob
from app.storage.local import LocalStorage
settings=Settings();db=Database(settings);storage=LocalStorage(settings);result={{}}
try:
 with db.sessions() as s:
  for model,opener in ((MediaAsset,storage.open_original),(AudioAsset,storage.open_audio)):
   for a in s.scalars(select(model).where(model.project_id==UUID({pid!r}))):
    opened=opener(a.storage_key,a.project_id,a.id,a.byte_size)
    with opened.file as stream:
     sha=hashlib.sha256()
     while chunk:=stream.read(1048576): sha.update(chunk)
    result[str(a.id)]={{'sha256':sha.hexdigest(),'bytes':opened.stat_result.st_size,'mode':format(stat.S_IMODE(opened.stat_result.st_mode),'04o'),'mtime_ns':opened.stat_result.st_mtime_ns}}
 print(json.dumps(result))
finally: db.close()
""",
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("/tmp/edivo-r04/fixtures"))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/r04/evidence/preview-http-worker.json",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart only Edivo db/api/worker after the queue is empty.",
    )
    args = parser.parse_args()
    checks, jobs = [], []
    pid = None
    worker_stopped = False
    result = {
        "status": "failed",
        "checks": checks,
        "jobs": jobs,
        "restart_requested": args.restart,
    }

    def check(name, value, detail=None):
        if not value:
            raise foundation.AcceptanceError(name)
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1]), flush=True)

    def post(payload):
        return r02.json_request("POST", base + "/previews", payload)

    def wait_job(job):
        deadline = time.monotonic() + 150
        stages = []
        while time.monotonic() < deadline:
            status, body = r02.json_request("GET", job["status_url"])
            if status != 200:
                raise foundation.AcceptanceError("job_status_unavailable")
            if not stages or stages[-1] != body["stage"]:
                stages.append(body["stage"])
            if body["status"] in ("completed", "failed"):
                check(
                    "worker_completed_valid_artifact",
                    body["status"] == "completed" and body["artifact_available"],
                    {"id": body["id"], "observed_stages": stages},
                )
                jobs.append(body)
                return body
            time.sleep(0.25)
        raise foundation.AcceptanceError("preview_did_not_complete")

    try:
        active = json.loads(
            foundation.execute_python(
                "api",
                """
import json
from sqlalchemy import select,func
from app.core.config import Settings
from app.db.session import Database
from app.models.preview_job import PreviewJob
db=Database(Settings())
try:
 with db.sessions() as s:
  query=select(func.count()).select_from(PreviewJob).where(PreviewJob.status.in_(['queued','running']))
  print(json.dumps(s.scalar(query)))
finally: db.close()
""",
            )
        )
        check("safe_empty_queue_before_worker_stop", active == 0)
        foundation.compose("stop", "-t", "10", "worker")
        worker_stopped = True
        status, project = r02.json_request(
            "POST", "/api/v1/projects", {"name": f"QA R04 preview {uuid4()}"}
        )
        check("create_project", status == 201)
        pid = project["id"]
        result["project_id"] = pid
        base = "/api/v1/projects/" + pid
        video = (args.fixtures / "horizontal.mp4").read_bytes()
        status, media = r02.upload(pid, video, "horizontal.mp4")
        check(
            "real_r02_video_upload",
            status == 201 and media["checksum_sha256"] == hashlib.sha256(video).hexdigest(),
        )
        music = (args.fixtures / "music-short.mp3").read_bytes()
        body, headers = r02.multipart(music, "music-short.mp3")
        status, audio = r02.request("POST", base + "/audio", body, headers)
        check(
            "real_r04_mp3_upload",
            status == 201 and audio["checksum_sha256"] == hashlib.sha256(music).hexdigest(),
        )
        before = snapshot(pid)
        plan = {
            "schema_version": 2,
            "source_media_id": media["id"],
            "trim": {"start_ms": 2000, "end_ms": 11000},
            "canvas": {
                "preset": "9:16",
                "fit": "contain",
                "focal_x": 0.5,
                "focal_y": 0.5,
            },
            "text_overlays": [
                {
                    "id": str(uuid4()),
                    "text": "Edição: 50% 'á' \\ Ω\nPrévia real",
                    "start_ms": 3000,
                    "end_ms": 10000,
                    "position": "top",
                    "style": "blue_outline",
                    "size": "medium",
                }
            ],
            "audio": {
                "source_volume": 1.25,
                "background_audio_id": audio["id"],
                "background_volume": 0.15,
                "background_loop": True,
                "fade_in_ms": 500,
                "fade_out_ms": 500,
            },
        }
        status, revision = r02.json_request(
            "PUT", base + "/edit-spec", plan | {"expected_revision": 0}
        )
        check("saved_v2_revision", status == 201 and revision["schema_version"] == 2)
        payload = {
            "edit_spec_revision_id": revision["id"],
            "preview_start_ms": 5000,
            "duration_ms": 4000,
        }
        barrier = Barrier(3)

        def concurrent():
            barrier.wait(timeout=10)
            return post(payload)

        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda _: concurrent(), range(3)))
        check(
            "three_concurrent_equivalent_requests_one_job",
            all(s == 202 for s, _ in responses) and len({j["id"] for _, j in responses}) == 1,
        )
        first = responses[0][1]
        status, second = post(payload | {"preview_start_ms": 10000, "duration_ms": 8000})
        check(
            "second_active_job_window_truncated",
            status == 202 and second["duration_ms"] == 1000,
        )
        status, error = post(payload | {"preview_start_ms": 6000})
        check(
            "third_active_job_rejected_409",
            status == 409 and error["code"] == "preview_active_limit",
        )
        status, error = r02.json_request("GET", first["status_url"] + "/file")
        check(
            "queued_artifact_not_readable",
            status == 409 and error["code"] == "preview_not_ready",
        )
        foundation.compose("start", "worker")
        worker_stopped = False
        first, second = wait_job(first), wait_job(second)
        status, reused = post(payload)
        check(
            "completed_equivalent_reused_200",
            status == 200 and reused["id"] == first["id"],
        )
        status, headers, data = raw_request("GET", first["file_url"])
        lower = {k.lower(): v for k, v in headers.items()}
        check(
            "full_mp4_checksum_headers",
            status == 200
            and hashlib.sha256(data).hexdigest() == first["checksum_sha256"]
            and len(data) == first["byte_size"]
            and lower.get("content-type") == "video/mp4"
            and lower.get("accept-ranges") == "bytes"
            and lower.get("cache-control") == "private, no-store"
            and lower.get("x-content-type-options") == "nosniff",
        )
        for value, start, end in [
            ("bytes=0-15", 0, 16),
            ("bytes=1024-2047", 1024, 2048),
            ("bytes=2048-", 2048, len(data)),
            ("bytes=-31", len(data) - 31, len(data)),
            ("bytes=7-7", 7, 8),
        ]:
            status, headers, content = raw_request("GET", first["file_url"], {"Range": value})
            h = {k.lower(): v for k, v in headers.items()}
            hs, hh, hb = raw_request("HEAD", first["file_url"], {"Range": value})
            check(
                "range_" + value,
                status == hs == 206
                and content == data[start:end]
                and hb == b""
                and h["content-range"] == f"bytes {start}-{end - 1}/{len(data)}"
                and int(h["content-length"]) == end - start
                and {k.lower(): v for k, v in hh.items()}["content-length"] == h["content-length"],
            )
        for value in ("bytes=0-1,5-6", "bytes=-0", f"bytes={len(data)}-"):
            status, headers, content = raw_request("GET", first["file_url"], {"Range": value})
            check(
                "invalid_range_" + value,
                status == 416
                and json.loads(content)["code"] == "range_not_satisfiable"
                and {k.lower(): v for k, v in headers.items()}["content-range"]
                == f"bytes */{len(data)}",
            )
        status, _headers, content = raw_request("HEAD", first["file_url"])
        check("head_full_no_body", status == 200 and content == b"")
        status, newer = r02.json_request(
            "PUT",
            base + "/edit-spec",
            plan | {"canvas": plan["canvas"] | {"preset": "1:1"}, "expected_revision": 1},
        )
        check(
            "new_revision_keeps_old_preview",
            status == 200
            and newer["revision_number"] == 2
            and r02.json_request("GET", first["status_url"])[1]["revision_id"] == revision["id"],
        )
        status, old = post(payload)
        check(
            "explicit_old_revision_reused",
            status == 200 and old["revision_number"] == 1,
        )
        if args.restart:
            foundation.compose("restart", "db")
            foundation.wait_ready()
            foundation.compose("restart", "api", "worker")
            foundation.wait_ready()
            check(
                "job_and_revision_persist_after_restart",
                r02.json_request("GET", first["status_url"])[1] == first
                and r02.json_request("GET", base + "/edit-spec")[1] == newer,
            )
            status, _headers, content = raw_request(
                "GET", first["file_url"], {"Range": "bytes=0-31"}
            )
            check("range_after_restart", status == 206 and content == data[:32])
        after = snapshot(pid)
        check(
            "mp4_mp3_originals_sha_permissions_mtime_unchanged",
            before == after and all(x["mode"] == "0400" for x in after.values()),
            after,
        )
        foreign = f"/api/v1/projects/{uuid4()}/previews/{first['id']}"
        check(
            "cross_project_denied",
            r02.json_request("GET", foreign)[0] == 404
            and raw_request("GET", foreign + "/file")[0] == 404,
        )
        status, _body = r02.json_request("DELETE", base)
        check(
            "soft_delete_blocks_jobs_and_files",
            status == 204
            and r02.json_request("GET", first["status_url"])[0] == 404
            and raw_request("GET", first["file_url"])[0] == 404,
        )
        check("soft_delete_preserves_originals", snapshot(pid) == before)
        result["status"] = "passed"
    except Exception as error:  # noqa: BLE001 — persist sanitized QA failure and restore worker
        result["failure"] = type(error).__name__
        if isinstance(error, foundation.AcceptanceError):
            result["failed_check"] = str(error)
        print(json.dumps({"status": "failed", "type": type(error).__name__}), flush=True)
    finally:
        if worker_stopped:
            foundation.compose("start", "worker")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        attempt = args.output.with_name(f"{args.output.stem}-{time.time_ns()}.json")
        result["attempt_evidence"] = attempt.name
        with attempt.open("x") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
