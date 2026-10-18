# Edivo — R03 | Especificação operacional: Player, Timeline, Cortes e Presets

**Data:** 17/10/2026  
**Produto:** Edivo  
**Repositório:** `edivo-editor` — `davioliveiraes/edivo-editor`  
**Status:** planejamento para aprovação; **não autoriza implementação**.  
**Base técnica:** R01 e R02 concluídas conforme os aceites publicados. R02 → R03 sem antecipar renderização ou IA.

## 1. Escopo de inspeção e rastreabilidade

A proposta foi elaborada após examinar, **somente para leitura**, arquivos publicados na branch padrão `main` do repositório GitHub: `README.md`, `AGENTS.md` (quando disponível), `docs/architecture.md`, `docs/r02/acceptance.md`, `docs/r02/qa-results.json`, `backend/app/models/{project,media_asset}.py`, `backend/app/schemas/{projects,media}.py`, `backend/app/api/v1/{projects,media}.py`, `backend/app/main.py`, `backend/app/storage/local.py`, `backend/app/integrations/ffmpeg/probe.py`, `backend/alembic/versions/0002_r02_projects_media.py`, `frontend/src/app/app.tsx`, `frontend/src/features/projects/projects-workspace.tsx`, `frontend/src/features/media/media-library.tsx` e `frontend/src/lib/api/{projects,media}.ts`.

**Limite da auditoria:** código no GitHub pode divergir de alterações ainda não commitadas ou enviadas no Ubuntu/WSL. Antes de escrever código, o executor deve comparar contratos publicados com o estado local real, ler relatórios e testes, identificar divergências e interromper se houver incompatibilidade essencial. Nenhum teste R03, migração ou endpoint novo foi executado nesta especificação.

### Contratos R02 confirmados

- `Project` possui `id` UUID, `name`, `purpose`, `target_preset` opcional (`9:16`, `1:1`, `4:5`, `16:9`), UTC timestamps e `deleted_at` (soft delete).
- `MediaAsset` possui `id`, `project_id`, `kind=source_video`, `storage_key` **privada**, checksum SHA-256, `byte_size`, `duration_ms`, `width`, `height`, `fps_num`, `fps_den`, codecs e `has_audio`.
- Os oito endpoints R02 implementam CRUD e biblioteca, sem servir bytes de vídeo. Listagens paginadas usam `{items,total,limit,offset}`. Respostas de erro usam `{code,message,request_id}` com `X-Request-ID`.
- `storage/originals/<project_uuid>/<asset_uuid>/source.mp4` é gerenciado pelo backend, fora da publicação estática; arquivo original `0400`, sem sobrescrita; `LocalStorage` rejeita symlinks em diretórios ao gravar.
- Navegação frontend atual usa `#/projects` e `#/projects/{uuid}`, tratada por `ProjectsWorkspace`; hooks e API client atuais permanecem.
- Baseline `0001_r01_baseline`; R02 `0002_r02_projects_media` cria somente `projects` e `media_assets`.
- Worker separado permanece ocioso; API somente em loopback, 8001 e frontend 5174 neste ambiente do usuário (portas internas 8000 e 5173).
- A importação pode aceitar codecs MP4 que o navegador não reproduz. FFprobe na R02 valida apenas prefixo de frames, **não** a integridade de todo o vídeo. FPS racional não implica frame rate constante, e propriedades armazenadas não resolvem por si só rotatividade/display matrix ou pixel aspect ratio.

## 2. Objetivo e definição de pronto

Um projeto ativo com MP4 previamente importado pode abrir uma tela de editor, selecionar um vídeo, reproduzi-lo **sem expor diretórios privados**, posicionar o playhead, marcar início/fim, escolher formato e modo de enquadramento, salvar um plano de edição tipado/versionado no PostgreSQL e reabri-lo após reiniciar a aplicação. O arquivo original conserva hash, tamanho, bytes e permissões.

A seleção de corte e o enquadramento visual são **não destrutivos**. A tela representa somente a seleção e uma visualização de composição no navegador; não produz vídeo final, FFmpeg preview, novas mídias, thumbnails nem arquivos cortados.

