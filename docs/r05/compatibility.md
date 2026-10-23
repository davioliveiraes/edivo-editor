# Edivo — R05.0: auditoria e compatibilidade

Auditoria do checkout local em **21/10/2026**, registrada antes de alterar código,
configurações, migrations ou dados da R05. O agente principal leu integralmente
os 16 documentos obrigatórios; a inspeção delegada do backend foi somente
leitura. O estado inicial informado pela inspeção do checkout contém apenas a
especificação R05 não rastreada, sem alterações preexistentes de implementação.
Documentos, evidências, dependências e migrations R00–R04 serão preservados.

A autorização expressa do usuário libera a implementação R05. O texto de
proposta da especificação e os limites R04 anteriores no AGENTS são históricos;
não serão usados para impedir o escopo R05 agora autorizado nem para autorizar
R06, IA, heditor ou integrações externas. Não há incompatibilidade essencial que
exija alteração destrutiva. ObraControl permanece fora de qualquer intervenção.

**Gate R05.0 concluído e liberado antes da implementação.** O
[baseline operacional](evidence/baseline.json) foi capturado por leitura e
conferido: PostgreSQL na revisão `0004_r04_text_audio_previews`, 80 projetos,
83 mídias, seis áudios, 60 revisões e 49 PreviewJobs. Nenhum job ativo.
Os 83 MP4, seis MP3 e cinco artefatos de prévia conferem com SHA-256 e modo
0400, sem temporários, arquivos desconhecidos ou divergências. Foram registrados
165 hashes de documentos/evidências/migrations/locks históricos e os metadados
dos cinco containers ObraControl, sem alterar serviços ou dados. Python 3.12.3
na venv existente, Node 24.14.0 e npm 11.9.0 foram encontrados; a venv será
preservada. FFmpeg/FFprobe estão nos runtimes Docker, não no host.
Este gate autoriza avançar para R05.1; não declara os recursos R05 implementados.

## Fontes e referência histórica

Fontes lidas: AGENTS, README, documentação permanente, planejamento R00,
especificações R01–R05, arquitetura, roadmap, aceite R03, aceite/QA/matriz R04.
Foram inspecionados modelos, schemas, repositórios, serviços, API, storage,
compositor, supervisor, worker, configurações, migrations e testes do backend;
os contratos React do editor, textos, áudio, prévias e navegação também são
parte da auditoria do agente principal.

R04 reporta 692 casos padrão e oito PostgreSQL distintos, 149 frontend e 17/17
critérios, com 83 MP4, seis MP3 e cinco prévias consistentes na auditoria final.
Esses números são referências históricas, não resultados de execução R05. A
falha intermitente Vitest histórica permanece documentada. Novas falhas terão
logs completos e contexto retidos, sem substituir uma tentativa negativa por
uma reexecução aprovada.

## Matriz de compatibilidade

