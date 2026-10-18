#!/usr/bin/env python3
"""Generate small synthetic R03 QA fixtures in the real API FFmpeg runtime.

Does not read user media, overwrite existing files, import assets or render an
editing plan. Run before browser-r03.py; outputs stay outside version control.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location(
    "foundation", ROOT / "scripts/acceptance-compose.py"
)
assert module_spec and module_spec.loader
foundation = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(foundation)

GENERATE = r"""
import base64,json,subprocess,tempfile
from pathlib import Path
result={}
with tempfile.TemporaryDirectory(prefix='edivo-r03-synthetic-') as directory:
    def run(args):
        return subprocess.run(args,check=True,capture_output=True,timeout=45).stdout
    cases=[('horizontal','640x360',True),('silent_vertical','360x640',False),
           ('square','360x360',False),('vfr','320x180',False),
           ('sar','320x240',False),('unsupported','320x180',False)]
    for name,size,audio in cases:
        target=Path(directory)/(name+'.mp4')
        args=['ffmpeg','-v','error','-nostdin','-f','lavfi','-i',
              'testsrc2=size='+size+':rate=30000/1001']
        if audio: args+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000']
        args+=['-t','4']
        if name=='vfr': args+=['-vf',r'select=not(mod(n\,3))+not(mod(n\,5))','-fps_mode','vfr']
        if name=='sar': args+=['-vf','setsar=2/1']
        args+=['-c:v','mpeg4' if name=='unsupported' else 'libx264',
               '-threads','1','-pix_fmt','yuv420p']
        if name!='unsupported': args+=['-preset','ultrafast','-crf','28']
        if audio: args+=['-c:a','aac']
        run(args+['-movflags','+faststart',str(target)])
    rotated=Path(directory)/'rotated.mp4'
    run(['ffmpeg','-v','error','-nostdin','-i',str(Path(directory)/'horizontal.mp4'),
         '-c','copy','-metadata:s:v:0','rotate=90',str(rotated)])
    for target in sorted(Path(directory).glob('*.mp4')):
        probe=run(['ffprobe','-v','error','-show_entries',
            'format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,sample_aspect_ratio:stream_side_data=rotation',
            '-of','json',str(target)])
        result[target.name]={'data':base64.b64encode(target.read_bytes()).decode(),
                             'probe':json.loads(probe)}
print(json.dumps(result))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/edivo-r03-browser/fixtures"))
    options = parser.parse_args()
    try:
        payload = json.loads(foundation.execute_python("api", GENERATE))
        options.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        manifest = {}
        for name, item in payload.items():
            if Path(name).name != name or not name.endswith(".mp4"):
                raise ValueError("invalid_fixture_name")
            path = options.output / name
            data = base64.b64decode(item["data"], validate=True)
            try:
                with path.open("xb") as stream:
                    stream.write(data)
                state = "generated"
            except FileExistsError:
                state = "existing_preserved"
                if (
                    path.is_symlink()
                    or path.stat().st_size != len(data)
                    or path.read_bytes() != data
                ):
                    raise ValueError("existing_fixture_differs_use_new_directory") from None
            manifest[name] = {
                "status": state,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "probe": item["probe"],
            }
        print(json.dumps({"status": "passed", "fixtures": manifest}, indent=2))
        return 0
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        foundation.AcceptanceError,
    ):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "message": "Geração indisponível ou destino diferente já existente; "
                    "arquivos preservados.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
