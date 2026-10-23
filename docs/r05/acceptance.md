# Edivo — R05: implementação e aceite

**Estado: R05 concluída e validada — 22/22 critérios aprovados. R06 não iniciada.**

A autorização desta etapa permite exportação local de revisões salvas, sem
alterar originais, prévias, revisões anteriores, ObraControl ou histórico Git.
R06 e integrações de IA não foram iniciadas. A referência operacional é
`docs/R05_EDIVO_ESPECIFICACAO_OPERACIONAL.md`, reconciliada com o checkout em
[compatibility.md](compatibility.md).

## Gates técnicos

### R05.0 — concluído antes de código

Auditoria integral e matriz concluídas. O [baseline](evidence/baseline.json)
registra 165 arquivos históricos, 80 projetos, 83 mídias, seis áudios,
60 revisões, 49 PreviewJobs, 94 artefatos e cinco containers ObraControl.
Nenhum job ativo. Migrations, snapshots e arquivos existentes foram preservados.

### R05.1 — concluído

Entregues migration `0005_r05_render_exports`, RenderJob, validação, API,
histórico, identidade persistente, cancelamento durável e download privado.
**Gate liberado para R05.2 após os testes e verificações abaixo.**

- 123 novos testes de API/modelo/repositório passaram; a regressão proporcional
  passou com 415 casos, incluindo esses 123. Não são 538 casos distintos.
- 111 novos casos de configurações/storage/attachment passaram; 101 regressões
  focadas também passaram. Ruff check/format passaram.
- 12 casos adicionais foram executados no PostgreSQL real, com concorrência,
  constraints, locks, fencing e idempotência: 12 passaram.
- Migração incremental real aplicada e API saudável.
- A [preservação após migração](evidence/preservation-after-migration.json)
  passou: todos os registros históricos, hashes/modos dos arquivos, trigger,
  documentos/locks, Git e ObraControl conferem.
- `alembic upgrade head` repetido passou sem novas operações; `alembic check`
  passou com `No new upgrade operations detected`. Hashes dos cinco módulos
  de produção R05 no runtime conferem com as fontes locais.

Logs negativos foram mantidos: namespace novo criado com 0755 (corrigido para
0700 sem alterar modos existentes), execução ASGI interrompida no sandbox e
allowlist de rotas R04 desatualizada (ajustada somente às novas rotas R05).
As reexecuções aprovadas não eliminam essas ocorrências.

### R05.2 — concluído

**Gate liberado para R05.3.** O núcleo FFmpeg da R04 foi parametrizado, mantendo
as prévias em 24 fps e acrescentando exportação direta do trim completo em
30 fps, H.264/yuv420p/SAR 1:1, AAC quando aplicável e faststart.

- 122 casos distintos de compositor executados com FFmpeg real passaram:
  69 regressões de prévia e 53 novos casos finais. As reexecuções não são somadas.
- 83 casos de supervisor/parser passaram: 65 novos e 18 regressões POSIX R04.
- Matriz separada: 23 composições, 77 verificações, incluindo os quatro presets
  e ambos os perfis, revisões v1/v2 e arquivos completos de 12 s/360 frames.
- FFprobe, decodificação integral e SHA-256 passaram. Foram exercitados rotação,
  SAR, VFR, enquadramento, foco, textos Unicode, música, loop e fades.
- O runtime FFmpeg 5.1.9 confirmou que `out_time_us` e `out_time_ms` são ambos
  microssegundos. O parser consome saída incremental limitada sem guardar logs
  de progresso indefinidamente.
- Ganhos originais 0/0,5/1/2 e da música 0/0,5/1 foram comparados por RMS/peak.
  Sinais sintéticos ficaram abaixo do clipping; mixagens arbitrárias com ganho
  alto ainda podem saturar. Não houve escuta humana.
- Inspeção visual pelo agente de frame sintético confirma textos legíveis nas
  três posições; não equivale a validação por usuário ou dispositivo físico.