| Contrato real R04                                                                                | Evolução compatível R05                                                                               |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Migration `0004_r04_text_audio_previews`, descendente de `0003_r03_edit_spec_revisions`          | Criar somente `0005_r05_render_exports` descendente de 0004; preservar 0001–0004                      |
| `EditSpecRevision` aceita schemas 1/2; listener ORM e trigger PostgreSQL bloqueiam UPDATE/DELETE | Render aponta para UUID da revisão salva; nenhum snapshot será regravado                              |
| `EditSpecService.put()` bloqueia Project, verifica `expected_revision` antes do no-op e usa 409  | Exportar não salva nem altera numeração; revisões antigas continuam exportáveis                       |
| União discriminada `EditSpecV1`/`EditSpecV2`, extras proibidos e valores finitos                 | Aceitar ambas no mesmo compositor; defaults neutros v1 apenas em memória                              |
| `MediaAsset` MP4 e `AudioAsset` MP3 separados, originais SHA-256/0400                            | Reusar inventário/descritores; não mudar arquivos, constraints ou uploads                             |
| `PreviewJob` com janela 1–8000 ms e quatro estados                                               | Manter tabela/contrato; adicionar `RenderJob` próprio com cancelamento e histórico                    |
| `PreviewJobRepository` usa SKIP LOCKED, token, lease e relógio de parede PostgreSQL              | Reusar protocolo e limites finitos em RenderJob, com verificação de cancelamento                      |
| `PreviewWorker` processa uma tarefa por vez e confirma claim antes do subprocesso                | Um scheduler compartilhado escolhe preview/render; um FFmpeg ativo por worker                         |
| `ProcessRunner` supervisiona grupo de processos, limites e heartbeat                             | Acrescentar política explícita de render e progresso incremental, conservando defaults R04            |
| `render_preview()` compõe diretamente uma janela e valida toda a saída                           | Parametrizar o núcleo existente por perfil/janela; renderizar o trim completo, sem concatenar prévias |
| `LocalStorage` usa namespace autorizado, diretórios privados e publicação exclusiva              | Acrescentar `renders/{project_uuid}/{render_uuid}/final.mp4`, sem overwrite/limpeza automática        |
| `PrivateMediaResponse` serve um descritor autorizado com GET/HEAD/Range                          | Reusar para download com attachment/filename controlados, mantendo inline R03/R04                     |
| Respostas R04 expõem `revision_id`, `byte_size`, `checksum_sha256`, `status_url`, `file_url`     | Usar os mesmos nomes públicos na R05 e acrescentar progresso/metadados reais                          |
| React usa hash routing, salvamento explícito, dirty/conflitos, player e polling abortável        | Integrar painel de exportação/histórico no editor existente; nenhum autosave implícito                |
| Serviços locais em loopback, portas externas 8001/5174 e banco/worker privados                   | Manter rede, mounts, volumes, permissões e ObraControl                                                |

## Modelos, schemas e transações existentes

- `app/models/preview_job.py`: `PreviewJob` registra revisão, janela, versão de
  perfil, fingerprint interno, status/stage, tentativas, `claim_token`, lease,
  heartbeat, timestamps, storage key interna, SHA-256, tamanho e código de erro.
  Estados: `queued`, `running`, `completed`, `failed`. Estágios em execução:
  `preparing`, `encoding`, `validating`, `publishing`. Constraint de duração
  limita a 8000 ms; tentativas são limitadas a duas.
- `app/models/edit_spec_revision.py`: revisão append-only com número único por
  projeto e JSONB. A trigger `edit_spec_revisions_immutable` foi criada na 0003 e
  permanece intocada pela 0004. A 0004 amplia apenas o CHECK de versão para 1/2.
- `app/schemas/edit_spec.py`: `EditSpec` e `EditSpecPut` são uniões discriminadas;
  schemas rejeitam extras, tempos não inteiros, coordenadas/volumes não finitos,
  textos/intervalos inválidos e IDs duplicados. `AudioSpec()` fornece defaults
  neutros para v1 sem alterar o snapshot persistido.
- `ProjectRepository.get_active(..., for_update=True)` fornece o lock usado em
  save, soft delete, enqueue e publicação. A ordem será **Project → Job** também
  para cancelamento/finalização R05. Sessões são síncronas, com transações curtas
  e commit explicitamente pertencente ao serviço; nenhuma sessão de banco deve
  permanecer aberta durante codificação ou transmissão do arquivo.
- `Database` usa psycopg, pool com pre-ping, parâmetros ocultos e timeout de
  statement; `safe_rollback()` preserva o erro sanitizado original se a conexão
  já estiver indisponível.

## Infraestrutura reutilizável de worker

`app/repositories/preview_jobs.py` contém `PreviewClaim` congelado e
`PreviewJobRepository`. Funções/métodos relevantes:

- `claim_query()`/`claim_next()`: FIFO por `created_at,id`, somente projeto ativo,
  `FOR UPDATE OF preview_jobs SKIP LOCKED`, novo token por tentativa.
- `database_now()`: `clock_timestamp()`, inclusive após espera por lock.
- `ownership()`, `heartbeat()`, `set_stage()`, `is_owned()`: alterações
  condicionadas a job running, token correto, projeto ativo e lease vigente.
