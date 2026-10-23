# Edivo — VIS-01: identidade visual

Estado: atualização visual concluída e validada. R06 não iniciada.

**Ajuste posterior autorizado:** a marca do cabeçalho agora usa transparência e
wordmark escuro no tema claro, sem caixa de fundo. O [relatório deste ajuste](brand-followup.md)
registra a implementação atual e o QA adicional. As capturas e os resultados
abaixo conservam a execução inicial da VIS-01.

O [ajuste posterior do favicon](favicon-followup.md) remove o tile preto da aba,
mantendo somente o símbolo verde transparente; possui evidências próprias.

## Auditoria anterior às alterações

Lidos AGENTS, README, DESIGN, arquitetura, roadmap e aceite/relatório/QA R05.
Logo e ícone oficiais foram inspecionados visualmente. HTML de referência lido
como texto/CSS; seus scripts, recursos remotos, marcas e textos não serão usados.

Entrada React: `frontend/src/main.tsx`; layout `app/app.tsx`; tokens/Tailwind e
componentes globais em `app/styles.css`; player/timeline/painéis em
`features/editor/editor.css`; exportação/histórico em `features/editor/export.css`.
Temas: `app/use-theme.ts` e bootstrap em `frontend/index.html`, persistência
`edivo-theme`. Fontes locais DM Sans/Manrope serão mantidas. DejaVu e as cores
dos overlays da composição não serão alteradas, preservando a referência FFmpeg.

## Plano visual

1. Derivar WebP/PNG da logo oficial, recortando apenas margem preta excedente,
   mantendo geometria, gradiente, wordmark e área de proteção. Criar ícones
   raster reais em16/32/48/180/192/512px e ICO; remover apenas os cantos externos
   claros do ícone por transparência. Preservar os dois JPEG de origem.
2. Substituir filhos visuais do link institucional pela logo, mantendo href e
   nome acessível. Ajustar favicon, metadados e título oficiais no HTML.
3. Atualizar a fonte única de tokens para fundos #0B0F14/#11161D/#171D25,
   verde #35E66B e tema claro correspondente; medir contraste e registrar ajustes.
4. Harmonizar botões/formulários/cards/modais/estados e os três CSS existentes,
   com hierarquia legível, foco visível, alvos úteis e reduced motion.
5. Tema inicial dark-first, escolha light/dark persistida sempre prioritária;
   sincronizar hook e bootstrap para evitar flash. Não alterar lógica de edição.
6. Capturar antes/depois e validar Chromium em desktop/tablet/375/390px, ambos
   temas, com APIs reais somente leitura e cenários isolados de erro/loading.
7. Rodar Vitest/ESLint/Prettier/TypeScript/Vite, reter falhas e resultados reais.

## Contratos preservados

Rotas hash, CRUD/importações, IDs e paginação, validações, dirty state,
salvamento explícito/conflitos, eventos nativos de player/timeline, áudio/textos,
polling/cancelamento/revisões/prévias/renders/download e histórico não mudam.
Não tocar backend, banco, migrations, worker, FFmpeg, storage, Compose, portas,
ObraControl ou Git. Nenhum projeto de QA será excluído, renomeado ou ocultado.
O baseline de hashes está em `evidence/baseline.json`; relatórios R01–R05 intactos.

## A. Auditoria e escopo entregue

A auditoria e o plano acima foram registrados antes das alterações. As fontes
oficiais foram examinadas, inclusive a arte raster e o CSS do HTML de referência.
O HTML não foi executado nem importado para o aplicativo. A interface mantém o
layout funcional da R05; a atualização centraliza cores, superfícies, tipografia,
foco, raios e espaçamentos nos estilos compartilhados que já existiam.

Arquivos de produção modificados:

- `frontend/index.html`: título, metadados, favicons e bootstrap do tema.
- `frontend/src/app/app.tsx`: filhos visuais do link institucional substituídos
  pela logo oficial; link, nome acessível e handlers preservados.
- `frontend/src/app/use-theme.ts`: preferência persistida continua prioritária;
  primeira visita e fallback passam a escuros, conforme a diretriz dark-first.
- `frontend/src/app/styles.css`: tokens, shell, componentes globais, biblioteca,
  formulários, modais, feedback e responsividade.
- `frontend/src/features/editor/editor.css`: apresentação do player, timeline,
  controles, textos, áudio e prévias.
- `frontend/src/features/editor/export.css`: exportação, estados e histórico.
- `frontend/tests/app.test.tsx`: regressão da marca e dos temas; testes anteriores
  preservados, com três casos adicionais.

