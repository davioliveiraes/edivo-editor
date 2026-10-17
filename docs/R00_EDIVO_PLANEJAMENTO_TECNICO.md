# Edivo — R00: Fundação e planejamento arquitetural

> **Data:** 16/10/2026  
> **Produto:** Edivo · **Repositório:** `edivo-editor`  
> **Estado:** especificação arquitetural; nenhum código ou infraestrutura do projeto foi implementado nesta etapa.  
> **Base:** `EDIVO_DOCUMENTACAO_PROJETO.md` e `EDIVO_INSTRUCOES_CHATGPT(1).md`. Recomendações adicionais deste plano são propostas de R00, não funcionalidades existentes.

## 1. Objetivo, premissas e limites

Definir a arquitetura e contratos do Edivo para implementar, incrementalmente, um editor de vídeo **web local-first** com FastAPI/Python 3.12, React/TypeScript, PostgreSQL, FFmpeg/FFprobe e um worker de mídia separado. Planejar a evolução para edição assistida pela skill heditor e para integrações externas opcionais, sem depender delas no MVP.

**Resultado esperado desta R00:** decisões documentadas, monorepo especificado, responsabilidades por módulo, modelo de dados conceitual, fila e armazenamento definidos, matriz de dependências, requisitos não funcionais, critérios verificáveis e roadmap. Não se espera código, execução de containers, instalação de ferramentas, renderizações ou relatórios de testes executados.

**MVP (R01–R06):** projeto, importação MP4, validação e metadados, player com timeline de uma faixa, corte de entrada/saída, formatos 9:16 / 1:1 / 4:5 / 16:9, enquadramento por preenchimento ou barras, texto/CTA simples, ganho de áudio e trilha autorizada, prévia, MP4 H.264/AAC quando suportado, acompanhamento de jobs, download e histórico. O arquivo original nunca é sobrescrito.

**Excluído do MVP:** multitrilha e keyframes avançados, transcrição/legenda automática avançada, agentes em produção, geração remota, publicação automática, autenticação multiusuário, cobrança, GPU mandatória e recursos Higgsfield. Não implementar APIs simuladas desses recursos.

### Decisão de produto

Há **dois modos independentes** a evoluir: **Editor básico**, determinístico e executado com FFmpeg; e **Edição com IA**, que em fases posteriores poderá acionar um agente compatível com a skill heditor. O modo básico funciona mesmo se agentes/fornecedores estiverem indisponíveis.

## 2. Diagnóstico da stack

| Camada | Escolha recomendada | Justificativa / ressalvas |
|---|---|---|
| Runtime backend | Python **3.12** | Compatibilidade com diretriz do projeto e faixa declarada pelo heditor; versões devem ser fixadas após prova local. |
| API | FastAPI + Pydantic v2 + pydantic-settings | Validação e contrato OpenAPI; processamento de mídia fica fora do processo HTTP. |
| Persistência | PostgreSQL + SQLAlchemy 2.x + Alembic + psycopg 3 | Relações, integridade, migração e fila simples transacional. Proposta inicial: sessões SQLAlchemy síncronas; assíncrono no banco não é requisito da R01. |
| Frontend | React + TypeScript estrito + Vite | Editor interativo com separação de componentes e features. |
| Interface | Tailwind CSS + shadcn/ui + Lucide | Componentização, acessibilidade e temas claro/escuro. |
| Dados remotos | TanStack Query | Cache, loading, erros e polling; estados de controles locais com React hooks/reducer. |
| Processamento | FFmpeg + FFprobe invocados por subprocesso seguro | Encode, filtros, inspeção técnica e geração de previews. Verificar disponibilidade de codecs/filtros da build escolhida. |
| Jobs | Worker Python separado + tabela PostgreSQL | Confiabilidade sem Redis na primeira versão; polling transacional, heartbeat, recuperação e limite de concorrência. |
| Storage | Diretório gerenciado no host, montado em API/worker | Preserva originais, previews, temporários e exports; arquivo não é exposto por caminho bruto. |
| Ambiente | Docker Compose + Ubuntu/WSL2 | Serviços reproduzíveis, volume persistente e rede local. |
| Testes backend | pytest + httpx + testes integrados de banco e mídia | Contratos, workflow, segurança, integridade e subprocessos. |
| Testes frontend | Vitest + Testing Library | Player, formulários, timeline, estados e chamadas HTTP. |
| Formatação/qualidade | Ruff (lint + format), TypeScript strict, ESLint/Prettier | Manter configuração mínima; escolha final deve ser validada na R01. |