- `lock_owned_for_publish()`: lock seguido de nova consulta ao relógio de parede.
- `fail_owned()` e `recover_expired()`: retries finitos, recuperação de órfãos,
  terminalização de projetos excluídos e tentativas esgotadas, incluindo redução
  de configuração durante a vida do job.

`PreviewWorker.run_one()` confirma claim antes de chamar o processor. O
`LeaseController` renova lease e registra estágios depois de commit confirmado.
`worker.main.run()` preserva diagnósticos R01 e sinais SIGTERM/SIGINT.

R05 introduzirá um **scheduler único, FIFO entre os dois tipos elegíveis**, sem
preempção: uma exportação iniciada termina, falha ou é cancelada antes de o mesmo
worker começar outra tarefa. Não haverá dois loops de fila desconectados nem
dois subprocessos FFmpeg simultâneos por worker na configuração inicial. Claim,
lease, heartbeat, fencing e recuperação continuam transacionais. A política e
os testes de dois candidatos distinguirão concorrência de banco de execução
simultânea efetivamente autorizada.

`ProcessRunner`/`ProcessLimits`/`terminate_group()`/`resource_exec.py` já fornecem
argv estruturado sem shell, `-nostdin` no FFmpeg, ambiente sem DSN, sessão/grupo
próprio, descritores herdados autorizados, output limitado, deadline global,
heartbeat durante preparação/hash e encerramento SIGTERM/grace/SIGKILL. O
supervisor atual não tem callback de progresso incremental e usa orçamento
preview; a evolução acrescentará política explícita mantendo esses defaults.

Cancelamento necessita sinal/exceção próprio: o heartbeat booleano R04 não
distingue cancelamento de lease perdido. A API persiste intenção, nunca sinaliza
processos. O supervisor para somente o grupo pertencente à tentativa. Estado
`canceled` só é confirmado depois do término ou recuperação segura. A publicação
revalida token/lease e cancelamento sob lock antes/depois da promoção.

## Assinatura real do compositor e evolução

Arquivo: `app/integrations/ffmpeg/preview.py`.

```python
render_preview(
    settings,
    plan,
    *,
    source_fd,
    source_video_stream_index,
    source_has_audio,
    background_fd,
    preview_start_ms,
    duration_ms,
    workspace,
    runner,
    set_stage,
) -> PreviewArtifact
```

`PreviewArtifact` contém `path`, `byte_size`, `checksum_sha256` e metadados
controlados. O compositor não acessa banco nem publica arquivos. Recebe inputs
previamente autorizados por descritor. `framing_filter()`, `_text_filters()`,
`_audio_graph()`, `validate_metadata()`, `validate_artifact()` e `_faststart()`
formam o núcleo a ser parametrizado, preservando wrappers e testes R04.

Acoplamentos reais a preview: `PROFILE_SIZES`, FPS 24, teto de janela 8000 ms,
duração validada até 8.001 s, nome `preview.part`, veryfast/CRF28/AAC128k e
configurações `preview_*`. O perfil final introduzirá tamanhos finais, FPS 30 e
standard/high sem duplicar esse motor. O render final compõe diretamente todo
`trim.start_ms..trim.end_ms`; não monta trechos de prévia.

Textos usam arquivos UTF-8 exclusivos privados, expansão desativada, fonte local
aprovada, métricas FreeType e margem 7%. Música inicia no zero relativo do trim;
fades usam o trim completo, inclusive em janelas intermediárias. Essas semânticas
serão conservadas nos exports. `validate_artifact()` já verifica FFprobe, perfil,
faststart, decodificação completa/frame count, tamanho e checksum antes de
retornar sucesso. A tolerância final a 30 fps será medida, sem reaproveitar
automaticamente a tolerância de 24 fps.

