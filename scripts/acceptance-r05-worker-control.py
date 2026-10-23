#!/usr/bin/env python3
"""Real PostgreSQL/FFmpeg worker control QA, scoped to newly created synthetic data.

Run only after rebuilding R05 and stopping the application worker with an empty
queue. This harness never stops/starts that worker. It signals only dedicated,
verified Compose one-off containers. No original, preview, export or volume is
removed. Crash/lease-loss attempts write to a new private QA tree; only that
tree may be cleaned after every associated container has been confirmed gone.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/r05/evidence"
# Shorter than the production default (30 s), but above measured WSL wall-clock
# jumps and the database statement timeout. Earlier 2 s stress runs remain logged.
QA_LEASE_SECONDS = 10

WORKER_SOURCE = r"""
import json,os,re,signal,sys
from datetime import UTC,datetime
from pathlib import Path
from threading import Event,Lock,Thread,local
from time import monotonic,sleep,time
from uuid import UUID
from sqlalchemy import event,func,select
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.models.render_job import RenderJob
from app.repositories.render_jobs import RenderJobRepository
from app.worker.processor import PreviewProcessor
from app.worker.render_processor import RenderProcessor
from app.worker.render_worker import RenderLeaseController
from app.worker.scheduler import WorkerScheduler
from app.worker.supervisor import PreviewProcessError,ProcessRunner

settings=Settings(); configure_logging('INFO'); stopped=Event(); watching=Event(); os.umask(0o077)
qa_lock=Lock(); qa_context=local(); qa_emitted=0
def qa_emit(document):
 global qa_emitted
 with qa_lock:
  essential=document.get('qa_event') in ('run_one_finished','real_ffmpeg_observed','ready_to_claim')
  if qa_emitted>6000 and not essential: return
  if qa_emitted==6000 and not essential:
   document={'qa_event':'diagnostic_event_limit_reached','limit':6000}
  qa_emitted+=1
  wall=time(); steady=monotonic()
  document={'recorded_at':datetime.fromtimestamp(wall,UTC).isoformat(),
   'wall_epoch_seconds':wall,'monotonic_seconds':steady,**document}
  try: print(json.dumps(document),flush=True)
  except OSError: pass
def qa_resources():
 result={}
 for filename in ('cpu.stat','memory.events'):
  try:
   values={}
   for line in (Path('/sys/fs/cgroup')/filename).read_text().splitlines():
    key,value=line.split()
    if value.isdigit(): values[key]=int(value)
   result[filename]=values
  except (OSError,ValueError): pass
 return result
def qa_wrap_control(name,original):
 def measured(self,*args,**kwargs):
  started=monotonic(); previous=getattr(qa_context,'operation',None); qa_context.operation=name
  details={'operation':name}
  if name=='set_progress' and args and type(args[0]) is int: details['encoded_ms']=args[0]
  if name=='set_stage' and args and args[0] in ('preparing','encoding','validating','publishing'):
   details['stage']=args[0]
  qa_emit({'qa_event':'control_probe_begin',**details})
  try:
   result=original(self,*args,**kwargs)
  except Exception as error:
   qa_emit({'qa_event':'control_probe_error',**details,'exception_type':type(error).__name__,
     'duration_seconds':round(monotonic()-started,6),'resources':qa_resources()})
   raise
  else:
   qa_emit({'qa_event':'control_probe_end',**details,
     'duration_seconds':round(monotonic()-started,6),
     'result':result if type(result) is bool else None})
   return result
  finally: qa_context.operation=previous
 return measured
for qa_name in ('control','heartbeat','set_progress','set_stage'):
 setattr(RenderLeaseController,qa_name,qa_wrap_control(qa_name,getattr(RenderLeaseController,qa_name)))
qa_real_heartbeat=RenderJobRepository.heartbeat
def qa_heartbeat_snapshot(self,claim,lease_seconds):
 updated=qa_real_heartbeat(self,claim,lease_seconds)
 if updated:
  row=self.session.execute(select(func.clock_timestamp(),RenderJob.heartbeat_at,
   RenderJob.lease_expires_at,RenderJob.progress_encoded_ms).where(RenderJob.id==claim.id)).one()
  qa_emit({'qa_event':'database_heartbeat_snapshot','database_epoch_seconds':row[0].timestamp(),
   'heartbeat_epoch_seconds':row[1].timestamp(),'lease_epoch_seconds':row[2].timestamp(),
   'encoded_ms':row[3]})
 return updated
