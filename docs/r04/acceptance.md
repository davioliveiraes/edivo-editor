# Edivo — relatório de implementação e aceite R04

**Estado: R04 concluída tecnicamente, com os critérios essenciais validados.** A R04 entrega
textos, áudio MP3 e prévias locais curtas reais, preservando os contratos R01–R03.
Este relatório registra as provas executadas, as falhas encontradas e os limites
da validação, sem apresentar reexecuções como uma única rodada aprovada.
A matriz anterior às alterações está em [compatibility.md](compatibility.md).
Documentos e evidências históricos R00–R03 permanecem preservados.

## A. Implementação e arquitetura

O monorepo mantém API HTTP, schemas, serviços, repositórios, modelos, storage,
integração FFmpeg e worker separados. A API valida e agenda tarefas; o worker
independente codifica. Não há renderização em requests ou BackgroundTasks.

| Área | Componentes entregues |
| --- | --- |
| EditSpec | `schemas/edit_spec.py`, serviço/repositório e modelo de revisão: união v1/v2, validação, snapshots imutáveis e concorrência |
| Banco | Migration `0004_r04_text_audio_previews.py`, `models/audio_asset.py` e `models/preview_job.py` |
| MP3 | `api/v1/audio.py`, schemas/services/repositories de áudio, `integrations/ffmpeg/audio_probe.py`, limites de entrada e storage |
| Prévias | `api/v1/previews.py`, schemas/services de prévias, `repositories/preview_jobs.py` e publicação/leitura privadas |
| Worker | `worker/main.py`, `preview_worker.py`, `processor.py`, `supervisor.py` e launcher confiável `resource_exec.py` |
| Composição | `integrations/ffmpeg/preview.py` e `font_metrics.py`: perfis, enquadramento, texto, áudio, inspeção integral e hash |
| React | `text-panel.tsx`, `audio-panel.tsx`, `text-overlays.tsx`, `preview-panel.tsx`, integração editor/player e clientes `lib/api/{audio,edit-spec,previews}.ts` |
| Operação/QA | Compose R04, configurações de limites, testes, scripts de aceite/auditoria, README, arquitetura, roadmap, AGENTS e evidências |

Os caminhos backend da tabela são relativos a `backend/app`; os painéis ficam
em `frontend/src/features/editor`. API e worker usam `edivo-backend:r04`; a
interface usa `edivo-frontend:r04`. Serviços: db, migrate, api, worker e frontend.
API em `127.0.0.1:8001`, frontend em `127.0.0.1:5174`; internas 8000/5173.
Banco/worker sem portas publicadas. PostgreSQL permanece no volume
`edivo_postgres_data`; API/worker compartilham storage privado. Nenhuma nova
dependência de produto foi necessária para o compositor ou fila.

Versões observadas na imagem final: Python **3.12.14**, FastAPI **0.143.3**,
SQLAlchemy **2.0.55**, Node **24.14.0**, React **19.3.1** e Vite **8.3.4**;
Docker Server **29.8.0** e Compose **5.5.1**. A
[inspeção do runtime](evidence/runtime-final.json) registra as demais versões
efetivas e ausência de bytecode copiado do host.

## B. Banco, EditSpec e concorrência

`0004_r04_text_audio_previews` depende de `0003_r03_edit_spec_revisions`.
Substitui somente o CHECK de schema 1 por aceitação de 1/2 e cria `audio_assets`
e `preview_jobs`, com UUIDs, FKs RESTRICT, índices e constraints. Migrations
anteriores, snapshots v1 e trigger de imutabilidade não foram regravados.
Upgrade repetido e Alembic check sem deriva foram executados no PostgreSQL real,
inclusive na [regressão final consolidada](evidence/final-regression-summary.json).

GET de revisão v1 continua devolvendo v1. O frontend normaliza draft e baseline
em memória para v2, sem save automático ou dirty ao abrir/reproduzir/seek.
Uma gravação explícita persiste a alteração como nova revisão; conversão v1→v2
não reescreve o snapshot antigo. Alterar a versão do contrato é conteúdo novo.

V2 acrescenta até três textos e `audio`. Textos: UUIDs distintos, 1–120
caracteres, até duas linhas, tempos inteiros absolutos dentro do trim e tokens
fechados de posição/estilo/tamanho. Campos desconhecidos, tipos inválidos,
controles invisíveis, marcação e valores não finitos são rejeitados. Música
pertence ao mesmo projeto ativo. Volumes/fades são limitados; a soma dos fades
não ultrapassa a duração do trim.

