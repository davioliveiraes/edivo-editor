# Edivo — R05: relatório A–H

**R05 concluída tecnicamente: 22/22 critérios de aceite validados.** A suíte
final, os testes PostgreSQL, as verificações HTTP, o Chromium, o reinício com
persistência e a auditoria final passaram. A admissão global da fila também foi
limitada e revalidada antes do fechamento. As falhas anteriores e suas
resoluções permanecem nas evidências; as limitações estão registradas abaixo.

**O sistema está tecnicamente preparado para R06, que não foi iniciada.** Nova
etapa exige autorização expressa. Este relatório descreve somente a R05.

Fontes: [especificação](../R05_EDIVO_ESPECIFICACAO_OPERACIONAL.md),
[compatibilidade](compatibility.md), [aceite](acceptance.md) e
[QA](qa-results.json). Relatórios e documentos históricos R00–R04 foram
preservados; seus números não são contabilizados como uma nova execução R05.

## A. Implementação

A R05 acrescenta exportação completa de revisões salvas, perfis standard/high,
progresso real, cancelamento durável, download privado e histórico paginado.
O editor continua único, com os recursos anteriores de player, timeline,
textos, MP3, enquadramento, prévias e salvamento explícito.

| Área            | Arquivos principais entregues/evoluídos                                                                                          |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Persistência    | `backend/alembic/versions/0005_r05_render_exports.py`, `app/models/render_job.py`                                                |
| API e domínio   | `app/schemas/renders.py`, `app/repositories/render_jobs.py`, `app/services/renders.py`, `app/api/v1/renders.py`                  |
| Composição      | `app/integrations/ffmpeg/preview.py`, `progress.py`; supervisor e launcher de recursos existentes                                |
| Worker          | `app/worker/scheduler.py`, `render_worker.py`, `render_processor.py`, integração no processo principal existente                 |
| Storage/HTTP    | `app/storage/local.py`, `app/api/media_stream.py`                                                                                |
| React           | `frontend/src/lib/api/renders.ts`, `features/editor/export-panel.tsx`, `export.css` e integração no editor                       |
| QA/documentação | Suítes backend/frontend, scripts R05 de HTTP/processos/auditoria/navegador, README, arquitetura, roadmap, AGENTS e configurações |

Os caminhos `app/...` pertencem a `backend/`; não existe um segundo motor FFmpeg
ou uma fila de exportação desconectada. API, repositórios, serviços, compositor,
storage e worker mantêm suas responsabilidades. API não codifica mídia nem
envia sinais a processos.

## B. Banco e persistência

`0005_r05_render_exports` depende da revisão R04 real
`0004_r04_text_audio_previews`. Acrescenta somente RenderJob, preservando tabelas,
migrations, snapshots v1/v2 e trigger de imutabilidade. Upgrade repetido e
`alembic check` passaram sem operações novas inesperadas.

RenderJob armazena projeto/revisão imutável, perfil, motor, identidade idempotente,
estado/estágio, tentativas, lease/token/heartbeat, intenção de cancelamento,
progresso, datas UTC, erro seguro e metadados reais do resultado. FKs RESTRICT,
CHECKs e índices de fila/lease/histórico reforçam o contrato. Há índice único
parcial para um render ativo por projeto e identidade de chave por projeto.

Estados: queued → running → completed/failed/canceled; queued pode ser cancelado
atomicamente. Lease expirada permite retry elegível finito ou falha terminal.
Completed, failed e canceled nunca reabrem automaticamente.

- A requisição usa `edit_spec_revision_id`; a resposta usa `revision_id` e
  `revision_number`.
- Export utiliza somente o snapshot persistido. V1 recebe defaults neutros em
  memória, sem regravar a revisão; v2 mantém textos e áudio salvos.
- Novo save durante processamento não altera a revisão do job. Saves idênticos
  continuam no-op e conflitos de edição continuam HTTP 409.
- Chave UUID explícita é permanente: mesmo payload identifica o mesmo job;
  payload diferente causa 409. Replay de failed/canceled retorna 200 com o terminal
  conhecido; nova tentativa explícita exige nova chave.