Adicionados dez assets públicos e `frontend/scripts/brand-assets.mjs`, além deste
relatório, `qa-results.json`, helpers de QA e evidências próprias em
`docs/design-refresh/`. O apêndice 11 de `DESIGN.md` documenta somente as decisões
justificadas pela aplicação e pelo contraste medido. Não há novas dependências.
Arquivos preexistentes não relacionados, inclusive os arquivos de retomada e o
ZIP fornecido, foram preservados.

## B. Marca e metadados

A imagem oficial mantém símbolo, gradiente, wordmark e ponto verde. O recorte
remove apenas margem escura excedente e conserva área de proteção. O cabeçalho
usa `<picture>` com WebP e fallback PNG, sem reconstruir letras com uma fonte.
A arte fica sobre um badge escuro nos dois temas e mantém sua proporção.

| Asset em `frontend/public/`                                   | Dimensão/uso                            |
| ------------------------------------------------------------- | --------------------------------------- |
| `brand/edivo-logo.webp`                                       | 960 × 326; 11.096 bytes; qualidade 0,92 |
| `brand/edivo-logo.png`                                        | 960 × 326; fallback raster              |
| `brand/edivo-icon.png`                                        | 512 × 512; símbolo isolado              |
| `favicon-16x16.png`, `favicon-32x32.png`, `favicon-48x48.png` | Tamanhos efetivos                       |
| `favicon.ico`                                                 | Três frames: 16, 32 e 48 px             |
| `apple-touch-icon.png`                                        | 180 × 180                               |
| `icon-192.png`, `icon-512.png`                                | 192 × 192 e 512 × 512                   |

Somente os pixels externos claros conectados aos cantos do ícone foram tratados
com transparência. O símbolo verde foi preservado antes da redução; a conversão
é raster, sem alegação de fidelidade vetorial. Os JPEG oficiais permanecem
inalterados. O manifesto registra receitas, tamanhos e SHA-256:
[manifesto](evidence/asset-manifest.json),
[validação de assets](evidence/asset-validation.json) e
[prancha inspecionada](evidence/assets-contact-sheet.png).

O título é **Edivo — Editor de Vídeos com IA**. Isso identifica o produto, sem
introduzir recursos de IA. Os links reais de favicon/Apple usam `?v=vis01`; o QA
consultou os arquivos sem cache e validou dimensões, ICO e HTTP 200. O build
contém os mesmos bytes dos assets públicos. Não foi feita inspeção manual da
barra de abas ou da instalação de atalhos em um dispositivo físico.

## C. Design system

Tokens no sistema CSS/Tailwind existente, com um único mapeamento semântico:

| Papel              | Escuro    | Claro     |
| ------------------ | --------- | --------- |
| Fundo              | `#0B0F14` | `#F7F9FC` |
| Superfície         | `#11161D` | `#FFFFFF` |
| Superfície elevada | `#171D25` | `#EEF2F6` |
| Texto principal    | `#F5F7FA` | `#0F1720` |
| Texto secundário   | `#A9B3BF` | `#4B5A69` |
| Texto auxiliar     | `#7B8794` | `#5B6875` |
| Ação primária      | `#35E66B` | `#24C85A` |
| Texto sobre ação   | `#07130B` | `#07130B` |
| Borda de controle  | `#657485` | `#778492` |

A cor auxiliar clara inicial `#73808C` foi escurecida porque apresentava apenas
4,04:1 sobre branco. O verde de texto claro usa `#126C32`; verde luminoso continua
nas ações. Feedback possui tokens próprios, texto/ícones e contraste específico
por tema. A marca não se torna a única indicação de sucesso.

DM Sans e Manrope locais foram mantidas, sem Google Fonts ou downloads no
runtime. Corpo 14 px, rótulos funcionais de pelo menos 12 px, títulos com
hierarquia consistente e números tabulares em tempos/progresso/metadados.
Raios 6/10/14/18 px, pill em ações apropriadas, escala de espaçamento de 4 px e
sombras discretas. Botões, inputs, selects, textareas, sliders, cards, listas,
modais, alertas e progressos reutilizam os tokens.

O fundo interno `#101410`, DejaVu e as propriedades dos textos da composição
foram preservados para não mudar a referência visual do plano salvo/FFmpeg.
O verde da UI não recolore textos que pertencem à composição do vídeo.

## D. Interface preservada e harmonizada

- Projetos/biblioteca: cabeçalho, lista, metadados, indicadores, ações, upload,
  paginação, formulários e modais com superfícies e controles consistentes.
- Editor: player escuro central, controles contrastados e timeline com região
  selecionada, playhead e marcadores visíveis.