RenderJobRepository.heartbeat=qa_heartbeat_snapshot
qa_real_pulse=ProcessRunner.pulse
def qa_pulse(self):
 started=monotonic(); gap=started-getattr(self,'_qa_last_pulse',started)
 self._qa_last_pulse=started
 if gap>.5:
  qa_emit({'qa_event':'supervisor_pulse_gap','gap_seconds':round(gap,6),
   'heartbeat_due_in_seconds':round(self.next_heartbeat-started,6),'resources':qa_resources()})
 try: return qa_real_pulse(self)
 finally:
  elapsed=monotonic()-started
  if elapsed>.1:
   qa_emit({'qa_event':'supervisor_slow_pulse','duration_seconds':round(elapsed,6),
    'resources':qa_resources()})
ProcessRunner.pulse=qa_pulse; ProcessRunner.check=qa_pulse
def qa_sql_start(connection,cursor,statement,parameters,context,executemany):
 started=monotonic()
 verb=statement.lstrip().split(None,1)[0].upper() if statement.strip() else 'OTHER'
 if verb not in ('SELECT','UPDATE','INSERT','DELETE','COMMIT','ROLLBACK'): verb='OTHER'
 connection.info['qa_sql_start']=(started,verb)
 qa_emit({'qa_event':'sql_probe_begin','operation':getattr(qa_context,'operation',None),'verb':verb})
def qa_sql_end(connection,cursor,statement,parameters,context,executemany):
 started,verb=connection.info.pop('qa_sql_start',(monotonic(),'OTHER'))
 qa_emit({'qa_event':'sql_probe_end','operation':getattr(qa_context,'operation',None),
  'verb':verb,'duration_seconds':round(monotonic()-started,6)})
def qa_sql_error(context):
 original=context.original_exception; state=getattr(original,'sqlstate',None)
 qa_emit({'qa_event':'sql_probe_error','operation':getattr(qa_context,'operation',None),
  'exception_type':type(original).__name__,
  'sqlstate':state if isinstance(state,str) and re.fullmatch('[A-Z0-9]{5}',state) else None})
for sig in (signal.SIGTERM,signal.SIGINT):
 signal.signal(sig,lambda *_:stopped.set())
def observe():
 seen=set()
 while not watching.wait(.02):
  for entry in Path('/proc').iterdir():
   if not entry.name.isdigit() or entry.name in seen: continue
   try: name=(entry/'comm').read_text().strip()
   except OSError: continue
   if name=='ffmpeg':
    seen.add(entry.name)
    qa_emit({'qa_event':'real_ffmpeg_observed','pid':int(entry.name)})
observer=Thread(target=observe,daemon=True); observer.start(); database=Database(settings)
event.listen(database.engine,'before_cursor_execute',qa_sql_start)
event.listen(database.engine,'after_cursor_execute',qa_sql_end)
event.listen(database.engine,'handle_error',qa_sql_error)
try:
 sleep(.5) # Let the host record container ownership before a no-claim exit.
 class ScopedProcessor(RenderProcessor):
  def publish(self,*args,**kwargs):
   # Crash/lease attempts use disposable storage only. If the host misses
   # the encoding boundary, fail honestly rather than commit a canonical
   # key whose bytes live in that disposable tree.
   if str(settings.storage_root)=='/qa-owned':
    raise PreviewProcessError('render_qa_unexpected_publication')
   return super().publish(*args,**kwargs)
 scheduler=WorkerScheduler(settings,database.sessions,stopped,
   PreviewProcessor(settings,database.sessions,stopped),
   ScopedProcessor(settings,database.sessions,stopped),project_id=UUID(sys.argv[1]))
 gate=os.environ.get('EDIVO_QA_CLAIM_GATE')
 if gate:
  qa_emit({'qa_event':'ready_to_claim'})
  for _ in range(1000):
   if Path(gate).is_file() or stopped.is_set(): break
   sleep(.02)
  else: raise RuntimeError('qa_claim_gate_timeout')
 processed=scheduler.run_one()
 qa_emit({'qa_event':'run_one_finished','processed':processed,
         'stop_observed':stopped.is_set(),'resources':qa_resources()})
finally:
 watching.set(); observer.join(timeout=2); database.close()
