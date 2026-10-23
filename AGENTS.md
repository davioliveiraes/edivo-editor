# Edivo — convenções do repositório

## Fontes de verdade

Leia integralmente `docs/EDIVO_DOCUMENTACAO_PROJETO.md`,
`docs/EDIVO_INSTRUCOES_CHATGPT.md`, `docs/R00_EDIVO_PLANEJAMENTO_TECNICO.md`
e `docs/R01_EDIVO_ESPECIFICACAO_OPERACIONAL.md` antes de implementar. Leia também
`docs/R02_EDIVO_ESPECIFICACAO_OPERACIONAL.md`,
`docs/R03_EDIVO_ESPECIFICACAO_OPERACIONAL.md`,
`docs/R04_EDIVO_ESPECIFICACAO_OPERACIONAL.md`,
`docs/R05_EDIVO_ESPECIFICACAO_OPERACIONAL.md`, README, arquitetura, roadmap e
evidências históricas R01–R04. A especificação operacional R05 e a autorização expressa do
usuário definem o escopo atual; R00 define a arquitetura e R01–R04 permanecem
funcionais. Preserve os documentos de origem,
as evidências históricas e alterações preexistentes.

## Escopo atual: R05 — renderização, exportação e histórico

Preservar a fundação executável: React/TypeScript strict/Vite,
FastAPI/Python 3.12, PostgreSQL 16/SQLAlchemy 2/psycopg 3/Alembic baseline,
worker independente com espera ociosa quando sem tarefas, diagnóstico FFmpeg/FFprobe e Docker Compose local.
A R02 autoriza Project com exclusão lógica, MediaAsset, migração incremental,
CRUD de projetos, upload de um MP4 com validação segura e FFprobe, SHA-256,
originais imutáveis e interface de projetos/biblioteca. Limites padrão: 500 MiB
de arquivo e 600 segundos, com controle do corpo durante a entrada e parsing
multipart limitado. Não expor caminhos privados ou storage como conteúdo estático.
A R03 acrescenta streaming GET/HEAD privado com Range, player HTML5 real,
timeline de uma faixa, trim não destrutivo, quatro presets e cover/contain com
focal normalizado, EditSpec v1 com revisões imutáveis e conflitos 409. Salvar é
explícito; playhead não gera revisão. A R04 autoriza EditSpec v2 compatível com v1,
até três textos, MP3 em AudioAsset separado (30 MiB/600 s), controles de volume,
loop/fades e prévias privadas reais de até 8 s. PreviewJob usa PostgreSQL,
SKIP LOCKED, lease/heartbeat, fencing e até duas tentativas; codificação só no
worker independente. Preservar revisões v1 e trigger; migration nova 0004 aceita
schema 1/2, sem regravar snapshots. Seguir gates R04.0–R04.5 e a matriz em
`docs/r04/compatibility.md`. A R05 autorizada acrescenta RenderJob persistente,
exportação completa de revisão salva v1/v2, perfis standard/high em H.264/AAC
30 fps, progresso real, cancelamento, download privado GET/HEAD com Range e
histórico paginado. Reutilizar compositor, supervisor e fila PostgreSQL R04;
um scheduler e um FFmpeg por worker, lease/heartbeat/fencing/retry finito.
Limitar admissão a um render ativo por projeto e 20 no total por padrão
(`EDIVO_RENDER_MAX_ACTIVE_TOTAL`, 1–1000); COUNT/INSERT de novas solicitações
serializados por advisory lock PostgreSQL, preservando replay/reuso no teto.
Seguir os gates R05.0–R05.5 e `docs/r05/compatibility.md`, preservando baseline,
revisões, originais, prévias e exports concluídos. Mesma chave idempotente tem
identidade permanente; falha/cancelamento requer nova solicitação explícita.
Não publicar antes de validação integral e commit confirmado. Cancelamento é
intenção durável; só o worker encerra seu próprio grupo de processos.
O produto será orientado por prompts futuramente, mas IA/heditor/agentes/Higgsfield
não entram agora. Não implementar multitrilha, legendas automáticas, autenticação,
Redis/Celery, infraestrutura pública ou retenção destrutiva. R06 exige nova
autorização. Não alterar ObraControl.
Não criar abstrações ou módulos vazios para funcionalidades futuras.

## Implementação e qualidade

- Separar HTTP (`app/api`), configuração e logging (`app/core`), banco
  (`app/db`), serviços, integração FFmpeg e ciclo de vida do worker.
- API e worker compartilham pacote/imagem, executando em processos separados.
- Usar `uv`, `pyproject.toml` e `uv.lock`; conferir e preservar `backend/.venv`
  adequado antes de instalar. Usar npm e `package-lock.json` no frontend.
- Executar pytest, Ruff check/format, Vitest, ESLint, Prettier e build
  proporcionais à alteração. Registrar comandos e resultados realmente obtidos.
- Não apresentar mocks de testes como integração real nem declarar validações
  Docker/FFmpeg/banco aprovadas sem execução no runtime correspondente.
- Interface em português, acessível, responsiva, com temas persistidos e
  estados reais de conectividade; navegador usa caminhos relativos `/api`.

## Segurança e dados

- Publicar API/frontend somente em `127.0.0.1`, com portas de host configuráveis
  por `EDIVO_API_PORT`/`EDIVO_FRONTEND_PORT` (defaults 8000/5173). O usuário
  autorizou 8001/5174 no `.env` local para coexistir com outro projeto. Banco e
  worker sem portas no host. Não montar diretórios privados nem expor storage
  como estático.
- Segredos somente em `.env` local ignorado; nunca imprimir senha, DSN,
  caminhos privados ou stack traces em respostas ou logs da aplicação.
- Subprocessos com argumentos estruturados, timeout e sem `shell=True`.
- Containers sem privilégios desnecessários; respeitar UID/GID do storage.
- Preservar originais, arquivos existentes e volumes; tarefas destrutivas
  exigem autorização explícita. Nunca executar `down -v` automaticamente.

## Git manual

O usuário gerencia Git. Não inicializar repositório, trocar/criar branches,
fazer commits, alterar histórico, resetar, mesclar, publicar ou executar push.
Inspeções somente leitura são permitidas. Não contornar `.git` indisponível.
Sugestão de mensagem de commit pode ser entregue separadamente após conclusão.