- Ruff check/format passou. A falha inicial do roteiro por configuração ausente
  foi preservada e corrigida no próprio QA, sem alterar contratos de produção.

Evidências: [resumo](evidence/phase2-final-summary.json),
[matriz](evidence/ffmpeg-matrix.json), [unidades](evidence/progress-units.json) e
[ganhos](evidence/audio-gain.json). O worker ainda não executa exports nesta fase.

### R05.3 — concluído

**Gate liberado para R05.4.** Um scheduler PostgreSQL seleciona PreviewJob e
RenderJob, com FIFO entre candidatos elegíveis/desbloqueados, claim confirmado
antes da execução e um processo FFmpeg por worker.

- 29 casos PostgreSQL passaram: oito regressões R04, 12 API/repositório R05,
  duas corridas de publicação/cancelamento com FFmpeg real e sete do scheduler.
- Cinco cenários reais de controle passaram em 70 verificações: SIGTERM, crash,
  lease perdido, timeout e dois workers disputando o mesmo job. Quatro arquivos
  de 12 s foram integralmente decodificados; originais e tokens conferidos.
- Prévia R04 de quatro segundos passou em 26 verificações HTTP/runtime:
  540×960/24 fps/96 frames, Range, SHA e originais MP4/MP3 em 0400.
- Exportações v1 e v2 completas de 12 s passaram no worker de produção:
  1080×1920/30 fps, progresso real, download privado e idempotência.
- O primeiro roteiro HTTP encontrou falha real de alocação x264 no high 4:5
  com áudio. O diagnóstico repetiu o problema e a correção limita arenas glibc
  apenas nos filhos render, mantendo 1 GiB, qualidade e ambiente preview.
  A regressão da imagem corrigida passou com 166 casos R04/R05, incluindo
  12 s high 4:5/AAC/360 frames. Nenhum log negativo foi removido.
- O roteiro HTTP retomado passou em 191 verificações: cancelamento real durante
  encoding, nenhum artefato incompleto e histórico paginado com cinco jobs,
  incluindo a falha anterior. Não se somam verificações HTTP a testes pytest.
- A preservação após os testes do worker conferiu todos os registros/artefatos
  históricos, trigger, 165 documentos/locks, Git e cinco containers ObraControl.

Evidências: [PostgreSQL](evidence/phase3-postgres-attempt-1.json),
[processos](evidence/worker-control-attempt-5/report.json),
[prévia](evidence/preview-http-verify.json),
[HTTP retomado](evidence/render-http-finish.json),
[relógios](evidence/worker-clock-diagnostic.json) e
[preservação](evidence/preservation-after-worker-qa.json). As tentativas com
lease QA de dois segundos e o assert stdout/stderr incorreto permanecem
registrados. O ambiente apresentou avanço de parede/PG de até 2,76 s sobre
o monotônico; o padrão de produção permanece lease 30 s/heartbeat 5 s.

### R05.4 — concluído

Painel de exportação integrado ao editor existente, com perfis, estado real,
cancelamento confirmado pelo worker, resultado selecionado independente do
acompanhamento ativo, download privado e histórico paginado com datas/revisões.
Salvar continua explícito; drafts impedem novas exportações e retries. Chaves
incertas permanecem persistidas, sem POST automático, e polling possui abort e
cleanup. As referências de acompanhamento/resultado são recuperadas por projeto.

A suíte frontend completa passou com 312 casos distintos: 149 históricos,
126 do cliente HTTP e 37 de componentes. ESLint, Prettier, TypeScript e Vite
passaram. Logs iniciais de três e uma falhas foram preservados; a atualização
redundante de histórico e as esperas das asserções foram corrigidas. A execução
completa está em [phase4-vitest-final-attempt-1.log](evidence/phase4-vitest-final-attempt-1.log).

### R05.5 — concluído