PUT mantém `expected_revision` fora do snapshot. Primeiro save exige zero e
retorna 201; seguintes retornam 200. Conteúdo idêntico à revisão atual, com a
mesma versão de contrato e revisão esperada correta, é **no-op**: mesmo ID,
número e data. Revisão esperada antiga sempre retorna `409 edit_spec_conflict`,
inclusive para conteúdo idêntico. Lock `FOR UPDATE` no projeto serializa saves;
unique `(project_id,revision_number)` reforça integridade. ORM/trigger bloqueiam
UPDATE/DELETE; SQLSTATE 55000 e disputa real 200/409 foram comprovados.
Não há endpoint de modificação/restauração do histórico.

Provas: [v1/v2 e áudio](evidence/edit-spec-audio.json),
[storage/banco](evidence/audio-storage-database.json) e
[regressão final EditSpec R03](evidence/r03-edit-spec-final.json).

## C. Áudio, importação e originais

`AudioAsset` é independente de `MediaAsset` MP4. Registra projeto, nome saneado,
chave privada, SHA-256, bytes reais, MIME, duração, codec MP3, sample rate, canais
e data UTC. Paths físicos e JSON bruto de FFprobe não são respostas públicas.

MP3 aceita um campo multipart `file`, até **30 MiB/600 s**, com limite próprio
na entrada ASGI, parser e staging. O contrato MP4 de até 500 MiB permanece.
Há controle com/sem Content-Length, partes/headers, chunks, reserva de disco,
SHA-256 incremental, timeouts e limpeza. Extensão/MIME não substituem conteúdo.

O inspetor percorre o framing integral e confronta duração com FFprobe e
Info/Xing; índices forjados não podem encurtar artificialmente a duração aceita.
Decodifica até dois segundos significativos: isso **não comprova a decodificação
integral de um original de 600 s**. Free-format, tags finais APE e vídeo/capa
são recusados. Promoção exclusiva usa `originals-audio/{project}/{audio}/source.mp3`,
modo **0400**, sem sobrescrita. Falhas conhecidas compensam somente a operação
atual; COMMIT incerto não autoriza apagar original. Soft delete preserva bytes.

Mix: original 0–200%, música 0–100%, uma música opcional, loop e fades de
0–3000 ms. Defaults: original 100%, música 15%, sem música/loop/fades. Fonte
sem áudio é válida. O player original limita volume a 100%; música/fades/ganho
superior são avaliados no MP4 separado. Sem normalização automática ou ducking.
A interface orienta usar música própria ou com licença; não concede direitos.

[Segurança MP3 HTTP](evidence/mp3-http-security.json): **18 verificações e 13
requisições reais**, incluindo 30 MiB + 1 byte chunked sem Content-Length,
multipart inesperado, falsificação, corrupção, capa e duração forjada, com
400/413/415/422 conforme o caso. A [matriz FFmpeg](evidence/ffmpeg-matrix-run-1/report.json)
mediu ganhos 0,250445/2,00160 para controles 0,25/2 e comprovou loop, silêncio
após música esgotada, fades e offset. Não houve escuta auditiva humana.

## D. Prévias, worker, FFmpeg e streaming

Endpoints R01–R03 permanecem. Acréscimos, relativos a `/api/v1/projects/{project_id}`:

| Método/rota | Resultado |
| --- | --- |
| `POST /audio` | 201, MP3 validado e preservado |
| `GET /audio` | 200, paginação `{items,total,limit,offset}` |
| `GET /audio/{audio_id}` | 200 metadados; 404 para associação inexistente/excluída |
| `POST /previews` | 202 novo/equivalente pendente; 200 para artefato equivalente íntegro |
| `GET /previews/{preview_id}` | 200 estado/etapa/revisão/janela, inclusive `failed` com código seguro |
| `GET /previews/{preview_id}/file` | 200 completo, 206 Range, 416 inválido/múltiplo/insatisfatível |
| `HEAD /previews/{preview_id}/file` | Mesmos gates/cabeçalhos, sem corpo |

Exemplo de solicitação vinculada à revisão imutável salva:

```json
{
  "edit_spec_revision_id": "UUID_DA_REVISAO_SALVA",
  "preview_start_ms": 5000,
  "duration_ms": 4000
}
```