### Dentro da R03

1. Entrega de bytes MP4 privados com HTTP Range para `<video>` no navegador.
2. Player HTML5 com Play/Pause, seek, tempo atual e estado de carregamento/erro.
3. Timeline simplificada de **uma faixa** (um vídeo ativo por plano) com playhead, marcas de entrada/saída e teclado.
4. Presets de canvas `9:16`, `1:1`, `4:5`, `16:9`; controles `cover`/`contain` e ponto focal simples para `cover`.
5. Contrato `EditSpec v1` estruturado, revisões imutáveis, GET/PUT e controle de concorrência otimista.
6. Integração com UI existente, persistência e tratamento de conflito/alterações não salvas.
7. Migração incremental, testes, QA e documentação.

### Fora da R03

FFmpeg render/export/cópia de arquivo, vídeo cortado, preview codificado, worker de jobs, filtros de cor, texto, legendas, trilhas, transições, waveform/miniaturas, thumbnails derivadas, keyframes, multifaixa, autenticação/SaaS, IA/heditor, Higgsfield, alteração física do original ou migração de storage.

## 3. Backend — contrato de entrega segura (streaming)

**Adicionar** rota de leitura no namespace já existente, após inspeção local:

| Método | Endpoint | Semântica |
|---|---|---|
| GET | `/api/v1/projects/{project_id}/media/{media_id}/stream` | MP4 privado, `200` sem Range, `206` com Range válido |
| HEAD | mesmo caminho | Mesmos headers HTTP aplicáveis, **sem body** |

O endpoint DEVE:

- Verificar projeto ativo (`deleted_at IS NULL`) e associação correta `media.project_id == project_id`, retornando `404` quando inexistente, excluído ou pertencente a outro projeto.
- Resolver somente a `storage_key` previamente persistida, nunca caminho, filename ou URL de usuário; usar storage root privado e validação contra traversal/symlinks (inclusive destino final). Preferir abrir descritor com medidas anti-symlink / `O_NOFOLLOW`, verificar arquivo regular e restringir leitura aos IDs autorizados. Sem acesso genérico a `storage/`, sem `StaticFiles` sobre originais.
- Confirmar que mídia é MP4 e que arquivo real existe e é legível, sem modificar arquivo ou permissões. Arquivo ausente/inacessível: erro sanitizado de infraestrutura (`503`), sem revelar paths.
- Servir **streaming em chunks** com memória limitada, encerrando handles em conclusão/cancelamento; nunca usar `read_bytes()` para 500 MiB.
- Definir `Content-Type: video/mp4`, `Accept-Ranges: bytes`, `Content-Length` correto, `Content-Range` correto para `206`, `X-Content-Type-Options: nosniff` e `Cache-Control: private, no-store`; `Content-Disposition: inline` se configurado sem interpolar filename não confiável.
- Suportar `bytes=start-end`, `bytes=start-` e `bytes=-suffix`, inclusive um único byte e o final exato. Intervalo fora de arquivo deve retornar `416` e `Content-Range: bytes */<total>`; ranges inválidos/múltiplos devem ser rejeitados de modo consistente e testado. Sem Range retornar `200` e corpo inteiro por streaming.
- Opcional: ETag derivado do checksum já existente; se suportar `If-Range`, documentar e validar sem acesso cruzado indevido. Não é obrigatório implementar cache avançado/conditional requests.
- Manter CORS restrito à origem local e compatibilidade com proxy `/api`, sem publicar storage, URL do host, caminhos absolutos ou outro serviço HTTP.
- Não gerar transcoding quando browser não suportar codec.

**Nota de design:** inspecionar se o `FileResponse` da versão instalada de Starlette cobre corretamente o contrato de Range/HEAD requerido. Se adotado, aplicar sempre o gate de storage seguro e de IDs antes de criar a resposta. Se não cobrir, implementar wrapper de leitura limitado com testes completos. Evitar segunda implementação concorrente de streaming.

## 4. Persistência — EditSpec revisionado

