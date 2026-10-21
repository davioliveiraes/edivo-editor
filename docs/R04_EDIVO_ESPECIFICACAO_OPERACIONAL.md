# Edivo — R04 | Especificação operacional: Textos, Áudio e Geração de Prévia

**Data:** 18/10/2026  
**Produto:** Edivo · **Repositório:** `edivo-editor` (`davioliveiraes/edivo-editor`)  
**Situação:** especificação para revisão e aceite; **não autoriza implementação nesta entrega documental**.  
**Dependências:** R00–R03; R03 concluída e validada conforme `docs/r03/acceptance.md` e `docs/r03/qa-results.json`.  
**Identidade aprovada:** editor visual **orientado principalmente por prompts de IA**, com integração real da skill `heditor` na R07 e experiência de agente/conversa na R08. R04 prepara contratos e motor local, **sem implementar IA**.

## 1. Base da especificação e escopo de auditoria

Análise somente leitura da branch `main` publicada no GitHub em 18/10/2026: `AGENTS.md`, `README.md`, `docs/roadmap.md`, `docs/R03_EDIVO_ESPECIFICACAO_OPERACIONAL.md`, `docs/r03/acceptance.md`, `docs/r03/qa-results.json`, `backend/app/{models/edit_spec_revision.py,schemas/edit_spec.py,services/edit_specs.py,repositories/edit_specs.py,api/v1/edit_specs.py,worker/main.py,core/config.py}`, `backend/alembic/versions/0003_r03_edit_spec_revisions.py`, `frontend/src/{lib/api/edit-spec.ts,features/editor/editor-workspace.tsx}`. Reconsultar **o checkout local** antes de implementar: o GitHub pode não incluir mudanças locais em andamento.

### Contratos reais da R03 que DEVEM ser preservados

- `EditSpecV1`: `schema_version=1`, `source_media_id` UUID, `trim.{start_ms,end_ms}`, `canvas.{preset,fit,focal_x,focal_y}`; Pydantic rejeita propriedades extras; milissegundos inteiros.
- `EditSpecPut` exige `expected_revision`; `GET/PUT /api/v1/projects/{project_id}/edit-spec` retornam 404 antes do primeiro save, 201 na criação, 200 em revisões/no-op, 409 em conflito. Lock pessimista na linha de `Project`; o save idêntico à revisão atual é no-op, sem novo número. Histórico no banco é **append-only** por trigger SQL e proteção ORM.
- Migração `0003_r03_edit_spec_revisions`: `edit_spec_revisions.schema_version` possui **CHECK `= 1`**. Um esquema v2 não funcionará até evoluir a constraint em **nova migration**. Não editar migrations anteriores nem registros/revisões antigas.
- `MediaAsset` R02 é estritamente `kind=source_video`, `mime_detected=video/mp4`, dimensões e FPS positivos; **não reaproveitar esse modelo sem migração incompatível para música**. Preferir entidade independente `AudioAsset`.
- R03 dispõe de player e timeline de uma faixa; streaming MP4 privado com Range, sem servir storage como estático. Frontend navega por hash `#/projects/{uuid}/editor` e trabalha com rascunho e save explícito. O worker apenas valida dependências e aguarda `SIGTERM`; não há fila de jobs.
- Ambiente local usa API `127.0.0.1:8001`, frontend `127.0.0.1:5174`, portas internas 8000/5173; PostgreSQL volume `edivo_postgres_data`, storage privado montado na API e no worker. Manter ObraControl intocado.
- Baseline QA: 315 testes backend e 75 frontend informados, 30 originais MP4 auditados, modos `0400`. Esses números são **referência histórica**, não meta artificial de contagem futura.

### Limite da evidência

As verificações acima demonstram contratos **publicados**, não provam o funcionamento futuro da R04 nem substituem QA local. Nenhum endpoint/migration/render R04 foi criado ou executado durante esta especificação.

## 2. Meta de produto e definição de pronto

O usuário escolhe uma edição existente, adiciona até três textos simples com posição e intervalos temporais, regula o volume do áudio original, opcionalmente importa **uma música MP3** e a utiliza como fundo, salva o `EditSpec v2` sem alterar o original e solicita uma **prévia MP4 curta e real**, calculada com FFmpeg fora do processo HTTP. A interface exibe fila/processamento/erro/resultado reais, e reproduz o artefato com acesso privado.

A prévia estará **vinculada a uma revisão imutável salva** e a um intervalo explícito; alterações locais não salvas não poderão criar resultados que aparentem corresponder à revisão atual. O formato visual deve ser consistente com a futura composição R05 e reutilizável por agentes R07/R08.

