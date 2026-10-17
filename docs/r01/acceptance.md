# Edivo — relatório de implementação e aceite R01

**Data:** 17/10/2026. **Status:** R01 concluída, com critérios técnicos essenciais
validados. A rodada operacional final aprovou **27 verificações**, além de
**36 testes Python, 9 testes React, 9 verificações de navegador e 19 verificações
auxiliares de configuração/segurança**. API, frontend e banco ficaram saudáveis;
worker em execução e migração concluída com código zero.

## A. Resumo da implementação

Fundação local funcional, com frontend React/TypeScript, API FastAPI, PostgreSQL,
Alembic baseline, worker ocioso independente e diagnóstico real FFmpeg/FFprobe.
Não existe editor de vídeo funcional nem implementação de etapas posteriores.

```text
edivo-editor/
├── AGENTS.md, README.md, compose.yaml, .env.example, .gitignore
├── .python-version, .nvmrc
├── backend/
│   ├── Dockerfile, .dockerignore, pyproject.toml, uv.lock
│   ├── app/
│   │   ├── main.py, api/v1/system.py
│   │   ├── core/{config,logging}.py
│   │   ├── db/{base,session}.py
│   │   ├── services/readiness.py
│   │   ├── integrations/ffmpeg/capabilities.py
│   │   └── worker/main.py
│   ├── alembic.ini, alembic/{env.py,script.py.mako,versions/}
│   └── tests/
├── frontend/
│   ├── Dockerfile, .dockerignore, package.json, package-lock.json
│   ├── vite.config.ts, tsconfig*.json, eslint.config.js, components.json
│   ├── src/{app,components/ui,features/system,lib/api}
│   └── tests/
├── scripts/{setup-local.py,acceptance-compose.py,ffmpeg-smoke.py}
├── docs/{architecture.md,roadmap.md,adr/,r01/}
└── storage/{originals,previews,exports,workspaces,temp}  # ignorado
```

Os quatro documentos originais foram lidos e preservados byte a byte, conferidos
por SHA-256 antes/depois. `backend/.venv` existente permaneceu Python 3.12.3;
dependências foram acrescentadas com uv e `--inexact`, preservando uv/pip locais.
Nenhuma operação de alteração de Git foi realizada. A leitura inicial de status
informou que o `.git` disponibilizado neste ambiente não era um repositório
Git utilizável; isso não foi contornado.

## B. Ambiente e dependências reais

| Tecnologia | Versão observada |
| --- | --- |
| Host | Ubuntu 24.04.4 LTS / WSL2, kernel 6.18.33.2-microsoft-standard-WSL2 |
| Python host / venv preservado | 3.12.3 |
| Python API/worker | 3.12.14 |
| uv | 0.13.1 |
| Node host/container | 24.14.0 |
| npm host/container | 11.9.0 |
| Docker Engine / Compose | 29.8.0 / 5.5.1 |
| PostgreSQL | 16.13 (Debian 16.13-1.pgdg12+1) |
| FastAPI / Starlette | 0.143.3 / 1.8.0 |
| Pydantic / pydantic-settings | 2.14.1 / 2.16.0 |
| SQLAlchemy / psycopg | 2.0.55 / 3.3.6 |
| Alembic / Uvicorn | 1.20.0 / 0.54.0 |
| pytest / httpx / Ruff | 9.1.1 / 0.28.1 / 0.17.1 |
| React / React DOM | 19.3.1 / 19.3.1 |
| TypeScript / Vite | 6.0.3 / 8.3.4 |
| Tailwind CSS / Lucide React | 4.3.3 / 1.59.0 |
| Vitest / React Testing Library | 5.0.5 / 16.3.3 |
| ESLint / Prettier | 10.13.0 / 3.9.10 |
| FFmpeg / FFprobe, ambos os runtimes | 5.1.9-0+deb12u1 |
| Navegador auxiliar de QA | Playwright 1.58.2 / Chromium 145.0.7632.6 |

Dependências Python e JavaScript efetivamente resolvidas ficam em `uv.lock` e
`package-lock.json`. Imagens base e digests observados:

```text
python:3.12.14-slim-bookworm
sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
node:24.14.0-bookworm-slim
sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8
postgres:16.13-bookworm
sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60
```

API, worker e migrate compartilham `edivo-backend:r01`. Frontend usa
`edivo-frontend:r01`. Portas e mounts foram inspecionados no Docker:

| Serviço | Publicação no host | Persistência |
| --- | --- | --- |
| frontend | 127.0.0.1:5173 → 5173 | Código/dependências na imagem de desenvolvimento |
| api | 127.0.0.1:8000 → 8000 | `./storage` → `/data/storage` |
| worker | Nenhuma | Mesmo bind mount do storage |
| db | Nenhuma | `edivo_postgres_data` → `/var/lib/postgresql/data` |
| migrate | Nenhuma; execução única | Aplica baseline no banco |

