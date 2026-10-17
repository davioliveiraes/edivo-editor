#!/usr/bin/env python3
"""Exercise R02 over real HTTP/PostgreSQL/FFprobe, retaining QA originals.

Creates synthetic fixtures and soft-deleted QA projects. Briefly restarts only
Edivo services and tests a DB outage, restoring DB in finally. No volume removal.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.util
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("foundation", ROOT / "scripts/acceptance-compose.py")
assert spec and spec.loader
foundation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(foundation)


def request(method, path, data=None, headers=None, *, chunked=False):
    connection = http.client.HTTPConnection(
        "127.0.0.1", foundation.configured_port("EDIVO_API_PORT", 8000), timeout=30
    )
    try:
        connection.request(method, path, data, headers or {}, encode_chunked=chunked)
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None
    finally:
        connection.close()


def json_request(method, path, payload=None):
    return request(
        method,
        path,
        json.dumps(payload) if payload is not None else None,
        {"Content-Type": "application/json"},
    )


def multipart(data, name="sample.mp4", *, extra=b""):
    boundary = "edivo-r02-qa-boundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
        + extra
        + f"--{boundary}--\r\n".encode()
    )
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def upload(project_id, data, name="sample.mp4", *, chunked=False, extra=b""):
    body, headers = multipart(data, name, extra=extra)
    payload = (
        (body[offset : offset + 1024] for offset in range(0, len(body), 1024)) if chunked else body
    )
    return request(
        "POST", f"/api/v1/projects/{project_id}/media", payload, headers, chunked=chunked
    )


def storage_snapshot(project_id):
    return json.loads(
        foundation.execute_python(
            "worker",
            f"""
import hashlib, json
from pathlib import Path
from uuid import UUID
from sqlalchemy import select
from app.core.config import Settings
from app.db.session import Database
from app.models.media_asset import MediaAsset
settings=Settings(); db=Database(settings)
with db.sessions() as session:
    assets=list(session.scalars(select(MediaAsset).where(MediaAsset.project_id==UUID({project_id!r}))))
    result=[]
    for asset in assets:
        path=settings.storage_root/asset.storage_key
        checksum=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024): checksum.update(chunk)
        result.append({{'id':str(asset.id),'checksum':checksum.hexdigest(),
            'size':path.stat().st_size,'permissions':oct(path.stat().st_mode&0o777),
            'key':asset.storage_key,'probe_metadata':asset.probe_metadata}})
print(json.dumps({{'assets':sorted(result,key=lambda a:a['id']),
    'temp_count':sum(1 for _ in (settings.storage_root/'temp').iterdir())}}))