### Entra na R04

1. Evolução compatível `EditSpec v1 → v2` com textos e áudio.
2. Textos simples sobrepostos à visualização e à prévia codificada, com estilos controlados.
3. Volume do áudio original; música de fundo opcional de **um único MP3 fornecido pelo usuário** e volume independente; fades simples do mix.
4. Importação MP3 com storage privado, checksum, FFprobe, validações e biblioteca mínima de músicas por projeto.
5. Preview de **trecho de até 8 s**, em resolução reduzida, codificado pelo **worker** com FFmpeg, sem bloquear requisições API.
6. Modelo/estado mínimo persistente de `PreviewJob`, endpoint de solicitação/status/leitura e polling no frontend.
7. Testes, smoke real FFmpeg/FFprobe, segurança, regressões R01–R03 e documentação.

### Não entra na R04

- Render de vídeo completo, exportação final, perfis de qualidade final, histórico de exportações e interface de downloads finais (**R05**).
- Fila generalista de render, prioridade avançada, pausa/cancelamento via API, reprocessamento em lote, notificações distribuídas (**R05**).
- Legendas automáticas, reconhecimento de fala/Whisper, texto palavra a palavra, animações GSAP, templates heditor, motion graphics complexos, multitrilha, keyframes e transições (**R07/R08 ou posterior**).
- Campo de prompt, agente, modelos LLM, instalação/execução heditor, Higgsfield, Redis/Celery, GPU, SaaS/autenticação pública.
- Áudio externo por URL, catálogo comercial, licença presumida de músicas, download de fontes, MP3 transcodificado para original, alteração dos originais, publicação do storage.

## 3. Decisões arquiteturais para minimizar retrabalho

| Tema | Decisão R04 | Motivo / fronteira |
|---|---|---|
| Fonte da verdade | `EditSpecRevision` imutável, com `schema_version=1` ou `2` | Preserva todas as revisões R03 e elimina edição destrutiva. |
| Render | `PreviewJob` persistido e processado pelo **worker existente** | FFmpeg pesado jamais no request FastAPI nem em `BackgroundTasks` volátil. |
| Áudio extra | `AudioAsset` em tabela própria | Respeita constraints rígidas de `MediaAsset` R02. |
| MP3 | Um arquivo por upload; sem áudio remoto | Aumenta utilidade sem virar editor multitrilha. |
| Textos | Texto estático com duração e style tokens | Preparado para executor local e heditor futuro sem aceitar código livre. |
| Previews | MP4 H.264/AAC privado, max. 8 s, qualidade reduzida | Diferente de exportação final R05; validável no navegador. |
| Fila | PostgreSQL `FOR UPDATE SKIP LOCKED`, lease e reconciliação mínima | Sem infraestrutura adicional; base reutilizável na R05. |
| UX | Save explícito antes de gerar; status real por polling | Não renderiza rascunhos não confirmados. |

### Definição temporal (obrigatória)

- `trim.start_ms/end_ms`: tempos **absolutos na fonte**, como na R03.
- `text_overlays[].start_ms/end_ms`: também tempos **absolutos na fonte**; dentro do intervalo de trim.
- `preview_start_ms`: instante **absoluto da fonte** dentro do trim; a prévia representa `[preview_start_ms, min(preview_start_ms + duration_ms, trim.end_ms)]`.
- A música inicia no tempo relativo zero do **trecho completo recortado** (`trim.start_ms`); se a prévia começar no meio do trim, o offset musical deverá refletir o tempo já transcorrido. Sem saltos arbitrários ao mover o início da prévia.
- Os fades de mix aplicam-se aos extremos do **trim completo**. Uma prévia intermediária não deve artificialmente introduzir fade-in/fade-out só por começar/terminar o pequeno arquivo, salvo limitação explicitamente documentada.
- Converter corretamente PTS/áudio após seek. VFR, sample aspect ratio e matriz de rotação não devem gerar vídeo invertido, crop errado nem áudio fora de sincronia.

## 4. EditSpec v2 — contrato de dados proposto

Exemplo **ilustrativo de payload completo**, com IDs reais a substituir:

```json
{
  "schema_version": 2,
  "source_media_id": "11111111-1111-4111-8111-111111111111",
  "trim": { "start_ms": 1000, "end_ms": 9000 },
  "canvas": {
    "preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5
  },
  "text_overlays": [
    {
      "id": "22222222-2222-4222-8222-222222222222",
      "text": "Seu próximo vídeo começa aqui",
      "start_ms": 1000,
      "end_ms": 8500,
      "position": "bottom",
      "style": "blue_outline",
      "size": "medium"
    }
  ],
  "audio": {
    "source_volume": 1.0,
    "background_audio_id": null,
    "background_volume": 0.15,
    "background_loop": false,
    "fade_in_ms": 0,
    "fade_out_ms": 300
  }
}
```

O `PUT` mantém `expected_revision` **no mesmo nível superior do payload**, conforme contrato R03, mas `expected_revision` **não** integra o snapshot salvo em `spec_json`.

### Regras específicas

- Aceitar `schema_version=1` **sem mudança de significado e sem modificar a revisão gravada**. Para editar no novo UI, normalizar v1 em memória a v2 adicionando `text_overlays=[]` e `audio` com defaults; só persistir v2 depois de ação explícita de salvar. Uma revisão existente v1 deve continuar retornando seu formato v1 (discriminated union), com GET e testes históricos válidos.
- `text_overlays`: 0 a **3** itens; `id` UUID gerado no frontend/backend; sem IDs duplicados; `text` com 1 a 120 caracteres após trim, até **2 linhas**; sem controles invisíveis, HTML, scripts, nomes de arquivo ou expressões de filtros; entradas ainda precisam escapar corretamente na renderização (acentos/aspas/dois-pontos/barra invertida). Campos extras proibidos.
- `start_ms`/`end_ms`: inteiros, início menor que fim e ambos dentro do trim. Ao alterar trim ou fonte, não fazer save silencioso de textos fora do intervalo; solicitar ajuste/confirmar descarte de sobreposições incompatíveis.
- `position`: `top|center|bottom`; posicionamento dentro de margem segura de **7%** da composição; alinhamento horizontal central. `style`: `white_outline|blue_outline|white_plain`; `size`: `small|medium|large`. Fontes/cores/espessuras são presets da aplicação, **não** caminhos ou filtros enviados pelo usuário.
- `source_volume`: `[0,2]`, default `1`; áudio inexistente na fonte não é erro — saída sem áudio se nenhuma música for adicionada.
- `background_audio_id`: UUID da biblioteca do **mesmo projeto ativo**, ou `null`; só **uma** música por edição. `background_volume`: `[0,1]`, default `0.15` (valor de exemplo, não promessa de loudness); `background_loop`: booleano.
- `fade_in_ms`/`fade_out_ms`: inteiros no intervalo `[0,3000]`, soma não pode ultrapassar a duração do trim; aplicam-se ao mix de saída, respeitando instante absoluto do trim e preview intermediário.
- Ficar com `audio.background_audio_id=null` não remove áudio do vídeo original. Não exigir música para salvar a revisão.
- Preservar a validação do `source_media_id`, FPS/trim, `canvas`, projeto ativo, objeto JSON tipado, idempotência/no-op e conflito 409 já implementados na R03.
- A prévia **não** pode depender de strings FFmpeg arbitrárias, HTML ou código enviado por frontend/agente. Esse esquema é o futuro contrato de alto nível que o agente poderá preencher **após validação e revisão humana**.

### Migração `0004_r04_text_audio_previews`

Criar migração nova, descendente de `0003_r03_edit_spec_revisions`:

- Substituir **somente** a constraint `ck_edit_spec_revisions_schema_version` de `= 1` por `IN (1,2)`; manter trigger de imutabilidade e todos os registros antigos.
- Criar `audio_assets` e `preview_jobs` com índices, FKs e checks adequados.
- Preservar estrutura/constraints de `projects` e `media_assets`. Não criar migração de dados retroativa para v1 e não executar downgrade destrutivo.
- Registrar `schema_version=2` sem quebrar testes que esperam `schema_version=1` para planos já gravados.

### `AudioAsset` (tabela nova)

`id UUID`, `project_id UUID FK RESTRICT`, `original_filename` saneado, `storage_key` única **interna**, `checksum_sha256`, `byte_size`, `mime_detected`, `duration_ms`, `codec`, `sample_rate`, `channels`, `created_at UTC`, com constraints de valores válidos. Sem caminhos locais no JSON público. Original áudio separado de `MediaAsset`, modo `0400`, sem sobrescrita. Uploads do mesmo arquivo são entidades distintas (não deduplicar implicitamente).

### `PreviewJob` (tabela nova, mínimo para R04)