Progresso precisa leitura incremental limitada de `-progress pipe:1`, confirmação
experimental das unidades emitidas pelo FFmpeg instalado e persistência
monotônica por tentativa. `progress_encoded_ms`/`progress_percent` públicos serão
`null` quando indeterminados; até 99% antes da validação/publicação/commit e 100%
somente no estado completed confirmado.

## RenderJob, identidade e respostas fixadas

RenderJob será uma tabela própria, com FKs RESTRICT, estado/estágio, perfil,
revisão exata, tentativas finitas, token/lease/heartbeat, cancelamento durável,
progresso, identidade do motor/requisição e metadados reais do artefato. Não será
criada uma tabela redundante de exportações. Limite inicial: **um render ativo
por projeto**, incluindo queued/running. Histórico ordenado por
`created_at DESC,id DESC`, com paginação conforme os contratos R02.

Política de idempotência:

1. Chave explícita é permanente no escopo do projeto. Mesma chave e parâmetros
   iguais sempre identificam o mesmo job; parâmetros diferentes retornam 409.
2. Failed/canceled não reabrem. Nova solicitação explícita usa nova chave e
   preserva o job anterior.
3. Solicitação sem chave pode reutilizar fingerprint equivalente ativo ou
   completed íntegro. Fingerprint considera projeto, revisão, perfil, versão do
   compositor e parâmetros efetivos relevantes.
4. Uma chave explícita nova não será associada silenciosamente ao job de outra
   chave sem registro persistente de alias. A implementação inicial manterá a
   identidade própria e respeitará o limite ativo.
5. Completed ausente/corrompido permanece histórico completed, indisponível para
   download/reuso. Mesma chave não cria outro job; nova chave explícita pode
   gerar outro UUID, sem sobrescrever/apagar o artefato anterior.

As respostas usam nomes coerentes com R04: `revision_id`, `revision_number`,
`byte_size`, `checksum_sha256`, `status_url`, `file_url`, além de perfil, estado,
estágio, cancelamento, progresso e metadados reais. Nenhuma chave de storage,
token, path, comando, DSN ou stack trace entra no JSON público. Envelope de erros
`{code,message,request_id}` e `X-Request-ID` permanecem.

## Storage, streaming e integridade

`LocalStorage._open_managed()` fixa cada diretório por `dir_fd/O_NOFOLLOW`, exige
chave exata gerada pelo servidor, arquivo regular e tamanho esperado, rejeitando
symlinks e travessia. `OpenedOriginal` transfere a propriedade do descritor ao
chamador. `PreviewProcessor` verifica SHA/modo/mtime dos inputs, usa workspace
privado por job/token e verifica novamente a saída antes de publicar.

O padrão de `promote_preview()` será reutilizado para renders: destino UUID
exclusivo, hardlink sem overwrite, modo 0400, fsync e revisão conservadora de
falhas/COMMIT incerto. Namespace novo: `renders/{project_id}/{job_id}/final.mp4`.
Não haverá retenção destrutiva ou remoção de canônicos desconhecidos. Somente
temporários inequivocamente pertencentes à tentativa atual podem ser limpos.

`PrivateMediaResponse` adapta FileResponse para o FD autorizado, leitura limitada,
HEAD, Range único fechado/aberto/suffix, 200/206/416 e fechamento protegido sob
cancelamento. Bloqueia `pathsend`, ranges múltiplos e reabertura por pathname.
R05 acrescentará attachment e filename seguro gerado no servidor; os endpoints
R03/R04 continuarão inline. A resposta416 também deve manter os headers privados.

Integridade completa é exigida na reutilização e download. A listagem paginada
não deve provocar hash irrestrito de todos os arquivos de até 2 GiB a cada poll;
a política de disponibilidade/listagem será documentada e testada sem prometer
integridade que não tenha sido verificada.

## Riscos e validação necessária

- Preservar ordem de locks, relógio fresco, commit do claim e fencing, inclusive
  nas corridas cancelamento/publicação e perda de resposta de COMMIT.
- Não confundir a fingerprint interna R04 com chave idempotente explícita R05.
- Preservar o teto e orçamento de preview ao permitir render de até 3600 s de
  execução e resultado configurável de até 2 GiB; o teto não promete desempenho.
