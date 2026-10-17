# Edivo — R01 | Especificação operacional da Fundação Executável

> **Data:** 16/10/2026  
> **Produto:** Edivo · **Repositório:** `edivo-editor`  
> **Estado:** R00 aprovada; R01 especificada, **não implementada**.  
> **Fontes primárias:** `EDIVO_DOCUMENTACAO_PROJETO.md`, `EDIVO_INSTRUCOES_CHATGPT(1).md`, `R00_EDIVO_PLANEJAMENTO_TECNICO.md`.  
> **Objetivo:** implementar depois um ambiente local-first reprodutível com frontend, API, PostgreSQL, worker independente e diagnóstico FFmpeg/FFprobe. Nenhum recurso de edição do usuário entra nesta etapa.

## 1. Escopo estrito

### Incluir
1. Estrutura inicial de monorepo, README, `.gitignore`, `.dockerignore`, `.env.example`, `AGENTS.md` e documentação da arquitetura.
2. `compose.yaml` de desenvolvimento para quatro serviços: `db`, `api`, `worker`, `frontend`; serviço de uso único `migrate` para Alembic, se adotado.
3. Python 3.12, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2, psycopg 3, Alembic, Uvicorn, pytest/httpx e Ruff.
4. PostgreSQL 16 com volume persistente, healthcheck, conexão real via SQLAlchemy, migração inicial vazia/baseline.
5. Worker Python independente, compartilhando o código-base do backend e imagem de execução, que valide o ambiente ao iniciar, permaneça ocioso sem consumir CPU desnecessariamente e finalize corretamente ao receber SIGTERM.
6. FFmpeg e FFprobe instalados e detectados nos contextos de API e worker, com checagens reais de codecs/filtros necessários ao MVP futuro.
7. API mínima com endpoints de liveness, readiness e capabilities; tratamento de falhas sanitizado e logging sem segredos.
8. Frontend React + TypeScript strict + Vite + Tailwind + configuração shadcn/ui + Lucide, layout responsivo, alternância claro/escuro e estado da API (loading/ok/indisponível).
9. Infraestrutura de testes, lint e scripts documentados; executá-los e relatar os resultados reais.

### Excluir expressamente
- CRUD `Project`, `MediaAsset`, `EditSpecRevision`, `RenderJob`, `Export` e respectivas migrations.
- Upload, reprodução/player de mídia, timeline, corte, filtros, áudio, legenda, preview, render final, histórico, download.
- Agendamento de renders, criação de fila real, heartbeat de jobs, cancelamento, API de jobs.
- heditor instalado/orquestrado, IA, Whisper, Higgsfield, GPU, Redis/Celery/RQ, S3, autenticação, multiusuário, SaaS, pagamentos.
- Telas que simulem funcionalidades inexistentes ou status de worker não observável.

## 2. Decisões fechadas para a primeira implementação

| Área | Decisão | Motivo |
|---|---|---|
| Arquitetura | Monólito modular backend + worker em processo/container separado | Evita microsserviços prematuros; preserva isolamento operacional |
| Python | 3.12.x | Coerência com instruções permanentes e R00 |
| Node | 24.x LTS como alvo, confirmar tooling instalado; registrar patch real | Vite atual exige Node compatível; 24 está em LTS em 16/10/2026 |
| Banco | PostgreSQL **16** | Base já escolhida na R00; tag 16.x explícita |
| ORM | SQLAlchemy 2, sessão síncrona | Pouca complexidade e migrável |
| Migrations | Alembic baseline **sem tabelas de negócio** | Verifica workflow sem antecipar R02 |
| Python deps | `uv` + `pyproject.toml` + `uv.lock` (proposta operacional) | Instalações reproduzíveis; validar no ambiente |
| JS deps | `npm` + `package-lock.json` | Menor variedade de ferramentas |
| UI | React/TS, Vite, Tailwind, shadcn/ui mínimo e Lucide | Mesmo stack já aprovado |
| Comunicação de desenvolvimento | Vite proxy `/api` -> serviço `api:8000`; navegador usa URLs relativas | Evita CORS desnecessário no browser e URLs de container expostas |
| Banco exposto | **Não** publicar porta 5432 no host por padrão | Reduz superfície de acesso e conflitos |
| API e frontend | Publicar somente em `127.0.0.1:8000` e `127.0.0.1:5173` no host | Local-first sem autenticação; não expor LAN |
| Storage | Bind mount `./storage` em `/data/storage` para API/worker | Testa caminho de desenvolvimento e permissões reais |
| FFmpeg | Instalação via pacote da imagem Linux em API e worker, versões registradas | Diagnóstico consistente sem download arbitrário |
| Worker | Ocioso na R01; **nenhuma fila real** | Fila e processamento entram nas fases definidas no roadmap |
| Health | Liveness separado de readiness; capabilities reporta detecções, não funcionalidades | Estado transparente; facilita QA |

