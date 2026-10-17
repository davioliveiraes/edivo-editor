# Edivo — implementação e aceite R02

**Data:** 17/10/2026. **Status:** R02 implementada e validada nos limites desta
etapa. **167 testes backend e 37 frontend aprovados**, incluindo a regressão
R01. Aceites executados: **27 verificações da fundação, 13 HTTP R02, 19 de
limites/falhas com dependências reais e 16 de navegador**, além de três
verificações visuais. Resultados detalhados, comandos e versões estão em
[qa-results.json](qa-results.json).

## A. Implementação

Projetos com criação, consulta, listagem paginada, edição e exclusão lógica;
importação de um MP4 por requisição; biblioteca com metadados reais. Mantidos
health, readiness, capabilities, worker ocioso, tema e infraestrutura R01.
Não há player, timeline, edição, renderização, fila ou integração de IA.

```text
backend/
  app/
    api/{dependencies.py,v1/{system,projects,media}.py}
    core/{config,errors,logging,upload_limits}.py
    db/{base,session}.py
    models/{project,media_asset}.py
    repositories/{projects,media}.py
    schemas/{projects,media}.py
    services/{readiness,projects,media}.py
    storage/local.py
    integrations/ffmpeg/{capabilities,probe}.py
    worker/main.py
  alembic/versions/{0001_r01_baseline,0002_r02_projects_media}.py
  tests/                       # 167 casos
frontend/
  src/{app,components/ui,features/{system,projects,media},lib/api}
  tests/                       # 37 casos
scripts/
  setup-local.py, acceptance-compose.py, ffmpeg-smoke.py
  acceptance-r02.py, acceptance-upload-limits.py, audit-storage.py
docs/r02/{acceptance.md,qa-results.json,evidence/}
```

`AGENTS.md`, README, arquitetura e roadmap foram atualizados. Os documentos de
planejamento, especificações, baseline e evidências históricas R01 foram
conferidos por SHA-256 e preservados. Dependências e arquivos preexistentes
foram mantidos; apenas `python-multipart 0.0.32` foi acrescentado ao backend.
O ambiente virtual Python 3.12.3 existente foi preservado. Git foi inspecionado
somente para leitura, sem alterar branches, histórico ou publicar código.

## B. Banco, ambiente e storage

Antes da migração, PostgreSQL continha `0001_r01_baseline` e somente
`alembic_version`. O startup aplicou **`0002_r02_projects_media`**, acrescentando
somente `projects` e `media_assets`. `upgrade head` repetido não alterou o schema;
`alembic check` confirmou **No new upgrade operations detected**. A baseline
não foi reescrita. Não foi executado downgrade destrutivo.

- `Project`: UUID, nome obrigatório 1–120 após trim, finalidade até 500,
  preset opcional `9:16|1:1|4:5|16:9`, datas UTC e `deleted_at`.
- `MediaAsset`: UUID/FK RESTRICT, tipo `source_video`, nome saneado até 160,
  storage key única interna, SHA-256, bytes reais, MIME confirmado, duração ms,
  dimensões, FPS racional, codecs/áudio, JSONB filtrado e data UTC.
- Constraints de valores positivos, nome/preset, MIME/tipo/hash; índices de FK
  e paginação. Listagens: `created_at DESC, id DESC`, padrão 20, máximo 100.
  Não há unicidade de nome ou checksum, nem deduplicação automática.