Duração inteira de 1–8000 ms, início dentro do trim e fim truncado ao trim.
Até **dois jobs ativos por projeto**; excesso: `409 preview_active_limit`.
Arquivo pendente/falho: `409 preview_not_ready`; associação alheia/inexistente
ou projeto excluído: 404. Dependência/artefato indisponível na API: 503.
Envelope `{code,message,request_id}` preservado; sem stderr, comandos ou paths.

Idempotência inclui perfil/revisão/janela efetiva. Equivalentes pendentes usam um
job; concluído só é reutilizado após tamanho/SHA-256/0400 conferidos. Artefato
inválido não é servido. Novo POST explícito preserva o arquivo/registro anterior,
marca sua falha e cria outro UUID. Prévia antiga mantém a própria revisão.

O worker usa `FOR UPDATE SKIP LOCKED`, confirma o claim antes de processar e
executa uma tarefa por vez. Defaults: lease **30 s**, heartbeat **5 s**, polling
**1,5 s**, até **duas tentativas**. Token e lease válido cercam cada transição;
o relógio PostgreSQL é reavaliado após espera por lock. Crash/lease expirado
permite recuperação finita; falhas determinísticas são terminais.
SIGTERM/SIGINT encerram o grupo, aguardam filhos e impedem continuar sem posse.

Inputs fixados por descritor têm associação, SHA-256 e 0400 conferidos fora de
transação longa. FFmpeg usa argv estruturado, demuxers MOV/MP3, protocolos locais
e referências externas MOV desabilitadas. Textos UTF-8 privados usam
`expansion=none`, sem conteúdo cru em filtros. DejaVu Sans local tem SHA/licença
conhecidos; glifo ausente falha explicitamente. Linhas são centralizadas com
margem de 7%, sem truncar texto para caber.

Perfil: libx264/H.264, `yuv420p`, **24 fps**, AAC quando esperado e MP4 faststart.
Resoluções: 9:16 **540×960**, 1:1 **540×540**, 4:5 **544×680**, 16:9 **960×540**.
4:5 preserva proporção exata com dimensões pares, pois 540×675 tem altura ímpar.
Cover/contain, focal, autorrotação, SAR e PTS são tratados. Trim, textos e janela
usam tempos absolutos. Música começa no zero relativo do **trim completo**;
prévia intermediária usa o offset correto e não reinicia fades. Silêncio inicial
da fonte é preservado durante seek.

CFR quantiza limites em até um quadro de 41,667 ms, mais 1 ms de arredondamento;
1 ms pode resultar em um quadro/cerca de 42 ms. Máximo absoluto validado:
**8,001 s**. A borda do último 1 ms da fonte foi corrigida/testada; não há
promessa de precisão por frame.

Prazo global: **120 s**. Por subprocesso: AS 1 GiB, CPU 120 s, arquivo 64 MiB,
stdout/stderr combinados 256 KiB e threads limitadas. Worker Compose: **2 CPUs,
1536 MiB sem swap adicional, 128 PIDs**, Tini, sem root, raiz somente leitura,
capabilities removidas/no-new-privileges. Limites foram
[observados](evidence/worker-runtime-limits.json); [timeout/SIGTERM reais](evidence/ffmpeg-process-control.json)
usaram FFmpeg sintético isolado, sem sinalizar um job de usuário.

O compositor verifica FFprobe, codecs/dimensões/FPS/duração/SAR/áudio, faststart,
**decodificação integral com frames positivos**, tamanho e SHA-256. Publicação
bloqueia Project→PreviewJob, valida posse antes/depois do hardlink exclusivo e só
então confirma completed. COMMIT incerto não autoriza remover canônico. Destino
preexistente após interrupção é preservado e causa falha segura; uma solicitação
explícita cria outro UUID, sem adoção/sobrescrita. Sem retenção automática na R04.

GET/HEAD verificam projeto ativo, associação, revisão, job concluído, chave
exata e integridade. Abertura sem symlinks fixa o descritor; a transação termina
antes da transferência. O adaptador R03 usa chunks de 64 KiB, `video/mp4`,
Content-Length/Content-Range corretos, `Accept-Ranges: bytes`, `private, no-store`
e `nosniff`, sem StaticFiles ou leitor genérico. A [prova HTTP/worker](evidence/preview-http-worker.json)
passou **30 verificações**: concorrência equivalente, limite ativo, dois MP4
reais, Range/HEAD, revisão antiga e persistência após reinício.

## E. UX e navegador