**Compatibilidade pendente de execução:** os números completos dos pacotes, das imagens, do FFmpeg, as flags disponíveis e os SHA/digests não são declarados testados. Na R01, selecionar tags explícitas, resolver a instalação, gerar locks e registrar as versões realmente observadas. Nunca assumir suporte H.264 por existir executável `ffmpeg`.

## 3. Topologia de execução local

```text
Browser http://127.0.0.1:5173
          |
          v
Vite / frontend (porta 5173; bind host loopback)
          | proxy HTTP relativo /api
          v
FastAPI / api (porta interna 8000; host 127.0.0.1:8000)
          | SQLAlchemy / psycopg
          v
PostgreSQL / db:5432 (rede Docker interna + volume nomeado)
          ^
          |
Worker Python / worker (sem portas; mesmo backend package)
          |
          +-- diagnóstico FFmpeg/FFprobe
          +-- storage compartilhado /data/storage

Migrate (one-shot) -> aguarda db saudável -> Alembic upgrade head
api/worker -> aguardam db saudável e migração concluída
```

**Compose:** adotar `depends_on: condition: service_healthy` para `db` e, se houver serviço `migrate`, `condition: service_completed_successfully` para os consumidores. Sem healthcheck que afirme recurso indisponível. Persistir `db` em volume nomeado e `storage` no workspace ignorado pelo Git. API e worker podem compartilhar uma única imagem backend com comandos distintos; não duplicar código de configuração.

**WSL2:** preferir repositório no filesystem Linux para reduzir problemas de watch/performance e permissões; documentar alternativa se executado em filesystem montado Windows. Não depender de recursos de GPU.

## 4. Estrutura inicial de arquivos

```text
edivo-editor/
├── README.md
├── AGENTS.md
├── .env.example
├── .gitignore
├── compose.yaml
├── docs/
│   ├── architecture.md
│   ├── roadmap.md
│   ├── adr/            # decisões consolidadas da R00
│   └── r01/acceptance.md
├── backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/     # baseline inicial
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── api/v1/system.py
│   │   ├── core/config.py
│   │   ├── core/logging.py
│   │   ├── db/base.py
│   │   ├── db/session.py
│   │   ├── integrations/ffmpeg/capabilities.py
│   │   └── worker/main.py
│   └── tests/
│       ├── conftest.py
│       ├── test_system.py
│       ├── test_settings.py
│       └── test_ffmpeg_capabilities.py
├── frontend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/system/
│   │   ├── lib/api/
│   │   └── main.tsx
│   └── tests/
└── storage/           # ignorado pelo Git; preparado para R02+
    ├── originals/
    ├── previews/
    ├── exports/
    ├── workspaces/
    └── temp/
```

Essa árvore descreve áreas de responsabilidade, não obrigação de criar dezenas de arquivos vazios. Criar apenas módulos necessários. Não criar diretórios `heditor`/`providers` com métodos falsos; as interfaces pertencem a fases posteriores. O `AGENTS.md` deve conter as regras do repositório e os limites R01.

## 5. Especificação dos contratos mínimos

