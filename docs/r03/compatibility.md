# R03.0 — auditoria local e gate de compatibilidade

Auditoria realizada antes de alterar código. Os 12 documentos obrigatórios
estão presentes e foram lidos, junto aos modelos, contratos, routers, serviços,
storage, migrações, navegação, testes, Compose e ferramentas locais.
Git estava sem alterações rastreadas; somente a especificação R03 era nova.
Não existe incompatibilidade arquitetural essencial que impeça avançar.

O status de planejamento da especificação e o limite R02 do AGENTS são
históricos: a autorização expressa do usuário nesta conversa libera a R03.
Os documentos de origem e os aceites R01/R02 permanecem preservados.

| Contrato local R02 | Compatibilidade e decisão R03 |
| --- | --- |
| Project UUID, soft delete, target_preset opcional | Reusar consulta ativa e lock de projeto; preset do cadastro sugere somente o primeiro plano |
| MediaAsset com duração ms, FPS racional e key privada | Reusar inventário e associação; bytes somente pelo endpoint privado de stream |
| Storage UUID/source.mp4, originais 0400 | Reader adicional abre cada componente com dir_fd/O_NOFOLLOW, verifica arquivo regular e tamanho; não modifica upload/original |
| Starlette 1.8 FileResponse | Reusar parser, chunks de 64 KiB e cancelamento; wrapper fixa descritor e rejeita ranges inválidos/múltiplos com 416; não reabrir path validado |
| Erros `{code,message,request_id}`, request ID | Preservar envelope; 416 inclui Content-Range; acrescentar 409 edit_spec_conflict |
| SQLAlchemy síncrono, expire_on_commit=False, timeout SQL 3 s | Transação curta com FOR UPDATE em Project, expected_revision e unique por projeto; não manter conexão durante stream |
| Alembic 0001 → 0002, tabelas projects/media_assets | Acrescentar somente 0003/edit_spec_revisions; não reescrever migrações anteriores |
| Hash routes lista/detalhe | Expandir para `#/projects/{id}/editor`; manter arquitetura e clientes `/api`, sem dependência de roteamento nova |
| Biblioteca paginada e codecs MP4 variados | Preservar upload/lista; editor carrega páginas/fonte salva e informa erro real de codec/rede |
| Tema, fontes locais e componentes usados | Reusar identidade e variáveis; editor responsivo e controles de teclado/toque |
| API 8001, frontend 5174, db/worker privados | Manter portas, mounts e rede; nenhum serviço ObraControl será alterado |
| Worker diagnóstico e ociosidade | Permanece independente, sem fila ou render |

## Decisões de contrato fechadas antes da implementação

- PUT edit-spec recebe os campos completos de EditSpec v1 no topo mais
  `expected_revision`. Resposta acrescenta `id`, `project_id`,
  `revision_number` e `created_at` aos campos validados; sem envelope redundante.
- Primeira revisão: 201; posteriores: 200. Mesmo conteúdo com expected_revision
  atual é no-op 200, mantendo ID/número/data. Expected desatualizado sempre
  retorna 409, inclusive se o conteúdo for idêntico.
- Tempos/revisões/schema são inteiros estritos, sem bool/coerção de strings.
  Focais são números finitos em [0,1]. Trim mínimo 100 ms; mídia com duração
  inferior permite somente seu intervalo completo [0,duração].
- Revisões append-only, com proteção ORM e trigger na nova tabela PostgreSQL;
  não há endpoints de histórico, edição ou exclusão de revisões.
- Range único de bytes, fechado/aberto/suffix. Inválido, múltiplo ou
  insatisfatível: 416 com tamanho total. HEAD aplica os mesmos gates/headers,
  sem corpo. If-Range usa semântica nativa com ETag forte derivado do SHA-256;
  mismatch/weak produz 200 completo. A validação estrita de Range ocorre antes.
- Rotação/SAR/VFR e codec são limites reais do preview HTML5. Usar eventos e
  dimensões nativas; não prometer frame-perfect ou equivalência com render.

## Sequência de validação

R03.1 reader/Range/HEAD → R03.2 migração/EditSpec/conflito → R03.3 player real
→ R03.4 timeline/presets/salvamento → R03.5 regressão, HTTP/PostgreSQL/browser,
integridade e documentação final. Cada gate registra resultados executados.
As contagens históricas 167 backend/37 frontend são referência, não resultados
presumidos da R03. Snapshot local de hashes/documentos/locks e dos 13 arquivos
existentes foi guardado antes de qualquer alteração.

## Gate R03.1 executado

- `.venv/bin/pytest -q`: **241 pass**, incluindo 74 casos novos de streaming
  e as 167 regressões. Um aviso upstream Starlette/TestClient já existente.
