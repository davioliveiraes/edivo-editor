#!/usr/bin/env python3
"""Generate synthetic R04 media with the real API FFmpeg/FFprobe runtime.

Only the generator and optional JSON manifest belong in the repository. Media
defaults to /tmp/edivo-r04/fixtures; it never reads user media, uploads assets,
renders an EditSpec or replaces an existing file with different bytes.
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
import array
import base64
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path


def run(args, *, valid=True):
    completed = subprocess.run(args, capture_output=True, timeout=60, check=False)
    if valid and completed.returncode:
        raise RuntimeError('synthetic_fixture_command_failed')
    return completed


def encode(args):
    return run(['ffmpeg', '-v', 'error', '-nostdin', *args])


def audio_metrics(target):
    raw = encode(['-i', str(target), '-map', '0:a:0', '-ac', '1', '-ar', '48000',
                  '-f', 's16le', '-']).stdout
    samples = array.array('h')
    samples.frombytes(raw)
    rms = []
    for start in range(0, len(samples), 48000):
        window = samples[start:start + 48000]
        rms.append(round(math.sqrt(sum(x*x for x in window) / len(window)) / 32768, 7))
    frequency_window = samples[4800:min(43200, len(samples))]
    crossings = sum(a <= 0 < b for a, b in zip(frequency_window, frequency_window[1:]))
    return {
        'sample_rate': 48000,
        'decoded_samples': len(samples),
        'decoded_duration_ms': round(len(samples) / 48, 3),
        'rms_per_second': rms,
        'peak_abs': round(max(abs(x) for x in samples) / 32768, 7),
        'positive_zero_crossing_hz': round(crossings * 48000 / len(frequency_window), 2),
    }


with tempfile.TemporaryDirectory(prefix='edivo-r04-synthetic-') as directory:
    root = Path(directory)
    descriptions = {}
    for name, duration, frequency, envelope in [
        ('tone-short.mp3', '1.2', 440, '0.7'),
        ('music-short.mp3', '2', 660, '0.7'),
        ('music-long.mp3', '12', 880, '0.4+0.05*floor(t)'),
    ]:
        target = root / name
        encode(['-f', 'lavfi', '-i',
                f'sine=frequency={frequency}:sample_rate=48000:duration={duration}',
                '-af', f"volume='{envelope}':eval=frame", '-map_metadata', '-1',
                '-c:a', 'libmp3lame', '-b:a', '128k', '-threads', '1',
                '-id3v2_version', '3', str(target)])
        descriptions[name] = {
            'purpose': 'synthetic_music' if name.startswith('music') else 'short_upload',
            'expected_upload': 'accept',
            'nominal_duration_ms': round(float(duration) * 1000),
            'tone_hz': frequency,
            'volume_filter': envelope,
        }

    cases = [
        ('horizontal', '640x360', True),
        ('silent-vertical', '360x640', False),
        ('square', '360x360', False),
        ('vfr', '320x180', False),
        ('sar', '320x240', False),
        ('unsupported', '320x180', False),
    ]
    for name, size, audio in cases:
        target = root / (name + '.mp4')
        args = ['-f', 'lavfi', '-i', 'testsrc2=size=' + size + ':rate=30000/1001']
        if audio:
            args += ['-f', 'lavfi', '-i',
                     'sine=frequency=1000:sample_rate=48000:duration=12']
        args += ['-t', '12']
        if name == 'vfr':
            args += ['-vf', r'select=not(mod(n\,3))+not(mod(n\,5))', '-fps_mode', 'vfr']
        if name == 'sar':
            args += ['-vf', 'setsar=2/1']
        args += ['-map_metadata', '-1', '-c:v',
                 'mpeg4' if name == 'unsupported' else 'libx264',
                 '-threads', '1', '-pix_fmt', 'yuv420p']
        if name != 'unsupported':
            args += ['-preset', 'ultrafast', '-crf', '28']
        if audio:
            args += ['-af', 'volume=0.3', '-c:a', 'aac', '-b:a', '96k']
        encode([*args, '-movflags', '+faststart', str(target)])
        descriptions[target.name] = {
            'purpose': 'source_video_' + name,
            'expected_upload': 'accept',
            'nominal_duration_ms': 12000,
            'has_audio': audio,
        }
        if audio:
            descriptions[target.name].update({'tone_hz': 1000, 'volume_filter': '0.3'})

    encode(['-i', str(root / 'horizontal.mp4'), '-map_metadata', '-1', '-c', 'copy',
            '-metadata:s:v:0', 'rotate=90', '-movflags', '+faststart',
            str(root / 'rotated.mp4')])
    descriptions['rotated.mp4'] = {
        'purpose': 'source_video_rotation_matrix',
        'expected_upload': 'accept',
        'nominal_duration_ms': 12000,
        'has_audio': True,
        'tone_hz': 1000,
        'volume_filter': '0.3',
    }

    cover = root / 'cover.png'
    encode(['-f', 'lavfi', '-i', 'color=c=royalblue:size=64x64:rate=1',
            '-frames:v', '1', '-c:v', 'png', '-threads', '1', str(cover)])
    encode(['-i', str(root / 'music-short.mp3'), '-i', str(cover),
            '-map', '0:a:0', '-map', '1:v:0', '-c', 'copy', '-id3v2_version', '3',
            '-metadata:s:v', 'title=Synthetic cover',
            '-metadata:s:v', 'comment=Cover (front)', str(root / 'invalid-cover.mp3')])
    descriptions['invalid-cover.mp3'] = {
        'purpose': 'reject_mp3_with_attached_video',
        'expected_upload': 'reject_video_stream',
        'nominal_duration_ms': 2000,
    }
    for name, data in [
        ('invalid-fake.mp3', b'Edivo synthetic negative fixture; no media stream.\n'),
        ('invalid-truncated.mp3', b'ID3\x04\x00\x00\x00\x00\x00\x40' + b'incomplete'),
    ]:
        (root / name).write_bytes(data)
        descriptions[name] = {
            'purpose': 'reject_nondecodable_synthetic_bytes',
            'expected_upload': 'reject_invalid_audio',
        }

    fixtures = {}
    for name, description in sorted(descriptions.items()):
        target = root / name
        invalid = name in ('invalid-fake.mp3', 'invalid-truncated.mp3')
        probe = run(['ffprobe', '-v', 'error', '-show_entries',
                     'format=duration,format_name:'
                     'stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,'
                     'r_frame_rate,sample_aspect_ratio,sample_rate,channels,duration:'
                     'stream_disposition=attached_pic:stream_side_data=rotation',
                     '-of', 'json', str(target)], valid=not invalid)
        decode = run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(target),
                      '-map', '0:v?', '-map', '0:a?', '-f', 'null', '-'], valid=not invalid)
        if invalid and (probe.returncode == 0 or decode.returncode == 0):
            raise RuntimeError('invalid_fixture_unexpectedly_decodes')
        inspection = json.loads(probe.stdout) if probe.returncode == 0 else None
        data = target.read_bytes()
        fixtures[name] = {
            **description,
            'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
            'probe': inspection,
            'validation': {
                'probe': 'expected_failure' if invalid else 'passed',
                'full_decode': 'expected_failure' if invalid else 'passed',
            },
            'data': base64.b64encode(data).decode('ascii'),
        }
        if name in ('tone-short.mp3', 'music-short.mp3', 'music-long.mp3', 'horizontal.mp4'):
            fixtures[name]['audio_measurements'] = audio_metrics(target)

    versions = {}
    for binary in ('ffmpeg', 'ffprobe'):
        versions[binary] = run([binary, '-version']).stdout.decode().splitlines()[0]
    print(json.dumps({'runtime': versions, 'fixtures': fixtures}))
"""