"""

DATABASE_SOURCE = r"""
import hashlib,json,os,stat,sys
from uuid import UUID
from sqlalchemy import func,select,text
from app.core.config import Settings
from app.db.session import Database
from app.models.media_asset import MediaAsset
from app.models.preview_job import PreviewJob
from app.models.render_job import RenderJob
from app.storage.local import LocalStorage
settings=Settings(); database=Database(settings); operation=sys.argv[1]
try:
 with database.sessions() as session:
  if operation=='active':
   result={model.__tablename__:session.scalar(select(func.count()).select_from(model)
    .where(model.status.in_(['queued','running']))) for model in (PreviewJob,RenderJob)}
  elif operation=='job':
   job=session.scalar(select(RenderJob).where(RenderJob.id==UUID(sys.argv[3]),
                                           RenderJob.project_id==UUID(sys.argv[2])))
   assert job is not None
   result={name:(str(getattr(job,name)) if getattr(job,name) is not None else None)
      for name in ('id','claim_token','status','stage','error_code','lease_expires_at')}
   result['attempts']=job.attempts
   result['published']=job.artifact_storage_key is not None
  elif operation=='expire':
   result=session.execute(text('UPDATE render_jobs SET lease_expires_at=clock_timestamp() '
    "- interval '1 second' WHERE id=:id AND project_id=:project AND claim_token=:token "
    "AND status='running'"),{'id':UUID(sys.argv[3]),'project':UUID(sys.argv[2]),
                            'token':UUID(sys.argv[4])}).rowcount
   session.commit()
   assert result==1
  elif operation=='snapshot':
   assets=list(session.scalars(select(MediaAsset).where(MediaAsset.project_id==UUID(sys.argv[2]))))
   assert len(assets)==1
   result={}
   for asset in assets:
    opened=LocalStorage(settings).open_original(asset.storage_key,asset.project_id,asset.id,asset.byte_size)
    with opened.file as source:
     checksum=hashlib.sha256()
     while chunk:=source.read(1048576): checksum.update(chunk)
    assert checksum.hexdigest()==asset.checksum_sha256
    assert stat.S_IMODE(opened.stat_result.st_mode)==0o400
    result[str(asset.id)]={'sha256':checksum.hexdigest(),'bytes':opened.stat_result.st_size,
      'mode':'0400','mtime_ns':opened.stat_result.st_mtime_ns}
  else: raise RuntimeError('unknown_qa_operation')
 print(json.dumps(result))
finally: database.close()
"""

CLONE_SOURCE = r"""
import hashlib,json,os,stat,sys
from pathlib import Path
from time import sleep
from uuid import UUID
from sqlalchemy import select
from app.core.config import Settings
from app.db.session import Database
from app.models.media_asset import MediaAsset
from app.storage.local import LocalStorage
settings=Settings(); database=Database(settings); target=Path('/qa-owned'); os.umask(0o077)
try:
 assert target.is_dir() and not target.is_symlink()
 # The host releases this gate only after recording our exact ID and label.
 for _ in range(1000):
  if (target/'.clone-go').is_file(): break
  sleep(.02)
 else: raise RuntimeError('qa_clone_ownership_gate_timeout')
 with database.sessions() as session:
  assets=list(session.scalars(select(MediaAsset).where(MediaAsset.project_id==UUID(sys.argv[1]))))
  assert len(assets)==1
  asset=assets[0]
  expected=f'originals/{asset.project_id}/{asset.id}/source.mp4'
  assert asset.storage_key==expected
  opened=LocalStorage(settings).open_original(expected,asset.project_id,asset.id,asset.byte_size)
  destination=target/expected
  destination.parent.mkdir(mode=0o700,parents=True)
  checksum=hashlib.sha256(); count=0
  with opened.file as source:
   descriptor=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(descriptor,'wb') as output:
    while chunk:=source.read(1048576):
     output.write(chunk); checksum.update(chunk); count+=len(chunk)
    output.flush(); os.fchmod(output.fileno(),0o400); os.fsync(output.fileno())
  assert count==asset.byte_size and checksum.hexdigest()==asset.checksum_sha256
  (target/'temp').mkdir(mode=0o700)
  print(json.dumps({'asset_id':str(asset.id),'sha256':checksum.hexdigest(),'bytes':count,'mode':'0400'}))