### `GET /api/v1/system/health` — liveness
- Devolve **HTTP 200** quando o processo HTTP atende.
- Exemplo conceitual: `{ "status": "ok", "service": "edivo-api", "version": "0.1.0" }`.
- **Não** usa esse endpoint para atestar conexão com DB ou worker; não faz subprocessos em cada request.

### `GET /api/v1/system/ready` — readiness
- **HTTP 200** quando banco executa query simples e storage requerido pode ser acessado pelo processo; incluir estado por dependência.
- **HTTP 503** com resposta sanitizada quando banco/storage essencial falha.
- Nunca retornar senha, DSN, caminho absoluto do host ou stack trace.
- Exemplo conceitual de sucesso: `{ "status":"ready", "checks": {"database":"ok", "storage":"ok"} }`.
- Falha controlada não pode derrubar o processo API.

### `GET /api/v1/system/capabilities` — capacidades do processo
- **HTTP 200** com status/campos objetivos: versões detectadas de `ffmpeg` e `ffprobe`, disponibilidade de `libx264` ou outro H.264 encoder selecionado, AAC, filtros `scale`, `crop`, `pad`, `drawtext`.
- Especificar `available=false` e motivo seguro caso alguma verificação falhe; não inventar recursos.
- Detecção executada no startup ou via cache com timeout e argv controlado; **não** executar encode em cada chamada HTTP.
- Restringir saída a diagnóstico do processo API. O worker comprova seu próprio runtime por diagnóstico/log/teste separado. Não afirmar `worker: healthy` sem monitoramento implementado.
- Não confundir encoder com decoder nem presença de filtro com sucesso real na composição.

### Contrato de erro transversal
- Respostas sanitizadas com `code`, `message` e `request_id` quando relevante.
- CORS em allowlist de origens locais se acesso direto à API; não usar `*` indiscriminadamente.
- `/docs` do FastAPI pode permanecer habilitado em desenvolvimento local.

## 6. Banco, Alembic e storage

1. `db` com database/user/password configurados via `.env` local ignorado, e valores de demonstração **não secretos** em `.env.example` sem credenciais reais.
2. SQLAlchemy com `pool_pre_ping` e conexão usada em readiness, sem consultas de entidades ainda inexistentes.
3. Alembic configurado com conexão via settings; criar **baseline vazia**, aplicar `upgrade head` e validar a revisão corrente.
4. `migrate` pode ser serviço Compose de execução única. Não disparar `alembic upgrade` concorrente em cada inicialização de `api` e `worker`.
5. Verificar que o banco mantém `alembic_version` após reiniciar e que executar novamente a migração não causa falhas.
6. `storage` com pastas definidas, montado nos processos necessários; testar criação/leitura de arquivo temporário com permissões do usuário de runtime, limpar artefato de teste. Não implementar APIs de arquivos nesta fase.
7. Não criar `Project` nem qualquer tabela futura por especulação; alinhar a primeira migration de domínio à R02.

## 7. Worker ocioso, mas real

- Executar `python -m app.worker.main` (ou entrypoint equivalente real) no seu container.
- Verificar configuração e conexão com DB; checar diretório storage e `ffmpeg`/`ffprobe` no startup.
- Registrar uma mensagem estruturada de readiness do **processo worker** (não um suposto endpoint HTTP).
- Aguardar trabalho de forma ociosa, sem polling em tabelas que ainda não existem, sem loop de CPU descontrolado.
- Tratar SIGTERM/SIGINT, fechar conexões e sair sem deixar recursos presos.
- **Não** criar fila, tabela de jobs, workers falsos ou job de render. Isso só acontecerá no marco de processamento do produto.

## 8. Frontend mínimo de verdade

- Base Vite com React/TypeScript strict; Tailwind CSS, shadcn/ui configurado apenas para componentes efetivamente usados, ícones Lucide.
- Tela inicial com identidade **Edivo**, layout organizado, tema claro/escuro persistido localmente e composição responsiva.
- Um estado técnico da API consumido por URL relativa `/api/v1/system/health` e, opcionalmente, `/ready` e `/capabilities`.
- Estados distintos: carregando, acessível, API indisponível, capacidade ausente; respeitar que **não** há status real do worker disponível via HTTP.
- Textos de ação informam que editor/importação chegarão em etapas posteriores; não incluir botões que simulem funcionamento.
- Acessibilidade mínima: contraste, título semântico, controle de tema com rótulo, foco visível e mensagens legíveis.
- Testes com API simulada em Vitest/Testing Library apenas para comportamento de UI; não alegar integração real a partir de mocks.

