# DESIGN.md — Edivo | Identidade visual e sistema de interface

**Estado:** diretrizes aprovadas para atualização visual entre R05 e R06.  
**Escopo:** frontend, identidade e arquivos públicos; **não** autoriza alterações nas regras de negócio nem o início da R06.  
**Produto:** Edivo, editor visual de vídeos orientado futuramente por prompts de IA, com edição manual preservada.  
**Origem:** logo, ícone e HTML de referência fornecidos pelo responsável do projeto.

## 1. Fontes de verdade e hierarquia

Arquivos de entrada, relativos à raiz do repositório:

1. `design/source/logo-edivo.jpg` — **marca oficial**: símbolo geométrico verde + `edivo.` branco com ponto verde, sobre fundo escuro.
2. `design/source/icone-edivo.jpg` — **símbolo oficial** isolado sobre fundo escuro com cantos arredondados; base do favicon.
3. `design/references/design-system-dark.html` — **referência de estilo**, não código pronto nem identidade da marca.
4. `DESIGN.md` — regras para adaptação ao frontend Edivo.
5. Código local React/CSS/Tailwind e design tokens existentes — verificar antes de alterar, sem presumir que a última versão R05 está publicada remotamente.

**Ordem de precedência:** identidade das imagens oficiais > decisões deste documento > princípios visuais relevantes do HTML > convenções atuais que não conflitem. Se faltar um arquivo ou houver incompatibilidade importante, documentar e solicitar orientação antes de alterações destrutivas.

### O que o HTML realmente contém

O arquivo foi salvo de uma página **“Design System Inspired by Spotify”** do `getdesign.md`. Inclui estilos de fundo `#0a0a0a`, superfícies `#111111`, `#1f1f1f` e `#252525`, verde de referência `#1ed760`, texto branco/cinza, fonte Inter, botões pill, cartões e estados de formulário. Isso **não** define as cores exatas da marca Edivo e **não** autoriza copiar nome, logotipos, textos, layout proprietário, scripts ou links de terceiros.

Aproveitar somente os princípios: tema dark-first, hierarquia sóbria, contraste, controles compactos, superfícies graduadas, focos perceptíveis e destaque verde. **Não executar scripts do HTML nem incorporar scripts, CSS remoto, analytics ou tracking.** O HTML pode ter recursos relativos ausentes (pasta `design-system-dark_files`); sua leitura de CSS e estrutura HTML deve funcionar sem esses recursos.

## 2. Personalidade visual

O Edivo transmite **criatividade, precisão, velocidade, automação inteligente e controle profissional**. A interface prioriza conteúdo de vídeo e fluxo de trabalho, não elementos decorativos. Estilo premium, tecnológico e minimalista; **dark-first**, preservando tema claro acessível.

## 3. Marca e aplicações

### Logo principal

- Fonte visual oficial: `design/source/logo-edivo.jpg` (2048 × 1143 px, JPEG).
- Usar símbolo + wordmark `edivo.` no cabeçalho, telas institucionais e quando houver largura suficiente.
- Preservar geometria, proporções, gradiente verde, pontuação e contraste da arte.
- Nunca esticar, recriar aproximadamente com fontes comuns, recolorir arbitrariamente ou remover o ponto verde.
- Como o wordmark original é branco em fundo preto, **não colocar diretamente sobre fundo claro** sem variante aprovada: exibir em superfície escura própria ou criar versão adequada somente após validação visual.
- Prever alternativa `alt="Edivo"` para `<img>` quando transmitir informação; usar imagem decorativa com `alt=""` quando o nome acessível já estiver próximo.

### Símbolo / favicon

- Fonte oficial: `design/source/icone-edivo.jpg` (2048 × 2048 px, JPEG).
- Usar o símbolo isolado em favicon, favoritos, atalhos e eventualmente navegação compacta.
- Criar **assets derivados reais** em `frontend/public/brand/` e `frontend/public/` somente conforme demanda do aplicativo.
- Candidatos: `brand/edivo-logo.png` ou `.webp`, `brand/edivo-icon.png`, `favicon.ico`, `favicon-32x32.png`, `apple-touch-icon.png` (180×180), `icon-192.png`, `icon-512.png`.
- Produzir favicon 16, 32 e 48 px a partir do ícone fornecido, com nitidez e antialiasing apropriados; inspecionar os resultados nos tamanhos efetivos.
- A imagem tem cantos escuros arredondados e área clara externa do JPEG. Preservar o desenho e tratar a área externa com transparência quando aplicável, **sem apagar partes do símbolo**. Não afirmar que um SVG reconstruído manualmente corresponde perfeitamente à marca sem revisão.
- Preferir manter fontes JPG no diretório `design/source/`, não servir originais de referência como arquivos públicos.