- Textos/áudio: painéis, campos, selects, volumes, MP3, loop e fades com a mesma
  hierarquia; regras de sincronização e tempos não foram alteradas.
- Prévias: estados e resultado real continuam ligados à revisão persistida;
  avisos de revisão anterior permanecem visíveis.
- Exportação/histórico: perfis, progresso, cancelamento, erro, download e histórico
  usam estados reais existentes. Nenhum percentual ou job foi simulado no produto.

Rotas hash, clientes HTTP, contratos e handlers de CRUD, upload, salvamento,
conflito, edição e jobs não foram modificados. Não há chat, prompt, botão fictício
de IA nem integração heditor. Os projetos de QA permanecem listados, sem filtros,
exclusões ou renomeações introduzidos por esta entrega.

## E. Responsividade, acessibilidade e inspeção visual

Chromium real **145.0.7632.6**, por Playwright driver **1.50.1** disponível no
ambiente; imagem de QA existente `mcr.microsoft.com/playwright:v1.58.2-noble`.
Os números do driver e do navegador são informados separadamente. O Chromium
local não possuía `libnspr4.so`; usou-se a imagem de QA sem instalar dependências.

| Viewport emulada   | Temas        | Resultado                                         |
| ------------------ | ------------ | ------------------------------------------------- |
| Desktop 1366 × 900 | Escuro/claro | Sem overflow horizontal                           |
| Tablet 768 × 1024  | Escuro/claro | Painéis reorganizados, sem overflow               |
| Mobile 375 × 812   | Escuro/claro | Biblioteca/editor funcionais, sem overflow        |
| Mobile 390 × 844   | Escuro/claro | Timeline/painéis/histórico legíveis, sem overflow |

Foram geradas 48 capturas após a atualização e 42 na segunda passagem anterior.
São evidências de execução, não 90 testes adicionais. Imagens representativas
foram efetivamente abertas e inspecionadas, incluindo projetos, biblioteca,
editor, timeline, textos/áudio, prévias, exportação/histórico e modal.

Comparação de entrada:
[antes](evidence/browser-before-2/desktop-dark-projects.png) e
[depois](evidence/browser-after-1/desktop-dark-projects.png).
Exemplos: [timeline clara mobile](evidence/browser-after-1/mobile-390-light-timeline.png),
[biblioteca escura mobile](evidence/browser-after-1/mobile-375-dark-library.png),
[textos/áudio claros](evidence/browser-after-1/desktop-light-text-audio.png) e
[editor completo](evidence/browser-after-1/desktop-dark-editor.png).

Foco por teclado no modal e nos marcadores, preferência de tema persistida,
primeira visita escura mesmo com SO claro e reduced motion foram verificados.
Logo tem `alt`; link e botões mantêm seus nomes acessíveis. Alvos de controles
globais chegam a 44 px; os handles existentes da timeline mantêm sua área e
alternativas de teclado/campos numéricos.

Os 24 pares de tokens avaliados passam nos limiares usados: texto normal 4,5:1
e borda funcional 3:1. Menores valores medidos: texto 4,54967:1 e borda 3,39403:1.
Também passaram 16 amostras de contraste computado no navegador. Isso não é uma
certificação WCAG integral: vídeo, textos sobre conteúdo dinâmico, todos os
estados disabled/hover e tecnologias assistivas não foram auditados integralmente.

### Limitação de captura investigada

Alguns PNGs de seção obtidos por `locator.screenshot` mostram o link “Pular para o
conteúdo” deslocado sobre o painel. As imagens foram preservadas. Um diagnóstico
isolado reproduziu o mesmo efeito usando o CSS real do link: ele estava sem foco,
com borda inferior em **−11,195 px**, totalmente fora da viewport; o screenshot
normal da viewport não mostrava o link. Ao receber foco, apareceu corretamente
em `top:12px`. Portanto, o recorte não representa uma sobreposição vista na
viewport normal. Nenhuma regra de CSS foi alterada para mascarar a evidência.
Ver [diagnóstico e métricas](evidence/capture-diagnostic/report.json),
[recorte com artefato](evidence/capture-diagnostic/locator-section.png),
[viewport normal](evidence/capture-diagnostic/viewport-after-locator.png) e
[foco visível](evidence/capture-diagnostic/focus-visible.png).

Algumas capturas pausadas também mostram o spinner nativo do vídeo. A aprovação
de reprodução se baseia em avanço real de `currentTime`, frames decodificados,
ausência de MediaError e respostas Range 206, não no frame estático do screenshot.
Uma captura simulada de erro ocorreu antes de a logo terminar de decodificar;
a carga da logo foi validada separadamente nos oito cenários reais de viewport/tema.