**Nova migration:** `0003_r03_edit_spec_revisions` após `0002_r02_projects_media`, sem alteração destrutiva de `projects` ou `media_assets`.

**Tabela proposta:** `edit_spec_revisions`:

| Coluna | Semântica |
|---|---|
| `id` | UUID PK gerado pelo servidor |
| `project_id` | FK obrigatória para `projects`, índice |
| `revision_number` | inteiro >=1, crescente por projeto, unique (`project_id`, `revision_number`) |
| `source_media_id` | FK `media_assets.id`, vídeo escolhido para a revisão |
| `schema_version` | `1` para contrato atual |
| `spec_json` | JSONB contendo os campos declarativos, validado com Pydantic |
| `created_at` | timestamp UTC |

Não adicionar `RenderJob`, `Export` ou `ProjectEditState` nesta entrega. A revisão corrente é a de maior `revision_number` do projeto. Gravação deve ocorrer em transação com bloqueio do registro `Project` antes de calcular novo número, evitando disputa, com proteção UNIQUE no banco.

**EditSpec v1 — contrato proposto (não é resposta atualmente implementada):**

```json
{
  "schema_version": 1,
  "source_media_id": "UUID_DE_UMA_MIDIA_DO_PROJETO",
  "trim": { "start_ms": 1000, "end_ms": 8500 },
  "canvas": {
    "preset": "9:16",
    "fit": "cover",
    "focal_x": 0.5,
    "focal_y": 0.5
  }
}
```

Regras:

- `source_media_id` precisa corresponder a `MediaAsset` do projeto ativo, do tipo `source_video`, com bytes existentes quando for pré-visualizar; não aceitar vídeo de outro projeto.
- `0 <= start_ms < end_ms <= MediaAsset.duration_ms`; tempos inteiros em ms, nunca float na persistência. Intervalo mínimo de 100 ms ou outro valor fechado após teste com vídeos reais, mas sem invalidar importações curtas previamente aceitas: se `duration_ms < 100`, permitir todo o intervalo ou definir política explícita e testada.
- `schema_version=1`; rejeitar campos desconhecidos, presets inválidos, fit diferente de `cover|contain`, focal fora de `[0,1]`, NaN/Infinity e números não inteiros onde exigidos.
- `focal_x/y` normalizados, 0 esquerda/topo e 1 direita/baixo; `contain` preserva valor focal para uma volta futura a `cover`, mas não o usa para o posicionamento visual.
- Ao criar o primeiro plano, sugerir `project.target_preset ?? '9:16'`; **após salvar, a revisão tem seu próprio preset**. Editar `Project.target_preset` depois **não modifica** revisões existentes e vice-versa.
- Salvar só mediante ação explícita; mudanças no playhead não geram revisões. Texto, música, efeitos e export profile ficam de fora do schema v1.
- Revisões persistidas são imutáveis; alterações criam nova linha. Salvar uma revisão idêntica pode ser tratado como no-op documentado (sem criar nova revisão).

### API de plano de edição

| Método | Endpoint | Resultado |
|---|---|---|
| GET | `/api/v1/projects/{project_id}/edit-spec` | `200` última revisão; `404` com `code=edit_spec_not_found` se nunca salva |
| PUT | `/api/v1/projects/{project_id}/edit-spec` | `201` na primeira revisão, `200` nas posteriores, ou `409` em conflito |

`PUT` envia o documento completo e `expected_revision` (inteiro >=0) para concorrência otimista, sendo `0` para a primeira criação; resposta retorna `id`, `revision_number`, `created_at`, `schema_version` e todo o conteúdo validado. Em divergência, retornar erro sanitizado `409 edit_spec_conflict` sem sobrescrever. Leitura atual deve estar disponível antes de tentar regravar. Testar duas chamadas simultâneas com mesma versão; somente uma deve criar nova revisão.

**Observação de desenho:** usar `PUT` nesta fase de forma consistente com o plano R00, apesar de armazenar histórico append-only; a semântica de atualização do recurso corrente é preservada. Não criar rotas de histórico/listagem de revisões sem necessidade do MVP; histórico interno fica no banco como base auditável para fases seguintes.