## 4. Paleta de cores e tokens

### Marca

- Verde funcional inicialmente aprovado: `#35E66B` (`--edivo-brand`). **Token de interface proposto, não amostra exata da imagem**; a logo possui variações/gradiente. Inspecionar a relação com o logotipo e ajustar somente se necessário e documentado.
- Verde hover: `#29D85C` (`--edivo-brand-hover`).
- Texto sobre botões verdes: `#07130B` — testar contraste real.
- Verde suave para superfície/foco: `rgba(53,230,107,0.14)`.
- Branco institucional no tema escuro: `#F5F7FA`.

### Tema escuro (principal)

| Papel | Valor inicial |
| --- | --- |
| Fundo da aplicação | `#0B0F14` |
| Superfície dos painéis | `#11161D` |
| Superfície elevada | `#171D25` |
| Superfície do player | `#090B0E` |
| Borda discreta | `#242C36` |
| Borda enfatizada | `#313A46` |
| Texto principal | `#F5F7FA` |
| Texto secundário | `#A9B3BF` |
| Texto auxiliar | `#7B8794` |

### Tema claro (suportado)

| Papel | Valor inicial |
| --- | --- |
| Fundo da aplicação | `#F7F9FC` |
| Superfície dos painéis | `#FFFFFF` |
| Superfície elevada | `#EEF2F6` |
| Borda discreta | `#DCE3EA` |
| Borda enfatizada | `#C5D0DB` |
| Texto principal | `#0F1720` |
| Texto secundário | `#4B5A69` |
| Texto auxiliar | `#73808C` |
| Verde de ação sobre claro | `#24C85A` (revisar contraste de texto/foco) |

### Status semânticos

- Sucesso `#22C55E`; atenção `#F59E0B`; falha `#EF4444`; informação `#3B82F6`.
- Usar ícone/texto **além da cor** para sinalizar estados de jobs, erro e sucesso.
- Estados de renderização (`queued`, `running`, `validating`, `completed`, `failed`, `canceled`) devem ser legíveis e semanticamente distintos em ambos os temas.
- Os valores são tokens iniciais; validar contraste WCAG AA, inclusive texto pequeno, disabled, hover, focus e UI densa.

### Implementação de tokens

Concentrar cores, raios, espaçamento e tipografia nos tokens CSS/Tailwind já utilizados. Preferir mapear valores semânticos existentes (`--background`, `--surface`, `--primary`, `--foreground`, `--border`, etc.) a criar uma segunda fonte de verdade. Evitar hexadecimais repetidos em componentes.

## 5. Tipografia

- Direção visual de referência: **Inter** para controles, listas e dados; títulos com peso 600–700.
- O projeto pode já utilizar **DM Sans e Manrope**. **Auditar fontes atuais primeiro**; reutilizar as disponíveis se atingirem a hierarquia visual, evitando dependências e downloads remotos desnecessários.
- Caso se adote Inter, instalar por mecanismo local/reprodutível com licença adequada e locks atualizados conscientemente; **não depender de Google Fonts no runtime**.
- Corpo de interface: 14 px (alvos de leitura confortáveis); títulos de painel 16–24 px; rótulos/captions 12 px ou mais quando funcionalmente importantes.
- Usar números tabulares para tempo, duração, fps, progresso e tamanhos.
- Não utilizar a tipografia da marca como motivo para redesenhar o wordmark da imagem.

## 6. Formas, espaçamento e componentes

- Escala de espaçamento: 4, 8, 12, 16, 20, 24 e 32 px.
- Raios: 6 px pequenos controles, 10 px campos e botões, 14 px painéis, 18 px modais; botões pill apenas quando apropriados.
- Sombras discretas; hierarquia definida sobretudo por fundo, borda e espaçamento.
- Botão primário: verde Edivo, alto contraste, hover e foco discerníveis.
- Secundário: superfície neutra, borda perceptível, ação clara.
- Ghost: ferramenta compacta com hover/foco; Danger: semântica de exclusão/cancelamento.
- Formulários: labels visíveis, estados de foco/erro, mensagens úteis; evitar apenas placeholder como label.
- Listagens: densidade equilibrada, ações clicáveis, estados vazios e paginação consistente.
- Ícones: reutilizar `lucide-react` existente, com `aria-label` em ações apenas com ícone.

## 7. Regras específicas por tela

### Projetos e biblioteca