finally: database.close()
"""

FIXTURE_SOURCE = r"""
import base64,json,subprocess,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='edivo-r05-synthetic-') as temporary:
 target=Path(temporary)/'control.mp4'
 argv=['ffmpeg','-hide_banner','-v','error','-nostdin','-threads','1',
       '-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=14',
       '-c:v','libx264','-threads','1','-preset','ultrafast','-crf','34',
       '-pix_fmt','yuv420p','-an','-movflags','+faststart','-n',str(target)]
 subprocess.run(argv,check=True,capture_output=True,timeout=30)
 print(json.dumps({'data':base64.b64encode(target.read_bytes()).decode(),
                   'duration_seconds':14,'synthetic_only':True}))
"""

VALIDATE_SOURCE = r"""
import hashlib,json,sys
from threading import Event
from uuid import UUID
from app.core.config import Settings
from app.db.session import Database
from app.services.renders import RenderService
from app.worker.supervisor import ProcessPolicy,ProcessRunner
settings=Settings(); database=Database(settings)
try:
 with database.sessions() as session:
  opened,expected,filename=RenderService(session,settings).open_file(UUID(sys.argv[1]),UUID(sys.argv[2]))
  session.rollback()
 with opened.file as source:
  checksum=hashlib.sha256()
  while chunk:=source.read(1048576): checksum.update(chunk)
  assert checksum.hexdigest()==expected
  runner=ProcessRunner(settings,Event(),policy=ProcessPolicy.for_render(settings))
  descriptor=source.fileno(); path=f'/proc/self/fd/{descriptor}'
  probe=runner.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',path],
                   pass_fds=(descriptor,),timeout=30)
  assert probe.returncode==0
  metadata=json.loads(probe.stdout)
  source.seek(0)
  decoded=runner.run(['ffmpeg','-hide_banner','-v','error','-xerror','-nostdin','-threads','1',
    '-i',path,'-map','0:v:0','-map','0:a?','-threads','1','-f','null','-'],
    pass_fds=(descriptor,),timeout=90)
  assert decoded.returncode==0
 print(json.dumps({'sha256':expected,'bytes':opened.stat_result.st_size,
  'mode':oct(opened.stat_result.st_mode&0o777),'full_decode_passed':True,
  'duration_seconds':float(metadata['format']['duration']),
  'streams':[{k:stream.get(k) for k in ('codec_type','codec_name','width','height',
    'pix_fmt','r_frame_rate','sample_aspect_ratio')} for stream in metadata['streams']]}))