- Sem chave, pode reaproveitar equivalente ativo ou completed íntegro. A
  fingerprint inclui projeto, revisão, perfil e motor `r05-1`.

As suítes PostgreSQL reais passaram **30 casos**, incluindo concorrência de
requisições, SKIP LOCKED, constraints, fencing, recuperação e duas corridas
cancelamento/publicação. Logs: [contagem final distinta](evidence/backend-distinct-final-counts.json).
A corrida real de dois projetos pelo último slot global passou. O reinício e a
persistência foram confirmados no [Compose final](evidence/full-compose-final-summary.json)
e na [verificação HTTP posterior](evidence/render-http-admission-final.json).

## C. Renderização e exportação

| Preset | Resolução final |
| ------ | --------------- |
| 9:16   | 1080×1920       |
| 1:1    | 1080×1080       |
| 4:5    | 1080×1350       |
| 16:9   | 1920×1080       |

Todos usam H.264/libx264, yuv420p, CFR 30, SAR 1:1, MP4 faststart e AAC 48 kHz estéreo
quando há áudio. Standard: veryfast/CRF 23/AAC 160k; high: medium/CRF 20/AAC 192k.
Não se promete tamanho ou bitrate fixo. Prévias R04 continuam reduzidas,
24 fps/CRF 28/AAC 128k e até 8 s; o final compõe o trim inteiro diretamente.

O núcleo aplica trim, cover/contain, focal, orientação, SAR/VFR, textos UTF-8
com fontes locais, volume original, música opcional, loop e fades. Tempos de
fonte são absolutos; música/fades seguem o trim completo. A tolerância final é
um frame a 30 fps + 1 ms, aproximadamente 34,333 ms, medida e validada; não há promessa
de edição frame-perfect.

Um exit code zero não publica resultado. FFprobe, formato/streams, dimensões,
codecs/FPS/SAR/duração, faststart, decodificação integral, frames, tamanho e
SHA-256 precisam passar antes da promoção e do commit. Originais são verificados
por descritores fixados, SHA-256 e modo 0400, sem sobrescrita.

A [matriz FFmpeg](evidence/ffmpeg-matrix.json) executou 23 composições e 77 checks,
incluindo os quatro presets e ambos os perfis, v1/v2, áudio/sem áudio, loop/fades,
texto Unicode, rotação, SAR, VFR, cover/contain e focal. Houve renders completos
de 12 s/360 frames; a regressão específica high 4:5 com texto longo e música também
passou após a correção de alocação descrita na seção D.

A prova Chromium baixou um MP4 de **12 s, 1080×1920**, 2.513.760 bytes:

```text
SHA-256: 6d565a018c9f0daf391a2d291a6ebd315991de9d5e8458b9224bc31b9ff54aea
Filename: edivo-r1-9x16-standard.mp4
```

O hash foi calculado dos bytes efetivamente baixados e comparado ao registro
persistido; reprodução, seek e Range ocorreram no navegador real.

## D. Worker e cancelamento

O scheduler único escolhe FIFO entre candidatos PreviewJob/RenderJob elegíveis,
sem preempção. Claim usa transação curta e SKIP LOCKED; nenhum subprocesso inicia
antes do commit. Um FFmpeg por worker e um worker Compose por padrão. O projeto
limita um render ativo; prévias mantêm seu contrato de até dois ativos.
A fila de renders também possui teto global **20 queued/running**, configurável
por `EDIVO_RENDER_MAX_ACTIVE_TOTAL` de 1 a 1000. Criação nova segue
Project→advisory transaction lock PostgreSQL `EDIVOR05`, após replay/reuso e
validação, serializando COUNT+INSERT entre projetos. Quando cheia, retorna
`409 render_queue_full`. Replays/reuso já existentes continuam disponíveis.
A contagem inclui projetos soft-deleted ainda não recuperados; criar ou excluir
projetos não contorna o teto quando o worker está offline. A disputa concorrente
pelo último slot foi validada em PostgreSQL real.

