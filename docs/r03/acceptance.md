# Edivo — aceite R03

Estado: **concluída tecnicamente; critérios essenciais validados**. Escopo autorizado: player, timeline de uma
faixa, cortes não destrutivos, presets e EditSpec versionado. Nenhuma R04 foi
iniciada. A matriz anterior às alterações está em [compatibility.md](compatibility.md).
Os documentos operacionais originais e os aceites R01/R02 foram preservados.

## A. Implementação e arquitetura

O monorepo mantém API/serviços/repositórios/schemas/modelos separados, frontend
por funcionalidades e worker independente. Acréscimos centrais:

| Área | Arquivos e responsabilidade |
| --- | --- |
| Streaming | `backend/app/api/media_stream.py`, router de mídia e reader em `storage/local.py`: autorização, descritor seguro e Range nativo |
| EditSpec | `models/edit_spec_revision.py`, `schemas/edit_spec.py`, `repositories/edit_specs.py`, `services/edit_specs.py`, `api/v1/edit_specs.py` |
| Banco | `backend/alembic/versions/0003_r03_edit_spec_revisions.py`; registro de metadata sem reescrever migrações anteriores |
| Editor | `frontend/src/features/editor/{editor-workspace,video-player,timeline}.tsx`, `editor.css` e cliente `lib/api/edit-spec.ts` |
| Navegação | Extensão das rotas por hash em projetos/biblioteca e shell compacto do editor |
| Testes | `backend/tests/test_media_stream.py`, `test_edit_spec.py`; `frontend/tests/editor.test.tsx`, `editor-controls.test.tsx` |
| QA reproduzível | `scripts/acceptance-r03.py`, `acceptance-editspec.py`, `generate-r03-fixtures.py`, `browser-r03.py`, `browser-r03.mjs` |

README, arquitetura, roadmap, AGENTS e aceitação operacional Compose foram
atualizados para a etapa. Imagens `edivo-backend:r03`/`edivo-frontend:r03` mantêm
os serviços db, migrate, api, worker e frontend. Portas externas 8001/5174 somente
em loopback; internas 8000/5173. Banco e worker não publicam portas.
PostgreSQL permanece no volume `edivo_postgres_data`; storage é privado e
compartilhado pela API/worker. Nenhuma dependência do produto foi acrescentada.

## B. Banco, concorrência e integridade

`0003_r03_edit_spec_revisions` depende de `0002_r02_projects_media` e cria a
tabela de revisões com UUID, FKs RESTRICT, sequência por projeto, schema 1,
JSONB e timestamp UTC. Índices e unique `(project_id, revision_number)` evitam
duplicação. A baseline `0001_r01_baseline` e a migração R02 permanecem intactas.

O serviço adquire `FOR UPDATE` no projeto antes de consultar a versão atual.
O primeiro save exige revisão esperada 0; os seguintes exigem a atual.
Conteúdo idêntico com versão atual é no-op 200 (mesmo ID, número e data).
Uma versão esperada antiga sempre retorna 409, inclusive para conteúdo igual.
Revisões anteriores permanecem imutáveis por proteção ORM e trigger PostgreSQL.

A prova real R03.2 registrou concorrência simultânea **200/409**, quatro revisões
preservadas, trigger UPDATE/DELETE rejeitando SQLSTATE 55000, unique/FK/checks
rejeitando entradas inválidas e persistência após restart de db/api/worker.
Provas SQL negativas foram revertidas; não alteraram registros de usuário.
Veja [edit-spec-http.json](evidence/edit-spec-http.json).

Os oito projetos e 13 registros de mídia anteriores à R03 tiveram seus hashes
de linha conferidos: todos iguais ao snapshot inicial. Documentos e evidências
históricas, migrações antigas e locks foram comparados por SHA-256 e Git somente
leitura. Veja [preservation.json](evidence/preservation.json). O snapshot
temporário perdido na interrupção não foi apresentado como evidência disponível.

## C. Streaming HTTP e segurança