**Dependências deliberadamente adiadas:** Redis/RQ/Celery, objeto de armazenamento S3, faster-whisper/MediaPipe/GSAP/HyperFrames no runtime do editor básico, GPU, autenticação e serviços externos. O heditor pode exigir sua própria instalação isolada na R07, sem injetar sua extensa árvore de dependências no backend do MVP.

### Ambiente e versões

- **Python:** fixar 3.12.x na configuração do projeto. Registrar versões efetivamente testadas no relatório R01.
- **Node:** escolher release LTS suportada pelo conjunto Vite/shadcn/heditor, validar e fixar em arquivo de versão; não presumir compatibilidade sem teste. O README do heditor consultado documenta Node >= 18 para seu instalador.
- **FFmpeg/FFprobe:** instalar versões compatíveis da mesma distribuição/build. Checar `libx264`, AAC, `drawtext` e filtros necessários. A existência do binário por si só não valida capacidade de renderização.
- **PostgreSQL:** major explicitamente fixado na R01 (sugestão PostgreSQL 16, se compatível com o ambiente disponível). Não usar tags `latest` em produção/reprodução.
- **Dependências Python/JS:** declarar faixas em manifestos, gerar locks reproduzíveis e registrar atualização de versões; não inventar pins antes da primeira instalação verificada.
- **Máquina-alvo:** Ubuntu 24.04/WSL2 com Docker. heditor declara recomendação de Linux/macOS ou Windows via WSL2, 4 núcleos e 8 GB RAM; trata-se de informação do projeto externo e não de benchmark do Edivo.

## 3. Visão arquitetural e fronteiras

```text
Browser (React/Vite)
  ├── projetos, edição e arquivos
  └── REST /api/v1 + polling GET job
             |
             v
FastAPI (autorização local futura, validação, domínio)
  ├── SQLAlchemy -> PostgreSQL (entidades, specs, jobs, auditoria)
  ├── StoragePort -> storage local gerenciado (originals, preview, export)
  └── JobService -> cria job persistente e retorna 202
                                 |
                       SELECT ... FOR UPDATE SKIP LOCKED
                                 v
                    Worker Python separado
                      ├── FFprobeInspector
                      ├── BasicEditor (FFmpeg)
                      ├── OutputValidator
                      ├── HeditorExecutor (R07/R08, isolado)
                      └── MediaProvider (R09+, externo opcional)
                                 |
                      logs saneados, status e artefatos
                                 v
                              PostgreSQL
```

**Regra de dependência:** `api -> services -> repositories/ports`; `worker -> services/ports -> adapters`; camada de domínio não importa FastAPI nem detalhes de comandos FFmpeg. API e worker usam o mesmo pacote backend e contratos tipados, mas processos/containers separados. Portas/interfaces devem ser práticas, sem produzir dezenas de abstrações especulativas.

### Componentes

1. **Frontend:** apresenta a fonte (player nativo), timeline simples, controles, estado de edição e resultados. Não monta comandos do sistema e não acessa o filesystem do host diretamente.
2. **API:** recebe uploads com limites, valida corpos, atribui IDs, persiste revisões/jobs e devolve URLs opacas de leitura. Não executa codificação longa dentro de requests, nem usa `BackgroundTasks` como fila de render.
3. **Banco:** fonte de verdade para histórico, relações e estado de jobs. Storage contém bytes; banco contém caminhos internos e metadados, nunca arquivos grandes em BLOB.
4. **Worker:** captura jobs confirmados, cria workspace exclusivo, resolve arquivos por identificador, compõe comando seguro, executa com limites, registra progresso e valida saída antes de promover a exportação.
5. **Storage:** arquivos originais imutáveis, derivados versionados, staging e temporários sujeitos a política de retenção. Separar leitura de mídia e download.
6. **Integração heditor futura:** executor dedicado para agente + skill, isolamento forte, revisão humana do plano, artefatos e QA. Não converter livremente prompts de usuários em comandos shell.

## 4. Estrutura proposta do monorepo