## 5. Interface e experiência de edição

**Rota proposta:** `#/projects/{project_uuid}/editor`. Alterar o parser de hash atual para reconhecer rota de editor sem quebrar `#/projects` e `#/projects/{uuid}`, sem introduzir roteador novo se não for necessário. O detalhe do projeto e a biblioteca conservam comportamento e testes.

**Tela do editor:**

- Cabeçalho do projeto, voltar à biblioteca, título, estado salvo/não salvo e ação `Salvar edição`.
- Se projeto não tem mídia: estado vazio com link funcional para importar; não mostrar player falso.
- Se projeto tem múltiplas mídias: seleção de **uma fonte**. Trocar fonte ao existir edição não salva pede confirmação; ao trocar, reinicializar limites de trim segundo a nova duração, sem apagar revisões antigas.
- `<video>` com `src` baseado exclusivamente em `project_id` e `media_id`, origem relativa `/api/v1/.../stream`, `preload=metadata`, `controls` adequados ou controles acessíveis próprios; sem autoplay com áudio, sem download explícito habilitado, sem afirmar DRM.
- Estados: carregando metadados, reproduzindo, pausado, erro de rede, erro de decodificação/codec, vídeo indisponível. Se o navegador não reproduzir o codec, mostrar mensagem honesta: importação válida não garante compatibilidade HTML5; **não transcodificar**.
- Controles Play/Pause, seek, tempo atual/total e atalho de teclado acessível sem interferir em inputs. Estado `currentTime` atualizado por eventos da mídia, com `requestAnimationFrame` somente quando necessário e cleanup em unmount.
- Timeline com **uma única faixa** visual (sem thumbnails/waveform), eixo temporal relativo ao vídeo, playhead móvel, controles de in/out arrastáveis e operações teclado. A barra representa a fonte inteira; região selecionada apresenta recorte de entrada/saída. Teclado fornece alternativa real ao drag, labels aria claros e feedback dos valores.
- Visualização em canvas de proporção selecionada, usando CSS `aspect-ratio`, `object-fit: cover|contain` e `object-position` calculada do ponto focal. Distinguir claramente **simulação visual no navegador** da futura preview/export FFmpeg. Em `contain`, permitir barras/fundo neutro sem alterações no arquivo.
- Presets `9:16` (1080x1920), `1:1` (1080x1080), `4:5` (1080x1350) e `16:9` (1920x1080). A resolução é referência futura de export, não resolução de vídeo gerado nesta etapa.
- Ao pressionar play dentro de intervalo salvo, reproduzir até `end_ms` e pausar (ou voltar ao início mediante comando explícito). Não prometer precisão frame-perfect: seek HTML5 pode apresentar aproximações, especialmente com VFR/codec e keyframes. Para marcação, timestamps são inteiros ms.
- Estado de salvamento explícito. Se salvar retorna `409`, avisar e oferecer recarregar revisão atual sem sobrescrita silenciosa. Quando houver alterações não salvas, pedir confirmação ao navegar/trocar mídia; em fechamento/recarregamento de página, `beforeunload` conforme suportado.
- Layout responsivo: player, ações e timeline utilizáveis em desktop e mobile; contraste/tema claro-escuro da R01/R02 preservados; touch acessível sem dependência de mouse.
- Não apresentar botão "Exportar" ou "Gerar vídeo" como operacional.

## 6. Limites e peculiaridades de mídia

- FPS racional (ex.: `30000/1001`) é metadado, **não** uma garantia de CFR. Timeline opera em milissegundos de duração; testes precisam cobrir VFR se houver fixture pequena.
- `MediaAsset.width/height` da R02 podem representar dimensões codificadas antes de rotação/display matrix ou SAR. O navegador usa orientação de exibição do vídeo para o preview; se houver divergência detectável, documentar limitações e adicionar somente a leitura/metadados complementares estritamente necessários, sem migrar/destruir originais ou prometer pixel-perfect com export futuro.
- MP4 aceito pelo backend pode ter codec não suportado pelo navegador; erro deve ser distinguido de 404 ou erro de conexão.
- A inspeção curta de R02 não garante decodificação integral. Durante playback podem surgir erros tardios; tratar como falha de mídia sem modificar originais.
- Não usar `blob:` criado por leitura integral de um arquivo de até 500 MiB; player deve utilizar rota HTTP Range.
- Manter segurança local-first; não assumir que restrição loopback é autenticação multiusuário.