`id UUID`, `project_id FK`, `edit_spec_revision_id FK` para snapshot imutável, `preview_start_ms`, `duration_ms`, `status` (`queued|running|completed|failed`), `attempts`, `lease_expires_at`, `heartbeat_at`, `created_at`, `started_at`, `finished_at`, `artifact_storage_key` interna opcional, `artifact_sha256`, `artifact_byte_size`, `error_code` sanitizado e chave determinística/idempotente da solicitação conforme decisão documentada na implementação. Sem URL física ou caminho de usuário no banco público. Limitar tarefas ativas por projeto.

## 5. Endpoints da R04

Os endpoints R01–R03 permanecem iguais. Adicionar os seguintes contratos:

| Método | Endpoint | Semântica de sucesso |
|---|---|---|
| POST | `/api/v1/projects/{project_id}/audio` | `201` importa um MP3 validado |
| GET | `/api/v1/projects/{project_id}/audio` | `200` lista músicas do projeto (`{items,total,limit,offset}`) |
| GET | `/api/v1/projects/{project_id}/audio/{audio_id}` | `200` metadados de música |
| POST | `/api/v1/projects/{project_id}/previews` | `202` tarefa criada/em execução; `200` se reusar resultado válido |
| GET | `/api/v1/projects/{project_id}/previews/{preview_id}` | `200` status e metadados de job |
| GET / HEAD | `/api/v1/projects/{project_id}/previews/{preview_id}/file` | `200`/`206` no artefato pronto, suporte a Range |

`GET/PUT /api/v1/projects/{project_id}/edit-spec` continuam existentes; somente passam a aceitar/retornar v1 **ou** v2 conforme `schema_version`. Não mudar comportamento de `expected_revision`, conflict 409 ou GET inicial 404.

**Exemplo de `POST /previews`:**

```json
{
  "edit_spec_revision_id": "33333333-3333-4333-8333-333333333333",
  "preview_start_ms": 1000,
  "duration_ms": 8000
}
```

- Só aceitar revisão **salva e pertencente ao projeto**. O usuário deve escolher gerar a partir da revisão atual; um job criado para revisão anterior mantém a referência anterior e seu resultado deve ser rotulado com esse número, não falsamente associado ao draft atual.
- `preview_start_ms` dentro do trim e `duration_ms` inteiro `1..8000`, respeitando o trim/limites do vídeo. A janela real truncada ao fim do trim deve ter duração positiva; validar uso de início/fim e arredondamentos.
- Resposta de job inclui `id`, `project_id`, `revision_id`, `revision_number`, janela, `status`, `stage` quando fundamentada, tempos, `error_code` sanitizado, `artifact_available` e URLs **relativas** de consulta quando permitido. Nunca expor armazenamento físico ou logs brutos.
- Solicitações equivalentes ao mesmo plano/janela/configuração podem reusar tarefa pendente ou artefato íntegro; não reutilizar artefato inexistente, corrompido, de revisão diferente ou de outra janela. Documentar a idempotência adotada e testar concorrência.
- Arquivo ainda não pronto: `409 preview_not_ready` (ou outro código consistente **documentado e testado**). Job inexistente, de outro projeto ou projeto soft-deleted: `404`. Falha de dependência: `503`, falha validável: `422` ou `415` conforme contrato. Manter envelope de erro R01 `{code,message,request_id}`.
- A rota da prévia usa gate de projeto/job/artifact antes de reutilizar o leitor privado de R03, com Range/HEAD/nosniff/no-store e descritores fechados. **Nunca** servir arquivo via `/storage` estático.

## 6. Importação segura de áudio

R04 aceita apenas **MP3** de música/efeito licenciado pelo próprio usuário, um arquivo por requisição. Os limites iniciais são:

- `EDIVO_AUDIO_UPLOAD_MAX_BYTES=31457280` (**30 MiB**), configurável.
- Duração **até 600 s**, sem stream de vídeo; MP3 decodificável ao menos em amostra significativa (documentar que inspeção parcial não prova o arquivo inteiro).
- MIME/extensão são sinais auxiliares, mas FFprobe real e validação da estrutura/codec são obrigatórios; rejeitar uploads renomeados, corrompidos, ausentes, multipart inesperado, stream de vídeo, oversized body com/sem Content-Length e MP3 sem áudio.
- Reutilizar os princípios do middleware/parsing R02; aplicar limite **menor** na rota de áudio, sem ampliar silenciosamente o limite genérico. Limites em bytes reais, chunks, staging, SHA-256, quota de disco, timeout, output FFprobe limitado, protocolo local e cleanup.
- Caminho gerado pelo servidor, por exemplo `storage/originals-audio/{project_uuid}/{audio_uuid}/source.mp3`; modo `0400` e storage privado sem symlinks, traversal ou escrita sobre outra mídia. Manter auditabilidade e compensação de falhas, sem apagar originais existentes.
- A biblioteca não concede direitos sobre música. UI deve orientar usar arquivos próprios ou com licença válida; não fazer upload para serviço externo.