```text
edivo-editor/
├── README.md
├── AGENTS.md                         # regras para agentes no repositório, quando necessárias
├── .env.example
├── .gitignore
├── compose.yaml
├── docs/
│   ├── vision.md
│   ├── architecture.md
│   ├── roadmap.md
│   ├── requirements.md
│   ├── api-contracts.md
│   ├── media-pipeline.md
│   ├── heditor-integration.md
│   ├── qa-plan.md
│   └── adr/
│       ├── 0001-local-first-modular-monolith.md
│       ├── 0002-postgres-job-queue.md
│       ├── 0003-edit-spec-and-immutable-revisions.md
│       ├── 0004-filesystem-storage.md
│       └── 0005-heditor-isolated-executor.md
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock                       # se o gerenciador uv for escolhido na R01
│   ├── alembic.ini
│   ├── alembic/
│   ├── app/
│   │   ├── main.py                   # API entrypoint
│   │   ├── api/v1/                   # routers + dependencies
│   │   ├── core/                     # settings, errors, logging
│   │   ├── domain/                   # regras e contratos tipados independentes de HTTP
│   │   ├── models/                   # SQLAlchemy entities
│   │   ├── schemas/                  # Pydantic requests/responses
│   │   ├── repositories/             # acesso ao banco
│   │   ├── services/                 # casos de uso
│   │   ├── storage/                  # storage port + adapter filesystem
│   │   ├── integrations/
│   │   │   └── ffmpeg/               # probe, command builder, validation
│   │   └── worker/                   # entrypoint, claim, subprocess, lifecycle
│   └── tests/{unit,integration,media}/
├── frontend/
│   ├── package.json
│   ├── [arquivo lock do gerenciador escolhido]
│   ├── src/
│   │   ├── app/                      # providers, routes, theme
│   │   ├── components/ui/
│   │   ├── features/
│   │   │   ├── projects/
│   │   │   ├── media/
│   │   │   ├── editor/
│   │   │   ├── jobs/
│   │   │   └── exports/
│   │   ├── hooks/
│   │   ├── lib/api/
│   │   └── types/
│   └── tests/
├── scripts/                          # health/diagnósticos verificáveis
├── fixtures/                         # pequenos arquivos de mídia de teste licenciados/sintéticos
└── storage/                          # GITIGNORE; montado em API/worker
    ├── originals/
    ├── previews/
    ├── exports/
    ├── workspaces/
    └── temp/
```

**Notas:** árvores são alvo de organização, não pastas criadas. `integrations/heditor/` e `integrations/providers/` podem aparecer apenas quando seus contratos/implementações forem necessários (R07/R09); nesta R00 é suficiente documentá-los. Não versionar os arquivos de vídeo produzidos, chaves, logs com dados privados ou instalações do heditor.

## 5. Domínio e persistência — contrato conceitual

| Entidade | Campos essenciais | Invariantes |
|---|---|---|
| `Project` | id UUID, name, purpose?, target_preset?, created_at, updated_at | Projeto identificado de forma estável; pode possuir mídia, specs e jobs. |
| `MediaAsset` | id UUID, project_id, kind, original_filename, storage_key, checksum SHA-256, byte_size, mime_detected, probe_json, duration_ms, width, height, fps_num/den, has_audio, created_at | Original nunca sobrescrito; localização resolvida somente pelo serviço de storage; vinculado ao projeto. |
| `EditSpecRevision` | id UUID, project_id, revision INTEGER, schema_version, source_asset_id, payload JSONB, created_at | Snapshot **imutável**; revision único por projeto; operações são dados tipados e validados. |
| `RenderJob` | id UUID, project_id, spec_revision_id, job_type, state, stage, progress, attempts, cancel_requested, worker_id?, heartbeat_at?, error_code?, error_public?, timestamps | Job usa revisão congelada; transições de estado são controladas; não conclui antes de QA e publicação. |
| `Export` | id UUID, project_id, render_job_id, spec_revision_id, version, storage_key, checksum, codec, duration_ms, width, height, byte_size, qa_report JSONB, created_at | Registro aponta para artefato verificado; versões não sobrescrevem as anteriores. |

`ProviderConfig` é **futuro** (R09), não entra nas migrations iniciais. Para o MVP, uma revisão vigente pode ser referenciada pelo projeto e as revisões anteriores mantidas para consistência histórica.

### Contrato `EditSpec` versão 1 (conceitual, não JSON executável)

- `schema_version`: inteiro; rejeitar versões desconhecidas.
- `source_asset_id`: UUID do vídeo importado dentro do mesmo projeto.
- `trim`: `start_ms` e `end_ms`, inteiros, 0 <= start < end <= duração fonte.
- `canvas`: `preset` (`9:16`, `1:1`, `4:5`, `16:9`), `width`, `height`, `fps` racional aprovado.
- `framing`: `mode` (`contain` com padding, `cover` com crop), `focus_x`, `focus_y` no intervalo [0, 1], `background_color` permitida no modo contain.
- `texts`: no MVP **um elemento simples** opcional com `value`, `font_id` da allowlist, `color`, `position` normalizada/âncora, `start_ms`, `end_ms` relativos à composição.
- `audio`: ganho da fonte em dB dentro de faixa segura; `music_asset_id?` + ganho da trilha, sem upload implícito de músicas de terceiros.
- `export_profile`: `mp4/h264/aac`, parâmetros predefinidos, jamais flags arbitrárias fornecidas pelo usuário.