| Runtime ou ferramenta | Versão real |
| --- | --- |
| Python host/venv / containers | 3.12.3 / 3.12.14 |
| uv | 0.13.1 |
| FastAPI / Starlette | 0.143.3 / 1.8.0 |
| Pydantic / pydantic-settings | 2.14.1 / 2.16.0 |
| SQLAlchemy / psycopg | 2.0.55 / 3.3.6 |
| Alembic / Uvicorn | 1.20.0 / 0.54.0 |
| python-multipart | 0.0.32 |
| PostgreSQL | 16.13 |
| Node / npm | 24.14.0 / 11.9.0 |
| React / TypeScript / Vite | 19.3.1 / 6.0.3 / 8.3.4 |
| Tailwind / Lucide | 4.3.3 / 1.59.0 |
| pytest / Ruff / httpx | 9.1.1 / 0.17.1 / 0.28.1 |
| Vitest / RTL / ESLint / Prettier | 5.0.5 / 16.3.3 / 10.13.0 / 3.9.10 |
| Docker / Compose | 29.8.0 / 5.5.1 |
| FFmpeg / FFprobe | 5.1.9-0+deb12u1 |
| Playwright / Chromium auxiliar | 1.58.2 / 145.0.7632.6 |

API, worker e migrate usam `edivo-backend:r02`; frontend usa
`edivo-frontend:r02`. PostgreSQL mantém `edivo_postgres_data`. API/worker montam
o mesmo `./storage`, sem exposição estática; UID/GID 1000, diretórios privados,
originais **0400**, backend read-only, capabilities removidas e sem privilégios
adicionais. `.env` permanece ignorado com **0600**, storage raiz **0700**.

Por escolha do usuário para coexistir com o Obra Control, o `.env` local usa:

| Serviço | Publicação real |
| --- | --- |
| Frontend | **http://127.0.0.1:5174** → porta interna 5173 |
| API | **http://127.0.0.1:8001** → porta interna 8000 |
| db / worker / migrate | Nenhuma porta no host |

`EDIVO_API_PORT`/`EDIVO_FRONTEND_PORT` tornam os padrões 8000/5173 configuráveis.
O proxy permanece `api:8000`; CORS local foi ajustado. Os containers do Obra
Control continuaram executando nas portas originais, sem serem interrompidos.

## C. API e contratos

| Método / caminho | Resposta normal |
| --- | --- |
| POST `/api/v1/projects` | 201, projeto |
| GET `/api/v1/projects?limit=20&offset=0` | 200, `{items,total,limit,offset}` |
| GET `/api/v1/projects/{project_id}` | 200, projeto ativo |
| PATCH `/api/v1/projects/{project_id}` | 200, projeto atualizado |
| DELETE `/api/v1/projects/{project_id}` | 204; repetição 404 |
| POST `/api/v1/projects/{project_id}/media` | 201, metadados de mídia |
| GET `/api/v1/projects/{project_id}/media` | 200, página de metadados |
| GET `/api/v1/projects/{project_id}/media/{media_id}` | 200, metadados |

Exemplo local, substituindo o UUID retornado:

```bash
curl -sS -X POST http://127.0.0.1:8001/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Meu projeto","purpose":"Vídeos locais","target_preset":"9:16"}'
curl -sS -X POST http://127.0.0.1:8001/api/v1/projects/UUID/media \
  -F 'file=@amostra.mp4'
```

Swagger/OpenAPI: **http://127.0.0.1:8001/docs**. `MediaResponse` contém id,
project_id, kind, original_filename, checksum_sha256, byte_size, mime_detected,
duration_ms, width/height, fps_num/fps_den, video_codec, has_audio, audio_codec e
created_at. Storage key e JSONB interno não são retornados. Não há download.

Erros mantêm o envelope R01 **`{code,message,request_id}`**, com
`X-Request-ID`: **400** multipart malformado/campos ou arquivos extras;
**404** projeto/asset ausente, excluído ou de outro projeto; **413** corpo ou
arquivo excedido; **415** extensão/container não suportado; **422** payload,
UUID/paginação, vídeo inválido, duração ou metadados inconsistentes; **503**
banco/storage/FFprobe indisponível ou timeout. Erros inesperados continuam
sanitizados (500). Entradas extras e PATCH vazio são rejeitados.

Endpoints R01 responderam 200; banco parado manteve `health` 200 e produziu
`ready` 503 e listagem de projetos 503 sanitizada. A recuperação devolveu 200.
Nenhuma resposta de erro inspecionada expôs credenciais ou stack traces.