Preservados player/timeline de uma faixa, trim, presets, focal, temas, hash,
dirty-state, confirmação de descarte e conflito 409. Textos CSS seguem
currentTime nativo e são identificados como referência de edição. Os painéis
adicionam/removem/configuram três textos, MP3, volumes, loop e fades.

**Gerar prévia** exige revisão salva/draft limpo, sem PUT implícito. Polling de
cerca de dois segundos acompanha estados reais e para ao sair/concluir/falhar,
sem percentual fictício. A referência de job por projeto é revalidada no reload.
Players separados usam eventos nativos para pausar um ao reproduzir o outro;
resultado mantém revisão/janela e indica alterações posteriores.

Chromium **145.0.7632.6**, container oficial Playwright:

- [Composição](evidence/browser-composition.json): **15 verificações** com MP4
  real, v1 aberto sem autosave/dirty, textos sincronizados, MP3 por XHR, controles,
  save v2 e recuperação da revisão 2 no reload.
- [Prévia](evidence/browser-preview.json): **11 verificações** com POST 202 de
  revisão salva, fila/running/completed reais, MP4 de 4 s reproduzido, frames e
  áudio decodificados, seek/Range 206, reload, temas e mobile 375/390 px. Nenhum
  erro JavaScript não tratado.
- [Verificação final após reinício](evidence/browser-verify.json): **7 verificações**,
  recuperando o mesmo job, revisão 2 e SHA-256 do artefato de **210.935 bytes**.
  Nenhum PUT/POST de salvamento ou geração foi necessário. Reprodução nativa
  decodificou 9 frames e 8.727 bytes de áudio; em mobile emulado 375/390 px,
  64 frames foram decodificados, sem overflow horizontal.
- [Composição visual](evidence/browser-composition-review.json) e
  [quadros FFmpeg](evidence/ffmpeg-visual-review.json): inspeção de posições,
  textos literais, estilos/margens, cover/contain, rotação e SAR.

Tentativas do roteiro com espera incorreta de pause e busca imprecisa de label
foram preservadas, corrigidas e repetidas, sem fabricar eventos de reprodução.
Somente Chromium headless foi testado; Firefox/WebKit e dispositivos físicos
não foram validados. Mobile/orientação/toque são emulados conforme cada prova.
CSS/FreeType podem diferir por pixels. `white_plain`, sem contorno, perde
contraste em cenas claras; há estilos com contorno. Texto longo pode ficar
pequeno, e glifos ausentes geram erro. Áudio foi avaliado por RMS/frequências/
picos: **sem escuta humana ou estudo subjetivo de inteligibilidade de fala**.

## F. Testes, comandos e resultados reais

Casos distintos por suíte; rodadas repetidas não são somadas. Provas HTTP,
FFmpeg e navegador são separadas. O [QA estruturado](qa-results.json) e a
[conciliação da regressão final](evidence/final-regression-summary.json) distinguem
o harness interrompido das verificações posteriores aprovadas. São **700 casos
backend distintos**: 692 da suíte padrão e oito PostgreSQL opt-in executados
separadamente; frontend possui 149 casos.