db.close()
""",
        )
    )


FIXTURES = """
import base64,json,subprocess,tempfile
from pathlib import Path
result={}
with tempfile.TemporaryDirectory(prefix='edivo-r02-qa-') as directory:
    for name in ('audio','silent','audio_only'):
        target=Path(directory)/(name+'.mp4')
        args=['ffmpeg','-v','error','-nostdin']
        if name!='audio_only':
            args+=['-f','lavfi','-i','color=c=0x164C3F:s=320x180:r=30000/1001']
        if name!='silent':
            args+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000']
        args+=['-t','1']
        if name!='audio_only': args+=['-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
        if name!='silent': args+=['-c:a','aac']
        args+=['-brand','mp42','-movflags','+faststart',str(target)]
        subprocess.run(args,check=True,capture_output=True,timeout=20)
        result[name]=base64.b64encode(target.read_bytes()).decode()
print(json.dumps(result))
"""


def main():
    checks, projects = [], []
    database_stopped = False

    def passed(name, detail=None):
        checks.append({"check": name, "status": "passed", "detail": detail})
        print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

    def require(value, message):
        foundation.require(bool(value), message)

    def create(name):
        status, body = json_request("POST", "/api/v1/projects", {"name": name})
        require(status == 201, "Criação de projeto não retornou 201.")
        projects.append(body["id"])
        return body["id"]

    try:
        foundation.wait_ready()
        fixture_json = json.loads(foundation.execute_python("api", FIXTURES))
        fixtures = {key: base64.b64decode(value) for key, value in fixture_json.items()}
        passed(
            "ffmpeg_real_synthetic_fixtures", {key: len(value) for key, value in fixtures.items()}
        )
        token = uuid4().hex[:8]
        project = create(f" QA R02 {token} ")
        other = create(f"QA R02 isolated {token}")
        deleted = create(f"QA R02 deleted {token}")
        status, body = request("GET", f"/api/v1/projects/{project}")
        require(status == 200 and body["name"] == f"QA R02 {token}", "Trim/detalhe incorreto.")
        status, body = json_request(
            "PATCH",
            f"/api/v1/projects/{project}",
            {"name": f"QA R02 updated {token}", "purpose": "Synthetic QA", "target_preset": "9:16"},
        )
        require(status == 200 and body["target_preset"] == "9:16", "PATCH inválido.")
        for payload in ({"name": "  "}, {"target_preset": "invalid"}, {"purpose": "a" * 501}):
            require(
                json_request("POST", "/api/v1/projects", payload)[0] == 422,
                "Validação deveria retornar 422.",
            )
        require(
            json_request("PATCH", f"/api/v1/projects/{project}", {})[0] == 422,
            "PATCH vazio deveria retornar 422.",
        )
        passed("project_crud_and_validation_201_200_422")
        _, page = request("GET", "/api/v1/projects?limit=1&offset=0")
        _, next_page = request("GET", "/api/v1/projects?limit=1&offset=1")
        require(
            len(page["items"]) == 1
            and page["items"][0]["id"] == deleted
            and next_page["items"][0]["id"] == other,
            "Paginação/ordenação incorreta.",
        )
        require(request("GET", "/api/v1/projects?limit=101")[0] == 422, "Limite máximo incorreto.")
        passed("project_pagination_deterministic")

        status, audio = upload(project, fixtures["audio"], "../../private/sample.MP4")
        require(status == 201, f"MP4 com áudio falhou: HTTP {status}.")
        require(
            audio["has_audio"]
            and audio["audio_codec"] == "aac"
            and audio["original_filename"] == "sample.MP4",
            "Áudio/nome incorreto.",
        )
        status, silent = upload(project, fixtures["silent"], chunked=True)
        require(
            status == 201 and not silent["has_audio"] and silent["audio_codec"] is None,
            f"MP4 sem áudio/sem Content-Length falhou: HTTP {status}.",
        )
        require(
            (silent["width"], silent["height"], silent["fps_num"], silent["fps_den"])
            == (320, 180, 30000, 1001),
            "Metadados racionais incorretos.",
        )
        passed(
            "real_upload_audio_and_silent_chunked_without_content_length",
            {
                "audio": {key: audio[key] for key in ("duration_ms", "video_codec", "audio_codec")},
                "silent": {
                    key: silent[key] for key in ("duration_ms", "fps_num", "fps_den", "has_audio")
                },
            },
        )

        status, duplicate = upload(project, fixtures["audio"], "../../private/sample.MP4")
        require(status == 201 and duplicate["id"] != audio["id"], "Mesmo nome sobrescreveu asset.")
        for asset, data in (
            (audio, fixtures["audio"]),
            (silent, fixtures["silent"]),
            (duplicate, fixtures["audio"]),
        ):
            require(
                asset["checksum_sha256"] == hashlib.sha256(data).hexdigest()
                and asset["byte_size"] == len(data),
                "SHA-256/tamanho real incorreto.",
            )
            require(
                "storage_key" not in asset and "probe_metadata" not in asset,
                "Resposta expõe dados internos.",
            )
        baseline = storage_snapshot(project)
        require(
            len(baseline["assets"]) == 3 and baseline["temp_count"] == 0,
            "Staging residual ou assets ausentes.",
        )
        require(
            len({asset["key"] for asset in baseline["assets"]}) == 3,
            "Storage keys não são independentes.",
        )
        for asset in baseline["assets"]:
            require(asset["permissions"] == "0o400", "Original não está somente leitura.")
            public = next(item for item in (audio, silent, duplicate) if item["id"] == asset["id"])
            require(
                asset["checksum"] == public["checksum_sha256"]
                and asset["size"] == public["byte_size"],
                "Original no worker/DB não corresponde ao upload.",
            )
            require(
                "tags" not in json.dumps(asset["probe_metadata"]), "Probe bruto/tag persistida."
            )
        passed("postgres_storage_worker_sha256_size_0400_distinct_keys")

        _, media_page = request("GET", f"/api/v1/projects/{project}/media?limit=1&offset=1")
        require(
            media_page["total"] == 3 and len(media_page["items"]) == 1, "Paginação mídia incorreta."
        )
        require(
            request("GET", f"/api/v1/projects/{project}/media/{audio['id']}") == (200, audio),
            "Detalhe mídia incorreto.",
        )
        require(
            request("GET", f"/api/v1/projects/{other}/media/{audio['id']}")[0] == 404,
            "Mídia cruzada entre projetos.",
        )
        passed("media_pagination_detail_and_project_isolation")

        negative_cases = [
            (b"", "empty.mp4", 422),
            (b"not a video", "fake.mp4", 422),
            (fixtures["audio"][:-50], "truncated.mp4", 422),
            (fixtures["audio"], "bad.mp4evil", 415),
            (fixtures["audio"], "video.mov", 415),
            (fixtures["audio_only"], "audio.mp4", 422),
        ]
        for data, name, expected in negative_cases:
            status, body = upload(project, data, name)
            require(status == expected, f"Negativo {name}: esperado {expected}, obtido {status}.")
            require(set(body) == {"code", "message", "request_id"}, "Envelope divergente.")
        extra = (
            b'--edivo-r02-qa-boundary\r\nContent-Disposition: form-data; name="extra"\r\n\r\nx\r\n'
        )
        require(upload(project, fixtures["audio"], extra=extra)[0] == 400, "Campo extra aceito.")
        extra_file = (
            b"--edivo-r02-qa-boundary\r\nContent-Disposition: form-data; "
            b'name="file"; filename="second.mp4"\r\n\r\nx\r\n'
        )
        require(
            upload(project, fixtures["audio"], extra=extra_file)[0] == 400, "Dois arquivos aceitos."
        )
        require(
            request(
                "POST",
                f"/api/v1/projects/{project}/media",
                b"bad",
                {"Content-Type": "multipart/form-data"},
            )[0]
            == 400,
            "Multipart malformado aceito.",
        )
        passed("real_invalid_empty_fake_corrupt_extension_audio_only_multipart_400_415_422")

        limit = int(
            foundation.execute_python(
                "api",
                "from app.core.config import Settings\ns=Settings()\n"
                "print(s.upload_max_bytes+s.upload_multipart_overhead_bytes)",
            )
        )
        status, body = request(
            "POST",
            f"/api/v1/projects/{project}/media",
            None,
            {"Content-Length": str(limit + 1), "Content-Type": "multipart/form-data; boundary=x"},
        )
        require(
            status == 413 and body["code"] == "upload_too_large",
            "Ingress não rejeitou antes de receber body.",
        )
        passed(
            "oversized_content_length_rejected_before_receiving_body_413", {"max_body_bytes": limit}
        )

        # Close an incomplete request after the parser has received file bytes.
        partial, headers = multipart(fixtures["audio"])
        connection = http.client.HTTPConnection(
            "127.0.0.1", foundation.configured_port("EDIVO_API_PORT", 8000), timeout=10
        )
        connection.putrequest("POST", f"/api/v1/projects/{project}/media")
        for key, value in {**headers, "Content-Length": str(len(partial))}.items():
            connection.putheader(key, value)
        connection.endheaders(partial[: len(partial) // 2])
        connection.close()
        time.sleep(0.5)
        require(
            storage_snapshot(project) == baseline,
            "Negativos/desconexão deixaram artefatos ou alteraram originais.",
        )
        passed("interrupted_upload_and_all_negatives_leave_no_rows_or_temp_and_preserve_originals")

        require(
            request("DELETE", f"/api/v1/projects/{deleted}")[0] == 204,
            "Delete deveria retornar 204.",
        )
        for target in (deleted, str(uuid4())):
            require(
                upload(target, fixtures["audio"])[0] == 404,
                "Upload para projeto ausente/excluído aceito.",
            )
            require(
                request("GET", f"/api/v1/projects/{target}/media")[0] == 404,
                "Biblioteca ausente deveria retornar 404.",
            )
        passed("missing_and_soft_deleted_projects_upload_404")

        foundation.compose("restart", "db", "api", "worker")
        foundation.wait_ready()
        require(
            request("GET", f"/api/v1/projects/{project}/media/{audio['id']}") == (200, audio),
            "Registro não persistiu.",
        )
        require(storage_snapshot(project) == baseline, "Originais não persistiram após restart.")
        passed("project_media_and_sha256_persist_after_db_api_worker_restart")

        database_stopped = True
        foundation.compose("stop", "db")
        status, body = request("GET", "/api/v1/projects")
        require(
            status == 503 and body["code"] == "database_unavailable",
            "DB offline não retornou 503 sanitizado.",
        )
        require(
            not any(
                word in json.dumps(body) for word in ("Traceback", "postgresql", "/data/", "SELECT")
            ),
            "Erro vaza infraestrutura.",
        )
        require(request("GET", "/api/v1/system/health")[0] == 200, "Liveness regrediu.")
        foundation.compose("start", "db")
        foundation.wait_ready()
        database_stopped = False
        passed("database_offline_domain_503_sanitized_health_200_and_recovery")

        require(
            request("DELETE", f"/api/v1/projects/{project}")[0] == 204, "Exclusão lógica falhou."
        )
        require(
            request("DELETE", f"/api/v1/projects/{project}")[0] == 404,
            "Delete repetido deveria retornar 404.",
        )
        require(
            request("GET", f"/api/v1/projects/{project}/media/{audio['id']}")[0] == 404,
            "Projeto excluído ainda acessível.",
        )
        require(storage_snapshot(project) == baseline, "Soft delete apagou mídia/original.")
        passed("soft_delete_preserves_records_and_originals", {"retained_qa_originals": 3})
        print(
            json.dumps({"status": "passed", "checks": len(checks)}, ensure_ascii=False), flush=True
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "completed_checks": len(checks),
                    "message": str(error)
                    if isinstance(error, foundation.AcceptanceError)
                    else f"Verificação interrompida ({type(error).__name__}).",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    finally:
        if database_stopped:
            foundation.compose("start", "db")
            foundation.wait_ready()
        for project_id in projects:
            try:
                request("DELETE", f"/api/v1/projects/{project_id}")
            except (OSError, http.client.HTTPException):
                print("Projeto sintético de QA preservado para revisão.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
