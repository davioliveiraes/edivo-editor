# Edivo

Editor de vídeo web **local-first**, com React/TypeScript, FastAPI/Python e
FFmpeg. A **R01 — Fundação Executável** entrega o ambiente e seus diagnósticos.
Os recursos de edição serão implementados em etapas posteriores.

O resultado real da validação, as versões observadas e o checklist de aceite
estão em [docs/r01/acceptance.md](docs/r01/acceptance.md). Os quatro documentos
originais de planejamento em `docs/` foram preservados. Consulte também
[arquitetura](docs/architecture.md), [decisões](docs/adr/0001-r01-foundation.md),
[roadmap](docs/roadmap.md) e [convenções para agentes](AGENTS.md).

## Requisitos

- Ubuntu 24.04 LTS, inclusive via WSL2; prefira `~/edivo-editor` no filesystem
  Linux em vez de `/mnt/c` para permissões e desempenho de watch.
- Docker Engine ou Docker Desktop com integração ativada para a distribuição
  WSL2 e Docker Compose com suporte a condições de dependência.
- Python 3.12 no host para os scripts de preparo/aceite.
- Portas locais 5173 e 8000 disponíveis. PostgreSQL não publica porta no host.
- Rede para baixar imagens e dependências no primeiro build; o produto não
  depende de serviços externos de IA, GPU ou assinatura.
- Para desenvolver fora dos containers: Node 24 LTS, npm 11 e uv. As versões
  usadas e os patches das imagens estão registrados no relatório de aceite.

## Preparar e iniciar

Na raiz do repositório:

```bash
python3 scripts/setup-local.py
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps -a
```

O preparo cria `.env` com senha aleatória, permissões 0600 e UID/GID locais,
além dos diretórios de storage com acesso privado ao usuário. Se `.env` ou
arquivos já existirem, são preservados. Não publique nem envie esse arquivo.
Use `config --quiet`: a saída completa de `docker compose config` contém
variáveis interpoladas, inclusive credenciais.

O serviço `migrate` deve terminar com código zero. Os serviços `api` e `worker`
aguardam banco saudável e migração concluída; o frontend aguarda a API saudável.
Na primeira inicialização, aguarde os healthchecks antes de abrir a interface.

| Serviço | Função | Acesso no host |
| --- | --- | --- |
| `frontend` | React e servidor Vite de desenvolvimento | http://127.0.0.1:5173 |
| `api` | FastAPI/Uvicorn | http://127.0.0.1:8000 |
| `db` | PostgreSQL 16 | Nenhuma porta publicada |
| `worker` | Diagnóstico e espera ociosa | Nenhuma porta publicada |
| `migrate` | Alembic, execução única | Nenhuma porta publicada |

Em `docker compose ps`, entradas como `5432/tcp` ou `8000/tcp` sem mapeamento
`127.0.0.1:...->` representam portas internas/metadados da imagem, sem publicação
no host. O worker não inicia servidor HTTP.

Swagger local: http://127.0.0.1:8000/docs. O navegador utiliza `/api`, encaminhado
pelo Vite para `http://api:8000` dentro do Docker. O frontend exibe disponibilidade
da API; isso não representa monitoramento do worker nem aptidão de um editor.

```bash
curl -fsS http://127.0.0.1:8000/api/v1/system/health
curl -fsS http://127.0.0.1:8000/api/v1/system/ready
curl -fsS http://127.0.0.1:8000/api/v1/system/capabilities
docker compose logs --tail=50 worker
```

## Configuração e persistência

`.env.example` documenta as variáveis sem conter credenciais reais. O Compose
constrói o DSN a partir de `POSTGRES_USER`, `POSTGRES_PASSWORD` e `POSTGRES_DB`;
`EDIVO_DATABASE_URL` é usado pelo Python fora do Compose. Senhas geradas em
hexadecimal não precisam de URL encoding. Este Compose exige senha URL-safe
(prefira o hexadecimal gerado): ele usa o mesmo valor diretamente no PostgreSQL
e na montagem do DSN. Não codifique apenas `POSTGRES_PASSWORD`, pois isso mudaria
a senha do banco. Um DSN nativo configurado separadamente precisa de URL encoding
quando suas credenciais contêm caracteres reservados.