**Importante:** um projeto pode ter um formulário de edição mutável em tela, mas jobs sempre recebem um snapshot publicado e imutável. Os tipos em TypeScript devem refletir Pydantic/OpenAPI, sem duas implementações divergentes de validação. Prévia e export final consomem o mesmo `EditSpec`, variando apenas parâmetros de qualidade/resolução.

### Considerações temporais e mídia

- A API expressa tempos em **milissegundos inteiros**. FFprobe deve preservar FPS racional, timestamps/PTS e duração de streams; não presumir `fps=30`.
- Avaliar na R02/R03 arquivos móveis com rotação por metadados, sample aspect ratio e FPS variável (VFR). Para cortes, garantir consistência temporal mesmo após transcodificação e tolerância de até um frame na saída, quando verificável.
- Fonte sem áudio é válida se o vídeo for decodificável; a exportação só terá AAC se houver áudio resultante. Recusar codecs/containers não suportados com erro compreensível, sem prometer suporte universal a MP4.

## 6. Contratos HTTP propostos

| Rota | Finalidade | Respostas-chave |
|---|---|---|
| `GET /api/v1/system/health` | Liveness/readiness distinguíveis | 200; não confundir API viva com worker apto a renderizar. |
| `GET /api/v1/system/capabilities` | Informar versões verificadas de FFmpeg, suporte a codecs/filtros, limites | 200, capacidades reais; indisponível ≠ simulado. |
| `POST /api/v1/projects` | Criar projeto | 201 + ID. |
| `GET /api/v1/projects` e `GET /api/v1/projects/{id}` | Lista e detalhe | 200/404. |
| `POST /api/v1/projects/{id}/media` | Upload stream, inspeção e registro | 201 ou erro 413/415/422. |
| `GET /api/v1/projects/{id}/media` | Assets do projeto | 200. |
| `GET /api/v1/media/{id}/stream` | Acesso controlado ao vídeo para player | 200/206, `Range` e 416 para faixa inválida. |
| `PUT /api/v1/projects/{id}/edit-spec` | Validar e registrar nova revisão | 200 + `revision_id` ou 409 se revisão esperada divergir. |
| `POST /api/v1/projects/{id}/previews` | Enfileirar prévia | 202 + job ID. |
| `POST /api/v1/projects/{id}/renders` | Enfileirar render final | 202 + job ID. |
| `GET /api/v1/render-jobs/{id}` | Estado, progresso, etapa, erro seguro | 200/404. |
| `POST /api/v1/render-jobs/{id}/cancel` | Solicitar cancelamento | 202 em execução, 200/204 se cancelamento já efetivo; documentar comportamento idempotente. |
| `GET /api/v1/projects/{id}/exports` | Histórico | 200. |
| `GET /api/v1/exports/{id}/download` | Download autorizado | 200/206. |

São **rotas propostas**, não implementadas. Padrão de erros: `code`, `message`, `request_id`, `details` seguros; nenhum caminho interno, stack trace, segredo ou comando bruto. `/api/v1` é o prefixo único. CORS restrito aos origins locais declarados; servidor acessível por loopback no modo single-user.

**Sem autenticação no MVP** só é aceitável se o app ficar **local** e não for exposto à rede ou Internet. Antes de exposição pública, autenticação, isolamento por proprietário e modelo de autorização tornam-se bloqueadores.

## 7. Ciclo de vida dos jobs

**Estados públicos obrigatórios:** `queued`, `running`, `completed`, `failed`, `cancelled`. `cancel_requested` é sinal transitório separado, não conclusão. **Etapas:** `waiting`, `probing`, `preparing`, `encoding`, `validating`, `publishing` (nomes internos sujeitos a refinamento).

