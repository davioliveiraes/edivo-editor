# VIS-01 — ajuste da logo nos temas

O usuário solicitou retirar o preenchimento escuro atrás da logo e adaptar a
marca ao tema claro. A solicitação substitui a aplicação anterior em badge
escuro registrada no aceite inicial, sem alterar outras telas ou comportamentos.

## Alteração entregue

- Caixa do cabeçalho sem fundo e sem borda decorativa. Área clicável, nome
  acessível, foco por teclado e link institucional preservados.
- Tema escuro: logo transparente com wordmark branco, símbolo e ponto verdes.
- Tema claro: logo transparente com wordmark `#0F1720`, símbolo e ponto verdes.
- Duas variantes PNG de 960 × 326 px, escolhidas pelo tema persistido; geometria
  e disposição mantidas a partir da arte existente. Não há fonte substituta.
- Fonte JPEG, PNG/WebP anteriores, favicon e demais assets preservados.

Arquivos deste ajuste: `frontend/src/app/app.tsx`, `frontend/src/app/styles.css`,
`frontend/tests/app.test.tsx`, `frontend/scripts/brand-transparent.mjs` e dois
novos PNGs em `frontend/public/brand/`. A regra aprovada está no apêndice 12 do
`DESIGN.md`; evidências novas estão em `evidence/transparent-brand/`.

O script local estende o gerador Canvas existente para remover o matte raster
sem redesenhar o wordmark e recolorir somente as letras na variante clara.
As duas tentativas de `imagegen` apresentaram resíduos e não foram incorporadas.
Não há arte generativa no frontend. Essa é uma conversão raster, sem alegação
vetorial. Receitas, dimensões, tamanhos e SHA-256 estão no
[manifesto](evidence/transparent-brand/manifest.json).

## QA e resultados

- Testes focados do cabeçalho: 12 aprovados; verificam inclusive a troca da logo
  ao alternar o tema e a restauração da preferência. São subset da suíte completa.
- `npm run lint`: aprovado. `npm run format:check`: aprovado.
- `npm run build`: TypeScript e Vite aprovados.
- `npm run test -- --run --maxWorkers=2`: **316 testes em nove arquivos aprovados**,
  sem mudar testes históricos, timeouts ou configuração do projeto.
- `docker compose build frontend` e `docker compose up -d --no-deps frontend`:
  aprovados; somente frontend atualizado. Nenhum restart de API/banco/worker ou
  operação sobre ObraControl.
- Navegador real e inspeção das capturas: resultados finais no
  [QA deste ajuste](evidence/transparent-brand/qa-results.json).

Chromium 145 passou **63 verificações**, com seis capturas do cabeçalho:
desktop 1366 px e mobile 375/390 px nos dois temas. As seis imagens foram abertas
e inspecionadas; não há caixa escura, borda ou overflow no cabeçalho. O PNG possui
alpha real (inclusive cantos transparentes), com 74,7% dos pixels totalmente
transparentes. A cor do wordmark, proporção e troca persistida do tema passaram.
Nenhuma requisição de mutação, recurso externo ou exceção de página ocorreu.

Capturas finais: [escuro desktop](evidence/transparent-brand/browser-2/1366-dark-header.png),
[claro desktop](evidence/transparent-brand/browser-2/1366-light-header.png),
[escuro mobile](evidence/transparent-brand/browser-2/375-dark-header.png) e
[claro mobile](evidence/transparent-brand/browser-2/375-light-header.png).
Os dez assets anteriores foram conferidos por SHA-256 e permanecem inalterados.
Os logs também estão preservados em `evidence/transparent-brand/logs.json`, pois
o `.gitignore` existente ignora arquivos `.log`.

### Instabilidade de testes preservada

Duas execuções da suíte completa com o paralelismo padrão apresentaram dois
timeouts de 5.000 ms cada: 314 aprovados/2 falhas em cada passagem. Os arquivos
foram `composition.test.tsx` e `previews.test.tsx` na primeira, e
`composition.test.tsx` e `editor-controls.test.tsx` na segunda. Os logs integrais
ficam em `vitest.log` e `vitest-without-build.log` no diretório de evidência deste
ajuste. A primeira concorria com builds; a segunda não. Portanto, não se atribui
causa comprovada ao build.

A investigação dos dois arquivos iniciais passou 74 testes. A suíte completa,
com dois workers e o mesmo timeout padrão, passou os 316 testes. A redução de
concorrência é uma opção da execução, sem edição de código para ocultar falhas.
A causa raiz da sensibilidade ao paralelismo permanece uma limitação de QA;
esses resultados não apagam as falhas anteriores nem a ocorrência histórica R04.

O primeiro helper de navegador amostrou um espaço transparente entre letras,
produzindo uma falha na asserção da cor; a amostragem passou a localizar um pixel
opaco da região do wordmark. O relatório inicial foi conservado. A primeira
execução do gerador também encontrou um caminho de Chromium ausente, corrigido
pela descoberta do executável instalado, sem gravar assets nessa tentativa.
Ver [registro de tentativas](evidence/transparent-brand/attempts.json).

Nenhum backend, modelo, banco, migration, contrato, worker, FFmpeg, storage,
Compose, porta ou recurso funcional foi alterado. Favicons permanecem como
aprovados. Não houve operações Git de escrita. A R06 não foi iniciada.
