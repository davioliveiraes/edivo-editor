# Edivo — R02 | Especificação operacional: Projetos, Upload e Metadados

**Data de elaboração:** 17/10/2026  
**Produto:** Edivo | **Repositório:** `edivo-editor` | **Estágio:** especificação para implementação  
**Pré-requisito:** R01 entregue e declarada validada pelo relatório do executor; arquivos reais de QA e código local devem ser inspecionados pelo Codex antes de modificar o projeto.  
**Limite estrito:** R02 não implementa editor, timeline, player, cortes, preview, render, export, IA ou integrações externas.

## 1. Objetivo e definição de pronto

Entregar o primeiro fluxo funcional de gerenciamento de conteúdo do Edivo: criar/listar/atualizar/excluir logicamente projetos; importar um único MP4 por requisição; realizar validação de segurança, cópia controlada, SHA-256 e FFprobe; armazenar o original imutável; persistir metadados no PostgreSQL; mostrar biblioteca e status de upload/erros no frontend. A aplicação continua local-first, sem autenticação por enquanto e vinculada exclusivamente ao loopback.

**Experiência completa esperada:** criar projeto → abrir projeto → importar MP4 válido → visualizar nome, tamanho, duração, dimensões, FPS, codec e presença de áudio → reiniciar serviços → recuperar registro e arquivo preservado. Arquivos inválidos não devem criar `MediaAsset`, não devem deixar staging permanente e não devem modificar originais existentes.

## 2. Fontes obrigatórias e verificação inicial

Ler antes de alterar código:
- `AGENTS.md` e `README.md`;
- `docs/EDIVO_DOCUMENTACAO_PROJETO.md`;
- `docs/EDIVO_INSTRUCOES_CHATGPT.md`;
- `docs/R00_EDIVO_PLANEJAMENTO_TECNICO.md`;
- `docs/R01_EDIVO_ESPECIFICACAO_OPERACIONAL.md`;
- `docs/r01/acceptance.md` e `docs/r01/qa-results.json`, se existirem;
- este arquivo, caso copiado para `docs/R02_EDIVO_ESPECIFICACAO_OPERACIONAL.md`.

Inspecionar arquitetura e contratos realmente implementados na R01, migrations, settings, storage, API errors, frontend, scripts, Compose, versões fixadas. Preservar convenções atuais; não duplicar serviços. Antes de codificar, listar incompatibilidades entre esta proposta e os contratos reais da R01, solucionando somente as necessárias para R02.

## 3. Escopo e não escopo

### Incluir

1. CRUD de `Project` com paginação da listagem; exclusão **lógica** (`deleted_at`) com confirmação visual.
2. Modelagem, migração Alembic incremental e repositórios `Project` e `MediaAsset`.
3. Upload multipart de **um vídeo MP4 por requisição**, com limites de ingress/body e de bytes persistidos, checagem de extensão + estrutura/assinatura + FFprobe; aceitar arquivo sem áudio se houver vídeo válido.
4. Armazenamento local em staging e destino gerenciado; SHA-256; política de promoção atômica, rollback/limpeza e preservação do original.
5. Persistência de metadados selecionados, verificados e normalizados; listagem e detalhe de mídia por projeto.
6. Frontend de projetos e detalhe com biblioteca de mídia, progresso **real da transferência**, estágio de inspeção indeterminado e mensagens de erro/sucesso.
7. Testes backend, frontend e integrados com amostra sintética MP4, sem mídia externa.
8. README, contratos OpenAPI e relatório de QA em `docs/r02/`.

### Não incluir

Player/streaming/range (será R03), timeline, corte, presets operacionais de edição, texto, trilhas, preview, render, export, worker de jobs, thumbnail gerada, waveform, transcrição, heditor, Higgsfield, autenticação, SaaS, exclusão física de originais, deduplicação automática ou upload multipart em chunks resumíveis.

## 4. Entidades e persistência

### `Project`