def preserve_or_create(path: Path, data: bytes) -> None:
    """Exclusively create a fixture or preserve a byte-for-byte identical file."""
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise ValueError("existing_fixture_differs_use_new_directory") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/edivo-r04/fixtures"))
    parser.add_argument("--manifest", type=Path, help="Optional JSON evidence; never replaces it.")
    options = parser.parse_args()
    try:
        payload = json.loads(foundation.execute_python("api", GENERATE))
        if options.output.is_symlink():
            raise ValueError("symlink_fixture_directory")
        options.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name, item in payload["fixtures"].items():
            if Path(name).name != name or not name.endswith((".mp4", ".mp3")):
                raise ValueError("invalid_fixture_name")
            data = base64.b64decode(item.pop("data"), validate=True)
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("invalid_fixture_payload")
            preserve_or_create(options.output / name, data)
        manifest = {
            "status": "passed",
            "scope": "synthetic fixtures only; not an application preview or upload acceptance",
            "generator": "scripts/generate-r04-fixtures.py",
            "runtime_service": "api",
            "provenance": "FFmpeg lavfi generators and explicitly invalid synthetic bytes",
            "reproducibility": (
                "deterministic generators; existing bytes must match; runtime-specific"
            ),
            "timing_case": {
                "source": "horizontal.mp4",
                "trim_start_ms": 2000,
                "trim_end_ms": 11000,
                "preview_start_ms": 5000,
                "preview_duration_ms": 3000,
                "expected_music_offset_ms": 3000,
                "fades_reference": "full trim; do not restart at middle preview boundaries",
            },
            **payload,
        }
        rendered = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if options.manifest:
            options.manifest.parent.mkdir(parents=True, exist_ok=True)
            preserve_or_create(options.manifest, rendered)
        print(rendered.decode("utf-8"), end="")
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