## 7. Prévia MP4 — pipeline FFmpeg mínimo

### Perfil inicial proposto

- Máximo **8 segundos por solicitação**, uma janela por job. Duração recortada pela janela e pelo trim.
- `9:16`: **540×960**; `1:1`: **540×540**; `4:5`: **540×675**; `16:9`: **960×540**. Dimensões pares para codecs quando necessário; pixels quadrados no resultado (`setsar=1`).
- FPS de saída controlado, preferencialmente **24 fps** para previews, após tratar timestamps/VFR; H.264 (`libx264`, preset `veryfast`/`ultrafast` testado, `yuv420p`) e AAC (quando houver áudio), MP4 com `+faststart`.
- Logo, selo ou marca-d'água **não são obrigatórios** e não devem ser inventados. Sem qualidade de exportação final.
- Timeout inicial **120 s** para FFmpeg; limites efetivos de CPU/memória/processos, tamanho de saída e disco a serem verificados no runtime. Concorrência padrão **1** preview por worker e até **2 jobs ativos por projeto**. Configurações expostas em `.env.example`, nunca como credenciais públicas.

### Etapas técnicas

1. Ler e validar o snapshot **imutável** de revisão e suas fontes, incluindo pertencimento ao projeto e integridade do storage. O worker não executa comandos arbitrários do usuário.
2. Construir parâmetros FFmpeg estruturados (lista de argumentos, `shell=False`) e grafo de filtros a partir de uma **allowlist**, aplicando crop/contain/focal, dimensões, rotação, SAR e FPS/PTS.
3. Aplicar até 3 textos por `drawtext` (filtro diagnosticado na R01): fontes locais aprovadas, texto UTF-8 escrito em arquivos temporários de workspace **privado**, sem interpolar texto cru no filtro e com escaping testado para caminhos/argumentos. Posicionar/titular nas margens seguras de 7%, respeitando intervalos absolutos e estilo.
4. Ajustar áudio original por volume, preservar silêncio quando não houver som, combinar um MP3 opcional com `amix`/filtros compatíveis, música no volume selecionado, looping opcional limitado ao segmento, fades do mix. Não prometer normalização inteligente ou ducking automático.
5. Escrever MP4 em workspace isolado em `storage/temp` ou `storage/workspaces`, nunca sobre o original; tratar cancelamento do processo por SIGTERM e timeout, finalizando subprocessos descendentes.
6. Inspecionar **todo artefato pronto**: ffprobe de duração/resolução/FPS/codecs, decodificação da prévia, stream de áudio conforme expectativa, checksum/tamanho e limites. Só então promover de maneira exclusiva a `storage/previews/{project_uuid}/{preview_job_uuid}/preview.mp4` e publicar `completed` na transação adequada.
7. Em falha, publicar `failed` com código seguro, limpar apenas temporários deste job e preservar revisões, originais e artefatos de outros jobs. Corrigir o estado após reinício com reconciliação.

**Composição visual:** a pré-visualização CSS da R03 e a prévia FFmpeg não serão idênticas em todos os pixels, mas precisam concordar nos pontos verificáveis: proporção, cover/contain, orientação, enquadramento/focal, texto, período de exibição e cortes. Criar casos de QA para paridade e documentar limites conhecidos.

### Compatibilidade de áudio e fontes

- Áudio AAC de entrada ausente ou codificado em formato incomum deve ser tratado sem travar o worker. Se alguma combinação não for suportada, o job falha com mensagem amigável; não fingir sucesso.
- Arquivos MP3 são **dados**, nunca expressões de filtro ou caminhos do usuário.
- Fonte tipográfica incorporada/instalada na imagem precisa ter origem/licença conhecida, ser resolvida por configuração interna e existir no runtime API/worker. Ausência não deve gerar preview silenciosamente sem texto.

## 8. Worker — processamento assíncrono limitado e reaproveitável

Evoluir `app/worker/main.py` da espera ociosa R03 para um **loop de preview mínimo**. Preservar diagnóstico de inicialização e `SIGTERM` limpo.