## 7. Testes e QA obrigatórios

### Backend

- GET/HEAD streaming sem Range (`200`), faixa inicial, intermediária, final, aberta e sufixo (`206`), `Content-Length`, `Content-Range`, tipo MIME, cabeçalho anti-sniff, tamanho do corpo e ausência de corpo em HEAD.
- Range malformado/múltiplo/insatisfatível (`416` + `Content-Range: bytes */<size>`), stream interrompido, cliente desconectado, arquivo ausente/inacessível, symlink, paths maliciosos e arquivo de outro projeto (404).
- Não abrir mídia de projeto excluído; original permanece 0400, checksum/tamanho inalterados após leituras e após restart.
- `EditSpec`: create/get/update/version, constraints trim, source FK, preset, fit, ponto focal, unknown fields, `expected_revision`, dados inválidos, concorrência de duas atualizações, resposta 409, projeto excluído e mídia de outro projeto.
- Migração `0003`, repetibilidade upgrade, `alembic check`, R01 e R02 sem regressão.

### Frontend

- Projetos sem mídia; seleção de uma fonte; reprodução básica simulada via eventos `HTMLMediaElement` e fluxo real no browser.
- Play/Pause/seek, precisão de conversão seconds ↔ ms sob controle, ajustes in/out, teclado e arrasto/touch, comportamento na marca final.
- Seleção de quatro presets; `cover`/`contain` e ponto focal; composição responsiva e temas.
- Abrir, salvar, recarregar, feedback, dirty-state, rejeição 409 e correção/recuperação.
- Codec incompatível, rede indisponível e `404/503` com mensagens seguras.

### Integração/navegador reais

- Usar fixtures sintéticas FFmpeg pequenas: H.264/AAC, MP4 sem áudio, vídeo com rotação se suportado e resolução não vertical; fixtures de limites para Range. Não depender de arquivos reais de clientes.
- Importar R02 → abrir editor R03 → navegador requisita 206 → assistir/seeking → marcar intervalo → salvar revisão → reiniciar containers → confirmar persistência e original intacto.
- Verificar navegador em tema claro/escuro, desktop e mobile, teclado/touch quando ambiente suportar.
- Verificar que ObraControl não foi afetado e que publicações de Edivo permanecem loopback nas portas 5174 e 8001 do ambiente do usuário.
- Testar com bitrate/codec incomum se fixture disponível, e registrar limitações reais; não transformar suporte de browser em promessa universal.

**Executar e registrar:** pytest, Ruff check/format, Vitest/RTL, ESLint, Prettier, TypeScript/Vite build, Compose config/build/up, Alembic, HTTP com Range/HEAD, FFprobe e browser QA. Registrar comandos executados e resultados reais em `docs/r03/qa-results.json`. **Não presumir contagens nem aprovação sem execução.**

## 8. Riscos e mitigação

| Risco | Tratamento |
|---|---|
| Expor originais sem gate de projeto | Consultar FK e projeto ativo; sem StaticFiles nem caminhos arbitrários |
| Path traversal/symlink/race | Resolver ID→key do BD, restringir root e abrir com validações e flags seguras |
| Range errado/corpo lido por inteiro | Testes por bytes e streaming sem memória proporcional ao arquivo |
| Navegador sem codec | Mensagem de mídia não reproduzível, sem transcodificar automaticamente |
| Rotação/SAR e vídeo VFR | Registrar limitações e validar preview com fixtures direcionadas |
| Perda de edições por corrida | Revision number + `expected_revision` + bloqueio transacional + 409 |
| Explosão de revisões | Salvar explicitamente, sem uma revisão por movimento do playhead |
| Preset do projeto confundido com plano | Projeto é sugestão inicial; EditSpec salvo é fonte de verdade |
| Impacto na R01/R02 | Regressão obrigatória e migration incremental não destrutiva |