## D. Upload, FFprobe e integridade

Defaults reais: **500 MiB**, **600 s**, overhead multipart **64 KiB**, timeout
FFprobe **15 s**, saída combinada **1 MiB**, reserva livre **64 MiB**. As seis
variáveis `EDIVO_UPLOAD_MAX_BYTES`, `EDIVO_UPLOAD_MULTIPART_OVERHEAD_BYTES`,
`EDIVO_MEDIA_MAX_DURATION_SECONDS`, `EDIVO_FFPROBE_TIMEOUT_SECONDS`,
`EDIVO_FFPROBE_MAX_OUTPUT_BYTES` e `EDIVO_STORAGE_MIN_FREE_BYTES` estão no
`.env.example` e na tabela do README. A interface recebe os limites públicos.

1. Middleware ASGI valida Content-Length e conta bytes efetivamente recebidos,
   inclusive sem esse header. O parser permite um arquivo `file`, zero campos
   extras, headers de parte até 8 KiB e boundary limitado.
2. Spool em `storage/temp`, fora do tmpfs de 128 MiB, e staging privado em chunks
   de 1 MiB, com contagem real, espaço livre e SHA-256 incremental.
3. Extensão original verificada antes de sanear/truncar nome; inspeção das boxes
   ISO BMFF e brands MP4. O MIME do navegador não determina aceitação.
4. FFprobe real com argv fixo, sem shell, protocolo file e demuxer mov
   restritos, referências externas desativadas, limite de probe/alocação,
   threads, tempo e stdout/stderr. Metadados selecionados e normalizados;
   vídeo default primeiro, depois menor índice; capa não conta como vídeo.
5. Projeto ativo revalidado com lock, flush, promoção exclusiva por hardlink
   para UUIDs gerados, fsync e arquivo 0400; commit no PostgreSQL. Falhas
   confirmadas compensam apenas os artefatos da operação.

Áudio é opcional. O HTTP real aceitou vídeo H.264/AAC e H.264 sem áudio, 320×180,
FPS **30000/1001**, duração **1001 ms**. Nomes iguais geraram IDs/keys distintos;
SHA-256 e bytes coincidiram entre resposta, PostgreSQL e arquivo lido no worker.
Reiniciar db/API/worker preservou os valores e arquivos. Soft delete preservou
linhas e originais. A auditoria somente leitura conferiu hashes e temporários.

Negativos executados: vazio, texto disfarçado, truncamento real, ausência de
vídeo, `.mp4evil`, extensão MOV, multipart malformado, dois arquivos, campos
extras, header grande, limites com e sem Content-Length, duração excedida,
FFprobe ausente/timeout, storage e banco indisponíveis, falhas de flush/commit,
desconexão, traversal, projeto excluído e relação cruzada. Casos de falha de
commit e concorrência de exclusão foram isolados nos testes de domínio.

**Limitações explícitas:** a inspeção decodifica um prefixo de aproximadamente
0,1 s, além de validar estrutura e metadados; não comprova a decodificação de
todos os frames até 600 s. Corrupção posterior que preserve estrutura e prefixo
pode não ser detectada. Não promete suporte de reprodução de todo codec MP4.
Não houve teste de carga nem transferência física de 500 MiB: os limites foram
exercitados com configurações menores e header acima do default. Não há limite
global de memória equivalente a `max_alloc`; este limita alocações individuais.

PostgreSQL e filesystem não têm atomicidade conjunta. Após resposta incerta de
COMMIT, uma sessão nova aguarda o lock do projeto antes de verificar o asset;
se o resultado permanecer indisponível, preserva o original e registra
`media_commit_outcome_uncertain`. Queda abrupta também pode produzir órfãos.
`audit-storage.py` reporta inconsistências sem apagar arquivos; execute em janela
sem uploads. Não foram provocadas quedas SIGKILL entre promoção e commit.