| Verificação | Resultado registrado | Evidência |
| --- | --- | --- |
| Pytest no runtime | **692 aprovados, 8 opt-in ignorados**, 1 aviso; 71,63 s | [Regressão consolidada](evidence/final-regression-summary.json) |
| PostgreSQL opt-in | **8 aprovados**, 1 aviso, 3,45 s; separados dos 692 | [Fila na imagem final](evidence/queue-postgres-final-image.json) |
| Vitest/RTL | **149 aprovados / 7 arquivos**, 15,93 s, sem mudar fontes entre reexecuções | [Frontend final](evidence/frontend-final-qa.json) |
| Ruff backend | Check e format aprovados, **79 arquivos** | [Regressão consolidada](evidence/final-regression-summary.json) |
| ESLint/Prettier/TypeScript/Vite | Todos aprovados na imagem final | [Frontend final](evidence/frontend-final-qa.json) |
| Operação R01/Alembic | **20 verificações operacionais** aprovadas; 0004 aplicada/repetida e check sem deriva | [Regressão consolidada](evidence/final-regression-summary.json) |
| MP3 HTTP | **18 verificações / 13 requisições reais** | [MP3](evidence/mp3-http-security.json) |
| Preview HTTP/worker | **30 verificações**, dois artefatos e restart | [HTTP/worker](evidence/preview-http-worker.json) |
| FFmpeg | **25 prévias / 43 verificações**, 14 PNGs, 32,682 s | [Matriz](evidence/ffmpeg-matrix-run-1/report.json) |
| Compositor | 69 casos incluídos no pytest completo; provas anteriores separadas | [Compositor](evidence/compositor-qa.json) |
| Processos | Timeout/SIGTERM reais, filhos esperados e grupo encerrado | [Processos](evidence/ffmpeg-process-control.json) |
| HTTP R02 | **13 verificações** aprovadas na rodada final | [R02](evidence/r02-regression-final.jsonl) |
| Limites MP4 | **19 verificações** aprovadas, sem alterar limite padrão de 500 MiB | [MP4](evidence/mp4-limits-final.json) |
| EditSpec R03 | **14 verificações** aprovadas na rodada final | [EditSpec](evidence/r03-edit-spec-final.json) |
| Streaming R03 | **29 verificações** aprovadas na rodada final | [Range](evidence/r03-range-final.jsonl) |
| Integração HTTP | **Cinco scripts aprovados**, incluindo os 30 casos R04 e reinícios | [Rodada final HTTP](evidence/final-http-regressions.json) |
| Chromium | **15 composição + 11 prévia + 7 verificação final** | [Composição](evidence/browser-composition.json), [prévia](evidence/browser-preview.json), [verificação](evidence/browser-verify.json) |
| Storage final | **83 MP4, 6 MP3 e 5 prévias** consistentes; zero temporários, entradas desconhecidas ou inválidas | [Auditoria final](evidence/storage-final.json) |

Comandos usados, com variantes/saídas integrais nas respectivas evidências:

```bash
docker compose config --quiet
docker compose build api
docker compose build frontend
docker compose up -d --no-build --wait --wait-timeout 180
docker compose exec -T api alembic upgrade head
docker compose exec -T api alembic check
docker compose exec -T api pytest -q
docker compose exec -T api ruff check .
docker compose exec -T api ruff format --check .
# Em frontend/, também executados nas rodadas nativas documentadas:
npm run test -- --run
npm run lint
npm run format:check
npm run build
# Na raiz:
python3 scripts/acceptance-r04-audio.py
python3 scripts/acceptance-r04-audio.py --inspect-storage
python3 scripts/acceptance-r04-mp3-security.py
python3 scripts/acceptance-r04-process-control.py
python3 scripts/acceptance-r04-previews.py --restart
python3 scripts/acceptance-r02.py
python3 scripts/acceptance-upload-limits.py
python3 scripts/acceptance-r03.py
python3 scripts/acceptance-editspec.py --output docs/r04/evidence/r03-edit-spec-final.json
python3 scripts/browser-r04.py --stage composition --project-id 09b152db-5ca2-4868-b23a-8a00e14cd96d
python3 scripts/browser-r04.py --stage preview --project-id 09b152db-5ca2-4868-b23a-8a00e14cd96d
python3 scripts/browser-r04.py --stage verify --project-id 09b152db-5ca2-4868-b23a-8a00e14cd96d
docker compose exec -T api python - < scripts/audit-storage-r04.py
python3 scripts/r04-preservation.py
```

Comando integral da matriz FFmpeg e opção PostgreSQL constam nas evidências.
Ferramentas rodaram em containers isolados ou no runtime existente; somente
amostras sintéticas foram usadas, sem dependências novas no produto.

Falhas e reparos preservados:

- Primeira regressão parou após 530 aprovados: fixture unitária tentou reservar
  64 MiB + 64 MiB no `/tmp` de 128 MiB. A fixture foi dimensionada para seu arquivo
  pequeno e ganhou caso de disco cheio. Defaults/storage de produção não mudaram;
  [12 testes do processor passaram isolados](evidence/processor-container-fixture.json).
- Bytecode do host copiado à imagem foi excluído recursivamente no `.dockerignore`.
  Imports de teste e classificação Ruff de `app` foram corrigidos explicitamente,
  sem refatorar migrations ou evidências históricas.
- Último 1 ms inicialmente gerava só áudio, recusado na validação. Preroll/fps
  foram corrigidos e janelas alinhadas/desalinhadas/finais passaram, sem publicar
  saída inválida. Ver [compositor](evidence/compositor-qa.json).