- Priorizar leitura de nome, finalidade, data, formato e ações.
- Harmonizar cards/listas com superfícies e espaçamento do tema.
- Evitar que estados de QA fiquem visualmente indistinguíveis de ações perigosas; **não implementar filtro/categoria QA sem autorização funcional separada**.

### Editor de vídeo

- Player é o foco principal; fundo escuro estável para conteúdo independentemente do tema.
- Timeline legível, marcadores e seleção com contraste; não reduzir área de interação de playhead/handles.
- Inspector lateral para origem, enquadramento, textos, áudio, prévias e exportações: agrupar por finalidade, preservar scroll e dimensões úteis.
- Progresso e estados dos jobs devem corresponder aos dados reais vindos da API, nunca ser animação/percentual inventado.
- Controles complexos mantêm feedback de foco, atalhos, seleções e acessibilidade por teclado.

### Exportações e histórico

- Ação `Exportar` evidente, mas não mais chamativa que o conteúdo.
- Perfis, duração, formato e revisão visíveis.
- Estados de cancelamento e conclusão distinguíveis por texto/ícones, não apenas cor.
- Preservar seleção, polling, downloads privados, tratamento de erro e confirmação das ações.

### Futuro assistente de IA (R08)

- Reservar coerência de design para área de prompt estilo console criativo profissional.
- Área deverá suportar instrução, plano revisável, execução, status, logs resumidos e acompanhamento de prévias.
- **Não criar chat, botão IA, executor, placeholder funcional ou integração heditor agora**: essa etapa é só identidade visual entre R05 e R06.

## 8. Responsividade e acessibilidade

- Desktop é o ambiente primário de edição; não comprometer espaço do player ou timeline.
- Em 375/390 px: empilhamento útil, sem scroll horizontal involuntário; ações primárias acessíveis; modais e formulários utilizáveis.
- Não criar controles de interação menores que o necessário para toque; preservar foco visível e navegação por teclado.
- Respeitar `prefers-reduced-motion` para transições decorativas.
- Respeitar preferências persistidas de tema; evitar flash incorreto e regressão de contraste.
- Validar estados empty/loading/error, overflow, palavras longas e conteúdo PT-BR.

## 9. Aplicação técnica sem mudanças funcionais

Mudanças autorizadas apenas em:

- assets oficiais e derivados de imagem;
- cabeçalho, favicon, metadados institucionais HTML;
- estilos CSS/Tailwind/tokens e layout visual;
- componentes React somente quando necessário para aplicar o visual sem alterar semântica.

Não modificar:

- API, modelos, schemas, banco, migrações, worker, FFmpeg, contratos EditSpec;
- comportamento de player/timeline, jobs de prévia/render, downloads e histórico;
- arquivos originais, storage de mídia, Docker Compose/ObraControl;
- branches, histórico ou publicação Git.

Não importar HTML de referência para `frontend/src` nem copiar suas chamadas remotas. Não alterar as logos fornecidas no diretório `design/source/`.

## 10. Verificação de qualidade e entregáveis

Antes das alterações, analisar estrutura real da R05. Depois validar:

1. Logo nítida e proporcional no cabeçalho, inclusive em tema claro.
2. Favicon aparece corretamente (16/32 px), inclusive após atualizar a página/limpar cache em cenário de teste.
3. Tema escuro visualmente coerente; claro íntegro.
4. Layout e controles existentes no editor, biblioteca, textos, áudio, prévias, renders e histórico.
5. Responsividade 375/390 px e desktop.
6. Teclado, foco, contraste, `alt` e reduced motion.
7. ESLint, Prettier, TypeScript, Vite, Vitest e regressões proporcionais; verificar endpoints sem mudanças de contrato.
8. Nenhum recurso remoto/scripteamento estranho foi introduzido pela referência HTML.
9. Registro de arquivos adicionados/modificados e capturas de antes/depois em diretório próprio, sem mexer em evidências R01–R05.

O trabalho visual deve terminar antes de iniciar a R06. Relatar resultados reais e limitações; não declarar testes que não foram executados.


## 11. Decisões da aplicação VIS-01

A implementação reutiliza os três estilos existentes e o mesmo mapeamento
Tailwind, sem segundo sistema de tokens. DM Sans e Manrope continuam locais;
DejaVu, tamanhos, posições e cores dos textos da composição permanecem intactos
para preservar a referência visual do EditSpec/FFmpeg. Não há novas dependências
nem carregamento de fontes, CSS, scripts ou rastreadores remotos.