- Ruff check/format nos quatro arquivos de streaming: aprovados.
- Build API e startup api/worker/migrate: aprovados, sem nova tabela ainda.
- `python3 scripts/acceptance-r03.py`: **29 verificações HTTP reais aprovadas**,
  com MP4 sintético, GET/HEAD/Range/If-Range, bytes/headers, isolamento por projeto,
  desconexão e hash/permissões preservados. Projetos QA excluídos logicamente.
- R03.2 liberada após esses resultados; frontend permanece sem alterações.

## Gate R03.2 executado

- Pytest completo: **315 pass** (167 R01/R02, 74 streaming, 74 EditSpec);
  Ruff check e format check aprovados, 50 arquivos Python conformes.
- `0003_r03_edit_spec_revisions` aplicada no PostgreSQL existente; upgrade
  repetido aprovado e Alembic check sem operações novas. Migrações antigas intactas.
- `scripts/acceptance-editspec.py`: **14 verificações reais aprovadas**,
  incluindo 28 payloads inválidos, revisão idêntica/no-op, independência do
  preset de cadastro, fonte real de 34 ms e disputa simultânea **200/409**.
- Trigger PostgreSQL bloqueou UPDATE/DELETE (SQLSTATE 55000); unique, FKs e
  checks foram comprovados em transações revertidas. Snapshot de revisões,
  SHA-256, permissões 0400 e edição atual persistiram após restart db/API/worker.
- Três projetos sintéticos foram excluídos logicamente, preservando seus três
  originais e quatro revisões. R03.3 liberada após todos esses resultados.

## Gate R03.3 executado

- Vitest completo: **52 pass** (37 regressões e 15 casos do player/editor);
  ESLint, Prettier e build TypeScript/Vite aprovados.
- `scripts/browser-r03.py --stage player`: **10 verificações reais aprovadas**
  em Chromium 145.0.7632.6. MP4 H.264/AAC e MP4 vertical sem áudio reproduziram
  com avanço do tempo nativo e frames decodificados; seek e Range 206 observados.
- Metadados, ausência de autoplay, navegação biblioteca/editor e geometria
  desktop validados. Nenhum erro JavaScript não tratado. Evidência durável:
  `evidence/browser-player.json` e captura desktop escura correspondente.
- Durante a interrupção da sessão os serviços ficaram parados; foram retomados
  em 18/10/2026, sem recriar volumes ou reinstalar dependências da aplicação.
  A evidência anterior foi recuperada do repositório; arquivos QA temporários
  perdidos serão regenerados por scripts versionados. R03.4 liberada.

## Gate R03.4 executado

- Frontend completo: **75 testes Vitest aprovados**, incluindo as 37 regressões;
  ESLint, Prettier, TypeScript strict e build Vite aprovados no host e container.
- `browser-r03.py --stage editor`: **28 verificações reais aprovadas**, com
  seis variações H.264 reproduzidas, codec incompatível e rede offline reais,
  timeline por mouse/teclado/toque, pausa observada em 1,500 s, quatro presets,
  cover/contain/focal, save PostgreSQL, recarga e conflito 409 sem sobrescrita.
- Descarte/cancelamento de fonte e navegação, beforeunload, temas persistidos,
  mobile 375 px e landscape validados. Capturas desktop/mobile inspecionadas.
- A primeira tentativa offline reutilizou bytes do cache de mídia em memória;
  o harness passou a usar contexto novo e fonte não carregada. Sem alteração
  na aplicação para simular erro. Evidência final `evidence/browser-editor.json`.
- R03.5 liberada para prova final de persistência, auditoria e documentação.

## Gate R03.5 executado

- R01/R02/R03: **315 pytest e 75 Vitest aprovados**, Ruff check/format,
  ESLint, Prettier, TypeScript e Vite aprovados nas imagens atuais.
- Compose **27**, regressão HTTP R02 **13**, limites de upload **19**, streaming
  **29** e EditSpec real **14** verificações aprovadas. Alembic 0003 no head,
  upgrade repetível e check sem diferença. FFmpeg sintético aprovado API/worker.
- Rodada final do editor: **29 verificações**, acrescentando botão Voltar nativo
  cancelado com draft/componente/seek preservados. O harness aguarda o evento
  pause assíncrono; não simula eventos de vídeo na prova integrada.
- Restart controlado somente Edivo: revisão **8** recuperada integralmente;
  browser final **5 verificações aprovadas**, playback e Range 206 reais.
- Projeto QA excluído logicamente após a prova, preservando 7 originais e
  8 revisões. Auditoria final: **30/30 originais**, todos 0400, nenhum temporário,
  órfão ou checksum divergente. Oito projetos/13 mídias preexistentes e todos
  os documentos históricos/locks conferidos sem alterações.
- README, arquitetura, roadmap, aceite A–H, QA JSON e evidências entregues.
  Nenhuma R04+ iniciada; ObraControl e gerenciamento manual do Git preservados.