## F. Testes e resultados reais

Comandos executados em `frontend/`, exceto quando indicado:

| Comando                                                                                    | Resultado                                             | Evidência                                                               |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ----------------------------------------------------------------------- |
| `npm run test -- --run tests/app.test.tsx`                                                 | 12 testes; subset dos 316                             | `evidence/theme-brand-unit-attempt-1.log`                               |
| `npm run test -- --run`                                                                    | **316 testes, 9 arquivos**, 22,18 s                   | `evidence/vitest-attempt-1.log`                                         |
| `npm run lint`                                                                             | ESLint, exit 0                                        | `evidence/eslint-attempt-1.log`                                         |
| `npm run format:check`                                                                     | Prettier, exit 0                                      | `evidence/prettier-attempt-1.log`                                       |
| `npm run build`                                                                            | TypeScript + Vite, exit 0                             | `evidence/build-attempt-1.log`                                          |
| `node --check scripts/brand-assets.mjs`                                                    | Exit 0                                                | `evidence/asset-generator-node-check.log`                               |
| `node frontend/scripts/brand-assets.mjs --docker` (raiz)                                   | Geração/rerun idêntico; 14 verificações               | `evidence/asset-validation.json`                                        |
| `docker compose build frontend`                                                            | Somente imagem frontend, exit 0                       | `evidence/frontend-build-image.log`                                     |
| `docker compose up -d --no-deps frontend`                                                  | Somente frontend recriado, exit 0                     | `evidence/frontend-deploy.log`                                          |
| `node browser-qa.mjs --stage before --output evidence/browser-before-2` no container de QA | 47 verificações; 42 capturas                          | `evidence/browser-before-2/report.json`                                 |
| `node browser-qa.mjs --stage after --output evidence/browser-after-1` no container de QA   | **98 verificações**, 48 capturas                      | `evidence/browser-after-1/report.json`                                  |
| `node capture-diagnostic.mjs` no container isolado sem rede                                | Efeito de recorte reproduzido; foco/viewport corretos | `evidence/capture-diagnostic/report.json`                               |
| `python3 docs/design-refresh/preservation.py --stage before/after …`                       | Comparação somente leitura aprovada                   | `evidence/preservation-before.json`, `evidence/preservation-after.json` |
| Auditoria de escopo e build estático                                                       | 12 verificações de escopo e 19 de build aprovadas     | `evidence/final-scope-audit.json`, `evidence/build-integrity.json`      |

Host: Node **24.14.0**, npm **11.9.0**. Vitest **5.0.5**, Vite **8.3.4**, TypeScript
**6.0.3**, ESLint **10.13.0**, Prettier **3.9.10**; dependências/locks preservados.
CSS build 51,78 kB, JS 359,53 kB; fontes continuam locais. Os checks de assets,
contraste, navegador e preservação são registrados separadamente, sem inflar o
total de testes unitários. As duas passagens anteriores e o subset focado não
são somados aos 316 testes distintos.

O navegador usa API real somente GET/HEAD e bloqueia mutações/recursos externos.
Projeto existente `3f4bc7e0-3b4e-4db6-a812-18b581a398e3`, revisão atual 3; prévia
`2bd24155-09d5-45c7-aa99-1d9952142be5` e render
`0eb40bfc-88ea-4dbf-a9b6-0865234d533d` foram consultados e reproduzidos.
Fonte de 16 s, export de 12 s e prévia de 4 s passaram em play/seek/pause reais,
com Range 206. Playhead não deixou a edição dirty. Zero exceções não tratadas,
tentativas de mutação ou recursos externos inesperados durante essa execução.

Loading, vazio e erro são seis cenários separados com respostas GET simuladas,
identificados no relatório; não são prova de outage real. Não foram criados
projetos, mídias, revisões ou jobs nesta validação. Testes backend, novos renders,
crash/restart e migrations não foram reexecutados na VIS-01: não houve mudança
nessa camada e o escopo proíbe usar a atualização visual para alterá-la.

### Tentativas negativas retidas

- Primeira tentativa do gerador com Chromium completo excedeu 60 s. Nenhum asset
  ou original foi modificado; o resultado negativo consta em
  `evidence/asset-generation-attempt-1.json`. A receita final usa o headless shell
  disponível e concluiu, com rerun idêntico e logs retidos.
- Um `node --check` foi chamado com prefixo `frontend/` quando o cwd já era esse
  diretório: `MODULE_NOT_FOUND`, corrigido para `scripts/brand-assets.mjs`.
  `evidence/qa-command-path-negative.json` registra o contexto. Não foi falha do
  build nem do produto.