| Endpoint | Comportamento |
| --- | --- |
| `GET /api/v1/projects/{project_id}/media/{media_id}/stream` | 200 completo; 206 para Range único válido |
| `HEAD` no mesmo caminho | Mesmos gates/cabeçalhos; sem corpo |
| `GET /api/v1/projects/{project_id}/edit-spec` | 200 atual; 404 `edit_spec_not_found` se nunca salvo |
| `PUT` no mesmo caminho | 201 primeira revisão, 200 posterior/no-op; 409 `edit_spec_conflict` |

Range fechado, aberto e suffix usam o parser/streaming de FileResponse
Starlette 1.8, com chunks de 64 KiB. Range inválido, múltiplo ou insatisfatível
retorna 416 com envelope R01 e `Content-Range: bytes */total`. `If-Range` com
ETag forte SHA-256/Last-Modified correspondente permite 206; divergência ou ETag
fraca retorna 200. Range malformado é rejeitado antes de If-Range.

Mídia usa `video/mp4`, `Accept-Ranges: bytes`, comprimento/intervalo corretos,
`Cache-Control: private, no-store`, `Content-Disposition: inline` e `nosniff`.
Projeto inexistente/excluído ou associação inválida retorna 404; arquivo
ausente/inacessível retorna 503 sanitizado, sem caminhos ou stack trace.

O reader exige a chave UUID persistida exata, abre cada componente com
`dir_fd/O_NOFOLLOW` e verifica arquivo regular/tamanho. Não aceita paths do
browser, symlinks ou escapes. O descritor fica fixo até finalizar a resposta;
pathsend é desativado e fechamento é protegido mesmo em HEAD, erro e cancelamento.
A sessão PostgreSQL é liberada antes de transferir os bytes. O storage continua
sem rota estática. Testes incluem troca de pathname após abertura e desconexão.

## D. Editor e salvamento

`#/projects/{uuid}/editor` abre fontes da biblioteca, incluindo fonte salva fora
da primeira página. Projetos sem vídeos mostram orientação para importar.
O `<video preload="metadata">` usa URL relativa privada, Play/Pause, seek,
tempo nativo e estados reais de buffering/erro; não existe contador simulado.

A timeline tem uma faixa, playhead, região selecionada e marcadores de entrada
e saída. Clique/toque busca; arrasto, setas, Shift, Home/End e campos inteiros
em ms ajustam o trecho. Mínimo 100 ms, ou toda a duração para fontes menores.
Ao reproduzir, o player volta à entrada se necessário e pausa na saída.

Quatro presets: 9:16, 1:1, 4:5 e 16:9. Cover recorta visualmente e usa focal
normalizado; contain mostra a fonte inteira com fundo neutro e preserva o focal
sem aplicá-lo. As resoluções de referência não representam arquivo gerado.
O preset do projeto sugere somente o primeiro plano.

Save é explícito, completo e versionado. Playhead não marca dirty nem gera
revisão. Estados salvo/alterado/salvando/falha são distintos. Conflito mantém
o draft e exige confirmação para recarregar a versão atual. Navegação/troca de
fonte e beforeunload protegem alterações. Trocar fonte reinicia o trim e mantém
o enquadramento, conforme a confirmação apresentada.

## E. UX e navegador

O gate do player passou em Chromium **145.0.7632.6**: vídeo H.264/AAC horizontal
e vertical sem áudio tiveram avanço de currentTime e frames reais decodificados;
seek, ausência de autoplay e requisições Range 206 foram observados. Nenhum erro
JavaScript não tratado. Veja [browser-player.json](evidence/browser-player.json).

O fluxo completo final passou em **29 verificações**: seis variações compatíveis
reproduzidas, codec MPEG-4 Part 2 rejeitado pelo decoder nativo (erro 4),
indisponibilidade/recuperação de rede, arrasto real, teclado, pausa na saída,
quatro proporções CSS, cover/contain/focal, save 201, reabertura e conflito 409.
O navegador respeitou rotação de 90° (360×640) e SAR 2:1 (640×240) nas amostras.
Descarte de alterações, botão Voltar nativo cancelado sem perder o player/draft/seek,
beforeunload, temas persistidos, toque em viewport
375 px e landscape também passaram. Veja [browser-editor.json](evidence/browser-editor.json).
As capturas desktop escura e mobile clara foram inspecionadas visualmente.
Após reiniciar db, aguardar sua saúde e reiniciar api/worker, o navegador passou
nas **5 verificações finais**: mesma revisão **8** e UUID, trim **1001–2600 ms**,
9:16, cover e focais 0,25/0,75 recuperados. A reprodução avançou 0,477 s com
49 frames e 15.662 bytes de áudio decodificados; Range 206 observado e nenhum
erro não tratado. Veja [browser-verify.json](evidence/browser-verify.json) e
[reinício controlado](evidence/restart-persistence.json).