finally: database.close()
"""


class CheckFailed(RuntimeError):
    pass


class Harness:
    def __init__(self, output: Path, port: int):
        self.output, self.port = output, port
        self.run_id = uuid4().hex
        self.sequence = 0
        self.command_lock = Lock()
        self.containers = []
        self.owned_trees = []
        self.report = {
            "status": "failed",
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "checks": [],
            "jobs": [],
            "containers": [],
            "application_worker_signalled": False,
            "other_projects_mutated": False,
            "limitations": [
                "POSIX container QA, not a multi-host or distributed-queue test."
            ],
        }

    def check(self, name, condition, **details):
        self.report["checks"].append(
            {"check": name, "passed": bool(condition), **details}
        )
        if not condition:
            raise CheckFailed(name)

    def command(self, args, *, source=None, timeout=120, allow_failure=False):
        with self.command_lock:
            self.sequence += 1
            sequence = self.sequence
        stem = self.output.parent / f"command-{sequence:03d}"
        timed_out = False
        try:
            result = subprocess.run(
                args,
                cwd=ROOT,
                input=source,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            timed_out = True
            stdout, stderr = error.stdout or "", error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            result = subprocess.CompletedProcess(args, 124, stdout, stderr)
        logged_stdout = result.stdout
        sanitization = []
        if DATABASE_SOURCE in args and args[args.index(DATABASE_SOURCE) + 1] == "job":
            try:
                document = json.loads(logged_stdout)
            except ValueError:
                document = None
            if isinstance(document, dict) and isinstance(
                document.get("claim_token"), str
            ):
                digest = hashlib.sha256(document["claim_token"].encode()).hexdigest()
                document["claim_token"] = f"<owned-token-sha256:{digest}>"
                logged_stdout = json.dumps(document) + "\n"
                sanitization.append(
                    {"field": "claim_token", "replacement": "sha256_digest"}
                )
        for suffix, content in (("stdout", logged_stdout), ("stderr", result.stderr)):
            with Path(f"{stem}.{suffix}.log").open("x", encoding="utf-8") as stream:
                stream.write(content)
        # Never retain interpolated environment or DSNs. Embedded helper code is
        # identified by hash; its reviewed source lives in this script.
        safe_args = list(args)
        if "-c" in safe_args:
            index = safe_args.index("-c") + 1
            safe_args[index] = (
                "<qa-source-sha256:"
                + hashlib.sha256(safe_args[index].encode()).hexdigest()
                + ">"
            )
        if "expire" in safe_args:
            safe_args[-1] = "<owned-attempt-token>"
        with (self.output.parent / "commands.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(
                json.dumps(
                    {
                        "argv": safe_args,
                        "exit_code": result.returncode,
                        "log_sanitization": sanitization,
                    }
                )
                + "\n"
            )
        if timed_out or (result.returncode and not allow_failure):
            raise CheckFailed(f"qa_command_failed_{sequence:03d}")
        return result

    def python(self, source, *arguments):
        result = self.command(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                source,
                *arguments,
            ]
        )
        return json.loads(result.stdout)

    def database(self, operation, *arguments):
        return self.python(DATABASE_SOURCE, operation, *arguments)

    def request(self, method, path, payload=None, *, binary=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        body = json.dumps(payload).encode() if payload is not None else binary
        headers = headers or (
            {"Content-Type": "application/json"} if payload is not None else {}
        )
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def inspect(self, identifier):
        result = self.command(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .Id}} {{json .Name}} {{json .State.Running}} {{json .Config.Labels}}",
                identifier,
            ],
            allow_failure=True,
        )
        if result.returncode:
            # Inspect only a specifically owned ID/name; absence is expected
            # after --rm. Other daemon errors must not authorize cleanup.
            diagnostic = result.stderr.casefold()
            if "no such object" in diagnostic or "no such container" in diagnostic:
                return None
            raise CheckFailed("container_inspection_unavailable")
        decoder = json.JSONDecoder()
        rest, values = result.stdout.strip(), []
        for _ in range(4):
            value, consumed = decoder.raw_decode(rest)
            values.append(value)
            rest = rest[consumed:].lstrip()
        return {
            "id": values[0],
            "name": values[1].lstrip("/"),
            "running": values[2],
            "labels": values[3],
        }

    def assert_owned(self, record, *, allow_absent=False):
        state = self.inspect(record["id"] or record["name"])
        if state is None:
            if allow_absent:
                return None
            raise CheckFailed("qa_container_missing_before_signal")
        self.check(
            "container_ownership_confirmed",
            state["name"] == record["name"]
            and (not record["id"] or state["id"] == record["id"])
            and state["labels"].get("com.docker.compose.project") == "edivo"
            and state["labels"].get("com.docker.compose.service")
            == record.get("service", "worker")
            and state["labels"].get("edivo.qa.run") == self.run_id
            and str(state["labels"].get("com.docker.compose.oneoff", "")).lower()
            == "true",
            container_id=state["id"],
        )
        record["id"] = state["id"]
        return state

    def start_worker(self, case, project_id, *, storage=None, timeout=120, gate=None):
        name = f"edivo-r05-control-{self.run_id[:12]}-{case}-{uuid4().hex[:8]}"
        record = {"name": name, "id": None, "case": case}
        stem = self.output.parent / name
        args = [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--name",
            name,
            "--label",
            f"edivo.qa.run={self.run_id}",
        ]
        for key, value in {
            "EDIVO_RENDER_LEASE_SECONDS": str(QA_LEASE_SECONDS),
            "EDIVO_RENDER_HEARTBEAT_SECONDS": "0.5",
            "EDIVO_RENDER_CANCEL_POLL_SECONDS": "0.1",
            "EDIVO_RENDER_TERMINATE_GRACE_SECONDS": "0.2",
            "EDIVO_RENDER_TIMEOUT_SECONDS": str(timeout),
        }.items():
            args += ["-e", f"{key}={value}"]
        if storage is not None:
            args += ["-v", f"{storage}:/qa-owned", "-e", "EDIVO_STORAGE_ROOT=/qa-owned"]
        if gate is not None:
            args += [
                "-v",
                f"{gate.parent}:/qa-control:ro",
                "-e",
                "EDIVO_QA_CLAIM_GATE=/qa-control/go",
            ]
        args += ["worker", "python", "-u", "-c", WORKER_SOURCE, project_id]
        with (
            Path(f"{stem}.stdout.log").open("x", encoding="utf-8") as stdout,
            Path(f"{stem}.stderr.log").open("x", encoding="utf-8") as stderr,
        ):
            record.update(
                process=subprocess.Popen(args, cwd=ROOT, stdout=stdout, stderr=stderr),
                log_stem=stem,
            )
        self.containers.append(record)
        deadline = monotonic() + 15
        while monotonic() < deadline:
            state = self.assert_owned(record, allow_absent=True)
            if state is not None:
                self.report["containers"].append(
                    {k: record[k] for k in ("name", "id", "case")}
                )
                return record
            if record["process"].poll() is not None:
                break
            sleep(0.05)
        raise CheckFailed("dedicated_worker_did_not_start")

    def signal(self, record, signal):
        self.assert_owned(record)
        self.command(["docker", "kill", "--signal", signal, record["id"]])

    def wait_container(self, record, timeout=150, expected=(0,)):
        code = record["process"].wait(timeout=timeout)
        self.check(
            "dedicated_worker_exit",
            code in expected,
            case=record["case"],
            exit_code=code,
        )
        self.check(
            "dedicated_worker_removed",
            self.assert_owned(record, allow_absent=True) is None,
            case=record["case"],
        )
        return Path(f"{record['log_stem']}.stdout.log").read_text()

    def create_job(self, base, revision):
        status, job = self.request(
            "POST",
            base + "/renders",
            {
                "edit_spec_revision_id": revision["id"],
                "profile": "high",
                "idempotency_key": str(uuid4()),
            },
        )
        self.check(
            "render_queued_from_saved_revision",
            status == 202 and job["status"] == "queued",
        )
        self.report["jobs"].append({"id": job["id"], "status_url": job["status_url"]})
        return job

    def wait_encoding(self, record, project_id, job):
        deadline = monotonic() + 30
        while monotonic() < deadline:
            status, current = self.request("GET", job["status_url"])
            log = Path(f"{record['log_stem']}.stdout.log").read_text()
            if (
                status == 200
                and current["status"] == "running"
                and current["stage"] == "encoding"
                and '"qa_event": "real_ffmpeg_observed"' in log
            ):
                state = self.database("job", project_id, job["id"])
                self.check(
                    "real_ffmpeg_in_encoding_attempt",
                    state["status"] == "running" and not state["published"],
                )
                return state
            if status != 200 or current["status"] in {
                "completed",
                "failed",
                "canceled",
            }:
                raise CheckFailed("job_finished_before_control_boundary")
            sleep(0.05)
        raise CheckFailed("real_ffmpeg_not_observed")

    def completed(self, project_id, job, attempts):
        status, current = self.request("GET", job["status_url"])
        self.check(
            "completed_only_after_valid_artifact",
            status == 200
            and current["status"] == "completed"
            and current["artifact_available"]
            and current["attempts"] == attempts
            and current["progress_percent"] == 100,
        )
        artifact = self.python(VALIDATE_SOURCE, project_id, job["id"])
        self.check(
            "completed_real_mp4_sha_and_decode",
            artifact["full_decode_passed"]
            and artifact["sha256"] == current["checksum_sha256"]
            and artifact["bytes"] == current["byte_size"]
            and artifact["duration_seconds"] > 8,
        )
        self.report["jobs"][-1].update(result=current, artifact=artifact)

    def clone(self, project_id):
        tree = self.owned_tree()
        name = f"edivo-r05-control-{self.run_id[:12]}-clone-{uuid4().hex[:8]}"
        stem = self.output.parent / name
        record = {
            "name": name,
            "id": None,
            "case": "clone",
            "service": "api",
            "log_stem": stem,
        }
        args = [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--name",
            name,
            "--label",
            f"edivo.qa.run={self.run_id}",
            "-v",
            f"{tree}:/qa-owned",
            "api",
            "python",
            "-c",
            CLONE_SOURCE,
            project_id,
        ]
        with (
            Path(f"{stem}.stdout.log").open("x", encoding="utf-8") as stdout,
            Path(f"{stem}.stderr.log").open("x", encoding="utf-8") as stderr,
        ):
            record["process"] = subprocess.Popen(
                args, cwd=ROOT, stdout=stdout, stderr=stderr
            )
        self.containers.append(record)
        deadline = monotonic() + 15
        while monotonic() < deadline:
            if self.assert_owned(record, allow_absent=True) is not None:
                self.report["containers"].append(
                    {k: record[k] for k in ("name", "id", "case", "service")}
                )
                break
            if record["process"].poll() is not None:
                raise CheckFailed("clone_helper_exited_before_ownership_proof")
            sleep(0.05)
        else:
            raise CheckFailed("clone_helper_ownership_timeout")
        with (tree / ".clone-go").open("x") as marker:
            marker.write("only-this-owned-container-may-clone\n")
        copied = self.wait_container(record, timeout=120)
        self.report.setdefault("isolated_clones", []).append(json.loads(copied))
        return tree

    def owned_tree(self):
        tree = Path(
            tempfile.mkdtemp(prefix=f"edivo-r05-control-owned-{self.run_id[:8]}-")
        )
        self.owned_trees.append((tree, tree.stat().st_dev, tree.stat().st_ino))
        return tree

    def wait_claim_gate(self, worker):
        deadline = monotonic() + 15
        while monotonic() < deadline:
            log = Path(f"{worker['log_stem']}.stdout.log").read_text()
            if '"qa_event": "ready_to_claim"' in log:
                return
            sleep(0.05)
        raise CheckFailed("concurrent_worker_not_ready_at_gate")

    def run(self):
        main_ids = self.command(
            ["docker", "compose", "ps", "--all", "--quiet", "worker"]
        ).stdout.split()
        self.check(
            "application_worker_already_stopped",
            all(not self.inspect(identifier)["running"] for identifier in main_ids),
        )
        self.check(
            "all_queues_initially_empty",
            all(value == 0 for value in self.database("active").values()),
        )
        fixture = self.python(FIXTURE_SOURCE)
        content = base64.b64decode(fixture["data"], validate=True)
        status, project = self.request(
            "POST",
            "/api/v1/projects",
            {"name": f"QA R05 worker control {self.run_id[:12]}"},
        )
        self.check("synthetic_project_created", status == 201)
        project_id = str(UUID(project["id"]))
        self.report["project_id"] = project_id
        base = f"/api/v1/projects/{project_id}"
        boundary = "edivo-r05-" + self.run_id
        multipart = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                'filename="control.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
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
            "real_mp4_imported",
            status == 201
            and media["checksum_sha256"] == hashlib.sha256(content).hexdigest(),
        )
        before = self.database("snapshot", project_id)
        self.report["original_before"] = before
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
        self.check("immutable_v1_revision_saved", status == 201)
        self.report["revision_id"] = revision["id"]

        job = self.create_job(base, revision)
        worker = self.start_worker("sigterm", project_id)
        first = self.wait_encoding(worker, project_id, job)
        self.signal(worker, "TERM")
        self.wait_container(worker, timeout=15)
        state = self.database("job", project_id, job["id"])
        self.check(
            "sigterm_requeues_after_child_stops",
            state["status"] == "queued"
            and state["attempts"] == 1
            and not state["published"],
        )
        retry = self.start_worker("sigterm-retry", project_id)
        second = self.wait_encoding(retry, project_id, job)
        self.check(
            "sigterm_retry_uses_new_fencing_token",
            second["attempts"] == 2 and second["claim_token"] != first["claim_token"],
        )
        self.wait_container(retry)
        self.completed(project_id, job, 2)

        for case in ("crash", "lease"):
            job = self.create_job(base, revision)
            isolated = self.clone(project_id)
            worker = self.start_worker(case, project_id, storage=isolated)
            first = self.wait_encoding(worker, project_id, job)
            if case == "crash":
                self.signal(worker, "KILL")
                self.wait_container(worker, timeout=15, expected=(137, -9))
            else:
                self.database("expire", project_id, job["id"], first["claim_token"])
                self.wait_container(worker, timeout=15)
                log = Path(f"{worker['log_stem']}.stderr.log").read_text()
                self.check("expired_owner_observes_fencing", "render_lease_lost" in log)
            state = self.database("job", project_id, job["id"])
            self.check(
                case + "_old_attempt_never_publishes",
                state["status"] == "running"
                and state["attempts"] == 1
                and not state["published"],
            )
            if case == "crash":
                sleep(QA_LEASE_SECONDS + 0.2)
            retry = self.start_worker(case + "-retry", project_id)
            second = self.wait_encoding(retry, project_id, job)
            self.check(
                case + "_retry_uses_new_fencing_token",
                second["attempts"] == 2
                and second["claim_token"] != first["claim_token"],
            )
            self.wait_container(retry)
            self.completed(project_id, job, 2)
            self.report["jobs"][-1]["first_claim_token_sha256"] = hashlib.sha256(
                first["claim_token"].encode()
            ).hexdigest()

        job = self.create_job(base, revision)
        timeout_worker = self.start_worker("timeout", project_id, timeout=1)
        log = self.wait_container(timeout_worker, timeout=15)
        state = self.database("job", project_id, job["id"])
        self.check(
            "real_ffmpeg_timeout_is_terminal",
            '"qa_event": "real_ffmpeg_observed"' in log
            and state["status"] == "failed"
            and state["error_code"] == "render_timeout"
            and state["attempts"] == 1
            and not state["published"],
        )
        self.report["jobs"][-1]["result"] = state

        job = self.create_job(base, revision)
        gate = self.owned_tree() / "go"
        with ThreadPoolExecutor(max_workers=2) as pool:
            workers = list(
                pool.map(
                    lambda name: self.start_worker(name, project_id, gate=gate),
                    ("race-a", "race-b"),
                )
            )
        for worker in workers:
            self.wait_claim_gate(worker)
        with gate.open("x") as marker:
            marker.write("release-only-this-invocation\n")
        logs = [self.wait_container(worker) for worker in workers]
        self.check(
            "two_workers_one_real_claim",
            sum('"processed": true' in log for log in logs) == 1
            and sum('"processed": false' in log for log in logs) == 1,
        )
        self.completed(project_id, job, 1)
        after = self.database("snapshot", project_id)
        self.check("synthetic_original_sha_mode_mtime_unchanged", before == after)
        self.report["original_after"] = after
        self.check(
            "all_queues_finally_empty",
            all(value == 0 for value in self.database("active").values()),
        )
        self.report["status"] = "passed"

    def cleanup(self):
        all_gone = True
        for record in self.containers:
            try:
                state = self.assert_owned(record, allow_absent=True)
                if state is not None:
                    self.signal(record, "TERM")
                    try:
                        record["process"].wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.signal(record, "KILL")
                        record["process"].wait(timeout=5)
                    all_gone &= self.assert_owned(record, allow_absent=True) is None
            except (CheckFailed, OSError, subprocess.SubprocessError):
                all_gone = False
        self.report["all_dedicated_containers_gone"] = all_gone
        if not all_gone:
            self.report["status"] = "failed"
        # Failure must not silently leave a queued QA render for the application
        # worker. Completed/failed/canceled jobs are never changed here. A crashed
        # running job may need lease recovery when the caller restores the worker.
        for job in self.report["jobs"]:
            try:
                status, current = self.request("GET", job["status_url"])
                if status == 200 and current["status"] in {"queued", "running"}:
                    cancel_status, canceled = self.request(
                        "POST", job["status_url"] + "/cancel"
                    )
                    self.report.setdefault("cleanup_cancellations", []).append(
                        {
                            "id": job["id"],
                            "http_status": cancel_status,
                            "status": canceled.get("status"),
                            "cancel_requested_at": canceled.get("cancel_requested_at"),
                        }
                    )
            except (OSError, ValueError, http.client.HTTPException):
                self.report.setdefault("cleanup_pending_job_ids", []).append(job["id"])
        for tree, device, inode in self.owned_trees:
            current = tree.lstat()
            safe = (
                all_gone
                and not tree.is_symlink()
                and (current.st_dev, current.st_ino) == (device, inode)
            )
            if safe:
                # This tree was created by this invocation and contains only its
                # verified synthetic clone and that attempt's disposable files.
                shutil.rmtree(tree)
            self.report.setdefault("isolated_tree_cleanup", []).append(
                {"owned_tree": tree.name, "removed": safe}
            )


def configured_port():
    value = os.environ.get("EDIVO_API_PORT")
    if value is None and (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text().splitlines():
            key, separator, candidate = line.partition("=")
            if separator and key.strip() == "EDIVO_API_PORT":
                value = candidate.strip().strip("\"'")
    port = int(value or "8000")
    if not 1 <= port <= 65535:
        raise ValueError("invalid_local_api_port")
    return port


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=EVIDENCE / "worker-control/report.json"
    )
    parser.add_argument("--api-port", type=int, default=None)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(EVIDENCE.resolve()) or output.parent.exists():
        parser.error("--output must be inside a new directory under docs/r05/evidence")
    output.parent.mkdir(mode=0o700, parents=True)
    harness = Harness(output, args.api_port or configured_port())
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
        harness.report["failure"] = (
            str(error) if isinstance(error, CheckFailed) else type(error).__name__
        )
    finally:
        try:
            harness.cleanup()
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            harness.report["cleanup_failure"] = type(error).__name__
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
            }
        )
    )
    return 0 if harness.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