- `id`: UUID gerado pelo servidor (chave primária).
- `name`: texto obrigatório, trim, 1–120 caracteres; **não** exigir exclusividade.
- `purpose`: texto opcional de até 500 caracteres.
- `target_preset`: opcional e somente metadado nesta etapa; enum `9:16`, `1:1`, `4:5`, `16:9` ou nulo. Nenhuma transformação de mídia será executada.
- `created_at`, `updated_at`: timestamptz em UTC.
- `deleted_at`: timestamptz nulo por padrão; soft delete.
- Listagem padrão: projetos não excluídos, ordem `created_at DESC, id DESC`; paginação com limite padrão 20 e máximo 100.

### `MediaAsset`

- `id`: UUID gerado pelo servidor, chave primária.
- `project_id`: FK obrigatória com índice; não permitir referência a projeto excluído logicamente.
- `kind`: `source_video` (tipo único nesta etapa).
- `original_filename`: nome para exibição, saneado, limitado, **nunca** componente de path.
- `storage_key`: chave opaca gerada pelo servidor, única e relativa; não expor caminhos absolutos no JSON.
- `checksum_sha256`: hexadecimal SHA-256 minúsculo.
- `byte_size`: inteiro positivo, tamanho real persistido.
- `mime_detected`: tipo efetivamente confirmado (MP4), não copiar cegamente `content_type` do navegador.
- `duration_ms`: inteiro positivo, extraído/consolidado com precisão controlada.
- `width`, `height`: inteiros positivos da stream de vídeo escolhida.
- `fps_num`, `fps_den`: inteiros da fração racional; denominador positivo, permitir FPS desconhecido apenas com justificativa documentada.
- `video_codec`: texto opcional do probe; `has_audio`: booleano; `audio_codec`: texto opcional.
- `probe_metadata`: JSONB limitado e filtrado, sem confiar/retornar indiscriminadamente tags, paths e campos arbitrários do arquivo.
- `created_at`: timestamptz UTC.

**Constraints:** unique em `storage_key`; check `byte_size > 0`, `duration_ms > 0`, `width > 0`, `height > 0`; FK/index coerentes. Não exigir checksum único, pois a importação independente do mesmo arquivo pode ser válida. Não implementar tabela de jobs ou EditSpec.

**Migração:** criar nova revisão incremental após `0001_r01_baseline`, sem reescrever baseline aplicada, incluindo downgrade reversível de schema. Não executar downgrade destrutivo em banco com dados reais sem aprovação do usuário.

## 5. API REST sugerida, ajustar às convenções reais existentes

| Verbo | Endpoint | Semântica |
|---|---|---|
| POST | `/api/v1/projects` | Cria projeto (`201`) |
| GET | `/api/v1/projects?limit=20&offset=0` | Lista paginada de projetos ativos (`200`) |
| GET | `/api/v1/projects/{project_id}` | Detalhe (`200` ou `404`) |
| PATCH | `/api/v1/projects/{project_id}` | Atualização parcial validada (`200`) |
| DELETE | `/api/v1/projects/{project_id}` | Soft delete explícito (`204`), repetição tratada consistentemente |
| POST | `/api/v1/projects/{project_id}/media` | Multipart `file` único, inspeciona e grava mídia (`201`) |
| GET | `/api/v1/projects/{project_id}/media?limit=20&offset=0` | Lista metadados da mídia (`200`) |
| GET | `/api/v1/projects/{project_id}/media/{media_id}` | Detalhe do asset (`200` ou `404`) |

Adotar a estrutura de resposta e envelope de erros preexistentes na R01, se houver. IDs UUID como strings na API. Nunca devolver `storage_key` resolvida como caminho local, `probe_metadata` bruto sem filtragem, traceback ou segredos. Não implementar download nem endpoint de reprodução na R02; planejar contrato de leitura com HTTP Range na R03.