1. API valida existência do projeto, ativo de mídia e revisão; grava `RenderJob(queued)` em transação e responde 202 após commit.
2. Worker único no MVP pesquisa jobs elegíveis com transação curta, `FOR UPDATE SKIP LOCKED`, marca ownership/lease e muda para `running`. Não mantém transação aberta durante o FFmpeg.
3. Executa em workspace do job; atualiza `stage`, `heartbeat_at` e progresso por `-progress pipe:1`, convertendo tempo processado/duração alvo sem chegar a 100% antes de QA.
4. Em cancelamento, sinaliza processo e filhos, aguarda prazo de encerramento, encerra forçadamente se necessário, limpa artefato parcial e marca `cancelled`; não apaga original nem exports anteriores.
5. Em sucesso técnico, verifica arquivo, decode, tamanho, resolução, duração, codecs, sincronização amostrada e integridade. Publica com operação atômica no mesmo filesystem e grava `Export`.
6. Em erro, timeout, falta de disco ou morte de worker, registra erro sanitizado, deixa staging recuperável conforme retenção e permite recuperação controlada. Jobs com heartbeat expirado são reconciliados; retries apenas idempotentes e com limite configurado.

**Decisão MVP:** concorrência de **1 render por vez**, filas em PostgreSQL e polling da interface a cada **2 segundos** enquanto houver job ativo (valores iniciais **propostos**). `SELECT ... SKIP LOCKED` serve como mecanismo transacional local, não substitui uma plataforma de filas quando o produto escalar. Redis/RQ/Celery entram somente quando volume, prioridade, infraestrutura múltipla ou filas especializadas justificarem.

## 8. Pipeline FFmpeg: segurança e resultados

**Fluxo:** upload temporário -> limite por bytes -> extensão/assinatura + detecção -> `ffprobe` JSON -> política de mídia -> armazenamento do original -> `EditSpec` -> job -> resolução de `storage_key` -> comando FFmpeg parametrizado -> preview/export em staging -> FFprobe/QA -> publicação versionada.

**Operações:**

- Corte temporal com precisão adequada à timeline; priorizar transcodificação previsível quando o corte não coincide com keyframes.
- `cover`: escala preservando proporção, depois crop controlado por foco. `contain`: escala preservando proporção, depois padding; proibir distorção por estiramento involuntário.
- Presets finais: 9:16 = 1080×1920; 1:1 = 1080×1080; 4:5 = 1080×1350; 16:9 = 1920×1080. Preview proporcional reduzido, gerado pelo backend com os mesmos filtros lógicos.
- Texto simples com fonte permitida e safe area: escapar caracteres especiais nos filtros ou, preferencialmente, usar arquivo de texto gerenciado. Nunca interpolar texto livre em uma string shell.
- Áudio: ganho e trilha opcional; respeitar presença/ausência de áudio fonte, duração, fim/fade quando aplicável e prevenção de clipping; testar voz sempre inteligível.
- Export: MP4 com H.264, `yuv420p` e AAC quando houver faixa de áudio; sinalizar se build do FFmpeg não oferecer encoder necessário. Considerar `faststart` para playback.

**Restrições de subprocesso:** argv estruturado, sem `shell=True`, timeout e encerramento por grupo de processos, recursos limitados, protocolos de entrada restritos a caminhos gerenciados, diretórios canônicos sem traversal/symlink malicioso, sem execução de argumentos avançados vindos do cliente. Desabilitar acesso remoto implícito; investigar riscos de arquivos malformados, filtros e referências a arquivos externos.

**Políticas sugeridas, não validadas:** limite inicial de upload de **500 MB**, duração de fonte **até 10 min** e um render simultâneo, todos configuráveis em `Settings`; testar usabilidade/recursos antes de fixá-los como padrão de produto. Alertar sobre necessidade de espaço temporário para originais + intermediários + exports.

**Compatibilidade:** navegador deve conseguir navegar/seek com `HTTP Range`; Starlette `FileResponse` documenta suporte a 206/416, mas validar na versão escolhida. Não servir diretamente todo o diretório `storage` como estático público.

## 9. UX mínima por fase

- **R01:** shell da aplicação, layout responsivo, temas claro/escuro, páginas/estados vazios e status técnico.
- **R02:** tela Projetos, criação, lista, importação, metadados, erros legíveis.
- **R03:** Editor com player, timeline de **uma faixa**, controles de in/out e presets; distinguir posição do playhead do recorte salvo.
- **R04:** texto único/CTA opcional, controle simples de áudio e criação/revisão de preview; prévia backend é autoridade sobre render.
- **R05:** exportação com perfil, progresso, estado, cancelamento, download e versões.
- **R06:** acessibilidade teclado, foco visível, testes E2E, responsividade e tratamento consistente de falhas.

A visualização instantânea do browser pode aproximar o layout, mas a **prévia renderizada** deve ser tratada como a referência final para filtros de FFmpeg, fontes e áudio. Não chamar de WYSIWYG perfeito sem comprová-lo.