## 9. Diagnóstico FFmpeg/FFprobe

Verificações mínimas da R01 **no ambiente real de execução** (API e worker):

1. `ffmpeg -version` e `ffprobe -version` executados com timeout e registro das versões.
2. Inventário de encoders: detectar H.264 configurado (`libx264`, preferencialmente) e AAC.
3. Inventário de filtros: `scale`, `crop`, `pad`, `drawtext`.
4. Opcional recomendado: smoke técnico **sintético**, gerando 1 segundo de cor/test pattern no diretório temporário com encoder selecionado e inspecionando por `ffprobe`; apagar depois. **Não** é render de arquivo do usuário nem pipeline de edição da aplicação.
5. Se o codec ou filtro não estiver disponível, reportar resultado **não conforme**, registrando como bloqueio para R03–R05; não afirmar falsamente compatibilidade.
6. Não instalar a skill heditor nem executar `heditor doctor` na R01. O teste real desse sistema fica em R07 conforme roadmap consolidado.

## 10. Configuração e princípios de segurança

**Variáveis sugeridas:** `EDIVO_ENV`, `EDIVO_LOG_LEVEL`, `EDIVO_API_PORT`, `EDIVO_FRONTEND_PORT`, `EDIVO_DATABASE_URL`, `EDIVO_STORAGE_ROOT`, `EDIVO_CORS_ORIGINS`, `EDIVO_FFMPEG_BIN`, `EDIVO_FFPROBE_BIN`.

- Validar settings obrigatórias e não imprimir URL de banco com senha.
- Nunca vincular serviços publicamente no host; bind `127.0.0.1` do Compose é obrigatório em R01.
- Não publicar Postgres. Não assumir que `0.0.0.0` dentro do container torna o acesso externo seguro: proteção está no mapeamento do host.
- Não abrir `storage` via `StaticFiles` indiscriminadamente.
- Containers sem privilégios desnecessários; considerar UID/GID para permissão dos bind mounts.
- `.env`, volumes, temporários, chaves, arquivos pessoais e `node_modules` fora do Git.
- Nenhum subprocesso com `shell=True` ou instruções livres de usuários.

## 11. Pacotes de trabalho (ordem de implementação)

| Lote | Trabalho | Evidência exigida |
|---|---|---|
| R01.1 | Estrutura, README, env, ignore, convenções, Dockerfiles | árvore coerente, sem arquivos placeholder desnecessários |
| R01.2 | Compose `db` e migração baseline | banco `healthy`, Alembic `head` aplicado; reinício preserva volume |
| R01.3 | FastAPI + settings + SQLAlchemy + system endpoints | health 200, ready 200/503 por condição, respostas sanitizadas |
| R01.4 | Worker independente e startup diagnostics | processo sobe, fica ocioso e encerra corretamente; logs verificáveis |
| R01.5 | FFmpeg/FFprobe capabilities | versões reais, encoders e filtros, smoke sintético quando possível |
| R01.6 | Frontend shell e proxy `/api` | tela abre, tema funciona, status correto e responsividade |
| R01.7 | Testes, lint, QA e documentação final | comandos e resultados reais com falhas discriminadas |

Lotes são etapas **internas da R01**, não novos marcos de roadmap. Não expandir para R02 se algum lote ficar rápido.

## 12. Plano de testes e QA

### Comandos previstos (não executados nesta especificação)