A regressão final nas imagens reconstruídas passou: **1140 testes backend
distintos**, sendo 1110 da suíte padrão e 30 PostgreSQL opt-in executados em
separado. Os 30 skips da execução padrão não foram contados como passes; a
[união JUnit](evidence/backend-distinct-final-counts.json) confere identidade,
ausência de duplicação e execução real. **313 frontend** passaram: 149
históricos, 127 do cliente API e 37 de componentes. Nove testes Python host do
harness são um escopo separado, não somados ao backend.

O [aceite Compose final](evidence/full-compose-final-summary.json) passou seus
29 checks: config/build/up, migração/repetição/check, banco/storage e readiness,
portas/mounts, FFmpeg API/worker, worker ocioso/SIGTERM e todas as ferramentas
de qualidade. Logs integrais por comando estão em
[evidence/full-compose-attempt-2/commands.jsonl](evidence/full-compose-attempt-2/commands.jsonl).
A primeira execução completa (1130/312) permanece preservada, anterior à
correção final de admissão; não se somam reexecuções.

A revisão final identificou e corrigiu um requisito de fila: um render por
projeto não limitava o total entre projetos quando o worker estava parado.
`EDIVO_RENDER_MAX_ACTIVE_TOTAL=20` (1–1000) agora limita todos os RenderJobs
queued/running, inclusive de projetos excluídos, com Project→advisory lock
transacional PostgreSQL para COUNT/INSERT. Excesso responde
`409 render_queue_full`. Replay/reuso antecedem a admissão e continuam
permitidos; terminais liberam capacidade. A recuperação existente encerra
queued de projetos excluídos quando o worker retorna. A concorrência real de
dois projetos pelo último slot passou no PostgreSQL, incluída nos 30 casos.
Não foi necessária outra migration nem mudança do compositor/prévias.

Regressão HTTP executada: R02 **13**, R03 **29**, segurança MP3 **18** e prévias
R04 **28** verificações. A queda real do PostgreSQL durante codificação passou
em **37** checks, com encerramento sem publicação, restauração e retry 2
concluído. Os cinco cenários POSIX do worker passaram em **70** checks. Essas
verificações operacionais não são somadas aos testes unitários.

**Chromium 145.0.7632.6 real / Playwright 1.58.2:** o fluxo inicial passou em 15
checks, importando MP4/MP3 pela interface, salvando v2 explicitamente, bloqueando
draft dirty, exportando 12 s, mostrando progresso medido, reproduzindo/pausando/
buscando, baixando por link nativo com SHA correto e cancelando outro render
em encoding. Temas claro/escuro, teclado e larguras emuladas 375/390 px passaram.
Uma inspeção visual das sete capturas confirmou layout/legibilidade; não houve
teste físico nem escuta humana. A identificação visual foi atualizada para R05.

Após reinício/recriação dos serviços Edivo, a
[conferência na imagem final](evidence/post-admission-verification-summary.json)
passou novamente: HTTP render **88**, preview **26** e Chromium **5** checks,
recuperando revisão, histórico, bytes/SHA e reprodução dos mesmos resultados,
sem criar outro render. Os cinco checks de verificação são reexecuções de
persistência, não cinco novos testes distintos a cada execução.

A [auditoria final](evidence/storage-admission-final.json) verificou **197 MP4,
10 MP3, oito prévias concluídas e 14 exports**, todos registrados e íntegros,
com zero arquivos desconhecidos/inconsistentes e zero temporários. Há 97
PreviewJobs e 92 RenderJobs no banco, incluindo falhas/cancelamentos de QA
retidos; não representam 97/92 artefatos concluídos. A
[preservação final](evidence/preservation-admission-final.json) confirmou todos
os registros/94 artefatos anteriores, trigger, 165 arquivos históricos/locks,
Git e cinco containers ObraControl inalterados. Hashes das
[fontes no runtime](evidence/final-runtime-source-hashes.json) conferem entre
checkout, API, worker e frontend; o teto efetivo API/worker é 20.

## Critérios de aceite — avaliação final