- O [harness bruto final](evidence/final-regression.jsonl) terminou com **exit 1
  no Vitest**, após 20 verificações operacionais, pytest e Ruff aprovados. O
  diagnóstico detalhado não foi retido pelo harness sanitizado e a causa inicial
  permanece desconhecida. Duas reexecuções diretas aprovaram os **149 testes**,
  sem alterar fontes; a última também aprovou lint, Prettier, TypeScript e Vite.
  A [conciliação final](evidence/final-regression-summary.json) registra essas
  execuções separadamente; o harness interrompido não é apresentado como aprovado.
  Tentativas anteriores permanecem em `final-regression-attempt-*.jsonl`.
- Um aviso upstream Starlette/TestClient-httpx permanece por rodada. Os oito
  PostgreSQL opt-in foram realmente executados em separado; a cópia pedida do
  XML JUnit não ficou disponível. O resultado vem do console real, sem XML inventado.

Não houve prova física MP4 de 500 MiB/600 s como benchmark, carga extensa ou
crash em cada ponto possível. MP3 excedido em 1 byte foi transferido realmente.
Limites reduzidos, integridade, concorrência, lease, timeout e sinais têm provas
específicas; isso não constitui SLA ou certificação de segurança.

## G. Critérios de aceite e riscos

Não há pendência essencial de aceite. A [auditoria final](evidence/storage-final.json)
conferiu 83 MP4, seis MP3 e cinco prévias, sem temporários, entradas desconhecidas
ou arquivos inválidos. A [preservação final](evidence/preservation.json) comprovou
66 arquivos históricos, 30 originais de baseline e linhas preexistentes sem
alterações. A [coexistência](evidence/coexistence-final.json)
conferiu cinco containers ObraControl com IDs/estado/imagens/portas inalterados,
sem mutações. As portas locais e permissões foram revalidadas nas provas
operacionais; API/frontend seguem em loopback, banco/worker sem portas no host.

- [x] Contratos R01–R03 intactos e regressões finais aprovadas.
- [x] Migração 0004 aplicada/repetível; v1 preservado e v2 gravável.
- [x] Conflito expected_revision, no-op 200 e histórico imutável preservados.
- [x] Upload MP3 controlado, metadados reais e originais imutáveis nas provas executadas.
- [x] Até três textos com tempos/posições/estilos validados e recuperáveis.
- [x] Volume original, música opcional, loop/fades com comportamento medido.
- [x] Overlay CSS coerente e acessível, identificado como referência de edição.
- [x] Solicitação vinculada a revisão salva, sem renderizar draft dirty.
- [x] Worker realmente separado, com lease, recuperação, fencing e timeout.
- [x] MP4 curto H.264/AAC validado, com enquadramento/texto na janela escolhida.
- [x] Status API/UI reais, sem percentual ou sucesso fictício.
- [x] Prévia somente por rota privada GET/HEAD com Range.
- [x] Erros sanitizados e tentativas finitas, preservando originais/revisões.
- [x] QA completo backend/frontend/integração/FFmpeg/navegador e evidências finais.
- [x] Auditoria final de portas locais, dados R01–R03, storage, locks e ObraControl.
- [x] README, arquitetura, roadmap, AGENTS e QA finalizados consistentemente.
- [x] Sem export final, heditor, agente, prompt ou Higgsfield antecipados.

Riscos: filesystem/PostgreSQL não formam transação única; publicação incerta
pode preservar canônico não servido que exige revisão. Sem retenção automática,
originais/prévias consomem disco até política explícita posterior. Loopback não
é autenticação multiusuário. Decoder/fonte, precisão temporal, `white_plain` e
avaliação auditiva permanecem limitados às provas descritas.

Nenhuma remoção de original, volume ou dado preexistente foi autorizada. Não
houve envio de mídia a fornecedor externo, operação Git mutante ou alteração
de ciclo de vida/configuração do ObraControl nesta implementação.

## H. Continuidade

Composição local, contrato versionado, fila e supervisor são a base reutilizável
para R05. **A R04 está concluída tecnicamente e preparada para a R05**, dentro
dos limites e riscos documentados. Não há bloqueador essencial conhecido nas
verificações executadas; a falha inicial de Vitest sem causa identificada continua
registrada, junto das duas reexecuções aprovadas.

**R05 não foi iniciada** e exige nova autorização. Render completo, exportação,
downloads/histórico e cancelamento avançado permanecem fora da R04. heditor,
agentes/LLM, prompt, Higgsfield, autenticação, Redis e Celery não foram
introduzidos. Git permanece sob gestão manual do usuário.
