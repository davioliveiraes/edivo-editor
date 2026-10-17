# Edivo — convenções do repositório

## Fontes de verdade

Leia integralmente `docs/EDIVO_DOCUMENTACAO_PROJETO.md`,
`docs/EDIVO_INSTRUCOES_CHATGPT.md`, `docs/R00_EDIVO_PLANEJAMENTO_TECNICO.md`
e `docs/R01_EDIVO_ESPECIFICACAO_OPERACIONAL.md` antes de implementar. Leia também
`docs/R02_EDIVO_ESPECIFICACAO_OPERACIONAL.md`, README e evidências de `docs/r01/`.
A especificação operacional R02 define o escopo atual autorizado; R00 define a
arquitetura e R01 deve permanecer funcional. Preserve os documentos de origem,
as evidências históricas e alterações preexistentes.

## Escopo atual: R02 — projetos, upload e metadados

Preservar a fundação executável: React/TypeScript strict/Vite,
FastAPI/Python 3.12, PostgreSQL 16/SQLAlchemy 2/psycopg 3/Alembic baseline,
worker independente ocioso, diagnóstico FFmpeg/FFprobe e Docker Compose local.
A R02 autoriza Project com exclusão lógica, MediaAsset, migração incremental,
CRUD de projetos, upload de um MP4 com validação segura e FFprobe, SHA-256,
originais imutáveis e interface de projetos/biblioteca. Limites padrão: 500 MiB
de arquivo e 600 segundos, com controle do corpo durante a entrada e parsing
multipart limitado. Não expor caminhos privados ou storage como conteúdo estático.
Não implementar player/streaming, timeline, cortes, edição de áudio, preview,
render, jobs, exportações, autenticação, Redis, Celery, heditor, agentes de IA ou
Higgsfield. R03 exige nova autorização.
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