- Ajustes de formatação de evidência JSON e lint do helper Python foram registrados
  e corrigidos. O helper passou Ruff check/format/compilação. Não houve falha de
  Vitest nesta entrega; a ocorrência histórica da R04 não foi apagada nem negada.
- Os recortes com artefato e capturas iniciais menos estáveis foram conservados,
  com a distinção entre antes-1, antes-2 e depois-1 e o diagnóstico acima.

Logs originais `.log` ficam no workspace; o `.gitignore` preexistente os ignora.
O arquivo `evidence/execution-logs.json` também conserva seus conteúdos e hashes
em formato versionável, sem modificar as regras Git do usuário.

## G. Preservação e critérios de aceite

A comparação de preservação, concluída em **2026-10-23 13:35 UTC**, aprovou hashes
por registro, artefatos, modos/permissões, trigger e revisão Alembic. A auditoria
de arquivos posterior conferiu 1.337 arquivos protegidos, com única exceção
autorizada do apêndice de `DESIGN.md`; 1.218 documentos/evidências históricos
permanecem iguais. Nenhum Git write, commit, branch ou push foi executado.

| Dado preservado                 | Quantidade |
| ------------------------------- | ---------: |
| Projetos, incluindo QA          |        196 |
| MediaAsset / originais MP4      |  197 / 197 |
| AudioAsset / originais MP3      |    10 / 10 |
| Revisões EditSpec               |        174 |
| PreviewJob / arquivos de prévia |     97 / 8 |
| RenderJob / arquivos finais     |    92 / 14 |

Nenhum registro ou artefato foi adicionado, removido ou alterado. Permissões dos
originais, checksums, revisões e snapshots foram preservados. `.env`, locks,
backend, migrations, worker, compositor, proxy e Compose permanecem iguais.
Somente o container frontend foi recriado para receber os arquivos visuais.
API/db/worker/migrate e os cinco containers ObraControl comparados não foram
reiniciados/alterados durante a entrega. Portas e mounts foram preservados.

Na retomada final, os containers Edivo foram encontrados parados, depois da prova
de preservação e do QA real. A VIS-01 não os parou; nenhum foi iniciado nessa
retomada. O diagnóstico final rodou sem rede nem serviços da aplicação.
O estado operacional atual não deve ser confundido com o runtime ativo usado
nas evidências anteriores. URLs/configurações continuam 127.0.0.1:5174 e
127.0.0.1:8001; esta entrega não promete serviços atualmente em execução.

- [x] Documentos, imagens e estilos de referência examinados; auditoria/plano prévios.
- [x] Logo oficial proporcional, legível e sobre superfície escura nos dois temas.
- [x] Favicon 16/32/48, ICO, Apple180 e ícones192/512 gerados e servidos no QA.
- [x] Título/metadados corretos; assets também íntegros no build.
- [x] Tokens únicos, paleta, fontes locais e hierarquia consistentes.
- [x] Componentes globais, projetos, biblioteca e modais harmonizados.
- [x] Editor, player, timeline, textos, áudio e prévias harmonizados.
- [x] Exportação, estados reais, download e histórico preservados visualmente.
- [x] Desktop/tablet/mobile375/390 e ambos os temas verificados.
- [x] Contraste amostrado, foco, teclado, nomes acessíveis e reduced motion verificados.
- [x] Vitest/ESLint/Prettier/TypeScript/Vite e QA de navegador aprovados.
- [x] Dados, storage, documentos R01–R05 e ObraControl preservados.
- [x] Ausência de dependências remotas inesperadas e de recursos IA/R06.
- [x] Relatório, resultados reais, tentativas negativas e capturas próprios entregues.

## H. Continuidade e limitações

A atualização visual está concluída. O frontend pode seguir para a R06 mediante
nova autorização. **A R06 não foi iniciada.** A prontidão aqui é da camada visual;
não substitui o futuro trabalho de robustez nem uma nova certificação completa
de backend/renderização.

Limites restantes: Chromium foi o único navegador testado; mobile/tablet foram
emulados, sem dispositivo físico ou leitor de tela. Não houve escuta humana,
certificação WCAG completa, inspeção manual do favicon na barra do navegador,
novos uploads/salvamentos/renders ou nova validação de restart nesta etapa.
Os artefatos de captura documentados não foram escondidos. Não há pendência
essencial de implementação visual identificada; os serviços parados precisarão
ser iniciados pelo fluxo operacional habitual para uso local.