```bash
# Diagnóstico de infraestrutura
docker compose version
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps

# Verificação da API
curl -fsS http://127.0.0.1:8000/api/v1/system/health
curl -fsS http://127.0.0.1:8000/api/v1/system/ready
curl -fsS http://127.0.0.1:8000/api/v1/system/capabilities

# Banco / migrations (ajustar o serviço conforme Compose implementado)
docker compose exec api alembic current

# Testes e lint backend — scripts reais devem ser documentados
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .

# Frontend — ajustar à configuração final de scripts
docker compose exec frontend npm run test -- --run
docker compose exec frontend npm run lint
docker compose exec frontend npm run build

# Worker
docker compose logs --tail=100 worker
```

**Casos negativos obrigatórios:**
- Com `db` inacessível, `/health` continua 200 enquanto `/ready` responde 503 sem expor segredos (quando o processo API já estiver iniciado); depois que DB voltar, readiness volta a 200.
- Com um binário FFmpeg indisponível ou caminho inválido, capacidades devem reportar indisponibilidade sem traceback para o usuário; o worker pode falhar explicitamente no startup segundo política documentada.
- Reinício da stack não apaga `alembic_version` nem diretórios de `storage`.
- UI exibe estado de API offline sem travar ou sugerir que editor funciona.
- SIGTERM no worker encerra o processo com fechamento limpo; evitar zombie.
- Configuração incorreta produz erro claro e sanitizado; porta do banco não é publicada no host.

Não se exige E2E de mídia, exportação, teste de importação nem medição de tempo de render em R01.

## 13. Critérios de aceite para fechar a R01

- [ ] Monorepo abre pelo VS Code e possui estrutura, scripts e documentação consistentes.
- [ ] Compose valida e sobe os serviços necessários; host publica **somente frontend e API em loopback**.
- [ ] Banco PostgreSQL 16 fica healthy, responde à query SQLAlchemy e preserva dados após reinício.
- [ ] Alembic baseline executa e idempotência da migração é comprovada.
- [ ] `GET /api/v1/system/health` responde 200.
- [ ] `GET /api/v1/system/ready` responde 200 em estado saudável e 503 para indisponibilidade de banco/storage essencial.
- [ ] `GET /api/v1/system/capabilities` retorna capacidades verificadas de FFmpeg/FFprobe, incluindo codecs/filtros exigidos ou ausência explícita.
- [ ] Worker roda como processo separado, comprova startup, permanece ocioso e para de forma limpa.
- [ ] Frontend responsivo exibe Edivo, alterna temas e apresenta estados verdadeiros da API.
- [ ] `.env` e arquivos privados estão ignorados, logs não expõem credenciais, serviços não ficam acessíveis pela LAN.
- [ ] Testes e verificações automatizados da R01 executados; resultado real registrado sem inventar testes.
- [ ] README documenta preparo, startup, URLs, checks, comandos de QA, troubleshooting de WSL2 e limitações do estágio atual.
- [ ] Não foi implementado nenhum recurso da R02+ (upload, projetos, timeline, render, IA).

## 14. Entrega final exigida do Codex (na futura execução)

Relatar:
1. Arquivos criados/alterados (em grupos por responsabilidade).
2. Dependências e versões concretas usadas (Python, Node, PostgreSQL, FFmpeg, pacotes relevantes).
3. Serviços Compose e volumes; portas efetivamente expostas.
4. Endpoints retornados com códigos HTTP reais (e evidências de indisponibilidade).
5. Diagnóstico FFmpeg/FFprobe e resultado do smoke sintético, quando executado.
6. Migrações aplicadas e persistência verificada.
7. Testes, lint e build: comandos, quantidades e erros, sem ocultar falhas.
8. Pendências e limitações reais antes de iniciar R02.
9. Evidência de que o escopo não ultrapassou R01.

A gestão do Git permanece manual. **Não incluir comandos de commit/push em prompts para Codex.** Mensagem de commit sugerida somente depois de implementação realmente concluída e revisada, separada do prompt.

## 15. Prompt operacional para Codex CLI — usar apenas quando autorizar a implementação

> **Observação:** o bloco abaixo é um plano de execução destinado ao Codex; sua inclusão neste documento **não** executa ações.

