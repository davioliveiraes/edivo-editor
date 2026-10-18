#!/usr/bin/env python3
"""Execute a regressão operacional R01 nas evoluções autorizadas R02/R03.

Requer Docker operacional e .env preparado. Durante os casos negativos, o banco
e o worker desta stack param brevemente e são restaurados mesmo em falhas.
Não executa down, remove volumes ou altera arquivos de mídia do usuário.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose"]


class AcceptanceError(Exception):
    """Falha sanitizada de uma verificação de aceite."""


def command(args: list[str], *, data: str | None = None, timeout: int = 180) -> str:
    result = subprocess.run(
        args,
        cwd=ROOT,
        input=data,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # A saída bruta pode conter configurações privadas de bibliotecas.
        raise AcceptanceError(f"Comando falhou (exit {result.returncode}): {' '.join(args[:7])}")
    return result.stdout.strip()


def compose(*args: str, data: str | None = None, timeout: int = 180) -> str:
    return command([*COMPOSE, *args], data=data, timeout=timeout)


def configured_port(name: str, default: int) -> int:
    value = str(default)
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, candidate = line.partition("=")
            if separator and key.strip() == name:
                value = candidate.strip().strip("\"'")
    value = os.environ.get(name, value)
    try:
        port = int(value)
    except ValueError:
        raise AcceptanceError(f"Porta {name} inválida; use um inteiro entre 1 e 65535.") from None
    require(1 <= port <= 65535, f"Porta {name} fora do intervalo permitido.")
    return port


def get(path: str, *, port: int | None = None) -> tuple[int, object]:
    port = port if port is not None else configured_port("EDIVO_API_PORT", 8000)
    try:
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except HTTPError as error:
        status, body = error.code, error.read().decode("utf-8")
    except (OSError, HTTPException) as error:
        raise AcceptanceError("Serviço HTTP local indisponível.") from error
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def wait_ready() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if get("/api/v1/system/ready")[0] == 200:
                return
        except AcceptanceError:
            pass
        time.sleep(1)
    raise AcceptanceError("API não ficou pronta em até 90 segundos.")


def execute_python(service: str, source: str) -> str:
    return compose("exec", "-T", service, "python", "-", data=source)


DATABASE_DIAGNOSTIC = """import json, os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ['EDIVO_DATABASE_URL'], connect_args={'connect_timeout': 3})
with engine.connect() as connection:
    revision = connection.scalar(text('SELECT version_num FROM alembic_version'))
    version = connection.scalar(text('SHOW server_version'))
    table_query = "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    tables = list(connection.scalars(text(table_query)))