O `.env` foi gerado com senha aleatória e modo 0600; storage com 0700.
API/worker usam UID/GID 1000:1000, imagem somente leitura, `/tmp` temporário,
`cap_drop: ALL`, `no-new-privileges` e apenas o bind de storage. Senha e DSN
reais não apareceram nos logs inspecionados; o frontend não recebeu credenciais
de banco nem `NODE_ENV` global forçado. As regras de ignore excluem
segredos, ambientes, dependências, storage e configurações privadas.

## C. Backend e frontend

| Endpoint | Resultado real |
| --- | --- |
| `GET /api/v1/system/health` | 200, inclusive com PostgreSQL parado |
| `GET /api/v1/system/ready` | 200 com banco/storage acessíveis |
| `GET /api/v1/system/ready`, banco parado | 503; database unavailable, storage ok |
| `GET /api/v1/system/ready`, storage inacessível | 503; database ok, storage unavailable |
| `GET /api/v1/system/capabilities` | 200; versões/encoders/filtros reais disponíveis |

Respostas de erro usam mensagens públicas e request ID, sem credenciais,
caminhos privados ou stack traces. Erros 404, 422 e 500 e configuração inválida
foram verificados por testes automatizados. CORS aceita somente origens locais.

A interface apresenta Edivo, descrição do produto, estágio R01 e limites atuais.
Tema claro/escuro é persistido em localStorage, com fallback se storage do browser
for bloqueado. Há estados de conexão carregando/disponível/indisponível, timeout
de cinco segundos, nova tentativa e horário/versão da última resposta válida.
O indicador representa a API, sem afirmar saúde do banco ou worker.

O proxy real `/api` do Vite respondeu 200. No navegador foram testados desktop
1440px e celular 375px, sem overflow horizontal, foco do link de pular conteúdo,
tema após reload, parada real da API, mensagem offline, tema ainda funcional
e recuperação pela ação de tentar novamente. Não houve `pageerror` no browser.
Não há botões de editor, timeline, player ou exportação fictícios.

## D. Banco e worker

Alembic aplicou `0001_r01_baseline`; uma consulta real confirmou que a única
tabela pública é `alembic_version`. `upgrade head` repetido preservou a revisão
sem erro; reiniciar PostgreSQL manteve a baseline. Um marcador temporário próprio
comprovou escrita na API, leitura no worker e persistência após reiniciar ambos;
o marcador foi removido ao terminar.

O worker registrou seu próprio diagnóstico de banco, storage, FFmpeg/FFprobe,
encoders e filtros. Permaneceu bloqueado em `Event.wait()`; a amostra Docker
final mediu **0,01% CPU**. SIGTERM produziu log de parada, fechamento do engine e
**exit 0**. O worker foi reiniciado e permaneceu separado da API, sem filas/jobs.

## E. FFmpeg

API e worker detectaram FFmpeg/FFprobe **5.1.9-0+deb12u1**, encoder H.264
**libx264**, encoder **AAC** e filtros **scale, crop, pad, drawtext**.
A detecção tem timeout por subprocesso, argumentos controlados e cache de
300 segundos; ausências retornam motivo explícito, sem saída bruta do comando.

O smoke sintético foi executado nos dois containers, com **H.264/AAC, 320×180,
1,0 segundo**, os quatro filtros e decodificação completa aprovados. Somente cor
e seno gerados localmente foram usados. Os arquivos temporários foram removidos.
Não foi processada mídia de usuário e heditor não foi instalado/executado.

## F. Testes e QA

| Comandos efetivamente executados | Resultado |
| --- | --- |
| `python3 scripts/setup-local.py` e repetição | `.env`/storage criados e depois preservados |
| `docker info`, `docker compose version` | Engine/Compose acessíveis após ativar integração WSL2 |
| `docker compose config --quiet` | Aprovado, sem imprimir segredos interpolados |
| `docker compose build api` | Build aprovado |
| `docker compose build frontend` | Build aprovado |
| `docker compose up -d --wait --wait-timeout 120 api worker` | Banco/migração/API/worker inicializados |
| `python3 scripts/acceptance-compose.py --skip-build` | 27 verificações aprovadas na imagem final, exit 0 |
| `docker compose exec -T api alembic upgrade head` | Baseline aplicada/repetida sem erro |
| `docker compose exec -T api alembic current` | `0001_r01_baseline (head)` |
| `docker compose ps -a` | API/frontend/db healthy, worker Up, migrate Exited (0) |
| `docker compose exec -T api pytest` | 36 aprovados, 1 aviso upstream |
| `docker compose exec -T api ruff check .` | Aprovado |
| `docker compose exec -T api ruff format --check .` | 19 arquivos conformes |
| `docker compose exec -T frontend npm run test -- --run` | 9 aprovados |
| `docker compose exec -T frontend npm run lint` | Aprovado |
| `docker compose exec -T frontend npm run format:check` | Aprovado |
| `docker compose exec -T frontend npm run build` | TypeScript strict e Vite aprovados |
| `docker compose exec -T api python -`, script smoke no stdin pelo harness | Smoke API aprovado |
| `docker compose exec -T worker python -`, script smoke no stdin pelo harness | Smoke worker aprovado |
| `backend/.venv/bin/ruff check --config backend/pyproject.toml scripts` | Aprovado |
| `backend/.venv/bin/ruff format --check --config backend/pyproject.toml scripts` | 3 arquivos conformes |
| `python3 -m py_compile scripts/setup-local.py scripts/acceptance-compose.py scripts/ffmpeg-smoke.py` | Aprovado |
| `docker pull mcr.microsoft.com/playwright:v1.58.2-noble` | Runtime auxiliar de navegador disponível |
| `python3 /tmp/edivo-browser/orchestrate.py` (auxiliar temporário) | 9 verificações de navegador aprovadas |
| `python3 /tmp/edivo-browser/read-only-final.py` (auxiliar temporário) | Imagem final: proxy 200 e screenshots aprovadas |
| `python3 /tmp/edivo-r01-audit.py` (auditoria auxiliar) | 19 verificações aprovadas |