**Erros a documentar e testar:** `400` multipart/formato malformado; `404` projeto/asset ausente ou soft-deleted; `413` body ou arquivo acima do limite; `415` extensão/container/formato rejeitado; `422` dados de projeto ou conteúdo MP4 inválido, sem stream de vídeo, duração fora do limite ou inconsistência de metadados; `503` falha temporária de armazenamento/FFprobe indisponível ou timeout (distinguir claramente de vídeo inválido). `409` opcional para tentativa de ação em recurso já logicamente excluído se o contrato do sistema preferir essa semântica. Respostas devem ser estáveis, legíveis e sanitizadas.

Não prometer atomicidade perfeita entre PostgreSQL e sistema de arquivos; coordenar compensação e reconciliação.

## 6. Política de upload seguro

**Limites iniciais configuráveis, herdados da R00:** por padrão, até **500 MiB (524.288.000 bytes)** de arquivo e duração até **600 segundos**. Esses valores são hipótese de produto, devem constar do `.env.example` e ser testados; não são garantia de adequação para todos os hardwares. Um arquivo por requisição. Estabelecer limites para multipart e metadados de formulário para evitar abuso.

**Observação de segurança importante:** `UploadFile` usa um arquivo temporário spooled e o código da rota recebe a entrada após parsing multipart. Por isso, limitar apenas `await file.read(n)` no endpoint **não limita necessariamente o body já recebido**. Implementar/validar também controle de ingress/body total antes ou durante o parsing compatível com a versão instalada de Starlette/FastAPI, cobrindo requests com e sem `Content-Length`; somar overhead de multipart sem rejeitar uploads válidos injustamente. Streaming do upload não significa que o próprio parser esteja sob um limite global. Não usar `bytes = File(...)`, nem `await file.read()` sem tamanho.

**Validação mínima:**

1. Existência de projeto não excluído, limitação do body e número de campos/arquivos.
2. Nome recebido tratado apenas como exibição; remover diretórios, controles e caracteres perigosos, com tamanho máximo definido.
3. Extensão `.mp4` case-insensitive; MIME informado é somente indício, não confiança.
4. Escrita em staging local com chunks limitados (ex.: 1 MiB), SHA-256 calculado incrementalmente, contagem real de bytes; interromper antes de exceder limite.
5. Inspeção de assinatura/container MP4 (`ftyp` na estrutura ISO BMFF quando apropriado) + execução real de FFprobe com timeout, tamanho de stdout limitado, subprocesso sem shell, sem URL remota ou argumento arbitrário fornecido pelo usuário.
6. Confirmar que existe stream de vídeo válida, resolução positiva, duração >0 e ≤600 s; áudio é opcional. Selecionar stream principal determinística e documentar. Vídeos MP4 com codecs diversos podem ser aceitos se inspecionáveis; informar codec e possíveis restrições de reprodução na R03.
7. Filtrar metadados do probe; prevenir sobrecarga por saída excessiva, arquivo malicioso e valores numéricos inválidos.
8. Checar espaço livre e permissões; promover original para `storage/originals/{project_uuid}/{asset_uuid}/source.mp4` (exemplo interno), gerado pelo servidor e fora de rotas estáticas públicas. Garantir staging e destino no mesmo filesystem para rename atômico, se adotado.
9. Registrar `MediaAsset` apenas após validação; se falha de BD ou storage, limpar artefatos criados e não deixar registro apontando para arquivo ausente. Se processo cair entre rename e commit, registrar/reconciliar possíveis arquivos órfãos sem apagar mídia de usuário automaticamente.
10. Preservar original: nunca sobrescrever nem modificar bytes após importação; checksum persistido deve coincidir com arquivo final. Duas importações com mesmo nome geram keys/IDs independentes.

Não disponibilizar caminhos arbitrários, symlinks, diretórios ou acesso a arquivos de outros projetos. Não transformar `storage` inteiro em static mount. API em loopback; antes de exposição LAN/Internet exigir autenticação/autorizações.

## 7. UX e frontend