| Item | Critério                                       | Resultado e evidência                                                                                                                                                                               |
| ---- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01   | Auditoria antes das alterações                 | Aprovado: [compatibilidade](compatibility.md) e [baseline](evidence/baseline.json) anteriores ao código                                                                                             |
| 02   | R01–R04 preservadas                            | Aprovado: [suíte final](evidence/full-compose-final-summary.json), HTTP R02/R03/MP3/prévias e [preservação](evidence/preservation-admission-final.json)                                             |
| 03   | Migration 0005 incremental e repetível         | Aprovado: [upgrade/repetição/check reais](evidence/full-compose-final-summary.json), sem alterar migrations antigas                                                                                 |
| 04   | RenderJob, histórico, constraints e limites    | Aprovado: [PG real 30 casos](evidence/phase5-postgres-admission-final.log), cap global/per-projeto e [histórico](evidence/render-http-admission-final.json)                                         |
| 05   | Exportação somente de revisão salva            | Aprovado: v1/v2 e revisão antiga na API; [Chromium](evidence/browser-attempt-1/invocation.json) bloqueou dirty e salvamento implícito                                                               |
| 06   | Quatro presets finais                          | Aprovado: [23 composições/77 checks FFmpeg](evidence/ffmpeg-matrix.json), quatro resoluções em standard/high                                                                                        |
| 07   | Render completo maior que 8 s                  | Aprovado: [12 s/360 frames](evidence/phase2-final-summary.json), worker e [reprodução/download Chromium](evidence/browser-attempt-1/invocation.json)                                                |
| 08   | Textos, crop, focal, trim e áudio              | Aprovado nas fixtures: [matriz](evidence/ffmpeg-matrix.json), [ganhos](evidence/audio-gain.json) e [frame](evidence/complete-12s-text-music.png); sem escuta humana                                 |
| 09   | Worker/compositor R04 reutilizados             | Aprovado: [regressão completa](evidence/full-compose-final-summary.json), [scheduler PG](evidence/phase5-postgres-admission-final.log) e [prévia final](evidence/preview-http-admission-final.json) |
| 10   | Progresso real; conclusão após validação       | Aprovado: [unidades reais](evidence/progress-units.json), [processos](evidence/worker-control-attempt-5/report.json) e UI Chromium com percentual conferido à API                                   |
| 11   | Cancelamento, lease, fencing, retry e crash    | Aprovado: [70 checks de processo](evidence/worker-control-attempt-5/report.json), [queda real DB 37](evidence/db-outage-attempt-1/report.json), PG e cancelamento Chromium                          |
| 12   | Idempotência sem duplicação                    | Aprovado: [concorrência PG](evidence/phase5-postgres-admission-final.log), replay/reuso/conflitos HTTP, inclusive no teto global                                                                    |
| 13   | MP4 integralmente validado antes de completed  | Aprovado: FFprobe/decode/frame count/SHA na [matriz](evidence/ffmpeg-matrix.json), publicação PG/worker e [storage final](evidence/storage-admission-final.json)                                    |
| 14   | Download GET/HEAD/Range privado                | Aprovado: [88 checks HTTP](evidence/render-http-admission-final.json), 200/206/416, bytes e headers; browser reproduziu/baixou                                                                      |
| 15   | Histórico paginado e persistência após restart | Aprovado: [histórico HTTP após reinício](evidence/render-http-admission-final.json) e [Chromium final](evidence/browser-attempt-3/invocation.json)                                                  |
| 16   | Interface de exportação funcional              | Aprovado: 313 frontend e [Chromium](evidence/browser-attempt-1/invocation.json), incluindo progresso/cancel/download, desktop/mobile emulado/temas/teclado                                          |
| 17   | Segurança e erros sanitizados                  | Aprovado: testes de acesso/storage/erros, [HTTP](evidence/render-http-admission-final.json), limites e [runtime/portas](evidence/full-compose-final-summary.json)                                   |
| 18   | Originais, revisões e prévias preservados      | Aprovado: [auditoria de todos os namespaces](evidence/storage-admission-final.json) e [baseline final](evidence/preservation-admission-final.json)                                                  |
| 19   | QA automatizado, FFmpeg e navegador reais      | Aprovado: [contagem distinta](evidence/backend-distinct-final-counts.json), [matriz real](evidence/ffmpeg-matrix.json) e [Chromium final](evidence/browser-attempt-3/invocation.json)               |
| 20   | ObraControl, volumes, portas, locks e Git      | Aprovado: [baseline final](evidence/preservation-admission-final.json), [Compose](evidence/full-compose-final-summary.json); somente inspeções Git                                                  |
| 21   | Documentação e evidências R05                  | Aprovado: [relatório A–H](report.md), [QA](qa-results.json), matriz, README, arquitetura, roadmap, AGENTS e configurações                                                                           |
| 22   | R06/IA não iniciadas                           | Aprovado: somente composição/exportação manual R05; nenhuma integração ou funcionalidade posterior implementada                                                                                     |