Defaults: lease 30 s, heartbeat 5 s, consulta de cancelamento 0,5 s, grace 2 s, até duas
tentativas, timeout global 3600 s e arquivo até 2 GiB. AS por subprocesso 1 GiB,
duas threads e cgroup do worker 2 CPUs/1536 MiB/128 PIDs. Esses tetos são limites,
sem SLA de desempenho. Não foi executado benchmark de exportação de 600 s; a
prova decisiva de duração usou 12 s completos, acima do teto de prévia. Há reserva de espaço antes da execução; falta de disco
não autoriza apagar arquivos.

Fencing condiciona progresso/transições/publicação a token, running, projeto
ativo e lease vigente, usando relógio PostgreSQL fresco. Progresso vem de
`-progress pipe:1`, com consumo incremental limitado e persistência aproximadamente
uma vez por segundo. [Medição real](evidence/progress-units.json) confirmou que
`out_time_us` e o alias `out_time_ms` representam microssegundos no FFmpeg 5.1.9.
Encoding fica em 0–99%; validar/publicar são indeterminados; 100% aparece somente
após completed confirmado.

A API registra intenção, sem sinais ao SO. O worker termina exclusivamente seu
próprio grupo SIGTERM/grace/SIGKILL, aguarda encerramento/cleanup e confirma
canceled. Project→Job serializa publicação/cancelamento: intenção persistida
primeiro impede completed; commit concluído primeiro torna cancelamento 409.
SIGTERM, crash, timeout, perda de banco/lease e retry finito foram exercitados.
A [prova de processos](evidence/worker-control-attempt-5/report.json) passou 70 checks;
a [prova de indisponibilidade do banco](evidence/db-outage-attempt-1/report.json)
passou 37. Nenhum estado completed foi fabricado por esses testes.

**Falhas retidas e correções:**

1. High4:5 com texto/áudio de 12 s falhou em libx264 por malloc sob RLIMIT_AS 1 GiB.
   O [diagnóstico negativo](evidence/high-encoding-diagnostic-1.json) foi mantido.
   Limitar glibc a `MALLOC_ARENA_MAX=2` somente nos filhos de render resolveu
   exatamente a composição, mantendo limite e qualidade. O
   [diagnóstico positivo](evidence/high-encoding-diagnostic-2.json) mediu
   VmPeak 856.976 KiB/RSS 370.268 KiB e validou 360 frames.
2. Leases experimentais de 2 s no QA perderam posse em avanços de relógio observados
   no WSL. O fencing impediu publicação. O QA passou com 10 s; o default de produção
   permaneceu 30 s. [Diagnóstico de relógio](evidence/worker-clock-diagnostic.json).
3. Tentativas negativas dos roteiros e correções de testes/UI permanecem em
   evidência. Reexecuções aprovadas não apagam ocorrências anteriores.

Publicação usa workspace privado e promoção exclusiva para
`renders/{project_uuid}/{render_uuid}/final.mp4`, 0400. Commit incerto preserva
canônicos para reconciliação conservadora; somente temporários comprovadamente
pertencentes à tentativa podem ser limpos. Não há retenção destrutiva automática.

## E. API, frontend e navegador

Namespace: `/api/v1/projects/{project_id}`.

| Método/rota                 | Comportamento                                                               |
| --------------------------- | --------------------------------------------------------------------------- |
| POST `/renders`             | 202 ativo ; 200 reuso íntegro/replay terminal conhecido                     |
| GET `/renders`              | Histórico `{items,total,limit,offset}`, padrão 20/0, filtro status opcional |
| GET `/renders/{id}`         | Estado, estágio, revisão, progresso e metadados públicos                    |
| POST `/renders/{id}/cancel` | 200 queued/já canceled; 202 intenção running; 409 completed/failed          |
| GET `/renders/{id}/file`    | 200/206 MP4 completed íntegro; 416 Range inválido                           |
| HEAD `/renders/{id}/file`   | Cabeçalhos equivalentes sem corpo                                           |

