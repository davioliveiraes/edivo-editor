# Edivo — roadmap e estado da implementação

O planejamento R00 e as especificações operacionais originais permanecem em
`docs/`, sem reescrever o histórico. R01–R04 foram aceitas. **R05 está concluída,
com 22/22 critérios verificados**; os gates e as evidências estão no
[aceite R05](r05/acceptance.md) e na [compatibilidade](r05/compatibility.md).
As contagens distinguem testes automatizados, checks integrados e navegador real.

| Etapa | Escopo                                                                          | Situação                                           |
| ----- | ------------------------------------------------------------------------------- | -------------------------------------------------- |
| R01   | Fundação executável, banco, API/worker, diagnóstico e shell web                 | Aceita; regressão deve permanecer funcional        |
| R02   | Projetos, upload MP4 seguro e metadados                                         | Aceita; dados e originais preservados              |
| R03   | Streaming/Range, player, timeline/trim, presets e revisões v1                   | Aceita; contratos preservados                      |
| R04   | Textos, MP3, volumes/loop/fades, v2 e prévias curtas reais                      | Aceita; evidências históricas preservadas          |
| R05   | Render completo de revisão salva, progresso, cancelamento, download e histórico | Concluída; gates 0–5 e 22/22 critérios verificados |
| R06   | Robustez, segurança, acessibilidade e QA                                        | Não iniciada; exige nova autorização               |
| R07   | Integração técnica isolada do heditor da PycodeBr                               | Futura; não instalar/executar agora                |
| R08   | Prompts, agente de IA e edição assistida revisável                              | Futura; nenhuma integração LLM atual               |
| R09   | Integrações externas opcionais, como Higgsfield                                 | Futura; não implementada                           |

R05.0 registrou baseline, contratos e estratégia antes das alterações.
R05.1 entregou migration incremental 0005, RenderJob e API com identidade
idempotente persistente, cancelamento durável e histórico paginado.
R05.2 reutilizou o compositor R04: export direto do trim inteiro, quatro
resoluções finais, perfis standard/high a 30 fps, validação integral e progresso
real. Prévias continuam reduzidas, 24 fps e até oito segundos.

R05.3 integrou PreviewJob/RenderJob no mesmo scheduler PostgreSQL serial, com
lease, heartbeat, fencing, retry finito e cancelamento do grupo de processo do
próprio job. PostgreSQL concorrente, cinco cenários de processo, preview R04 e
exportações reais v1/v2 passaram; a falha high/4:5 inicial foi preservada e
corrigida sem aumentar limites ou mudar perfis.

R05.4 adicionou ao editor existente o painel de exportação, perfis, acompanhamento,
cancelamento confirmado, player/download e histórico paginado, com recuperação
de estado e identidade idempotente de solicitações incertas. O gate passou com
312 testes frontend inicialmente; o teste do erro de fila global levou a suíte
final a 313, com lint/formatação/build TypeScript/Vite aprovados.

Na R05.5, a [regressão final](r05/evidence/full-compose-final-summary.json) passou:
1140 casos backend distintos (1110 padrão + 30 PG opt-in), 313 frontend e
lint/formatação/build aprovados. Nove testes Python host do harness ficam
separados, conforme a [união JUnit](r05/evidence/backend-distinct-final-counts.json).
Os 29 checks operacionais Compose e os checks HTTP/FFmpeg não são somados aos
totais de testes. A [queda real do banco durante encoding](r05/evidence/db-outage-attempt-1/report.json)
passou em 37 verificações, incluindo fencing, restauração e retry concluído.

O [Chromium real](r05/evidence/browser-attempt-1/r05-export-1792717682595939151.json)
passou em 15 verificações, incluindo exportação/reprodução/download de 12 s,
cancelamento, temas e mobile emulado 375/390 px. A [reabertura na imagem final](r05/evidence/browser-attempt-3/r05-verify-1792720452716863901.json)
passou em cinco após reinício. [Históricos, prévia e preservação](r05/evidence/post-admission-verification-summary.json)
foram revalidados. A [auditoria final](r05/evidence/storage-admission-final.json)
conferiu 197 MP4, dez MP3, oito prévias e 14 renders, sem temporários ou
divergências; o [baseline histórico e ObraControl](r05/evidence/preservation-admission-final.json)
permaneceram intactos. As negativas anteriores continuam registradas.

Exports são vinculados exclusivamente a revisões imutáveis salvas v1/v2.
Nenhum draft é salvo implicitamente. Originais MP4/MP3, revisões, prévias,
exports concluídos e histórico permanecem privados e preservados. Não existe
retenção destrutiva automática. ObraControl, portas/volumes externos e Git
continuam fora da intervenção; Git é gerenciado manualmente pelo usuário.

A R04 possui QA histórico preservado e a R05 possui seu próprio QA Chromium e
áudio sintético objetivo. Não houve escuta humana nem testes em outros navegadores
ou dispositivos físicos. Mixagens de ganhos
altos podem saturar e a precisão temporal é limitada pelo FPS/seek.

A base está tecnicamente pronta para R06; essa etapa não foi iniciada e exige
nova autorização do usuário.
Não antecipar multitrilha, transições, 4K, novos codecs, exportação em nuvem,
autenticação SaaS, Redis/Celery, WebSockets, heditor, IA ou Higgsfield.