| Variável | Uso |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` | Credenciais locais do banco |
| `EDIVO_UID`, `EDIVO_GID` | Identidade do backend e permissões do bind mount |
| `EDIVO_ENV`, `EDIVO_LOG_LEVEL` | Ambiente e nível de logging |
| `EDIVO_DATABASE_URL` | URL `postgresql+psycopg://...` para SQLAlchemy/Alembic |
| `EDIVO_DATABASE_CONNECT_TIMEOUT` | Timeout de conexão em segundos |
| `EDIVO_STORAGE_ROOT` | Storage; no Compose é `/data/storage` |
| `EDIVO_CORS_ORIGINS` | Lista JSON de origens locais permitidas |
| `EDIVO_FFMPEG_BIN`, `EDIVO_FFPROBE_BIN` | Executáveis para diagnóstico |
| `EDIVO_FFMPEG_TIMEOUT_SECONDS` | Timeout por subprocesso de inventário |
| `EDIVO_CAPABILITIES_CACHE_SECONDS` | Prazo do cache de capacidades |
| `API_PROXY_TARGET` | Destino do proxy Vite; não é segredo nem URL do browser |

Settings rejeitam outros drivers de banco e origens CORS externas. Configuração
inválida falha com mensagem sanitizada. Nunca use variáveis `VITE_*` para segredos.
O DSN não aceita query; configure os timeouts pelas variáveis indicadas.

PostgreSQL usa o volume nomeado `edivo_postgres_data`; API e worker compartilham
`./storage`. A baseline Alembic cria somente o registro de versão, sem tabelas
de negócio. Para reaplicar e consultar a revisão:

```bash
docker compose run --rm migrate
docker compose exec api alembic current
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"'
```

As credenciais do entrypoint PostgreSQL são aplicadas quando o volume é criado;
alterar somente `.env` não modifica uma senha em banco já inicializado.
Não remova o volume para resolver isso sem avaliar os dados existentes.

Para encerrar preservando os dados:

```bash
docker compose stop
```

Para retomar, use `docker compose up -d`. Não execute `down -v`: ele apaga o
volume. O script de aceite não remove volumes nem dados existentes.

## Desenvolvimento e qualidade

Os containers incluem dependências de desenvolvimento para a R01:

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
docker compose exec frontend npm run test -- --run
docker compose exec frontend npm run lint
docker compose exec frontend npm run format:check
docker compose exec frontend npm run build
```

Os Dockerfiles copiam o código para a imagem. Após alterações, execute
`docker compose up -d --build`. O build garante instalação por `uv.lock` e
`package-lock.json`; não atualize locks incidentalmente.

Para desenvolvimento nativo, preserve `backend/.venv` se já for Python 3.12.
Com uv instalado fora do ambiente, execute `uv sync --frozen` dentro de `backend`, depois
`uv run pytest`, `uv run ruff check .` e `uv run ruff format --check .`.
Se uv foi instalado no próprio ambiente existente, use
`.venv/bin/uv sync --frozen --inexact` para preservar o gerenciador e o pip;
depois execute `.venv/bin/pytest` e `.venv/bin/ruff` diretamente.
No frontend, execute `npm ci`, `npm run test -- --run`, `npm run lint` e
`npm run build` dentro de `frontend`.

Para iniciar a API nativa, é preciso um PostgreSQL acessível nessa rede e um
`EDIVO_DATABASE_URL` correspondente. O hostname `db` é interno ao Docker;
o Compose padrão não publica o banco. Não abra uma porta pública para contornar
essa separação. FFmpeg/FFprobe também precisam existir no runtime nativo.

```bash
# Em backend/, após configurar ambiente nativo e uv sync --frozen
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
# Em outro terminal, no mesmo ambiente
.venv/bin/python -m app.worker.main
# Em frontend/, proxy padrão para 127.0.0.1:8000
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