Listagem ordena `created_at DESC,id DESC`, limit 1–100. Histórico verifica
presença/modo/tamanho; detalhe/reuso/download verificam SHA completo. Um completed
inválido conserva seu histórico e fica indisponível; download/replay da mesma
chave retornam 503, sem mudar o job para failed. Solicitação sem chave pode ignorar
o equivalente inválido e criar outro job, respeitando o limite ativo.

Arquivo usa leitor por FD, proteção contra symlink/traversal, memória limitada,
GET/HEAD/Range fechado/aberto/suffix e 200/206/416. Cabeçalhos: `video/mp4`,
`private, no-store`, `nosniff`, comprimento/intervalo corretos e attachment com
filename controlado. Nenhuma storage key, path, comando, token ou credencial é
pública. Envelope `{code,message,request_id}` e `X-Request-ID` permanecem.

O painel **Exportar vídeo** integra o editor existente, sem segundo editor ou
save implícito. Draft dirty bloqueia gerar; perfil, revisão, resolução e formato
são explícitos. Há progresso real, cancelamento confirmado, erro amigável,
resultado selecionado, player único e link **Baixar MP4**. Histórico permite
selecionar outro resultado sem interromper acompanhamento ativo. Referências de
acompanhamento e seleção persistem separadamente; polling abortável para em
terminal/desmontagem, e falha de rede não transforma o job em failed.

**Chromium real 145.0.7632.6, Playwright 1.58.2:**
[exportação](evidence/browser-attempt-1/invocation.json) passou 15 verificações,
com uploads MP4/MP3 pela UI, texto/áudio, save v2 explícito, export de 12 s, progresso
visível comparado à API, reprodução/pausa/seek, Range 206, download SHA, high
cancelado em execução, identificação de revisão anterior, histórico e reload.
Capturas desktop claro/escuro e mobile 375/390 nos dois temas foram retidas em
`evidence/browser-attempt-1/`. Houve controle de teclado; não houve erros de
página não tratados. Mobile é emulado, não dispositivo físico.

Projeto sintético de navegador preservado:
`4de80745-64ad-41e5-85dc-770d5e3462da`. Após reinício e aplicação da admissão global,
a [verificação Chromium final](evidence/browser-attempt-3/invocation.json) passou
**5 checks**, recuperando revisão/histórico/artefato e reproduzindo novamente o
MP4. Uma verificação intermediária após reinício também passou em
`evidence/browser-attempt-2/`; essas repetições não são somadas como novos testes
unitários. Falha/retry/offline e paginação longa receberam testes de
componente/cliente, não são anunciados como cenários integralmente exercitados
nesse fluxo Chromium de dois jobs.

Portas permanecem em loopback: frontend 5174/API 8001; internas 5173/8000.
Banco/worker não publicam portas. ObraControl não foi modificado. Ajuste final
de rótulo/footer para R05 passou qualidade e rebuild. Os
[hashes das fontes no runtime](evidence/final-runtime-source-hashes.json) conferem
com o checkout em API/worker e frontend, com teto global 20 e imagens atuais.
A verificação posterior observou essa configuração final.

## F. Testes e QA

Versões verificadas no runtime: Python 3.12.14, FastAPI 0.143.3,
Pydantic 2.14.1, SQLAlchemy 2.0.55, psycopg 3.3.6, Alembic 1.20.0,
PostgreSQL 16.13, FFmpeg/FFprobe 5.1.9, Node 24.14.0 e npm 11.9.0.
Os locks existentes foram preservados; detalhes estão no resumo Compose.