- Worker consulta tabela via PostgreSQL `FOR UPDATE SKIP LOCKED`, reivindica job com **lease** de curta duração e confirma a transação antes de iniciar FFmpeg.
- Heartbeat/renovação de lease durante processamento e mecanismo para recuperar jobs `running` abandonados após crash; distinguir jobs elegíveis a retry de jobs irrecuperáveis. Usar tentativa máxima inicial de **2**; não gerar loops de falha infinitos.
- Evitar execução duplicada do mesmo job e publicação concorrente do mesmo artefato; isolamento e idempotência testados com dois candidatos worker em QA, mesmo que o Compose normal execute só um.
- Limitar concorrência a 1 por runtime, CPU/IO, tempo e armazenamento; evitar polling intenso (intervalo curto configurável, por exemplo 1–2 s). Sem Redis ou Celery.
- Publicar estágios apenas se realmente medidos (`queued`, `preparing`, `encoding`, `validating`, `completed`, `failed`). Percentual somente se fundamentado em `-progress` ou métrica real; caso contrário UI indeterminada.
- Request API cria job e retorna rapidamente; **nunca executar codificação em FastAPI BackgroundTasks ou request síncrono**.
- Retenção de previews não deve apagar originais. Na R04, não programar limpeza destrutiva arbitrária: documentar espaço consumido e deixar política de limpeza/retention completa para R05. Aplicar limites de jobs ativos.
- Estruturar módulo separado `services/previews`, integração `ffmpeg/preview`, repositório `preview_jobs`, para que a R05 reutilize motores e filas quando acrescentar exportação.

## 9. UX React — evolução do editor existente

Reutilizar `frontend/src/features/editor/{editor-workspace,video-player,timeline}.tsx`, sem reescrever navegação R03 ou perder theme/draft/conflito.

### Painel de texto

- Controles para adicionar, editar e remover até três cartões de texto: conteúdo, início/fim, posição (top/center/bottom), estilo (white_outline/blue_outline/white_plain) e tamanho (small/medium/large).
- Exibir overlays **CSS locais** sobre o player durante edição, para feedback visual imediato. Não afirmar que CSS é o MP4 final. Evitar bloquear play/pause/seek; sincronizar aparição com o tempo real do player e não com timers simulados.
- Garantir legibilidade em 9:16/1:1/4:5/16:9, safe area, contraste, acesso por teclado e mensagens para erros/limites.

### Painel de áudio

- Slider de volume original `0–200%` e indicador quando a fonte não tem áudio.
- Biblioteca simples de MP3 do projeto com upload, seleção opcional, volume de fundo `0–100%`, loop e fades.
- Sem waveform, multitrilha, equalizador, presets de copyright, ducking automático ou reprodução simultânea de arquivos diretamente sem confirmação dos eventos reais.

### Prévia e revisão

- Ação **Salvar edição** mantém o contrato R03 com `expected_revision`, dirty, no-op e conflito 409.
- Ação **Gerar prévia** exige revisão salva e editor sem alterações pendentes. Se houver draft dirty, orientar **Salvar antes de gerar**, sem salvar implicitamente.
- Permitir escolher uma janela de até 8 s, padrão a partir de `trim.start_ms` (ou ponto selecionado, desde que a UI informe exatamente qual intervalo será renderizado).
- Exibir estados reais: na fila, preparando, processando, validando, concluída, falha; polling GET a cada ~2 s somente enquanto job ativo, com cleanup ao sair.
- Reproduzir MP4 de prévia produzido no backend em player separado, via endpoint privado Range, rotulando **revisão e janela**. Se a edição for alterada, mostrar que a prévia é de revisão anterior; não atribuí-la ao draft atual.
- Suportar falha, retry explícito, indisponibilidade do worker e recarregamento após reload sem perder referência do job pendente. Não exibir progresso fictício nem mensagem de conclusão antes da publicação verificada.
- Layout responsivo, temas, componentes atuais, navegação por hash e teclado preservados. Não adicionar campo de prompt nessa etapa.

## 10. Riscos e mitigação