- Primeira visita utiliza tema escuro, inclusive com sistema operacional claro.
  Uma escolha `edivo-theme` salva, clara ou escura, sempre tem prioridade. Hook e
  bootstrap HTML possuem o mesmo fallback escuro; alternância permanece funcional
  quando o armazenamento de preferências está bloqueado.
- Paleta principal aplicada conforme as tabelas anteriores. O verde de marca
  `--edivo-brand` permanece `#35E66B`; ação clara `#24C85A`, com texto `#07130B`.
- O auxiliar claro inicial `#73808C` apresentou contraste 4,04:1 sobre branco,
  abaixo de 4,5:1 para texto normal. A implementação utiliza `#5B6875` (5,70:1).
  Links/destaques textuais claros usam `#126C32`, em vez de verde luminoso sobre
  branco. Isso preserva a marca e diferencia cor de ação de cor de texto.
- Feedback em tema claro usa sucesso `#15803D`, atenção `#92400E`, erro `#B42318`
  e informação `#1D4ED8`. No escuro, erro textual `#F87171` e informação `#60A5FA`
  melhoram leitura sobre suas superfícies semânticas. Status mantêm texto/ícone.
- Bordas decorativas conservam os neutros discretos; campos interativos usam
  `--control-border` `#657485` no escuro / `#778492` no claro. Os pares avaliados
  superam 3:1. Foco visível e reduced motion foram mantidos/centralizados.
- Corpo de interface 14 px e captions funcionais de pelo menos 12 px; números
  tabulares em tempos, progresso e dados. Raios 6/10/14/18 px e escala de 4 px,
  com pill nas ações apropriadas. O player externo usa `#090B0E`; o fundo interno
  `#101410` da composição é preservado para equivalência com contain/FFmpeg.
- A logo oficial é uma imagem, não fonte reconstruída. Derivados PNG/WebP de
  960×326 preservam símbolo, wordmark, ponto, gradiente e área de proteção; apenas
  margem preta excedente foi removida. WebP usa qualidade 0,92 e possui 11.096
  bytes; PNG permanece como fallback. Ambos são raster, sem promessa vetorial.
- Ícones 16/32/48, ICO com três frames, Apple180 e192/512 derivam do JPEG oficial.
  Somente regiões externas claras conectadas aos cantos foram tratadas com
  transparência; pixels verdes originais foram preservados antes da redução.
  Fontes, parâmetros e hashes estão no manifesto próprio da VIS-01.

As medições de pares estão em
`docs/design-refresh/evidence/token-contrast.json`. Não equivalem a uma
certificação WCAG integral. Evidências de navegador e aceite da atualização
ficam em `docs/design-refresh/acceptance.md`, sem substituir o histórico R01–R05.
A VIS-01 não inicia R06 nem implementa recursos de IA.

## 12. Ajuste autorizado: marca sem caixa e variante clara

Após revisar os dois temas, o usuário solicitou explicitamente retirar o
preenchimento atrás da logo e adaptar a marca ao tema light. Essa decisão
substitui a aplicação anterior sobre badge escuro: o cabeçalho passa a usar
logo com fundo transparente, sem caixa nem borda decorativa.

- Tema escuro: símbolo e ponto verdes, wordmark branco.
- Tema claro: mesmo símbolo e ponto verdes, wordmark `#0F1720`.
- Geometria, disposição e gradiente vêm do raster oficial existente; o nome não
  foi reconstruído com fonte. JPEGs e derivados anteriores foram preservados.
- A área clicável e o foco de teclado permanecem; apenas a apresentação da marca
  muda. Favicons e demais componentes não são alterados neste ajuste.

As duas variantes PNG têm 960 × 326 px e são selecionadas pelo tema persistido.
O gerador `frontend/scripts/brand-transparent.mjs` estende a receita Canvas
local para remover o matte e recolorir somente o wordmark na variante clara.
O registro deste ajuste está em `docs/design-refresh/brand-followup.md`.

## 13. Ajuste autorizado: favicon sem fundo

Por solicitação posterior do usuário, o ícone da aba passa a mostrar somente o
símbolo verde sobre transparência real, sem tile preto. Os PNGs de 16/32/48 px e
o ICO com três frames são derivados do símbolo oficial existente, preservando
geometria, proporção e gradiente. Novos nomes e versão nas URLs evitam reutilizar
o favicon anterior em cache. A arte de origem e as evidências antigas permanecem
intactas; Apple Touch Icon e ícones de instalação não fazem parte deste ajuste.
Receita: `frontend/scripts/favicon-assets.mjs`. Registro e QA:
`docs/design-refresh/favicon-followup.md`. A R06 permanece não iniciada.