Os resultados completos da rodada final, versões e IDs das imagens estão em
[qa-results.json](qa-results.json). Dois builds consecutivos no host e no
container produziram os mesmos hashes: JS **263,08 KB** e CSS **17,22 KB**.
O JSON também registra os SHA-256 desses artefatos. Scripts auxiliares em `/tmp`
e screenshots são evidências temporárias desta sessão; o aceite repetível do
repositório usa `scripts/acceptance-compose.py` e as suítes versionáveis.

Pytest/Ruff e Vitest/lint/format/build também foram executados no ambiente local.
Os 45 testes são casos únicos; sua repetição em containers não foi somada como
novos testes. A suíte operacional inclui consultas SQLAlchemy, HTTP real,
`docker inspect`, stop/start/restart, probe de storage e smoke FFmpeg em cada
runtime. Os mocks de UI foram usados apenas nos testes isolados do frontend.

Ocorrências encontradas e tratadas:

- Docker inicialmente sem integração WSL2: usuário ativou a integração; testes
  subsequentes foram executados contra o daemon real.
- DNS e portal AnyIO bloqueados pelo sandbox: comandos necessários repetidos
  com escalada; a suíte backend completa passou fora da restrição.
- Primeiro build TypeScript acusou `baseUrl` obsoleto: opção removida e build
  repetido com sucesso.
- Primeira rodada do harness interrompeu durante reinício: tratamento de
  conexões transitórias ajustado; a rodada completa posterior passou.
- `NODE_ENV=development` global no Docker alterava o bundle de build; a variável
  foi removida. Fonte explícita do Tailwind limita o scanner a `src` e evita
  variação por `dist` de builds anteriores. Dois builds consecutivos em cada
  ambiente, local e Docker, produziram hashes idênticos para JS e CSS; a rodada final de
  27 verificações passou depois da reconstrução da imagem.
- Um aviso de depreciação permanece no TestClient da Starlette com httpx 0.28.1.
  Os testes passam; não foi escondido nem substituído o requisito de httpx.
- FFmpeg/FFprobe e bibliotecas do Chromium não estavam instalados no host;
  sudo não interativo exigiu senha. A validação completa usou os containers,
  incluindo Chromium oficial isolado, sem montar socket Docker no navegador.

Não foram executados recursos R02+, E2E de edição ou benchmarks de renderização,
pois estão fora do escopo. A abertura visual do VS Code não foi automatizada;
estrutura e arquivos do monorepo foram inspecionados. Imagens de desenvolvimento
continuam usando tags explícitas com os digests observados registrados acima;
rebuild futuro pode atualizar pacotes de distribuição do FFmpeg.

## G. Critérios de aceite

- [x] Monorepo organizado, scripts e documentação consistentes.
- [x] Compose valida, constrói e inicia os serviços na configuração final.
- [x] PostgreSQL 16 saudável, consulta SQLAlchemy e persistência após reinício.
- [x] Alembic baseline sem tabelas de negócio e repetibilidade comprovada.
- [x] Health HTTP 200, independente do banco.
- [x] Readiness HTTP 200/503 conforme banco e storage reais.
- [x] Capabilities com detecção real e ausência explícita nos testes negativos.
- [x] Worker separado, diagnóstico, ociosidade e SIGTERM limpo.
- [x] Interface Edivo responsiva, temas persistidos e estados reais de API.
- [x] Segredos ignorados, logs sanitizados, runtimes sem privilégios excessivos.
- [x] Somente API/frontend publicados em loopback; banco/worker sem publicação.
- [x] Testes e ferramentas de qualidade executados; ocorrências discriminadas.
- [x] README cobre instalação, URLs, configuração, diagnóstico, QA e WSL2.
- [x] Documentos originais/venv preservados; Git sob gestão manual.
- [x] Nenhuma funcionalidade da R02+ implementada.

## H. Próxima etapa

A fundação está **pronta tecnicamente para a R02**. Não há pendências essenciais
de execução da R01; o aviso upstream e as verificações manuais não automatizadas
estão discriminados acima. A próxima etapa prevista é **R02 — projetos, upload
seguro e metadados**, que exige nova autorização. A R02 não foi iniciada.