| Execução                     | Resultado efetivamente obtido                                                  |
| ---------------------------- | ------------------------------------------------------------------------------ |
| Pytest padrão no runtime     | 1110 passaram; 30 opt-in foram pulados nessa execução                          |
| PostgreSQL opt-in separado   | 30 passaram                                                                    |
| Backend distinto consolidado | **1140**, sem sobreposição/soma de reexecuções                                 |
| Vitest/Testing Library       | **313** distintos: 149 históricos, 127 cliente API e 37 componentes novos      |
| Testes do harness no host    | 9; classificados separadamente do backend                                      |
| Compose completo             | 29 checks, incluindo build/up/config, migração, HTTP, persistência e qualidade |
| Worker/processos             | 70 checks operacionais; não adicionados ao total Pytest                        |
| Indisponibilidade PostgreSQL | 37 checks operacionais separados                                               |
| Matriz FFmpeg                | 23 composições/77 checks separados                                             |
| HTTP R02/R03                 | 13/29 checks respectivamente                                                   |
| Segurança MP3                | 18 checks HTTP reais                                                           |
| Prévias R04 no scheduler R05 | 28 checks no roteiro completo; prova R05 de preview separada preservada        |
| Chromium export inicial      | 15 checks; não somados aos testes unitários                                    |

Evidências principais: [contagem distinta final](evidence/backend-distinct-final-counts.json),
[Compose final](evidence/full-compose-final-summary.json),
[HTTP R02](evidence/r02-http-regression-attempt-1.log),
[HTTP R03](evidence/r03-http-regression-attempt-1.log),
[MP3](evidence/r04-mp3-security-regression-1.json),
[prévias](evidence/r04-previews-regression-1.json) e
[HTTP R05](evidence/render-http-finish.json). Este último passou 191 checks,
incluindo conclusão real, download, identidade e histórico com falha preservada
mais nova solicitação explícita cancelada.

Comandos efetivamente usados nos roteiros e suites:

```bash
pytest -q
ruff check .
ruff format --check .
npm run test -- --run
npm run lint
npm run format:check
npm run build
python3 scripts/acceptance-compose.py --logs-dir docs/r05/evidence/full-compose-attempt-2
python3 scripts/acceptance-r02.py
python3 scripts/acceptance-r03.py
python3 scripts/acceptance-r04-mp3-security.py --api-port 8001 --fixtures /tmp/edivo-r05/regression-fixtures --output docs/r05/evidence/r04-mp3-security-regression-1.json
python3 scripts/acceptance-r04-previews.py --fixtures /tmp/edivo-r05/regression-fixtures --output docs/r05/evidence/r04-previews-regression-1.json
python3 scripts/browser-r05.py --stage export --work /tmp/edivo-r05/browser-attempt-1 --output docs/r05/evidence/browser-attempt-1
```

Os comandos do backend/frontend rodam em seus respectivos runtimes/diretórios;
flags detalhadas, logs integrais e XML identificam o contexto efetivo. PostgreSQL
usou alias privado `EDIVO_QA_DATABASE_URL` e flags opt-in, sem imprimir DSN.
Qualidade inclui Ruff check/format, ESLint, Prettier, TypeScript e build Vite.
Migrations, HTTP de sistema/proxy, FFmpeg/FFprobe, uploads, streaming e recursos
anteriores foram exercitados em runtime real. Nenhum mock é descrito como
integração real.

Após a última mudança de admissão global, os roteiros reais passaram novamente:
[render/histórico/download](evidence/render-http-admission-final.json) 88 checks,
[prévia](evidence/preview-http-admission-final.json) 26 e
[Chromium verify](evidence/browser-attempt-3/invocation.json) 5. O
[resumo pós-admissão](evidence/post-admission-verification-summary.json) concilia
as execuções. Esses checks operacionais não são adicionados aos totais Pytest ou
Vitest. O Compose final também repetiu build/up, migração, reinícios,
persistência, qualidade e suites: 29 checks passaram.

A [verificação final de reinício](evidence/final-restart-verification-2.json)
passou. Duas tentativas negativas do helper foram preservadas: chamada a função
`wait_frontend` inexistente no QA e expectativa de healthcheck no worker, que
não publica esse contrato. Corrigiu-se apenas a verificação; não foram ocultadas
falhas de produto nem eliminadas evidências anteriores.