```text
Você é desenvolvedor fullstack sênior Python/FastAPI e React/TypeScript, responsável por implementar a R01 — Fundação Executável do Edivo, repositório edivo-editor.

CONTEXTO
Leia os documentos do projeto EDIVO_DOCUMENTACAO_PROJETO.md, EDIVO_INSTRUCOES_CHATGPT(1).md, R00_EDIVO_PLANEJAMENTO_TECNICO.md e R01_EDIVO_ESPECIFICACAO_OPERACIONAL.md, quando disponíveis no workspace. Inspecione o repositório antes de criar/alterar arquivos. Se documentos não estiverem disponíveis, registre a limitação e trabalhe estritamente conforme as instruções fornecidas.

OBJETIVO
Criar exclusivamente a fundação local-first: monorepo, Docker Compose com frontend/api/worker/PostgreSQL, config/env, banco e Alembic baseline, endpoints de health/ready/capabilities, diagnóstico FFmpeg/FFprobe e shell React com tema claro/escuro e status real da API.

DECISÕES
Python 3.12; React/TypeScript strict + Vite; Tailwind/shadcn e Lucide mínimos; PostgreSQL 16; SQLAlchemy 2 síncrono + psycopg3 + Alembic; uv/uv.lock e npm/package-lock; Node 24 LTS se compatível; FFmpeg/FFprobe nos containers backend; API e worker na mesma base Python mas em processos independentes; serviços publicados apenas em loopback. Não criar tabelas de domínio antecipadamente. Não usar Redis, jobs reais ou render HTTP.

ESCOPO NEGATIVO
NÃO implementar CRUD de projetos, uploads, media player, timeline, specs de edição, fila de renders, preview, exportação, heditor/IA, Higgsfield, auth, SaaS, pagamentos nem placeholders fingindo recursos.

IMPLEMENTAÇÃO
1. Inspecionar estrutura existente e preservar mudanças do usuário.
2. Preparar documentação, env de exemplo, ignores, Dockerfiles e Compose seguro com healthcheck de DB, volume persistente, storage compartilhado e migração baseline.
3. Implementar API mínima e logging sanitizado; health 200, ready 200/503 por DB/storage, capabilities com verificações reais e cacheadas de FFmpeg/FFprobe.
4. Criar worker ocioso e independente com startup diagnostics e shutdown por sinais.
5. Implementar frontend mínimo funcional: identidade Edivo, tema, layout responsivo e status HTTP da API com estados de erro.
6. Criar testes de backend/frontend e documentação do fluxo local e das limitações da R01.

VALIDAÇÃO OBRIGATÓRIA
Executar, na medida permitida pelo ambiente, docker compose config, build/up/ps, endpoints HTTP, migrations Alembic, testes pytest, Ruff, Vitest, lint/build frontend e diagnóstico FFmpeg/FFprobe nos processos relevantes. Verificar perda temporária do DB e readiness 503, persistência do volume, bind em loopback e parada limpa do worker. Um smoke técnico de 1 s gerado sinteticamente via FFmpeg pode verificar encoder real; não implementar render de mídia do usuário. Registrar erros e testes não executados de forma explícita.

RELATÓRIO FINAL
Organize a entrega em: resumo, alterações por arquivo, serviços e URLs, dependências/versões, resultado REAL dos testes/verificações, limitações/pendências e conclusão sobre critérios de aceite. Não declarar sucesso sem evidências. Não execute comandos Git de commit ou push. A gestão Git pertence ao usuário.
```

## 16. Referências públicas usadas para ajustar a R01

- Vite (requisito Node e setup): https://vite.dev/guide/
- Node.js releases (LTS): https://nodejs.org/pt/about/previous-releases/
- Docker Compose (`depends_on`/healthcheck): https://docs.docker.com/compose/how-tos/startup-order
- FastAPI lifespan: https://fastapi.tiangolo.com/advanced/events/
- FFprobe: https://ffmpeg.org/ffprobe.html
- Docker Hub postgres tags: https://hub.docker.com/_/postgres/tags?name=16&page=1

**Estado real em 16/10/2026:** R01 especificada, sem arquivos do repositório implementados, sem containers inicializados, sem testes executados. O próximo evento é executar a implementação da R01 sob autorização do usuário.