- ProcessRunner atual acumula stdout limitado: render longo exige parser em
  fluxo, não aumento ilimitado de buffer.
- Cgroup do worker mantém recursos restritos; standard/high em resolução final
  precisam de prova real no runtime, sem aumento de privilégios nem alteração de
  ObraControl.
- Não usar a configuração de tamanho máximo de upload como limite de resultado.
  Verificar espaço antes de começar; não remover arquivos para liberar espaço.
- Preservar proporção/rotação/SAR/VFR, sincronização e a semântica temporal R04;
  codecs e glifos não suportados devem falhar explicitamente.
- Chromium e análise objetiva de áudio não equivalem a dispositivos físicos,
  outros navegadores ou escuta humana. Registrar somente o que for executado.
- As suítes PostgreSQL históricas são opt-in e usam `EDIVO_QA_DATABASE_URL`, pois
  `conftest.py` substitui `EDIVO_DATABASE_URL`; não imprimir nenhum dos valores.

### Reserva de memória do alocador — diagnóstico R05.3

A QA HTTP integrada registrou uma falha real `render_encoding_failed` no job
`79a1e1e9-2692-4337-b6ad-45274f7d8231`, perfil **high**, canvas **4:5** e trim
de 1000 a 13000 ms. O [primeiro diagnóstico isolado](evidence/high-encoding-diagnostic-1.json)
reproduziu a falha de libx264: `malloc of size 8418816 failed`, com exit code 1.
O limite efetivo de espaço de endereçamento era **1 GiB por subprocesso**.
O job falho e seu histórico foram preservados; o diagnóstico não alterou banco
nem publicou um artefato.

O [segundo diagnóstico](evidence/high-encoding-diagnostic-2.json) repetiu a mesma
revisão, perfil e limites, restringindo as arenas do alocador glibc por
`MALLOC_ARENA_MAX=2` no ambiente do subprocesso. Produziu **12 s**, **1080×1350**,
H.264/yuv420p/SAR 1:1 a **30 fps**, com AAC e **360 frames decodificados**.
O FFmpeg apresentou `VmPeak` de **856976 KiB** e RSS máximo observado de
**370268 KiB**. Espaço de endereçamento e memória residente medem recursos
distintos; RSS abaixo do teto não assegura que uma reserva virtual será aceita.
O SHA-256 da saída consta na evidência, assim como SHA-256 e modo 0400 dos dois
originais inalterados. Também não houve publicação nem mutação de banco nessa prova.

A correção fixa `MALLOC_ARENA_MAX=2` somente nos subprocessos com política
**render**, dentro do ambiente controlado de `ProcessRunner`. A aplicação não
herda uma configuração arbitrária do host nem cria uma nova variável pública
de configuração. O ambiente das prévias permanece preservado. Não foram
aumentados os limites de memória/CPU/arquivo, nem alterados qualidade, resolução,
threads, codecs ou semântica do compositor para fazer o caso passar.

Essa prova isolada valida o ajuste de alocação para o caso reproduzido; não
equivale à conclusão de um RenderJob publicado. Redeploy, retomada da QA HTTP,
cancelamento em codificação e histórico ainda precisam de suas evidências antes
de liberar o gate R05.3. A falha original permanece registrada, sem ser substituída
pelo diagnóstico aprovado.

### Relógios e margem operacional do lease — diagnóstico R05.3

A [instrumentação com dois relógios](evidence/worker-clock-diagnostic.json)
mediu avanço acumulado do relógio de parede sobre o monotônico de até
**2,761435 s** durante um worker de QA. Os deslocamentos permaneceram em
patamares por centenas de eventos; os snapshots de `clock_timestamp()` do
PostgreSQL acompanharam o relógio de parede do worker nas observações.
O mecanismo específico de ajuste no host/WSL/Docker não foi identificado.
Não houve alteração do relógio usado pelo fencing em produção.