Diagnóstico R01 repetido na API e no worker: FFmpeg/FFprobe **5.1.9**, **libx264**,
**AAC**, filtros **scale/crop/pad/drawtext**, codificação/inspeção/decodificação
sintética de 1 s aprovadas. Nenhum render de mídia do usuário foi implementado.

## E. Frontend e navegador

Tela de projetos com estados carregando/vazio/erro, paginação, criação, edição,
detalhe e confirmação de exclusão lógica. Detalhe mostra upload e biblioteca,
duração, resolução, FPS, bytes, codecs, áudio e SHA-256. Navegação por hash,
requests relativas `/api`, temas persistidos, dialogs nativos com foco,
labels e status acessíveis. Nenhuma dependência JavaScript nova.

XHR informa progresso real de transferência; conclusão de `xhr.upload` muda
para **Analisando vídeo…**, sem percentual fabricado. Apenas o 201 confirma a
importação. Validação local de extensão/tamanho e mensagens de falha do servidor.

Chromium real em container auxiliar Playwright, na rede local da stack:
**16 verificações aprovadas**, sem mocks de API e sem pageerrors. Cobriram
CRUD, confirmação/cancelamento, upload com/sem áudio, estados observados,
metadados/hash, MP4 falso, reload, tema, 375 px sem overflow, offline e recuperação.
Evidências duráveis ficam em [evidence/](evidence/) e o JSON funcional em
[browser-functional.json](evidence/browser-functional.json).

O primeiro harness usou uma busca case-sensitive de `fps` em uma interface
que exibe `FPS`; o seletor foi corrigido e a rodada completa passou. Capturas
full-page com página rolada mostraram um artefato do link fixo de pular conteúdo;
a geometria confirmou o link oculto fora da viewport. As capturas finais usam
scroll zero, sem alterar ou esconder estilos do produto.

## F. Testes e QA executados

| Comando efetivamente executado | Resultado |
| --- | --- |
| Baseline `docker compose exec -T worker pytest -q` (imagem R01) | 36 pass |
| Baseline `docker compose run --rm --no-deps -T frontend npm run test -- --run` | 9 pass |
| `docker compose config --quiet` | Aprovado, sem imprimir segredos |
| `docker compose build api frontend` e build API após testes extras | Aprovados |
| `docker compose up -d --wait --wait-timeout 180` | Stack saudável; migrate exit 0 |
| `python3 scripts/acceptance-r02.py` | 13 verificações HTTP reais aprovadas |
| `python3 scripts/acceptance-upload-limits.py` | 19 verificações aprovadas |
| `python3 scripts/acceptance-compose.py --skip-build` | 27 verificações aprovadas |
| `docker compose exec -T api pytest` | **167 pass**, 1 aviso upstream |
| `docker compose exec -T api ruff check .` | Aprovado |
| `docker compose exec -T api ruff format --check .` | 41 arquivos conformes |
| `docker compose exec -T frontend npm run test -- --run` | **37 pass**, 3 arquivos |
| `docker compose exec -T frontend npm run lint` | Aprovado |
| `docker compose exec -T frontend npm run format:check` | Aprovado |
| `docker compose exec -T frontend npm run build` | TS strict/Vite aprovados |
| `docker compose exec -T api alembic upgrade head` | Repetição aprovada |
| `docker compose exec -T api alembic current` | `0002_r02_projects_media (head)` |
| `docker compose exec -T api alembic check` | Sem operações novas |
| `docker compose exec -T api python - < scripts/audit-storage.py` | Consistência aprovada |
| `backend/.venv/bin/ruff check --config backend/pyproject.toml scripts` | Aprovado |
| `backend/.venv/bin/ruff format --check --config backend/pyproject.toml scripts` | 6 arquivos conformes |
| `python3 -m py_compile` nos seis scripts | Aprovado |
| `python3 /tmp/edivo-browser/r02-orchestrate.py` | 16 verificações funcionais aprovadas |
| `EDIVO_BROWSER_SCRIPT=r02-recapture.mjs python3 /tmp/edivo-browser/r02-orchestrate.py` | 3 combinações visuais e 5 PNG aprovados |
| Inspeção Docker, logs sanitizados, permissões e hashes das fontes | Aprovados |