O projeto sintético foi excluído logicamente depois da prova; seus **7 originais
e 8 revisões** permaneceram iguais, com acesso HTTP bloqueado por 404. A auditoria
final encontrou **30 mídias registradas e 30 originais**, sem ausências,
divergências, órfãos, symlinks ou temporários. Todos os originais mantiveram modo
0400, totalizando 3.489.278 bytes. Os 13 anteriores à R03 continuam preservados.
Veja [storage-audit.json](evidence/storage-audit.json),
[security-integrity.json](evidence/security-integrity.json) e
[limpeza lógica de QA](evidence/browser-cleanup.json).

## F. Testes e QA

Resultados efetivamente executados até este ponto:

| Verificação | Resultado |
| --- | --- |
| Pytest host, R01/R02/R03 | 315 aprovados: 167 regressões, 74 streaming e 74 EditSpec |
| Ruff backend | Check e format aprovados, 50 arquivos |
| Vitest/Testing Library | 75 aprovados, incluindo as 37 regressões |
| ESLint, Prettier, TypeScript e Vite | Aprovados; nova imagem frontend construída |
| HTTP streaming real | 29 verificações aprovadas |
| HTTP/PostgreSQL EditSpec real | 14 verificações aprovadas; 28 payloads negativos e disputa simultânea |
| Regressão HTTP R02 | 13 verificações aprovadas, com restart e indisponibilidade controlada do banco |
| Limites/erros de upload reais R02 | 19 verificações aprovadas em configuração isolada, sem alterar os defaults |
| Compose operacional | 27 verificações aprovadas, incluindo QA dentro das imagens finais |
| FFmpeg/FFprobe API e worker | Smoke sintético de 1 s, H.264/AAC, quatro filtros e decode aprovados nos dois runtimes |
| Alembic | 0003 aplicada, upgrade repetível e check sem operações novas |
| Chromium gate do player | 10 verificações aprovadas |
| Chromium editor completo | 29 verificações aprovadas |
| Chromium após restart final | 5 verificações aprovadas, revisão 8 intacta e reprodução real |
| Auditoria final | 30/30 originais consistentes; temporários/órfãos/divergências zero; todos 0400 |

Comandos completos, runtime, verificações operacionais e resultados finais
estão registrados em `qa-results.json`. Um aviso upstream Starlette/TestClient-httpx
permanece; não provocou falhas. TestClient bloqueado pelo sandbox foi repetido
fora dele; o resultado aprovado corresponde à execução completa.

Runtime verificado: Python **3.12.14** nos containers (**3.12.3** na venv
preservada), FastAPI **0.143.3**, Starlette **1.8.0**, SQLAlchemy **2.0.55**,
Alembic **1.20.0**, PostgreSQL **16.13**, uv **0.13.1**; Node **24.14.0**,
npm **11.9.0**, React **19.3.1**, TypeScript **6.0.3**, Vite **8.3.4**,
Tailwind **4.3.3**, Vitest **5.0.5**; Docker Engine **29.8.0**, Compose **5.5.1**.
FFmpeg e FFprobe **5.1.9-0+deb12u1**, libx264/AAC e filtros
scale/crop/pad/drawtext disponíveis. Demais versões e saídas estão em
[backend-qa.json](evidence/backend-qa.json).

Comandos para reproduzir as verificações executadas; opções e resultados
de cada execução estão no JSON:

```bash
# Em backend/, reutilizando a venv existente
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
# Em frontend/
npm run test -- --run
npm run lint
npm run format:check
npm run build
# Na raiz
docker compose config --quiet
docker compose build api
docker compose build frontend
docker compose up -d --no-build --wait --wait-timeout 180
docker compose exec -T api alembic current
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic check
python3 scripts/acceptance-r03.py
python3 scripts/acceptance-editspec.py --output docs/r03/evidence/edit-spec-http.json
python3 scripts/acceptance-r02.py
python3 scripts/acceptance-upload-limits.py
python3 scripts/acceptance-compose.py --skip-build
python3 scripts/generate-r03-fixtures.py
python3 scripts/browser-r03.py --stage player
python3 scripts/browser-r03.py --stage editor
python3 scripts/browser-r03.py --stage verify
docker compose exec -T api python - < scripts/audit-storage.py
```

Os scripts operacionais executaram ainda pytest/Ruff/Vitest/ESLint/Prettier/build
dentro dos containers, provas sintéticas FFmpeg nos dois runtimes, HTTP real e
interrupções controladas com recuperação. Não se somam execuções repetidas como
novos casos de teste: são **315 casos backend e 75 frontend**.

Limitações e verificações não executadas:

- Chromium headless oficial em Docker; Firefox/WebKit, outros sistemas/decoders
  e dispositivos físicos não foram testados. Teclado é real do navegador;
  toque e orientação de viewport foram emulados, sem alegar prova em hardware.
- Seek, pausa e preview CSS dependem de keyframes, VFR, SAR/rotação e decoder.
  Não existe garantia frame-perfect nem saída renderizada. MPEG-4 Part 2 foi
  corretamente informado como incompatível neste Chromium.
- A validação R02 continua limitada ao prefixo decodificado; não prova cada
  frame de um arquivo. Transferência física de 500 MiB, fonte de 600 s e carga
  concorrente extensa não foram medidas. Limites padrão/configuração e cenários
  reduzidos de bytes/duração foram testados realmente em 19 verificações.
- Não há atomicidade entre filesystem/PostgreSQL nem teste SIGKILL em cada
  ponto do upload; o comportamento R02 e a auditoria somente leitura permanecem.
- O primeiro teste offline precisou de contexto novo para evitar cache de mídia;
  outra tentativa precisou aguardar o evento assíncrono pause. As falhas do
  harness e as execuções finais aprovadas estão preservadas, sem simular sucesso.

Não restam falhas essenciais ou pendências técnicas da R03. O ObraControl foi
observado parado ao retomar, com os mesmos três IDs originais; nenhuma ação de
ciclo de vida ou configuração foi executada nele. Nenhuma operação Git mutante
foi executada. `.env` permanece 0600 e os diretórios de storage 0700; logs API/
worker foram conferidos sem senha, DSN ou stack trace.

## G. Critérios de aceite

- [x] R01/R02 mantidas na regressão automatizada e HTTP.
- [x] Migração R03 aplicada incrementalmente, sem apagar dados.
- [x] Streaming privado suporta HTTP Range.
- [x] Acesso indevido, symlink e traversal bloqueados.
- [x] MP4 reproduziu realmente no navegador.
- [x] Play/Pause e seek reais comprovados.
- [x] Timeline de uma faixa validada no fluxo integrado.
- [x] Entrada e saída validadas no navegador.
- [x] Quatro presets validados no navegador.
- [x] Cover/contain/focal validados no navegador.
- [x] Save/reabertura completos comprovados no navegador e após restart.
- [x] Revisões anteriores não sobrescritas.
- [x] Concorrência retorna 409.
- [x] Dados/originais anteriores preservados; auditoria final consistente 30/30.
- [x] QA automatizado/operacional e integrado final aprovado.
- [x] Navegador final validado com mídia sintética.
- [x] Documentação e todas as evidências finais entregues.
- [x] Nenhuma funcionalidade R04+ antecipada.

## H. Próxima etapa

Fundação R01/R02 e editor R03 estão tecnicamente preparados para a R04,
respeitando as limitações documentadas. A R04 não foi iniciada e exige nova
autorização. Git permanece sob gerenciamento manual do usuário.