**Tela Projetos:** criar, listar em ordem recente, identificar estados vazio/loading/falha, editar nome e finalidade, exclusão lógica com confirmação e retorno à lista; paginação simples ou botão carregar mais para listas extensas.

**Tela de Projeto:** nome e propósito, área para selecionar/arrastar MP4, indicação dos limites, validações imediatas de extensão/tamanho como feedback (não substituem segurança do servidor), progresso real de upload quando transport o permitir (XHR/Axios), estágio separado `Analisando vídeo...` sem percentual fabricado, erros claros e biblioteca com duração, resolução, tamanho, FPS, codec, presença de áudio, data.

**Sem player/timeline/ações inertes:** não renderizar funcionalidades futuras como operacionais. Responsividade, teclabilidade, labels, aria de progresso/status, contraste, temas da R01 preservados. Não confundir projeto excluído com arquivo removido; exclusão lógica oculta projeto e preserva mídia/arquivo até uma futura política explícita.

## 8. Testes obrigatórios e QA

### Backend unitário / integração de domínio
- Create/list/get/patch/soft delete + validação de nome/purpose/preset.
- Não listar projetos excluídos; não importar mídia para projeto excluído; 404 correto para relação projeto/asset inválida.
- Paginação e ordenação determinísticas.
- Multipart vazio, campos extras, múltiplos arquivos, extensão/MIME falsificados, arquivo corrompido, `.mp4` que não é vídeo, vídeo sem áudio válido, vídeo com áudio, arquivo acima do limite, duração maior que limite, FFprobe sem binário, timeout, saída malformada.
- Testar tamanho sem `Content-Length` e proteção antes do parser; não presumir que `UploadFile` aplica limite global ao multipart.
- Verificar SHA-256, metadados normalizados, imutabilidade, mesmo nome em dois uploads, rollback e limpeza de temporários.
- Verificar que não há traversal, vazamento de caminhos/segredos/logs ou arquivos órfãos evitáveis.

### Frontend
- Lista vazia, carregamento, erro e sucesso.
- Criar, renomear, excluir com confirmação e atualizar lista.
- Fluxo de upload, progresso genuíno, inspeção, resultados e erros; tratamento de cancelamento/desconexão se implementado.
- Dados dos assets formatados, tema e responsividade sem regressão.

### Integrado real
- Criar amostra MP4 sintética local de duração pequena via FFmpeg (ex.: 1 segundo; caso de áudio e sem áudio), usar upload HTTP real.
- Verificar BD, tamanho, FFprobe, SHA-256 e bytes do original armazenado.
- Reiniciar serviços e reconsultar projetos e mídia persistidos.
- Inspecionar serviços `api`, `worker`, `frontend`, `db` e seus healthchecks, além do Compose.
- Provar request inválida não cria registro nem deixa staging; não apaga outros arquivos.
- Rodar toda suite existente R01 para impedir regressão (36 backend, 9 frontend e demais verificações que continuarem aplicáveis — contagens reportadas, não garantidas).

**QA exigido:** pytest, Ruff, Vitest, ESLint, Prettier, build, Alembic upgrade, Compose config/build/up, chamadas HTTP e navegador quando disponível. Registrar versão das ferramentas e comandos/resultados exatos. Não inventar testes passados. Não usar dados reais do usuário em fixtures.

## 9. Sequência de implementação (lotes dentro da R02)

| Lote | Entrega | Gate |
|---|---|---|
| R02.1 | Inspeção R01, contratos e migração Project/MediaAsset | upgrade aplica sem alterar baseline e sem apagar dados |
| R02.2 | CRUD API de projetos | contratos e testes negativos aprovados |
| R02.3 | StoragePort local e política de upload | limitação de corpo/staging/permissões testadas |
| R02.4 | FFprobe/metadata/importação atômica | MP4 válido salvo, inválidos rejeitados sem sujeira |
| R02.5 | UI de projetos e biblioteca | fluxo real no navegador, sem controles fictícios |
| R02.6 | Regressão, integração, README, relatório de QA | todos os critérios essenciais aprovados |