| Risco | Tratamento obrigatório |
|---|---|
| Quebrar R03 ao trocar schema | Discriminated union v1/v2; migration incremental; testes GET/PUT legacy. |
| Corrida entre save e preview | Prévia recebe **revision_id imutável**, não objeto editável nem revisão “mais recente” resolvida tardiamente. |
| Corrupção/duplicação de jobs | Lease/heartbeat, no-replace publish, status transacional e reconciliação. |
| Código arbitrário via texto FFmpeg | Textfiles UTF-8 locais, presets e escaping de filtros/caminhos; sem shell. |
| Áudio de fundo sobrepor voz | Volume manual baixo por padrão; verificação auditiva e ausência de clipping grosseiro nos testes. |
| Upload MP3 nocivo | Extensão+assinatura+FFprobe, limites reais e storage privado; não confiar no MIME. |
| Incompatibilidade navegador | H.264/AAC yuv420p, +faststart, player real Chromium; outros navegadores não presumidos. |
| Consumo de CPU/disco | Preview curto e pequeno; concorrência, timeout, disponibilidade e limites configuráveis. |
| Divergência preview CSS/FFmpeg | Fixtures com posições, crop, fontes e intervalos; critérios de paridade documentados. |
| Arquivos de teste/clientes | Somente fixtures sintéticas/sem copyright; preservar originais e ObraControl. |

## 11. QA e critérios de aceite R04

### Backend, banco e segurança

- R01/R02/R03: testes e endpoints antigos preservados, inclusive conflito 409, Range e revisões v1 imutáveis.
- `0004_r04_text_audio_previews` aplicada repetidamente; `alembic check` sem deriva; revisões antigas `schema_version=1` continuam intactas, triggers de imutabilidade em vigor.
- v2 validado: textos vazios/longos/control chars/início/fim/duplicidade, presets, estilo, fonte, volumes NaN/Inf/negativo, IDs áudio de outro projeto, trim alterado, source inválida, campos extras e update concorrente.
- Upload MP3 válido e inválido, sem Content-Length, corpo muito grande, tempo/duração/tamanho excedido, symlink/path traversal, commit/FS failure, cleanup e SHA-256/0400.
- Preview de 8 s/menor que 8 s; janela interna ao trim; revisão antiga vs atual; áudio presente/ausente; MP3 curto loop/off; codecs não suportados; conflitos de job; stale lease/restart; timeout/SIGTERM; espaço insuficiente; validação MP4 e hash.
- Streaming privado da prévia: GET/HEAD 200/206/416, bytes corretos, origem associada, projeto excluído, Range e CORS, sem caminhos locais expostos.
- Auditoria antes/depois de todos os originais MP4 e MP3: SHA-256, modo 0400, sem alterações/órfãos/temporários indevidos. Não executar `down -v` nem expor PostgreSQL.

### FFmpeg e navegador

- Fixtures sintéticas horizontais/verticais/quadradas, com/sem áudio, VFR, SAR e rotação; texto com caracteres especiais/UTF-8; posições top/center/bottom; diferentes presets/cover/contain; prévia de janela intermediária, voz e música.
- FFprobe real confirma dimensões, duração, H.264, `yuv420p`, AAC quando aplicável; full decode da saída de até 8 s confirma arquivo reproduzível.
- QA visual/auditivo verifica texto dentro da margem, cor/contorno, sincronização texto/trim, volume da voz e ausência de sobreposição insuportável; declarar limites da prova automatizada e evidência de inspeção manual quando usada.
- Chromium real: abrir projeto, salvar v2, subir MP3, selecionar música, gerar preview, aguardar status, observar reprodução/Range e reabrir resultado após restart; desktop/mobile, claro/escuro, teclado. Não alegar Firefox/Safari ou dispositivo físico se não testados.
- `pytest`, Ruff, Vitest/RTL, ESLint, Prettier, TypeScript, Vite build; Compose config/build/up/health, migração e scripts de aceite. **Registrar contagens e comandos reais**; não declarar sucesso apenas por mocks.

### Checklist de aceite (sem marcar antecipadamente)

- [ ] Contratos R01–R03 intactos e regressões aprovadas.
- [ ] Migração 0004 aplicada/repetível; v1 preservado e v2 gravável.
- [ ] Conflito `expected_revision`/no-op 200 e histórico imutável preservados.
- [ ] Upload MP3 controlado, metadados reais e originais imutáveis.
- [ ] Até três textos com tempos/posições/estilos validados e recuperáveis.
- [ ] Volume original, música opcional, loop/fades produzindo comportamento esperado.
- [ ] Overlay CSS de edição coerente e acessível; não é confundido com render final.
- [ ] Solicitação de preview vinculada a revisão salva, nunca draft dirty.
- [ ] Worker de preview realmente separado, com lease, recuperação e timeout.
- [ ] MP4 preview curto H.264/AAC válido, crop e texto na janela escolhida.
- [ ] Status API e UI reais; sem progresso/sucesso fictício.
- [ ] Preview disponível somente por rota privada GET/HEAD+Range.
- [ ] Erros sanitizados, retries finitos, falhas não afetam originais ou revisões.
- [ ] Testes/backend/frontend/integração/FFmpeg/navegador aprovados e evidências registradas.
- [ ] Portas locais, dados R01–R03, storage, locks e ObraControl preservados.
- [ ] README, arquitetura, roadmap, AGENTS e QA R04 atualizados.
- [ ] Sem export final, heditor, agente, prompts ou Higgsfield implementados antecipadamente.