A falha intermitente histórica R04 do Vitest permanece registrada. A R05 retém
logs negativos próprios de desenvolvimento e suas correções; não ocorreu
reincidência inexplicada na suíte final aprovada. Não se afirma que o evento
histórico deixou de existir.

## G. Aceite e riscos

**22/22 critérios aprovados**, após a suíte final, admissão global, reinícios,
verificações posteriores e auditoria de preservação. As evidências abaixo
correspondem a execuções reais, separadas das suites unitárias.

| Item | Critério                               | Resultado e evidência                                                                                                                                                                                    |
| ---- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01   | Auditoria antes de alterar código      | **Aprovado**: [compatibilidade](compatibility.md) e [baseline](evidence/baseline.json)                                                                                                                   |
| 02   | R01–R04 preservadas                    | **Aprovado**: [Compose final](evidence/full-compose-final-summary.json), regressões HTTP e [preservação final](evidence/preservation-admission-final.json)                                               |
| 03   | Migration 0005 incremental/repetível   | **Aprovado**: [integração Alembic](evidence/phase1-integration-summary.json) e [Compose final](evidence/full-compose-final-summary.json)                                                                 |
| 04   | RenderJob/histórico/constraints        | **Aprovado**: [30 casos PostgreSQL e contagem](evidence/backend-distinct-final-counts.json), [HTTP final](evidence/render-http-admission-final.json)                                                     |
| 05   | Somente revisão salva                  | **Aprovado**: [Chromium export15](evidence/browser-attempt-1/invocation.json), [snapshot fixado em HTTP](evidence/render-http-finish.json)                                                               |
| 06   | Quatro presets finais                  | **Aprovado**: [matriz real dos dois perfis](evidence/ffmpeg-matrix.json)                                                                                                                                 |
| 07   | Render completo >8 s                   | **Aprovado**: [12s/360frames](evidence/high-encoding-diagnostic-2.json) e [download Chromium](evidence/browser-attempt-1/invocation.json)                                                                |
| 08   | Textos/crop/focal/trim/áudio           | **Aprovado**: [composição real](evidence/ffmpeg-matrix.json), [ganhos medidos](evidence/audio-gain.json) e [browser](evidence/browser-attempt-1/invocation.json)                                         |
| 09   | Reutilização R04 sem regressão         | **Aprovado**: [preview no scheduler final](evidence/preview-http-admission-final.json), [regressão R04](evidence/r04-previews-regression-1.json)                                                         |
| 10   | Progresso real/conclusão validada      | **Aprovado**: [unidades reais](evidence/progress-units.json), [MP4 validado](evidence/render-http-finish.json), [UI real](evidence/browser-attempt-1/invocation.json)                                    |
| 11   | Cancelamento/lease/fencing/retry/crash | **Aprovado**: [processos70](evidence/worker-control-attempt-5/report.json), [banco37](evidence/db-outage-attempt-1/report.json), PG e cancelamento Chromium                                              |
| 12   | Idempotência                           | **Aprovado**: [concorrência PostgreSQL](evidence/backend-distinct-final-counts.json), [replay/conflitos HTTP](evidence/render-http-finish.json)                                                          |
| 13   | Validação integral antes de completed  | **Aprovado**: [FFprobe/decode/hash](evidence/ffmpeg-matrix.json), [fronteiras PG](evidence/backend-distinct-final-counts.json)                                                                           |
| 14   | Download privado GET/HEAD/Range        | **Aprovado**: [HTTP final88](evidence/render-http-admission-final.json), [SHA/Range no browser](evidence/browser-attempt-1/invocation.json)                                                              |
| 15   | Histórico/persistência após restart    | **Aprovado**: [reinício verificado](evidence/final-restart-verification-2.json), [HTTP final](evidence/render-http-admission-final.json), [Chromium final](evidence/browser-attempt-3/invocation.json)   |
| 16   | Interface completa                     | **Aprovado**: [exportação Chromium15](evidence/browser-attempt-1/invocation.json), [313 frontend](evidence/full-compose-final-summary.json), [verify5 final](evidence/browser-attempt-3/invocation.json) |
| 17   | Segurança/erros sanitizados            | **Aprovado**: [suítes completas](evidence/full-compose-final-summary.json), [MP3 HTTP](evidence/r04-mp3-security-regression-1.json), [storage final](evidence/storage-admission-final.json)              |
| 18   | Originais/revisões/prévias preservados | **Aprovado**: [baseline intacto](evidence/preservation-admission-final.json) e [inventário consistente](evidence/storage-admission-final.json)                                                           |
| 19   | Testes/FFmpeg/navegador reais          | **Aprovado**: [contagens distintas](evidence/backend-distinct-final-counts.json), [matriz](evidence/ffmpeg-matrix.json), [verificações pós-admissão](evidence/post-admission-verification-summary.json)  |
| 20   | ObraControl/volumes/portas/locks/Git   | **Aprovado**: [preservação final](evidence/preservation-admission-final.json), [Compose29](evidence/full-compose-final-summary.json)                                                                     |
| 21   | Documentação/QA R05                    | **Aprovado**: [aceite](acceptance.md), [QA](qa-results.json), este relatório e evidências vinculadas                                                                                                     |
| 22   | R06/IA não iniciadas                   | **Aprovado**: escopo R05 respeitado; nenhuma integração futura implementada                                                                                                                              |