## 9. Etapas internas planejadas (a serem autorizadas depois)

| Lote | Entregável | Gate |
|---|---|---|
| R03.0 | Conferir contratos e diferenças do GitHub com WSL | Matriz de compatibilidade escrita, sem modificar código |
| R03.1 | Storage reader e endpoint Range/HEAD | Segurança e testes HTTP 200/206/416 |
| R03.2 | Alembic, EditSpec v1 e API | Revisões, validações e conflitos aprovados |
| R03.3 | Player e seleção de mídia | Reprodução real, estados de erro e seek |
| R03.4 | Timeline e presets | Trim, teclado/touch, cover/contain, dirty/save |
| R03.5 | QA, docs, evidências e regressão | Todos os critérios essenciais satisfeitos |

## 10. Checklist objetivo de aceite R03

- [ ] R01/R02 continuam operacionais; projetos, mídia, SHA-256 e storage não foram modificados.
- [ ] Migração R03 aplicada sobre `0002` e repetida sem alteração indevida.
- [ ] GET/HEAD stream só entrega mídia do projeto ativo, sem exposição de path/storage.
- [ ] HTTP Range retorna 200/206/416 e cabeçalhos/corpos corretos, incluindo bytes inicial/final.
- [ ] Reprodução real via navegador, seek, play/pause e erros honestos por codec/rede.
- [ ] Timeline de uma faixa, in/out e controles de teclado e toque utilizáveis.
- [ ] Quatro presets visuais e `cover/contain`; nenhum preview FFmpeg/export é prometido.
- [ ] EditSpec v1 validado no servidor, revisionado e recuperável após restart.
- [ ] Concorrência otimista impede perda silenciosa de trabalho (409).
- [ ] Projeto excluído/mídia de outro projeto permanecem inacessíveis.
- [ ] Nenhuma transformação do original, arquivo novo de vídeo ou worker de render.
- [ ] Scripts de QA, testes backend/frontend, lint/build e browser realmente executados.
- [ ] `docs/r03/acceptance.md`, `docs/r03/qa-results.json` e README atualizados.
- [ ] Nenhuma implementação R04+; Git sob gestão manual.

## 11. Formato de relatório A–H (somente após futura autorização e execução)

**A. Implementação:** código alterado, arquitetura, novas rotas.  
**B. Banco:** migration, EditSpec v1 e revisão/concorrência.  
**C. Streaming:** gates de segurança, Range, headers, leitura limitada, negativas.  
**D. Editor:** player, timeline, presets e regras de trim.  
**E. UX/navegador:** evidências reais, codecs, desktop/mobile, tema.  
**F. QA:** comandos, testes, lint, build, regressões R01/R02 e integrações.  
**G. Aceite:** checklist, pendências, limites e riscos residuais.  
**H. Próximo passo:** avaliar prontidão para R04, sem iniciar R04.

## 12. Decisões pendentes de aceite

1. **Mínimo do trim:** proposta 100 ms com exceção para mídia inferior a esse limite; Codex valida casos de borda antes de fixar.
2. **Navegação:** manter hash router da R02, expandindo para `#/projects/{id}/editor` sem nova dependência se viável.
3. **Leitura de range:** priorizar implementação nativa do Starlette se gates de segurança e testes completos forem atendidos.
4. **Precisão de preview:** posicionamento HTML5 é visual aproximado; export com FFmpeg em etapa posterior terá validação específica para rotação, SAR, VFR e crop.

## 13. Regras finais

- Esta entrega é **exclusivamente de planejamento**: não executar mudanças, testes ou scripts de alteração de dados, nem afirmar que a R03 já foi implementada.
- A próxima ação, **somente mediante aprovação expressa**, será criar o prompt operacional de implementação para Codex CLI e executar R03 por lotes, preservando decisões e dados anteriores.
- O prompt Codex futuro não deverá conter comandos Git de commit/push; após uma entrega de código validada, fornecer uma sugestão de mensagem de commit separada.