## 12. Organização da futura execução (lotes)

| Lote | Entrega | Gate de saída |
|---|---|---|
| R04.0 | Auditoria do checkout local, contratos R03 e matriz de compatibilidade | Sem divergência impeditiva; plano de migração não destrutivo. |
| R04.1 | Migration 0004, schemas EditSpec v1/v2, `AudioAsset`, importação MP3 | Persistência, constraints, segurança, legacy v1 e regressões. |
| R04.2 | Controles React de texto/áudio e CSS sobre o player | Testes UI, dirty/conflict, tempos e persistência reais. |
| R04.3 | `PreviewJob`, fila PostgreSQL e evolução do worker | Claim concorrente, crash/recovery, SIGTERM, timeout e estados. |
| R04.4 | Pipeline FFmpeg de preview, rotas de job/stream, integração frontend | Prova MP4 H.264/AAC, sync, QA visual/auditivo e Range. |
| R04.5 | Regressões completas e evidências A–H | Checklist real, documentos e limites reportados. |

**Futuro:** R05 reutiliza o pipeline de composição e o agendamento existente para render completo, cancelamento, exportações e histórico. R07 executa `heditor doctor` e provas de integração isoladas. R08 integra agentes e campo de prompt com revisão humana, aproveitando os schemas tipados.

## 13. Documentos e relatório A–H da futura implementação

Atualizar `README.md`, `docs/architecture.md`, `docs/roadmap.md`, `AGENTS.md`, `.env.example` conforme efetivamente implementado, **preservando documentos/aceites históricos**. Criar:

- `docs/r04/compatibility.md` — matriz de compatibilidade antes da edição.
- `docs/r04/acceptance.md` — relatório final A–H com comandos/decisões reais.
- `docs/r04/qa-results.json` — evidências estruturadas com resultados, versões e SHA-256 de artefatos significativos.
- `docs/r04/evidence/` — dados de testes reais, FFprobe, inspeção do player, capturas representativas.

Relatório A–H exigido:

**A. Implementação:** escopo, arquivos e arquitetura final.  
**B. Banco e EditSpec:** migração, v1/v2, imutabilidade, concorrência.  
**C. Áudio:** MP3, storage, integridade, mix, volumes e limitações.  
**D. Previews:** jobs, estados, FFmpeg, artefatos, streaming e validação.  
**E. UX e navegador:** textos/áudio, revisão salva, preview real, desktop/mobile.  
**F. Testes e QA:** comandos, contagens distintas, testes reais e regressões.  
**G. Aceite e riscos:** checklist preenchido com prova, pendências/resíduos.  
**H. Continuidade:** aptidão ou bloqueadores para R05; **não iniciar R05 sem autorização**.

## 14. Critérios para autorização

Esta especificação registra o planejamento. **Não altera o código, Git, migrações ou containers** e não autoriza iniciar R04 automaticamente. Depois de validada pelo usuário, produzir prompt operacional ao Codex CLI para implementar apenas a R04, com auditoria do checkout local, testes verificáveis, sem comandos Git mutantes e sugestão de commit separada **somente após implementação aceita**.

## 15. Referências técnicas e fontes de verdade

- Fonte de verdade do projeto: `docs/EDIVO_DOCUMENTACAO_PROJETO.md`, `docs/EDIVO_INSTRUCOES_CHATGPT.md`, R00, R01, R02, R03, `docs/r03/acceptance.md`, `docs/r03/qa-results.json`.
- FFmpeg filtros oficiais (`drawtext`, `volume`, `amix`, `afade`, `scale`, `pad`, `crop`): https://ffmpeg.org/ffmpeg-filters.html
- FFmpeg CLI e opções de tempo/progress: https://ffmpeg.org/ffmpeg.html
- FFprobe: https://ffmpeg.org/ffprobe.html
- SQLAlchemy locking: https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html
- PostgreSQL `SKIP LOCKED`: https://www.postgresql.org/docs/current/sql-select.html
- heditor (integração futura; skill/CLI, **não SDK HTTP presumido**): https://github.com/pycodebr/heditor