print(json.dumps({'revision': revision, 'postgres': version, 'tables': tables}))
engine.dispose()
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-build", action="store_true", help="Reutilizar imagens já construídas."
    )
    args = parser.parse_args()
    if not (ROOT / ".env").is_file():
        print("Execute python3 scripts/setup-local.py antes do aceite.", file=sys.stderr)
        return 1
    restore_database = False
    restore_worker = False
    storage_marker: dict[str, str] | None = None
    checks: list[dict[str, object]] = []

    def passed(name: str, detail: object = None) -> None:
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    try:
        print("Validando a fundação local Edivo e sua regressão R01...", flush=True)
        api_port = configured_port("EDIVO_API_PORT", 8000)
        frontend_port = configured_port("EDIVO_FRONTEND_PORT", 5173)
        compose("version")
        compose("config", "--quiet")
        passed("compose_config")
        if not args.skip_build:
            print(
                "Construindo imagens Docker (a primeira execução pode demorar)...",
                flush=True,
            )
            compose("build", "api", "frontend", timeout=1200)
            passed("compose_build")
        compose("up", "-d", "--wait", "--wait-timeout", "180", timeout=240)
        wait_ready()
        passed("compose_startup")

        backend_versions = json.loads(
            execute_python(
                "api",
                "import importlib.metadata as m, json, platform\n"
                "names = ['fastapi', 'pydantic', 'pydantic-settings', 'SQLAlchemy', 'psycopg',\n"
                "         'alembic', 'uvicorn', 'pytest', 'httpx', 'ruff']\n"
                "packages = {name:m.version(name) for name in names}\n"
                "print(json.dumps({'python': platform.python_version(), 'packages': packages}))\n",
            )
        )
        backend_versions["uv"] = compose("exec", "-T", "api", "uv", "--version")
        passed("backend_runtime_versions", backend_versions)
        passed(
            "frontend_runtime_versions",
            {
                "node": compose("exec", "-T", "frontend", "node", "--version"),
                "npm": compose("exec", "-T", "frontend", "npm", "--version"),
            },
        )

        for path in ("health", "ready", "capabilities"):
            status, response = get(f"/api/v1/system/{path}")
            require(status == 200, f"Endpoint {path} não retornou HTTP 200.")
            if path == "capabilities":
                require(
                    isinstance(response, dict) and response.get("status") == "available",
                    "Capabilities identificou dependência FFmpeg indisponível.",
                )
            passed(f"api_{path}_200", response)
        require(get("/", port=frontend_port)[0] == 200, "Frontend não respondeu HTTP 200.")
        require(
            get("/api/v1/system/health", port=frontend_port)[0] == 200,
            "Proxy Vite/API indisponível.",
        )
        passed("frontend_vite_proxy_200")

        baseline = json.loads(execute_python("api", DATABASE_DIAGNOSTIC))
        approved_schemas = {
            "0001_r01_baseline": ["alembic_version"],
            "0002_r02_projects_media": ["alembic_version", "media_assets", "projects"],
            "0003_r03_edit_spec_revisions": [
                "alembic_version",
                "edit_spec_revisions",
                "media_assets",
                "projects",
            ],
        }
        require(baseline["revision"] in approved_schemas, "Revisão Alembic não autorizada.")
        require(baseline["postgres"].startswith("16."), "PostgreSQL precisa ser major 16.")
        require(
            baseline["tables"] == approved_schemas[baseline["revision"]],
            "Foram encontradas tabelas fora do escopo autorizado.",
        )
        compose("exec", "-T", "api", "alembic", "upgrade", "head")
        require(
            json.loads(execute_python("api", DATABASE_DIAGNOSTIC)) == baseline,
            "Executar a migração novamente alterou o baseline.",
        )
        passed("alembic_baseline_and_repeatability", baseline)
        compose("restart", "db")
        wait_ready()
        require(
            json.loads(execute_python("api", DATABASE_DIAGNOSTIC)) == baseline,
            "Baseline não persistiu após reiniciar o PostgreSQL.",
        )
        passed("postgres_volume_persistence")

        storage_marker = json.loads(
            execute_python(
                "api",
                "import json, os, secrets, tempfile\n"
                "from pathlib import Path\n"
                "root = Path(os.environ['EDIVO_STORAGE_ROOT']) / 'temp'\n"
                "token = secrets.token_hex(16)\n"
                "with tempfile.NamedTemporaryFile(\n"
                "    mode='w', prefix='.r01-acceptance-', dir=root, delete=False\n"
                ") as stream:\n"
                "    stream.write(token)\n"
                "    marker = {'name': Path(stream.name).name, 'token': token}\n"
                "print(json.dumps(marker))\n",
            )
        )

        def marker_source(*, cleanup: bool = False) -> str:
            return (
                "import os\nfrom pathlib import Path\n"
                f"marker = {storage_marker!r}\n"
                "assert Path(marker['name']).name == marker['name']\n"
                "assert marker['name'].startswith('.r01-acceptance-')\n"
                "path = Path(os.environ['EDIVO_STORAGE_ROOT']) / 'temp' / marker['name']\n"
                "assert path.read_text() == marker['token']\n"
                + ("path.unlink()\n" if cleanup else "print('marker_confirmed')\n")
            )

        execute_python("worker", marker_source())
        compose("restart", "api", "worker")
        wait_ready()
        for service in ("api", "worker"):
            require(
                execute_python(service, marker_source()) == "marker_confirmed",
                "Storage não persistiu ou não foi compartilhado entre API e worker.",
            )
        execute_python("api", marker_source(cleanup=True))
        storage_marker = None
        passed("shared_storage_persistence_after_restart")

        smoke = (ROOT / "scripts" / "ffmpeg-smoke.py").read_text(encoding="utf-8")
        for service in ("api", "worker"):
            diagnostic = json.loads(execute_python(service, smoke))
            passed(f"ffmpeg_synthetic_smoke_{service}", diagnostic)

        storage_sources: list[str] = []
        image_versions: dict[str, object] = {}
        for service in ("db", "migrate", "api", "worker", "frontend"):
            container = compose("ps", "--all", "--quiet", service)
            require(bool(container), f"Container {service} não foi criado.")
            ports = (
                json.loads(
                    command(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            "{{json .NetworkSettings.Ports}}",
                            container,
                        ]
                    )
                )
                or {}
            )
            published = {key: value for key, value in ports.items() if value}
            expected = {
                "api": ("8000/tcp", str(api_port)),
                "frontend": ("5173/tcp", str(frontend_port)),
            }
            if service in expected:
                internal, host = expected[service]
                require(
                    published == {internal: [{"HostIp": "127.0.0.1", "HostPort": host}]},
                    f"Portas inesperadas no serviço {service}.",
                )
            else:
                require(not published, f"Serviço {service} publicou portas no host.")
            mounts = json.loads(
                command(["docker", "inspect", "--format", "{{json .Mounts}}", container])
            )
            if service == "db":
                require(
                    any(
                        mount["Type"] == "volume"
                        and mount["Destination"] == "/var/lib/postgresql/data"
                        and mount["Name"].endswith("_postgres_data")
                        for mount in mounts
                    ),
                    "PostgreSQL não está usando o volume nomeado esperado.",
                )
            if service in ("api", "worker"):
                storage_mounts = [
                    mount
                    for mount in mounts
                    if mount["Destination"] == "/data/storage" and mount["Type"] == "bind"
                ]
                require(len(storage_mounts) == 1, "Storage precisa ser um bind mount.")
                storage_sources.append(storage_mounts[0]["Source"])
            image_versions[service] = json.loads(
                command(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        '{"tag":{{json .Config.Image}},"image_id":{{json .Image}}}',
                        container,
                    ]
                )
            )
        require(
            len(set(storage_sources)) == 1,
            "API e worker não compartilham o mesmo storage.",
        )
        passed("only_loopback_api_and_frontend_ports")
        passed("persistent_volume_shared_bind_mount_and_images", image_versions)

        restore_database = True
        compose("stop", "db")
        require(get("/api/v1/system/health")[0] == 200, "Liveness depende do banco.")
        status, body = get("/api/v1/system/ready")
        require(status == 503, "Readiness com banco offline não retornou HTTP 503.")
        safe_body = json.dumps(body).lower()
        require(
            not any(
                word in safe_body
                for word in (
                    "traceback",
                    "postgresql://",
                    "postgresql+psycopg://",
                    "/data/storage",
                )
            ),
            "Resposta de erro expõe detalhes internos.",
        )
        passed("database_offline_health_200_ready_503", body)
        compose("start", "db")
        wait_ready()
        restore_database = False
        passed("database_recovery_ready_200")

        invalid_storage = """import json
from fastapi.testclient import TestClient
from app.main import create_app
with TestClient(create_app()) as client:
    response = client.get('/api/v1/system/ready')
    assert response.status_code == 503
    print(json.dumps({'http_status': response.status_code, 'response': response.json()}))
"""
        # Processo isolado da mesma imagem, DB real e storage realmente inacessível.
        storage_result = compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "EDIVO_STORAGE_ROOT=/proc/edivo-r01-unavailable",
            "api",
            "python",
            "-",
            data=invalid_storage,
        )
        # A aplicação também escreve logs JSON; guardar somente o resultado do check.
        storage_lines = [
            json.loads(line) for line in storage_result.splitlines() if line.startswith("{")
        ]
        passed(
            "storage_inaccessible_ready_503",
            next(line for line in storage_lines if "http_status" in line),
        )

        worker_id = compose("ps", "--quiet", "worker")
        logs = compose("logs", "--no-color", "worker")
        require("worker_ready_idle" in logs, "Worker não registrou startup pronto e ocioso.")
        stats = json.loads(
            command(["docker", "stats", "--no-stream", "--format", "{{json .}}", worker_id])
        )
        cpu = float(stats["CPUPerc"].rstrip("%"))
        require(cpu < 5, "CPU do worker ocioso ficou acima de 5% na amostra.")
        passed("worker_idle", {"cpu_percent_sample": cpu})
        restore_worker = True
        compose("stop", "worker")
        exit_code = command(["docker", "inspect", "--format", "{{.State.ExitCode}}", worker_id])
        stopped_logs = compose("logs", "--no-color", "worker")
        require(
            exit_code == "0" and "worker_stopped" in stopped_logs and "SIGTERM" in stopped_logs,
            "Worker não encerrou com SIGTERM e exit 0.",
        )
        passed("worker_sigterm_exit_0")
        compose("start", "worker")
        restore_worker = False

        for target, argv in (
            ("pytest", ("exec", "-T", "api", "pytest")),
            ("ruff_check", ("exec", "-T", "api", "ruff", "check", ".")),
            ("ruff_format", ("exec", "-T", "api", "ruff", "format", "--check", ".")),
            ("vitest", ("exec", "-T", "frontend", "npm", "run", "test", "--", "--run")),
            ("eslint", ("exec", "-T", "frontend", "npm", "run", "lint")),
            ("prettier", ("exec", "-T", "frontend", "npm", "run", "format:check")),
            ("frontend_build", ("exec", "-T", "frontend", "npm", "run", "build")),
        ):
            output = compose(*argv, timeout=240)
            passed(target, output)

        compose("ps", "--all")
        print(
            json.dumps({"status": "passed", "checks": len(checks)}, ensure_ascii=False),
            flush=True,
        )
        return 0
    except (
        AcceptanceError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
        StopIteration,
    ) as error:
        message = (
            str(error)
            if isinstance(error, AcceptanceError)
            else f"Verificação interrompida ({type(error).__name__}); confira a etapa e o ambiente."
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "completed_checks": len(checks),
                    "message": message,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    finally:
        if storage_marker:
            try:
                execute_python("api", marker_source(cleanup=True))
            except (AcceptanceError, subprocess.TimeoutExpired, OSError):
                print(
                    "Marker temporário de aceite preservado no storage/temp; "
                    "nenhum arquivo existente foi alterado.",
                    file=sys.stderr,
                )
        for service, restore in (("db", restore_database), ("worker", restore_worker)):
            if restore:
                try:
                    compose("start", service)
                    print(
                        f"Serviço {service} restaurado após o caso negativo.",
                        flush=True,
                    )
                except (AcceptanceError, subprocess.TimeoutExpired, OSError):
                    print(
                        f"Restaure manualmente o serviço {service}: "
                        f"docker compose start {service}.",
                        file=sys.stderr,
                    )


if __name__ == "__main__":
    sys.exit(main())