## 10. heditor: análise oficial e estratégia de integração

O repositório oficial `pycodebr/heditor` consultado em 16/10/2026 descreve a skill como **Agent Skill**, com `SKILL.md`, referências e uma CLI `heditor`, além de fluxo de transcrição, seleção, `DESIGN.md`, composição HTML/GSAP/HyperFrames, QA e finalização. A versão indicada no frontmatter da skill consultada é **2.0.0**; trata-se de referência documental do momento, não de versão instalada ou testada. A documentação atual também descreve vídeo longo e formatos além de 9:16, ampliando a visão inicial do documento do projeto. Não existe confirmação de que essa CLI seja um SDK estável de integração em aplicações web.

**Separar duas provas:** (a) instalar e executar manualmente `heditor doctor`, editar uma amostra e validar logs/artefatos; (b) depois demonstrar orquestração **segura** via um agente executor com limites, progressos, auditoria e revisão do plano. O sucesso de (a) não prova (b).

**Fase R07:** revisar versão/licenças e referências oficiais, instalar em ambiente dedicado, registrar dependências reais (FFmpeg, Python, Node, Chrome/Playwright, modelos, fontes, GSAP, HyperFrames), executar uma amostra, comparar áudio e vídeo, salvar logs e `DESIGN.md`; projetar executor isolado. Não rodar scripts gerados pelo agente como administrador nem dentro do container de API.

**Fase R08:** uma solicitação em linguagem natural resulta em **plano revisável**; usuário aprova execução custosa; agente segue fluxo oficial; worker mantém jobs/artefatos/versões; QA final é obrigatório. Caso o heditor falhe, o editor básico continua utilizável. A interface exibe capacidades reais, não promete integração que não passou por smoke test.

**Artefatos heditor sugeridos:** `AgentEditRequest`, `AgentPlan`, `approval_status`, `AgentExecution`/job especializado, relatório de QA, revisões/exports; modelo concreto apenas quando R07 fechar o contrato do executor.

**Privacidade/licenças:** revisar termos de GSAP, HyperFrames, fontes, modelos, música/SFX Mixkit e codificadores FFmpeg antes de distribuição, especialmente futura oferta SaaS. Músicas enviadas pelos usuários são responsabilidade de licenciamento do usuário. Não copiar o repositório heditor para dentro do monorepo sem necessidade.

## 11. Integrações futuras: Higgsfield

R09 é exclusivamente pesquisa e PoC de fornecedor, mediante documentação oficial atualizada. **API** representa chamadas de produto a endpoints disponibilizados e autorizados; **MCP** representa ferramentas/fluxos de agente compatível. Não inferir que todo recurso MCP tem endpoint público idêntico ou vice-versa. Credenciais ficam em backend/gerenciador de segredos, consentimento explícito antes de enviar mídia, relatório de custos/créditos quando documentados, retenção/privacidade e controle de timeout/retry. O editor básico e o projeto local devem funcionar sem rede.

## 12. Segurança, observabilidade e NFRs

- **Segurança de upload:** checar tamanho com streaming/limite, assinatura, extensão, decodificação FFprobe, duração, largura/altura; proteção contra arquivos hostis e consumo excessivo.
- **Acesso:** bind dos serviços à interface loopback no modo single-user, CORS allowlist, sem endpoints públicos de listagem dos diretórios; antes de qualquer exposição LAN/Internet exigir autenticação e autorização.
- **Segredos:** `.env` local ignorado, `.env.example` sem credenciais; nenhuma chave no frontend, arquivos exportados ou logs.
- **Durabilidade:** nunca sobrescrever original; publicação atômica e checksums; migrations Alembic; testar consistência após crash e falta de disco.
- **Recursos:** CPU/RAM/disco/timeouts configuráveis, máximo de jobs simultâneos; para código de agente em R07, sandbox sem privilégios, rede restrita, volumes limitados e isolamento maior que container padrão caso modelo de ameaça exija.
- **Observabilidade:** logs estruturados com `request_id`, `job_id`, `project_id`, etapa, tempo e código de erro, sem conteúdo privado; linhas FFmpeg sanitizadas e logs brutos com retenção controlada.
- **Performance:** objetivos mensuráveis da R01–R06 devem ser definidos por hardware de teste; não declarar SLA de tempo de render ou GPU sem medições. Respostas de agendamento devem ser rápidas e nenhum encode bloquear HTTP.
- **Acessibilidade:** navegação por teclado, rótulos, contraste, feedback de processamento e mensagens de erro que permitam ação.