## Falhas preservadas e limites das evidências

A tentativa [HTTP original](evidence/render-http-render.json) permanece falha:
libx264 high/4:5 encontrou malloc sob AS 1 GiB. O diagnóstico e correção
`MALLOC_ARENA_MAX=2` apenas em filhos render foram validados sem elevar limites
ou mudar perfis; o job histórico failed continua no banco. A
[retomada HTTP](evidence/render-http-finish.json) passou em 191 checks.

As tentativas negativas do worker com leases QA de 2 s e o assert stdout/stderr
incorreto foram mantidos, assim como os logs iniciais de testes frontend e suas
correções. O WSL/PG apresentou avanço de relógio de parede até 2,76 s sobre o
monotônico; a causa do ambiente não foi determinada. QA processual passou com
lease 10 s/heartbeat 0,5 s; produção permanece 30/5 s.

O primeiro [wrapper de reinício](evidence/final-restart.json) chamou um helper
inexistente depois de executar reinícios com sucesso. O segundo exigiu
incorretamente healthcheck Docker do worker, ausente no contrato preservado.
A [verificação corrigida](evidence/final-restart-verification-2.json) confirmou
HTTP/serviços/worker/filas e os arquivos após reinício. Nenhuma negativa foi
reescrita como sucesso. A formatação inicial de helpers também está retida em
[qa-helper-quality-attempt-1.log](evidence/qa-helper-quality-attempt-1.log), com
correção/check final aprovados. A ocorrência intermitente histórica R04 do
Vitest segue documentada; não houve reincidência inexplicada nas suítes finais.

Somente Chromium headless foi testado; mobile é emulado, sem Firefox/WebKit,
dispositivos físicos ou escuta humana. RMS/peak sintéticos não provam
inteligibilidade humana. Mixagens arbitrárias com ganho alto podem clipar.
Precisão de FPS/seek/keyframes/VFR e rasterização CSS/FreeType impõem tolerâncias;
saída final permite um frame a 30 fps + 1 ms, não edição frame-perfect.
Não houve benchmark físico de 600 s, 500 MiB ou saída de 2 GiB; esses limites
têm validações, não promessa de desempenho.

ENOSPC, fsync e COMMIT incerto têm injeções controladas; não se alegam panes
físicas observadas. A queda de PostgreSQL durante encoding foi real e separada.
PostgreSQL e filesystem não formam uma transação única: resultado de commit
incerto exige reconciliação conservadora. Não existe retenção destrutiva; nenhum
arquivo desconhecido pode ser removido automaticamente. Histórico usa
presença/modo/tamanho; detalhe/reuso/download conferem SHA completo, com custo
de leitura para arquivos grandes.

## Continuidade

**A fundação está tecnicamente pronta para a R06**, com os riscos residuais
acima documentados. Os 22 critérios essenciais da R05 passaram. **R06 não foi
iniciada** e exige nova autorização. Git permanece sob gestão manual do usuário.