O lease experimental de **2 s**, heartbeat de **0,2 s** e polling de **0,1 s**
do harness oferecem margem insuficiente para os avanços medidos neste ambiente.
As [tentativas 2](evidence/worker-control-attempt-2/report.json) e
[3](evidence/worker-control-attempt-3/report.json) registraram perda inesperada
de lease em execução normal de retry e permanecem como evidências negativas.
Elas não possuem os mesmos registros de relógios pareados: sua causa exata
não pode ser comprovada retrospectivamente pela medição da tentativa seguinte.

Na [tentativa 4](evidence/worker-control-attempt-4/report.json), a expiração
forçada encerrou a tentativa e registrou `render_lease_lost` no **stderr**.
O harness procurou esse evento no stdout, causando uma falha de verificação;
a busca foi corrigida sem modificar a execução do worker. O relatório original
continua marcado como falho. A rodada posterior usa **10 s** de lease e
**0,5 s** de heartbeat somente no harness. Esses valores não substituem os
defaults de produção, que permanecem **30 s** e **5 s**.

Recomenda-se não configurar leases abaixo do timeout SQL atual de **3 s** e
reservar margem adicional para consultas, commit, heartbeat, carga de CPU e
ressincronização do relógio. Um intervalo de heartbeat menor que metade do
lease, embora validado pelas configurações, não garante essa margem por si só.
Esta é uma orientação operacional; os validadores e defaults de produção não
foram alterados para contornar uma ocorrência de QA.

Perda ou expiração de posse impede progresso, publicação e conclusão pela
tentativa antiga. O encerramento conservador e a recuperação com no máximo
duas tentativas preservam os originais e impedem disponibilizar um resultado
sem ownership válido. Este diagnóstico não libera o gate R05.3 nem declara o
aceite final: as demais provas e os resultados completos continuam necessários.

## Sequência de gates

1. **R05.0:** concluir baseline operacional e liberar este gate antes de código.
2. **R05.1:** migration/modelo/schema/API/repository, constraints, histórico,
   idempotência e cancelamento durável, com regressões proporcionais.
3. **R05.2:** parametrizar compositor existente; gerar MP4 final real superior a
   oito segundos, perfis/quatro presets e validação integral.
4. **R05.3:** scheduler compartilhado, progresso real, cancelamento, lease,
   fencing/retry, SIGTERM/crash e provas concorrentes PostgreSQL.
5. **R05.4:** painel React no editor, polling, download privado e histórico,
   preservando controles R03/R04.
6. **R05.5:** regressões integrais, FFmpeg/browser, integridade, documentação e
   avaliação individual dos 22 critérios. R06 permanece não iniciada.

## Atualização cronológica — QA posterior aos diagnósticos

Os diagnósticos acima registram as condições e pendências no momento em que
foram escritos. Não substituem seus relatórios negativos nem reescrevem a
auditoria R05.0. Posteriormente, a imagem com o ajuste de arenas foi aplicada,
o [HTTP retomado](evidence/render-http-finish.json) passou em 191 verificações
e a falha anterior permaneceu no histórico de cinco jobs. Cancelamento real em
encoding, [70 checks de controle de processos](evidence/worker-control-attempt-5/report.json)
e [29 casos PostgreSQL](evidence/phase3-postgres-attempt-1.json) liberaram o gate
R05.3. A interface e seus 312 testes/lint/build liberaram R05.4.

Na execução posterior de R05.5, a [regressão completa Docker](evidence/full-compose-attempt-1.log)
e seus 29 checks operacionais passaram, incluindo Alembic repetível, readiness
com banco offline, storage indisponível, recuperação, worker ocioso/sinais,
FFmpeg/FFprobe e portas/mounts. O [relatório de identidades JUnit](evidence/backend-distinct-counts.json)
confirma **1130 testes backend distintos**: 1101 padrão e 29 PostgreSQL opt-in,
sem duplicação. **312 testes frontend** passaram no runtime final. Os **nove
testes Python host** do harness são outro escopo (1139 Python ao todo); checks
HTTP/FFmpeg/navegador não entram nesses totais.

