# Edivo — Documento de Visão e Planejamento

> **Status:** planejamento inicial (nenhum recurso implementado) · **Data:** 16/10/2026 · **Produto:** Edivo  
> **Repositório definido:** `edivo-editor` · **Tipo:** editor web local-first, preparado para serviços de IA.

## 1. Visão do produto

Desenvolver um editor de vídeo com interface web, desenvolvido com **Python/FastAPI** e **React/TypeScript**, que permita importar vídeos, aplicar operações básicas, visualizar o resultado e exportar arquivos prontos para Reels, Stories, TikTok, Shorts e anúncios do Meta Ads.

O sistema começa com recursos previsíveis e locais, executados com **FFmpeg**, e terá desde a fundação uma camada de integração para a skill **heditor** (https://github.com/pycodebr/heditor). Em fases posteriores, poderá coordenar agentes de IA e fornecedores externos, como a **API ou o MCP oficiais do Higgsfield**.

**Princípio:** primeiro construir um editor útil, estável e auditável; depois ampliar a automação criativa sem substituir indiscriminadamente o núcleo de edição.

## 2. Público e casos de uso

- Gestores de tráfego e criadores de anúncios, especialmente campanhas com destino ao WhatsApp.
- Pequenos negócios que desejam adaptar vídeos para redes sociais.
- Criadores de conteúdo que precisam de cortes, legendas, transições e exportações em formatos diferentes.
- Uso inicialmente local pelo desenvolvedor, com possível evolução para produto multiusuário/SaaS.

Casos de uso iniciais: cortar trechos indesejados; adaptar um vídeo para 9:16, 1:1, 4:5 ou 16:9; inserir título/CTA; aplicar ajustes simples de áudio; gerar prévia e exportar MP4. Modo avançado posterior: pedir em linguagem natural uma versão dinâmica usando o heditor e revisar o resultado.

## 3. Escopo por etapas

### Fase 0 — Fundação técnica e validação do heditor

- Criar monorepo com frontend, API e worker; ambiente reproduzível local no Ubuntu/WSL2.
- Verificar FFmpeg/FFprobe, dependências do heditor e `heditor doctor`.
- Instalar a skill **para um agente compatível** (p.ex., Codex CLI/Claude Code), sem assumir que o `SKILL.md` seja uma biblioteca Python importável ou um endpoint HTTP.
- Validar, separadamente, o pipeline real da skill com um vídeo pequeno de teste, guardando logs, artefatos e resultado.
- Verificar versões suportadas. A documentação consultada do heditor cita Python 3.10–3.13: usar Python 3.12 no início, salvo revisão técnica posterior.

**Saída:** projeto inicia localmente; FFmpeg e heditor passam nas verificações aplicáveis; o fluxo da skill é documentado.

### Fase 1 — MVP de edição básica (prioridade)

- Interface responsiva com tema escuro/claro: lista de projetos, página de edição e exportações.
- Criar projeto e importar um arquivo de vídeo local (MP4 inicialmente).
- Validar tipo, tamanho, duração e metadados via FFprobe.
- Player de pré-visualização com pontos de entrada/saída e seleção de proporção.
- Linha do tempo simplificada de **uma faixa de vídeo** com recorte de início/fim (não prometer editor multitrilha).
- Cortar, redimensionar, enquadrar ou preencher com barras/fundo conforme escolha; preservar proporções corretamente.
- Inserir um texto/título/CTA simples com configuração de cor e posição.
- Ajustar volume e aplicar trilha enviada pelo usuário, respeitando direitos de uso.
- Gerar uma prévia de baixa resolução e permitir render final MP4 (H.264/AAC quando apropriado).
- Apresentar progresso por etapas, logs sanitizados, erros e opção de baixar a exportação.
- Preservar original e versões exportadas; não sobrescrever o arquivo fonte.

**Fora do MVP:** timeline multifaixa, colaboração, cobrança, geração por modelos externos, marketplace, legendagem automática avançada, publicação automática, editor de keyframes completo, efeitos 3D.

### Fase 2 — heditor e edição assistida por IA

- Disponibilizar o modo “Edição com IA” como alternativa ao modo manual.
- Receber briefing: finalidade do vídeo, estilo, duração pretendida, formato, idioma, diretrizes de marca e CTA.
- Integrar o agente executor que lê a skill `SKILL.md` e segue seus documentos de referência.
- Orquestrar etapas do heditor, incluindo análise, transcrição, seleção, `DESIGN.md`, composição, QA e exportação.
- Expor ao usuário o plano de edição para revisão antes de uma renderização custosa.
- Guardar artefatos, versões e resultados de testes; permitir revisão de legendas e mensagens.
- Evitar inferir que todos os estilos da skill são templates estáticos; cada edição precisa respeitar o contexto do vídeo.
- Manter o modo manual funcionando mesmo com falhas de IA.

**Importante:** heditor é um conjunto de instruções e ferramentas de linha de comando **para agentes**, não uma API de edição pronta. A integração automática precisa ser projetada e validada. Código/comandos produzidos por agentes devem rodar em um ambiente isolado e com limites de recursos, não diretamente no processo do servidor web.

### Fase 3 — Integrações externas

- Criar interface `MediaProvider` ou adaptadores separados por provedor.
- Estudar integração via **Higgsfield API** para geração ou transformação de material audiovisual dentro do aplicativo, quando o endpoint/modelo escolhido suportar a operação.
- Estudar **Higgsfield MCP** para fluxos conduzidos por agentes compatíveis, distinguindo-o da API de produto.
- Disponibilizar configuração opcional por provedor, acompanhamento de status e custo estimado quando houver dados oficiais.
- Nunca inserir tokens no frontend, logs ou repositório; validar autenticação, documentação de modelo e preços antes de ativar recursos externos.
- Preservar controle do usuário sobre envio de mídia a terceiros.

### Fase 4 — Evolução futura

- Timeline multifaixa e editor visual mais sofisticado.
- Presets por marca/cliente, múltiplos projetos e processamento em lote.
- Legendas com revisão, transições e templates autorais.
- Autenticação, áreas de trabalho, quotas, armazenamento e arquitetura SaaS se houver demanda.

## 4. Tecnologias

| Camada | Stack recomendada | Por quê |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | UI modular e rápida |
| UI | Tailwind CSS, shadcn/ui, Lucide | Componentes consistentes |
| Estado no cliente | React Query/TanStack Query; estado local | Assincronia, cache e controles |
| Backend | Python 3.12, FastAPI, Pydantic | Contratos tipados e processamento |
| Persistência | PostgreSQL, SQLAlchemy 2, Alembic | Projetos, arquivos, jobs e versões |
| Motor básico | FFmpeg e FFprobe | Edição e extração de metadados |
| Processamento | Worker Python separado | Render não bloqueia requests |
| IA local posterior | heditor + agente compatível, faster-whisper, GSAP/HyperFrames | Criação, transcrição e motion |
| Infra local | Docker Compose para serviços, Ubuntu/WSL2 | Ambiente reproduzível |
| Escala posterior | Redis + Celery/RQ, storage de objetos | Fila persistente e workloads |
| Testes | pytest, httpx, Vitest, Testing Library | Controle de qualidade |

Não adicionar Redis, autenticação complexa ou infraestrutura distribuída sem necessidade concreta na primeira entrega. O desenho deve permitir essas evoluções.

## 5. Arquitetura proposta

```text
React + TypeScript (Vite)
      |
      | HTTP / API + status de jobs
      v
FastAPI (API e validação)
      |                    |
      v                    v
PostgreSQL           Armazenamento local
      |                 originals / previews / exports
      v
Worker Python (fila simples no MVP)
      |-- BasicEditorAdapter -> FFmpeg/FFprobe
      |-- HeditorAdapter (fase 2) -> agente compatível + skill heditor
      |-- ExternalMediaAdapter (fase 3) -> APIs/MCP autorizados
      v
Artefatos + logs + checks -> status -> UI / download
```

- API não deve executar renderização pesada durante uma requisição HTTP síncrona.
- Operações do FFmpeg devem receber argumentos estruturados, sem construir shell a partir de texto livre.
- A saída do heditor exige verificação independente; não marcar como “finalizado” antes de concluir os gates e a checagem de arquivos.
- Projetos e exportações precisam de IDs próprios; qualquer job pertence a um projeto existente.
- Arquivos originais são imutáveis; renderizações são versionadas.
- Evitar caminhos de entrada arbitrários, directory traversal, execução livre de comandos e exposição de diretórios privados.

## 6. Estrutura de pastas sugerida

```text
edivo-editor/
├── README.md
├── docs/
│   ├── vision.md
│   ├── architecture.md
│   ├── roadmap.md
│   └── heditor-integration.md
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── repositories/
│   │   ├── services/
│   │   ├── integrations/
│   │   │   ├── ffmpeg/
│   │   │   ├── heditor/
│   │   │   └── providers/
│   │   └── workers/
│   ├── alembic/
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   │   ├── projects/
│   │   │   ├── editor/
│   │   │   └── exports/
│   │   ├── hooks/
│   │   ├── services/
│   │   └── types/
│   └── tests/
├── storage/                  # ignorado pelo Git
│   ├── originals/
│   ├── previews/
│   ├── workspaces/
│   └── exports/
├── scripts/
├── compose.yaml
├── .env.example
└── .gitignore
```

## 7. Entidades de domínio iniciais

- `Project`: nome, finalidade, formato alvo, timestamps.
- `MediaAsset`: arquivo original, metadados FFprobe, checksum, tamanho, duração e caminho gerenciado.
- `EditSpec`: versão do plano estruturado e validado; trim, proporção, enquadramento, textos, volumes e mídia auxiliar.
- `RenderJob`: status (queued, running, completed, failed, cancelled), progresso/etapa, erros sanitizados, horários e referência ao projeto.
- `Export`: caminho, formato, duração, resolução, tamanho, versão, job e relatório de validação.
- `ProviderConfig` (fase 3): configuração e identidade do provedor; segredos fora do banco ou criptografados conforme arquitetura.

## 8. API inicial sugerida (a confirmar na implementação)

```text
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{project_id}
POST   /api/v1/projects/{project_id}/media
GET    /api/v1/projects/{project_id}/media
PUT    /api/v1/projects/{project_id}/edit-spec
POST   /api/v1/projects/{project_id}/previews
POST   /api/v1/projects/{project_id}/renders
GET    /api/v1/render-jobs/{job_id}
POST   /api/v1/render-jobs/{job_id}/cancel
GET    /api/v1/projects/{project_id}/exports
GET    /api/v1/exports/{export_id}/download
GET    /api/v1/system/capabilities
```

Observação: URLs são desenho inicial, não contratos implementados. Preferir status por polling no MVP; avaliar SSE/WebSockets se necessário.

## 9. UX e requisitos de produto

- Tela “Projetos”: criar, abrir, duplicar futuramente, visualizar status e exportações.
- Tela “Editor”: player à esquerda/centro, controles de corte e formato, configuração de texto e áudio, painel de exportação.
- Evitar aparência de editor complexo no MVP: timeline simples, ações identificáveis e prévia gerada pelo backend.
- Formatos prioritários: 9:16 (1080×1920), 1:1 (1080×1080), 4:5 (1080×1350) e 16:9 (1920×1080), com presets ajustáveis.
- Preferência para campanhas WhatsApp: CTA “Enviar mensagem” como preset opcional, não como texto compulsório em todo vídeo.
- Presets devem preservar áreas de segurança visual e legibilidade de títulos/legendas.
- Operações destrutivas exigem confirmação; nunca apagar o original automaticamente.

## 10. Qualidade e segurança

- Executar testes automatizados no backend/frontend das operações implementadas.
- Validar upload por assinatura de arquivo, extensão, limites configuráveis e FFprobe; nunca confiar só em MIME enviado pelo navegador.
- Garantir que uma falha/cancelamento deixe job consistente e preserve original.
- Para o heditor: respeitar `SKILL.md`, referências, `DESIGN.md`, preview, lint, test, render e QA; não declarar sucesso sem verificação.
- Testar sincronização áudio/vídeo, duração, resolução, reprodução do MP4 e textos fora das margens.
- Registrar versões/dependências e verificar direitos de música, fontes e ativos antes de redistribuir.
- Processos criativos que executem scripts devem ser isolados e receber limites de CPU, memória, duração, arquivos e permissões.
- Não enviar vídeos a fornecedores externos por padrão; exigir ação explícita e aviso de custo/privacidade.

## 11. Critérios de conclusão do MVP

1. A aplicação abre no ambiente local com frontend, API e banco saudáveis.
2. É possível criar um projeto e importar um MP4 válido.
3. Os metadados do vídeo são exibidos corretamente.
4. O usuário escolhe início/fim, formato, texto e ajustes simples de áudio.
5. O sistema gera uma prévia e um MP4 final que reproduz corretamente.
6. O status do job e os erros são apresentados de forma compreensível.
7. O arquivo original permanece intacto; exportações têm histórico.
8. Os testes essenciais passam; o processo é documentado no README.
9. Existe a camada preparada para heditor, com integração real planejada e sem simulação de capacidades indisponíveis.

## 12. Ordem sugerida de desenvolvimento

**R00:** especificação e ADRs (decisões arquiteturais)  
**R01:** ambiente, monorepo, health check e FFmpeg  
**R02:** CRUD de projetos + upload + metadados  
**R03:** player, cortes e presets de aspecto  
**R04:** textos/áudio e geração de preview  
**R05:** render, exportações, validação e histórico  
**R06:** testes de ponta a ponta, tratamento de erros e polimento  
**R07:** validação da skill heditor e desenho do executor isolado  
**R08:** edição com IA e revisão do plano  
**R09:** pesquisa e prova de conceito Higgsfield API/MCP

## 13. Referências oficiais

- heditor, repositório e documentação: https://github.com/pycodebr/heditor
- heditor, SKILL.md: https://github.com/pycodebr/heditor/blob/main/skills/heditor/SKILL.md
- Higgsfield API: https://open.higgsfield.ai/quick-start
- Higgsfield MCP: https://higgsfield.ai/creator-hub/help-center/integrations/what-is-higgsfield-mcp

## 14. Observações de decisão

- O nome do produto foi definido como **Edivo** e o nome planejado do repositório é **`edivo-editor`**. A disponibilidade de domínio, marca e nome no GitHub ainda precisa ser verificada antes do uso público.
- O heditor é externo, com versionamento e licenças de dependências que precisam ser respeitados. Evitar copiar/modificar fontes desnecessariamente; preferir integração desacoplada.
- Higgsfield API e MCP são caminhos distintos; disponibilidade de endpoints, permissões, créditos e condições comerciais devem ser verificadas no momento da implementação.
- Não há compromisso de SaaS no MVP: o alvo inicial é um produto local-first funcional.


## 15. Configuração na aba Projetos do ChatGPT

1. Crie um projeto chamado **Edivo**.
2. Copie o texto do arquivo `EDIVO_INSTRUCOES_CHATGPT.md` para as **Instruções do projeto**.
3. Adicione este documento, `EDIVO_DOCUMENTACAO_PROJETO.md`, como arquivo do projeto.
4. Na primeira conversa, peça: **"Vamos iniciar a R00 do Edivo: valide a arquitetura, liste decisões pendentes e prepare o plano incremental da fundação, sem implementar ainda."**
5. Depois da R00, avance somente uma etapa de cada vez e mantenha o documento atualizado quando houver mudanças de escopo ou decisões técnicas.

### Descrição curta (uso livre)

**Edivo** é um editor de vídeo web local-first, construído com Python/FastAPI e React/TypeScript, com FFmpeg para edição básica, integração planejada com a skill heditor para automações criativas e expansão futura para provedores de IA como Higgsfield.
