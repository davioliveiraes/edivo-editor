"""R04 preview regression through R05 scheduler; staged HTTP/read-only runtime QA.

No service lifecycle or direct database mutation. queue requires a stopped
worker coordinated by root. verify runs after root starts the shared scheduler.
Uses the existing synthetic render HTTP seed without changing its revisions or
render jobs. Outputs are exclusive; use a new --output after a negative run.
"""

import argparse
import hashlib
import http.client
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]


class QAFailure(RuntimeError):
    pass


class QA:
    def __init__(self, args, report):
        self.args, self.report = args, report
        address = urlsplit(args.api)
        if (
            address.scheme != "http"
            or address.hostname not in {"127.0.0.1", "localhost"}
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in {"", "/"}
        ):
            raise QAFailure("local_api_url_required")
        self.host, self.port = address.hostname, address.port or 8001
        self.render_seed = json.loads(args.render_seed.read_text())
        self.project_id = str(UUID(self.render_seed["project_id"]))
        self.revision = self.render_seed["v2_revision"]
        self.base = f"/api/v1/projects/{self.project_id}"
        self.seed_path = args.work / "preview-seed.json"
        self.preview = (
            json.loads(self.seed_path.read_text()) if self.seed_path.exists() else None
        )

    def check(self, name, condition, **details):
        result = {"check": name, "passed": bool(condition), **details}
        self.report["checks"].append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not condition:
            raise QAFailure(name)

    def connection(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=30)

    def request(self, method, path, payload=None, headers=None):
        connection = self.connection()
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            supplied = dict(headers or {})
            if body is not None:
                supplied["Content-Type"] = "application/json"
            connection.request(method, path, body, supplied)
            response = connection.getresponse()
            content = response.read(2 * 1024 * 1024 + 1)
            self.check("bounded_http_response", len(content) <= 2 * 1024 * 1024)
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                content,
            )
        finally:
            connection.close()

    def api(self, method, path, payload=None):
        status, headers, raw = self.request(method, path, payload)
        self.check("request_id_present", bool(headers.get("x-request-id")))
        return status, json.loads(raw) if raw else None

    def queue(self):
        self.check("preview_seed_is_new", self.preview is None)
        status, current = self.api("GET", self.base + "/edit-spec")
        self.check(
            "existing_latest_revision_preserved",
            status == 200
            and current["id"] == self.render_seed["latest_revision"]["id"],
        )
        status, job = self.api(
            "POST",
            self.base + "/previews",
            {
                "edit_spec_revision_id": self.revision["id"],
                "preview_start_ms": 3000,
                "duration_ms": 4000,
            },
        )
        self.check(
            "real_preview_queued_from_saved_v2",
            status == 202
            and job["status"] == "queued"
            and job["revision_id"] == self.revision["id"]
            and job["preview_start_ms"] == 3000
            and job["duration_ms"] == 4000,
        )
        descriptor = os.open(
            self.seed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w") as target:
            json.dump(job, target, indent=2)
            target.write("\n")
        self.preview = job
        self.report["job"] = job

    def download_hash(self, path, expected, *, name):
        connection = self.connection()
        checksum, total = hashlib.sha256(), 0
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            self.check(name + "_full_get_200", response.status == 200)
            first = bytearray()
            while chunk := response.read(65536):
                if len(first) < 64:
                    first.extend(chunk[: 64 - len(first)])
                checksum.update(chunk)
                total += len(chunk)
        finally:
            connection.close()
        self.check(
            name + "_sha256_exact",
            checksum.hexdigest() == expected["checksum_sha256"]
            and total == expected["byte_size"],
            byte_size=total,
            checksum_sha256=checksum.hexdigest(),
        )
        return bytes(first)

    def file_checks(self, job):
        path, size = job["file_url"], job["byte_size"]
        status, headers, body = self.request("HEAD", path)
        self.check(
            "preview_head_private_headers",
            status == 200
            and not body
            and headers.get("content-type") == "video/mp4"
            and headers.get("content-disposition") == "inline"
            and headers.get("accept-ranges") == "bytes"
            and headers.get("cache-control") == "private, no-store"
            and headers.get("x-content-type-options") == "nosniff"
            and int(headers.get("content-length", 0)) == size,
        )
        first = self.download_hash(path, job, name="preview")
        status, headers, body = self.request(
            "GET", path, headers={"Range": "bytes=0-31"}
        )
        self.check(
            "preview_range_206_exact",
            status == 206
            and body == first[:32]
            and headers.get("content-range") == f"bytes 0-31/{size}"
            and headers.get("content-length") == "32",
        )
        status, headers, body = self.request(
            "HEAD", path, headers={"Range": "bytes=-1"}
        )
        self.check(
            "preview_suffix_head_206",
            status == 206
            and not body
            and headers.get("content-range") == f"bytes {size - 1}-{size - 1}/{size}",
        )
        status, headers, _body = self.request(
            "GET", path, headers={"Range": f"bytes={size}-"}
        )
        self.check(
            "preview_range_416",
            status == 416 and headers.get("content-range") == f"bytes */{size}",
        )

    def inspect_runtime(self, job):
        project_id, job_id = str(UUID(self.project_id)), str(UUID(job["id"]))
        # UUIDs are parsed locally before constructing trusted Python source;
        # no URL/path from HTTP becomes a filesystem or subprocess argument.
        code = """
import hashlib,json,os,stat
from threading import Event
from uuid import UUID
from sqlalchemy import select
from app.core.config import Settings
from app.db.session import Database
from app.models.project import Project
from app.models.preview_job import PreviewJob
from app.models.edit_spec_revision import EditSpecRevision
from app.models.media_asset import MediaAsset
from app.models.audio_asset import AudioAsset
from app.integrations.ffmpeg.preview import validate_metadata
from app.storage.local import LocalStorage
from app.worker.supervisor import ProcessRunner
settings=Settings(); db=Database(settings); storage=LocalStorage(settings)
runner=ProcessRunner(settings,Event()); result={"originals":{}}
def hash_opened(opened):
 with opened.file as stream:
  before=os.fstat(stream.fileno()); digest=hashlib.sha256()
  while chunk:=stream.read(65536): digest.update(chunk)
  after=os.fstat(stream.fileno())
  assert (before.st_size,before.st_mode,before.st_mtime_ns)==(after.st_size,after.st_mode,after.st_mtime_ns)
  assert stat.S_IMODE(after.st_mode)==0o400
  return {"checksum_sha256":digest.hexdigest(),"byte_size":after.st_size,"mode":"0400"}
try:
 with db.sessions() as session:
  pid=UUID(PROJECT_ID); jid=UUID(JOB_ID)
  project=session.get(Project,pid); assert project is not None and project.deleted_at is None
  job=session.get(PreviewJob,jid); assert job.project_id==pid and job.status=="completed"
  revision=session.get(EditSpecRevision,job.edit_spec_revision_id)
  source=session.get(MediaAsset,revision.source_media_id)
  assert source.project_id==pid
  result["originals"][str(source.id)]=hash_opened(storage.open_original(source.storage_key,pid,source.id,source.byte_size))
  plan=revision.spec_json
  audio_id=plan.get("audio",{}).get("background_audio_id")
  if audio_id:
   audio=session.get(AudioAsset,UUID(audio_id)); assert audio.project_id==pid
   result["originals"][str(audio.id)]=hash_opened(storage.open_audio(audio.storage_key,pid,audio.id,audio.byte_size))
  expects_audio=source.has_audio or bool(audio_id)
  opened=storage.open_preview(job.artifact_storage_key,pid,jid,job.artifact_byte_size)
  with opened.file as stream:
   assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode)==0o400
   descriptor=stream.fileno(); path=f"/proc/self/fd/{descriptor}"
   probe=runner.run([settings.ffprobe_bin,"-v","error","-protocol_whitelist","file,pipe","-f","mov","-show_entries","format=format_name,duration,start_time:stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_aspect_ratio,duration,nb_frames,start_time,sample_rate,channels:stream_side_data=rotation","-of","json","-i",path],pass_fds=(descriptor,))
   assert probe.returncode==0
   controlled=validate_metadata(json.loads(probe.stdout),plan["canvas"]["preset"],job.duration_ms,expects_audio)
   decoded=runner.run([settings.ffmpeg_bin,"-v","error","-nostdin","-xerror","-threads","1","-err_detect","explode","-protocol_whitelist","file,pipe","-f","mov","-i",path,"-map","0:v:0","-map","0:a:0?","-fps_mode","passthrough","-f","null","-","-progress","pipe:1"],pass_fds=(descriptor,))
   fields=dict(line.split("=",1) for line in decoded.stdout.decode("ascii").splitlines() if "=" in line)
   assert decoded.returncode==0 and fields["progress"]=="end"
   assert int(fields["frame"])==controlled["frame_count"]
   controlled["decoded_frames"]=int(fields["frame"])
   stream.seek(0); digest=hashlib.sha256()
   while chunk:=stream.read(65536): digest.update(chunk)
   assert digest.hexdigest()==job.artifact_sha256
   result["preview"]={**controlled,"checksum_sha256":digest.hexdigest(),"byte_size":job.artifact_byte_size,"mode":"0400"}
 print(json.dumps(result))
finally: db.close()
""".replace("PROJECT_ID", repr(project_id)).replace("JOB_ID", repr(job_id))
        execution = subprocess.run(
            ["docker", "compose", "exec", "-T", "api", "python", "-"],
            input=code,
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            timeout=90,
        )
        self.check("real_api_runtime_inspection_passed", execution.returncode == 0)
        if execution.returncode:
            raise QAFailure("runtime_probe_or_complete_decode_failed")
        return json.loads(execution.stdout)

    def verify(self):
        self.check("queued_preview_seed_present", bool(self.preview))
        deadline, observations = time.monotonic() + self.args.timeout, []
        while time.monotonic() < deadline:
            status, job = self.api("GET", self.preview["status_url"])
            self.check("preview_status_available", status == 200)
            row = {key: job[key] for key in ("id", "status", "stage", "attempts")}
            if not observations or row != observations[-1]:
                observations.append(row)
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.2)
        else:
            raise QAFailure("preview_worker_timeout")
        self.report["observations"] = observations
        self.check(
            "real_shared_scheduler_completed_preview",
            job["status"] == "completed"
            and job["artifact_available"]
            and job["revision_id"] == self.revision["id"],
        )
        self.report["job"] = job
        self.file_checks(job)
        source = self.render_seed["media"]
        source_path = self.base + f"/media/{source['id']}/stream"
        first = self.download_hash(source_path, source, name="original_r03")
        status, headers, body = self.request(
            "GET", source_path, headers={"Range": "bytes=0-31"}
        )
        self.check(
            "original_r03_range_preserved",
            status == 206
            and body == first[:32]
            and headers.get("content-range") == f"bytes 0-31/{source['byte_size']}",
        )
        runtime = self.inspect_runtime(job)
        self.check(
            "preview_profile_r04_still_reduced_24fps",
            runtime["preview"]["fps_num"] == 24
            and runtime["preview"]["fps_den"] == 1
            and (runtime["preview"]["width"], runtime["preview"]["height"])
            == (540, 960)
            and abs(runtime["preview"]["duration_ms"] - 4000) <= 43
            and runtime["preview"]["decoded_frames"] == 96,
        )
        for asset in (self.render_seed["media"], self.render_seed["audio"]):
            observed = runtime["originals"][asset["id"]]
            self.check(
                "original_mp4_mp3_sha0400_unchanged",
                observed["mode"] == "0400"
                and observed["checksum_sha256"] == asset["checksum_sha256"]
                and observed["byte_size"] == asset["byte_size"],
                asset_id=asset["id"],
            )
        self.report["runtime"] = runtime
        status, current = self.api("GET", self.base + "/edit-spec")
        self.check(
            "existing_revision_not_modified",
            status == 200
            and current["id"] == self.render_seed["latest_revision"]["id"],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("queue", "verify"), required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--work", type=Path, default=Path("/tmp/edivo-r05/http"))
    parser.add_argument(
        "--render-seed",
        type=Path,
        default=Path("/tmp/edivo-r05/http/render-http-seed.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("timeout deve estar entre1 e600 segundos")
    args.work.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = (
        args.output or ROOT / f"docs/r05/evidence/preview-http-{args.stage}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {
        "status": "failed",
        "stage": args.stage,
        "created_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "service_lifecycle_performed": False,
        "direct_database_mutation": False,
        "browser_used": False,
    }
    started = time.monotonic()
    try:
        qa = QA(args, report)
        getattr(qa, args.stage)()
        report["project_id"] = qa.project_id
        report["status"] = "passed"
    except (
        QAFailure,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        http.client.HTTPException,
        subprocess.SubprocessError,
    ) as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, QAFailure):
            report["failure"] = str(error)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "failure": report.get("failure"),
                }
            ),
            flush=True,
        )
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        with os.fdopen(descriptor, "w") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "stage": args.stage,
                "checks": len(report["checks"]),
                "seconds": report["elapsed_seconds"],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