A [queda real do PostgreSQL durante encoding](evidence/db-outage-attempt-1/report.json)
foi então executada em um projeto sintético novo e passou em **37 verificações**.
O worker principal já estava parado e todas as filas estavam vazias antes da
prova. O roteiro parou/restaurou somente o banco Edivo, confirmou health 200 e
ready 503, encerramento da tentativa sem publicação e retry 2 com fencing novo.
O mesmo banco voltou saudável; o render de 12 s/1080×1920/30 fps passou por
FFprobe, decode e SHA-256. O original manteve SHA/0400/mtime, e os one-offs
dedicados foram removidos. ENOSPC, fsync e COMMIT incerto continuam cobertos por
falhas injetadas, sem alegação de pane física observada.

Chromium R05 está em execução; recuperação final após reinício, auditoria final
de integridade e avaliação dos 22 critérios ainda estão pendentes. A R05 não
foi declarada concluída, e R06 não foi iniciada.

## Fechamento R05.5 — admissão global finita

Após as suítes e a primeira prova Chromium, a revisão final identificou que o
limite de um render por projeto não limitava o total entre projetos com o worker
parado. Essa lacuna do requisito de fila finita foi corrigida dentro da R05,
sem migration adicional ou alteração dos snapshots/contratos de prévia.

`EDIVO_RENDER_MAX_ACTIVE_TOTAL=20` (1–1000) limita RenderJobs queued/running em
todos os projetos, inclusive excluídos. No caminho de criação nova, depois de
replay/reuso e da validação da fonte, Project→advisory lock transacional
PostgreSQL `EDIVOR05` serializa COUNT/INSERT. Excesso retorna
`409 render_queue_full`; cancelar ou terminar libera capacidade. A recuperação
já existente encerra queued de projetos excluídos quando o worker volta.
PreviewJob conserva seus limites anteriores; não foi criada segunda fila.

Testes cobrem o último slot entre dois projetos concorrentes, replay no teto,
liberação terminal e recuperação de projeto excluído. A nova regressão completa
e os resultados PostgreSQL serão registrados em saídas novas; os resultados
1130/312 e auditorias anteriores permanecem históricos desta execução, sem
ser sobrescritos ou apresentados como contagens posteriores à correção.

## Encerramento R05.5 — evidências finais

A correção de admissão passou no PostgreSQL real: dois projetos concorreram
pelo último slot e somente um novo job foi admitido. A
[regressão final das imagens](evidence/full-compose-final-summary.json) passou
29 checks Compose e todas as ferramentas de qualidade. A
[união final JUnit](evidence/backend-distinct-final-counts.json) confirma 1140
backend distintos (1110 padrão + 30 PG), além de nove testes host separados;
313 frontend passaram (149 históricos + 127 cliente + 37 componentes).
Os valores 1130/312 nas entradas cronológicas acima são execuções anteriores
preservadas, não os totais finais ou parcelas a somar.

O fluxo Chromium real passou 15 checks; a
[reabertura na imagem final](evidence/browser-attempt-3/invocation.json) passou
cinco checks após reinício/recriação. HTTP de renders/prévias passou 88/26,
sem recriar os resultados concluídos. A
[auditoria final](evidence/storage-admission-final.json) verificou 197 MP4,
10 MP3, oito prévias e 14 exports íntegros, zero temporários/desconhecidos.
A [preservação](evidence/preservation-admission-final.json) confirmou os 165
arquivos históricos, 94 artefatos anteriores, registros, trigger, locks/Git e
cinco containers ObraControl. As
[fontes API/worker/frontend no runtime](evidence/final-runtime-source-hashes.json)
conferem com o checkout, inclusive configuração efetiva do teto global.

R05 concluída com [22/22 critérios](acceptance.md) aprovados e
[relatório A–H](report.md). As negativas e limitações permanecem registradas;
nenhum documento histórico foi reescrito. A base está tecnicamente pronta para
R06, que **não foi iniciada** e depende de nova autorização.