## 10. Critérios objetivos de aceite

- [ ] R01 preservada: endpoints, worker, banco, Compose, frontend e health checks continuam funcionando.
- [ ] Migration R02 criada e aplicada sobre baseline existente sem perda de dados.
- [ ] CRUD de projetos opera na API e na interface; exclusão é lógica e confirmada.
- [ ] Listagem de projetos e mídias usa paginação e responde corretamente a IDs inválidos.
- [ ] MP4 válido é importado por multipart, com limites reais de requisição e bytes.
- [ ] FFprobe real extrai duração, resolução, FPS, codecs e áudio de modo consistente.
- [ ] SHA-256 e byte_size conferem com original persistido, jamais sobrescrito.
- [ ] Upload inválido/grande/interrompido falha com erro compreensível e sem arquivo órfão ou registro inconsistente evitável.
- [ ] A API não expõe paths locais/segredos, não aceita traversal e segue restrição loopback.
- [ ] UI mostra estados verdadeiros de upload/inspeção e metadados; não há player/timeline/render falso.
- [ ] Projetos e uploads permanecem íntegros após reinício dos containers.
- [ ] Suite nova e regressão R01, checks, lint e builds realmente executados e documentados.
- [ ] `docs/r02/acceptance.md`, `docs/r02/qa-results.json` e README atualizados com evidências, limitações e decisões.
- [ ] Nenhuma implementação da R03+; Git gerenciado manualmente pelo usuário.

## 11. Relatório obrigatório ao concluir a implementação

Estruturar de **A a H** como na R01:

A. Alterações implementadas e caminhos dos arquivos.  
B. Esquema de banco, migrations, dados de teste e segurança de storage.  
C. Endpoints com métodos, códigos HTTP, corpo de resposta e erros testados.  
D. Upload e FFprobe: formato real, SHA-256, latência, limites e casos negativos.  
E. Frontend: páginas, estados reais e evidências de navegador.  
F. Testes, lint, build, Compose e regressão R01 com comandos e contagens reais.  
G. Checklist de aceite, pendências, riscos e comportamento sob falhas.  
H. Recomendação fundamentada de prontidão para R03 (sem iniciar R03).

A documentação de QA deverá anonimizar nomes e paths privados. Sem comandos de commit/push; não executar operações Git que alterem histórico, branches ou publiquem código.

## 12. Decisões arquiteturais e pontos a revisar

- **Soft delete de Project** preserva media e é reversível tecnicamente, mas UI de restauração não entra na R02. Guardar `deleted_at`; planejar futura política de retenção para evitar crescimento do storage.
- **Upload single-request, síncrono até confirmação** simplifica o MVP; não prometer upload em chunks/resumível. FFprobe limitado por timeout; usar worker dedicado só quando o volume de mídia justificar, sem violar a proibição de render na API.
- **Biblioteca sem download/stream** mantém fronteira com o player da R03. Original pode ser inspecionado/validado via testes e filesystem controlado; endpoint protegido de reprodução com HTTP Range será especificado na R03.
- **Desduplicação por hash** não está incluída: mesma mídia pode existir em projetos distintos e deve permanecer isolada.
- **Erros e data transfer** devem seguir convenções já implementadas na R01, não introduzir formato divergente gratuitamente.
- **Limits** são defaults provisórios configuráveis; ajustar com evidências de teste no hardware alvo.

## 13. Referências

- Documentação de projeto Edivo e ADRs R00 existentes no repositório.
- FastAPI — Request Files: https://fastapi.tiangolo.com/tutorial/request-files/
- Starlette — Requests/multipart: https://www.starlette.io/requests/
- FFprobe: https://ffmpeg.org/ffprobe.html
- Alembic: https://alembic.sqlalchemy.org/

**Status deste documento:** especificação proposta para orientar execução pelo Codex CLI. Nenhum código, migração ou teste R02 executado por este documento.