## 13. Plano de verificação e testes (para fases de implementação)

| Eixo | Casos de teste necessários | Fase |
|---|---|---|
| Ambiente | `health`, banco disponível, FFprobe/FFmpeg e filtros/encoders detectados; volumes persistentes | R01 |
| API/domínio | CRUD básico, 404, 409 em revisão concorrente, 413/415/422 em upload, erros sem dados sensíveis | R02–R05 |
| Segurança de mídia | MIME falso, extensão divergente, arquivo corrompido, symlink/traversal, nome estranho, duração/tamanho excedidos | R02/R06 |
| Timings | início e fim corretos, fonte VFR, 29.97 fps, rotação e vídeo sem áudio | R03–R06 |
| Aspecto | quatro presets, `cover` e `contain` sem distorção; texto visível dentro de safe area | R03–R06 |
| Áudio | fonte com/sem voz, trilha autorizada, ganho, ausência de clipping e sync | R04–R06 |
| Jobs | concorrência 1, colisão de workers, reinício, expiração do lease, cancelamento e falta de disco | R05–R06 |
| Export | arquivo decodifica; perfil/codec/duração/resolução conformes; checksum; histórico e original imutável | R05–R06 |
| Frontend | editor básico, formulário, estados busy/error/empty, teclado, polling interrompido em estado terminal | R02–R06 |
| heditor | doctor real, vídeo amostra, DESIGN, QA, logs, isolamento e revisão/consentimento | R07–R08 |

A R00 é **documental**: a lista acima são testes a desenvolver, e não testes executados. Manter fixtures sintéticas curtas para testes repetíveis; o teste manual com vídeo real deve ter autorização de uso.

## 14. Roadmap e entregáveis incrementais

| Etapa | Escopo restrito | Critério de avanço |
|---|---|---|
| **R00** | Arquitetura, ADRs, requisitos, contratos, riscos e aceite | Decisões explícitas e backlog verificável; sem código. |
| **R01** | Estrutura de monorepo, Compose, DB, migração inicial, API/worker skeleton, health e capacidades de FFmpeg | Aplicação local sobe, serviços saudáveis, versões registradas. |
| **R02** | Projeto, upload seguro, FFprobe e armazenamento original | MP4 válido importado e metadados consistentes; inválidos rejeitados. |
| **R03** | Player, timeline uma faixa, EditSpec revisionado, trim, proporções | Controles funcionais e preview da transformação de tempo/aspecto conforme recorte (se o preview completo ficar em R04, validar por render técnico). |
| **R04** | Texto/CTA, ganho de áudio/trilha, prévia renderizada | Prévia fiel ao EditSpec, texto legível e áudio coerente. |
| **R05** | Pipeline de render, job status/cancel, QA, exports versionados | MP4 final validado, baixa funciona, original intacto. |
| **R06** | Reforço de segurança, robustez, testes E2E e UX | Matriz essencial de QA passa; erros recuperáveis e documentação atualizada. |
| **R07** | Auditoria e instalação controlada do heditor, smoke test e desenho do sandbox | `doctor` e workflow verificados em amostra; integração não é presumida. |
| **R08** | Briefing IA, plano revisável, executor isolado e QA | Edição assistida auditável, com aprovação e fallback manual. |
| **R09** | Pesquisa e PoC documental Higgsfield API/MCP | Capacidade real, custos, permissões, privacidade e prova de integração. |

**Ordem preservada** do documento de projeto. A menção a heditor na «Fase 0» é tratada na R00 como **revisão de arquitetura/documentação**; setup e render real ficam no marco específico R07, como no roadmap de entregas, evitando misturar dependências pesadas com R01.

## 15. ADRs propostos (decisões e trade-offs)

- **ADR-0001 — Local-first e monólito modular:** escolhido para reduzir custo e latência, aceitando necessidade de replanejamento de autenticação/storage para distribuição.
- **ADR-0002 — Fila PostgreSQL inicialmente:** evita Redis extra e suporta commit atômico, porém exige lease/heartbeat/reconciliação e não é ideal para grande escala.
- **ADR-0003 — EditSpec declarativo, versionado e imutável por job:** favorece reprodutibilidade, mas demanda mapeamento estável de filtros e migração de schema.
- **ADR-0004 — Storage local com chaves opacas:** simples e barato para único host; exige mudança para objeto distribuído antes de escalar horizontalmente.
- **ADR-0005 — FFmpeg como núcleo independente e heditor em sandbox:** robustez do MVP e isolamento de dependências, com custo de integrar outputs criativos ao projeto.
- **ADR-0006 (proposto) — API HTTP/polling inicial:** menor complexidade e controle de estado; SSE pode surgir quando polling for insuficiente.
- **ADR-0007 (proposto) — SQLAlchemy síncrono na primeira versão:** simplifica worker e transações; reavaliar somente se medição indicar gargalo na API.

