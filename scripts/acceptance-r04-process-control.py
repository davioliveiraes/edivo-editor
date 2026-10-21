#!/usr/bin/env python3
"""Prove timeout/SIGTERM supervision with real synthetic FFmpeg in the worker.

Only the isolated Python harness receives SIGTERM; the application worker PID1,
queue, storage and originals are untouched. No media is written or imported.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("foundation", ROOT / "scripts/acceptance-compose.py")
assert spec and spec.loader
foundation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(foundation)

SOURCE = r"""
import json,os,signal
from pathlib import Path
from threading import Event,Timer
from app.core.config import Settings
from app.worker import supervisor

original_popen=supervisor.subprocess.Popen
checks=[]
for mode in ('timeout','sigterm'):
    children=[]
    def tracked(*args,**kwargs):
        child=original_popen(*args,**kwargs)
        children.append(child)
        return child
    supervisor.subprocess.Popen=tracked
    stopped=Event()
    budget=0.6 if mode=='timeout' else 5
    configured=Settings().model_copy(update={'preview_ffmpeg_timeout_seconds':budget})
    observed=[]
    def sample():
        for child in children:
            try: observed.append(Path(f'/proc/{child.pid}/comm').read_text().strip())
            except OSError: pass
    observer=Timer(0.25,sample)
    observer.start()
    previous=signal.signal(signal.SIGTERM,lambda *_:stopped.set())
    stop_timer=Timer(0.4,lambda:os.kill(os.getpid(),signal.SIGTERM)) if mode=='sigterm' else None
    if stop_timer: stop_timer.start()
    code=None
    try:
        supervisor.ProcessRunner(configured,stopped).run([
            configured.ffmpeg_bin,'-v','error','-nostdin','-threads','1',
            '-filter_threads','1','-filter_complex_threads','1','-re',
            '-f','lavfi','-i','color=c=navy:size=64x64:rate=24',
            '-t','10','-threads','1','-f','null','-'])
    except supervisor.PreviewProcessError as error:
        code=error.code
    finally:
        observer.cancel(); observer.join()
        if stop_timer: stop_timer.cancel(); stop_timer.join()
        signal.signal(signal.SIGTERM,previous)
        supervisor.subprocess.Popen=original_popen
    expected='preview_timeout' if mode=='timeout' else 'preview_worker_interrupted'
    assert code==expected and len(children)==1 and 'ffmpeg' in observed
    assert children[0].poll() is not None
    try: os.killpg(children[0].pid,0)
    except ProcessLookupError: group_closed=True
    else: group_closed=False
    assert group_closed
    checks.append({'check':mode,'status':'passed','error_code':code,
                   'real_ffmpeg_observed':True,'child_waited':True,'process_group_closed':True})
print(json.dumps({'status':'passed','runtime':'worker','synthetic_only':True,
                  'application_worker_signalled':False,'database_or_storage_mutated':False,'checks':checks}))
"""


def main():
    result = json.loads(foundation.execute_python("worker", SOURCE))
    destination = ROOT / "docs/r04/evidence/ffmpeg-process-control.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