A [auditoria final somente leitura](evidence/storage-admission-final.json)
registrou inventário consistente: **197 MP4, 10 MP3, 8 prévias e 14 renders** físicos,
todos vinculados, sem erros, entradas inseguras/desconhecidas ou temporários.
Há 97 PreviewJobs e 92 RenderJobs; estados failed/canceled incluem os cenários
sintéticos negativos preservados, e não são apresentados como arquivos válidos.
Não houve limpeza destrutiva para obter consistência.

A [comparação final](evidence/preservation-admission-final.json) confirmou os
**165 arquivos históricos**, **94 artefatos anteriores** (83 MP4, 6 MP3, 5 prévias),
todos os registros anteriores, trigger, Git/locks e os cinco containers
ObraControl sem alteração. A configuração privada de ambiente manteve seu modo.
[Fontes carregadas no runtime](evidence/final-runtime-source-hashes.json)
conferem com oito arquivos backend em API/worker e três frontend; a configuração
ativa usa teto global 20, timeout 3600 s, saída 2 GiB, lease 30 s e heartbeat 5 s.

Limitações e riscos residuais: somente Chromium headless; dispositivos móveis
emulados; sem Firefox/WebKit/dispositivos físicos; sem escuta humana. Métricas
sintéticas de RMS/peak não provam inteligibilidade. Mixagens arbitrárias com
volume alto podem clipar. FPS, VFR, keyframes, seek e rasterização CSS/FreeType
limitam equivalência por frame/pixel. WSL pode apresentar variações de relógio;
leases agressivas não são recomendadas. PostgreSQL/filesystem não formam uma
transação única; commit incerto pode requerer reconciliação conservadora e
arquivos desconhecidos nunca são apagados automaticamente.

## H. Continuidade

**R05 concluída e tecnicamente preparada para R06. Não há critério essencial
pendente.** Renderizações completas, cancelamento, histórico, download e
preservação foram validados em runtime real, incluindo a configuração final de
admissão global e a recuperação após reinício.

R06 deverá tratar apenas seu escopo mediante nova autorização. Recomendações de
continuidade: benchmark de mídias próximas do limite de 600 s; avaliação humana
representativa de áudio; outros navegadores/dispositivos e condições de I/O;
monitoramento de espaço e política explícita de reconciliação/retenção. Essas
limitações são declaradas e não correspondem a funcionalidades R06 antecipadas.

**R06 não foi iniciada.** Heditor, IA/LLM, prompts, Higgsfield, Redis/Celery, 4K,
SaaS e infraestrutura pública não foram implementados. Git permaneceu sob gestão
manual do usuário. Não houve exclusão automática de originais, prévias, exports
ou volumes, nem intervenção em ObraControl.