## 16. Riscos priorizados e mitigação

| Risco | Impacto | Prevenção |
|---|---|---|
| Upload MP4 com codec que navegador ou FFmpeg não reproduz | Alto | Inventário real de codecs, erros claros, opção futura de proxy compatível. |
| FFmpeg consome memória/disco ou trava | Alto | Worker limitado, timeout, logs, espaço mínimo e cancelamento. |
| Desalinhamento áudio/vídeo, rotação, FPS variável | Alto | Tempo e metadata corretos, fixtures e QA audiovisual. |
| Job fica `running` para sempre após interrupção | Alto | Leases, heartbeats, reaper e reconciliação determinística. |
| Fonte modificada ou versão de edição altera resultado anterior | Alto | Original imutável, hash, job com spec snapshot e exports versionados. |
| Exposição indevida do app sem autenticação | Crítico | Loopback no MVP; bloquear exposição pública até ter auth. |
| Execução arbitrária por agente ou filtros | Crítico | Sem shell livre, allowlists, sandbox com rede/FS/processos limitados. |
| Transição prematura para IA/complexidade | Médio | Fases independentes, provas reais R07/R08, evitar dependências no MVP. |
| Mudança upstream na skill/licenças | Médio | Pin de versão/commit na R07, revisão de licenças e contrato adaptador. |

## 17. Critérios de aceite objetivos para **R00**

- [ ] O escopo MVP e o que está fora dele estão explicitados.
- [ ] A arquitetura mostra navegador, API, Postgres, storage, worker e futuros adaptadores, com responsabilidades e dependências claras.
- [ ] Há uma estrutura de monorepo e divisão de módulos aptas a implementar R01 sem microsserviços desnecessários.
- [ ] Stack e dependências essenciais/opcionais estão classificadas; versões exatas pendentes de teste estão sinalizadas.
- [ ] Existem entidades, invariantes e contratos de EditSpec/revisões, armazenamento e jobs.
- [ ] O pipeline de mídia prevê validação, preservação do original, render com segurança e QA antes de publicar.
- [ ] O controle de jobs descreve fila, status, leases, tentativas, cancelamento, crash e progressos sem render HTTP síncrono.
- [ ] As rotas API são documentadas como propostas, sem alegar implementação.
- [ ] heditor é distinguido de biblioteca importável, plano de executor seguro é descrito e prova real fica agendada para R07.
- [ ] Critérios de aceite e matriz de testes para cada R01–R09 estão definidos.
- [ ] Nenhum código, migration, container ou instalação foi criado/executado nesta etapa.

**Aceite da R00 não equivale ao aceite do MVP.** O MVP só estará concluído após os fluxos de mídia testados nas etapas R01–R06.

## 18. Próxima ação

Após revisar/aprovar este plano, a **R01** deve criar somente a fundação operável: estrutura do monorepo, Compose, PostgreSQL, API `/health`, worker separado inicialmente ocioso, persistência mínima, verificação real de FFmpeg/FFprobe e interface React inicial. Não implementar upload, timeline, render final, heditor nem Higgsfield nessa primeira entrega.

**Sem sugestão de commit da R00:** nenhuma etapa de código foi realizada. O Git continua gerenciado manualmente pelo usuário.

## 19. Fontes públicas consultadas em 16/10/2026

- [pycodebr/heditor — README oficial](https://github.com/pycodebr/heditor)
- [heditor — SKILL.md oficial](https://github.com/pycodebr/heditor/blob/main/skills/heditor/SKILL.md)
- [heditor — workflow](https://github.com/pycodebr/heditor/blob/main/skills/heditor/references/workflow.md)
- [heditor — licenças](https://github.com/pycodebr/heditor/blob/main/skills/heditor/references/licenses.md)
- [FastAPI — Background Tasks (cautela com computação pesada)](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [FFprobe — documentação oficial](https://ffmpeg.org/ffprobe.html)
- [FFmpeg — documentação oficial](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg — filtros](https://ffmpeg.org/ffmpeg-filters.html)
- [PostgreSQL — SELECT / SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html)
- [Starlette — FileResponse / Range](https://www.starlette.dev/responses/)
