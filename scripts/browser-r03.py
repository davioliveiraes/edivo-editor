"""Run real-browser R03 QA in an isolated helper container, without app dependencies.

Requires the official Playwright 1.58.2 image, an external Playwright npm install,
and synthetic fixtures under WORK/fixtures. Only the QA directory and read-only
QA dependencies are mounted. Existing services/volumes are never stopped here.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "mcr.microsoft.com/playwright:v1.58.2-noble"


def command(args: list[str]) -> str:
    return subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("player", "editor", "verify"), default="player")
    parser.add_argument("--work", type=Path, default=Path("/tmp/edivo-r03-browser"))
    parser.add_argument(
        "--qa-node-modules", type=Path, default=Path("/tmp/edivo-browser/node_modules")
    )
    options = parser.parse_args()
    work = options.work.resolve()
    dependencies = options.qa_node_modules.resolve()
    if not (dependencies / "playwright" / "index.mjs").is_file():
        parser.error("Instale Playwright 1.58.2 no diretório auxiliar de QA indicado.")
    if not (work / "fixtures" / "horizontal.mp4").is_file():
        parser.error("As amostras sintéticas FFmpeg precisam existir em WORK/fixtures.")
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    artifacts = work / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile(ROOT / "scripts" / "browser-r03.mjs", work / "browser-r03.mjs")

    frontend = command(["docker", "compose", "ps", "--quiet", "frontend"])
    if not frontend:
        parser.error("O frontend Edivo precisa estar em execução.")
    networks = json.loads(
        command(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                frontend,
            ]
        )
    )
    network, settings = next(iter(networks.items()))
    run_id = f"r03-{options.stage}-{int(time.time())}"
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            network,
            "--shm-size",
            "256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--volume",
            f"{work}:/work",
            "--volume",
            f"{dependencies}:/work/node_modules:ro",
            "--workdir",
            "/work",
            "--env",
            f"EDIVO_BROWSER_BASE_URL=http://{settings['IPAddress']}:5173",
            "--env",
            f"EDIVO_BROWSER_RUN_ID={run_id}",
            "--env",
            f"EDIVO_BROWSER_STAGE={options.stage}",
            "--env",
            "EDIVO_PLAYWRIGHT_MODULE=/work/node_modules/playwright/index.mjs",
            IMAGE,
            "node",
            "browser-r03.mjs",
        ],
        cwd=ROOT,
        check=False,
    )
    evidence = ROOT / "docs" / "r03" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    report = artifacts / f"{run_id}.json"
    if report.is_file():
        shutil.copyfile(report, evidence / report.name)
        if result.returncode == 0:
            shutil.copyfile(report, evidence / f"browser-{options.stage}.json")
    for screenshot in artifacts.glob(f"{run_id}-*.png"):
        shutil.copyfile(screenshot, evidence / screenshot.name)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