## Diagnóstico FFmpeg

`capabilities` verifica versões, encoders `libx264`/AAC e filtros `scale`, `crop`,
`pad`, `drawtext`, com timeout e cache. O endpoint sempre retorna HTTP 200 com o
diagnóstico: ausências aparecem como `available=false` e motivo seguro. Readiness
mede banco e storage; a presença dos binários é informada separadamente.

Execute a mesma prova sintética de cerca de um segundo em ambos os runtimes:

```bash
docker compose exec -T api python - < scripts/ffmpeg-smoke.py
docker compose exec -T worker python - < scripts/ffmpeg-smoke.py
```

O script usa somente cor e seno gerados localmente, exercita os quatro filtros,
codifica H.264/AAC, inspeciona codecs/resolução/duração por FFprobe e verifica
decodificação. Os temporários são removidos ao finalizar. Não aceita mídia de
usuário e não é uma funcionalidade de renderização do produto.

## QA operacional

```bash
python3 scripts/acceptance-compose.py
```

O script valida a stack e executa cenários operacionais, inclusive interrupção
temporária do banco e reinício do worker. Execute quando não houver outra
validação usando os mesmos serviços. Consulte seu resultado e o relatório de
aceite; testes de UI com mocks não substituem a comunicação real pelo proxy.

## Problemas comuns no Ubuntu/WSL2

- **Docker não encontrado ou integração desativada:** inicie o Docker Desktop
  e ative a distribuição em Settings → Resources → WSL Integration. Confira
  `docker info` e `docker compose version` no terminal WSL.
- **Acesso negado ao socket Docker:** confira a integração/permissões da
  distribuição. Não use `chmod 666` no socket nem monte esse socket no app.
- **Portas ocupadas:** identifique o processo com `ss -ltn`; pare o processo
  conflitante de forma controlada. Não mude o bind para `0.0.0.0` no host.
- **Storage indisponível:** execute o preparo com seu usuário e confira
  proprietário/UID/GID do diretório. Não aplique `chmod 777` indiscriminadamente.
- **API 503:** confira `checks.database` e `checks.storage` e os logs locais.
  `health` 200 com `ready` 503 é esperado durante indisponibilidade de dependência.
- **Worker encerra ao iniciar:** seu diagnóstico exige banco, storage e as
  capacidades FFmpeg necessárias; corrija a dependência e reinicie o serviço.
- **Interface offline:** confira API, proxy e `docker compose ps -a`; o tema e
  a mensagem de indisponibilidade continuam funcionando sem API.
- **Downloads falham:** confira rede/proxy e acesso aos registries. Não copie
  tokens para manifestos, Dockerfiles ou variáveis do frontend.
- **Watch lento no WSL2:** mantenha o repositório no filesystem Linux. O fluxo
  Docker copia código no build; o fluxo nativo usa o HMR do Vite.

## Limites atuais e próxima etapa

Esta entrega não possui editor funcional, projetos, uploads, player, timeline,
recortes, áudio, previews, renderização do usuário, exportações, filas, IA,
heditor, Higgsfield, autenticação ou recursos SaaS. Não há monitoramento HTTP
do worker. O servidor Vite e o Compose são destinados ao desenvolvimento local.

A próxima etapa prevista é **R02 — projetos, upload seguro e metadados**, somente
depois do aceite da fundação e de autorização explícita. Nenhuma operação de
gerenciamento de Git é executada pelos scripts do projeto.

Referências de compatibilidade: [Vite](https://vite.dev/guide/),
[ordem de startup do Compose](https://docs.docker.com/compose/how-tos/startup-order/)
e [filtros FFmpeg](https://ffmpeg.org/ffmpeg-filters.html).