Os comandos das suítes e smoke API/worker foram chamados pelo harness da
fundação. Repetições host/container não foram somadas como testes novos.
Dos 167 testes Python, **36 são regressões R01 e 131 são R02**; dos 37 React,
**9 são R01 e 28 são R02**. A suíte isolada usa SQLite e fixtures de probe para
falhas controladas; aceites reais usam PostgreSQL, arquivos, FFmpeg e FFprobe.
O caso real de timeout impõe um deadline artificial de **1 microssegundo**;
não é benchmark de desempenho do FFprobe.

Build final: JS **288,42 kB** (gzip **90,20 kB**), CSS **27,51 kB** (gzip
**6,64 kB**). Worker ocioso amostrado em **0,0% CPU**; SIGTERM produziu
**exit 0**, seguido de reinicialização. Banco/frontend/API saudáveis ao concluir.

Ocorrências resolvidas: conflito de portas com outro produto (portas do Edivo
tornadas configuráveis, sem parar o outro produto); ajustes de seletores do
harness e formatação Ruff durante desenvolvimento; validação da extensão
antes da sanitização e sincronização da verificação de COMMIT incerto.
Permanece um aviso upstream Starlette sobre TestClient/httpx, sem falha de teste.
Não foi introduzido httpx2 fora da stack requerida.

Limitações de validação: navegador Chromium, sem rodada Firefox/WebKit;
FFmpeg validado nos runtimes Docker, não instalado no host; sem benchmark de
hardware/carga, varredura externa ou falha abrupta em cada ponto de persistência.
Não são critérios essenciais pendentes da R02. Não houve declaração de teste
aprovado sem execução.

## G. Critérios de aceite

- [x] R01 preservada: endpoints, banco, worker, Compose, shell, temas e proxy.
- [x] Migração incremental aplicada e repetível, baseline/histórico preservados.
- [x] CRUD API/UI com validação, paginação, ordem determinística e soft delete.
- [x] MP4 com/sem áudio importado, limite real de entrada e arquivo.
- [x] FFprobe real com metadados filtrados e FPS/duração normalizados.
- [x] SHA-256, tamanho, 0400 e importações independentes sem sobrescrita.
- [x] Negativos e desconexão sem registros/temporários evitáveis; compensação testada.
- [x] IDs/projetos ativos, isolamento de relação, traversal e erros sanitizados.
- [x] Storage privado e apenas frontend/API em loopback, portas escolhidas pelo usuário.
- [x] UI com estados verdadeiros e metadados, responsiva e acessível no escopo.
- [x] Banco e originais íntegros após reinício dos serviços.
- [x] Testes novos/regressões, linters, build, migrações, Compose e HTTP executados.
- [x] README, relatório, JSON de QA e evidências de navegador entregues.
- [x] Sem funcionalidades R03+, sem Git mutável, sem exclusão física de mídia/volumes.

**Pendências essenciais:** nenhuma. Os **13 originais sintéticos de QA (90.627
bytes)** ficam deliberadamente preservados em projetos excluídos logicamente.
A auditoria final encontrou 13 registros/arquivos correspondentes, hashes
íntegros, nenhum órfão e zero temporários. Inventário, hashes das fontes e
evidências constam do JSON. Nenhum dado existente foi removido.

## H. Próxima etapa

A R02 está tecnicamente pronta para fundamentar a **R03**, após aceite do
usuário. R03 não foi iniciada. Player/HTTP Range, timeline, cortes, presets
operacionais e qualquer outra evolução precisam de autorização e especificação
próprias; a biblioteca atual retorna apenas metadados.
