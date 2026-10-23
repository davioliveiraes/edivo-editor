# VIS-01 — favicon verde transparente

O usuário solicitou remover o fundo preto do ícone da aba, deixando somente o
símbolo verde. Foram criados três PNGs transparentes (16/32/48 px) e um ICO com
os mesmos três frames. O símbolo mantém sua geometria, proporção e gradiente.

- `frontend/public/favicon-green-16x16.png`
- `frontend/public/favicon-green-32x32.png`
- `frontend/public/favicon-green-48x48.png`
- `frontend/public/favicon-green.ico`

O pipeline Canvas local `frontend/scripts/favicon-assets.mjs` usa o ícone oficial
já derivado em `frontend/public/brand/edivo-icon.png`, remove o matte neutro e
conserva o símbolo. Não há redesenho nem fontes ou dependências novas. Arquivos
originais, ícones anteriores e evidências históricas foram preservados.

`frontend/index.html` aponta para os novos arquivos com `?v=vis01-green`,
evitando o cache do favicon anterior. Apple Touch Icon e ícones de instalação
não foram alterados: o pedido refere-se somente à aba do navegador.

## Validação

ESLint do script, Prettier dos arquivos alterados, `node --check` e build
TypeScript/Vite executados. A imagem frontend foi reconstruída e somente seu
container foi recriado com `docker compose up -d --no-deps frontend`. Nenhum
serviço backend, banco, worker, storage, volume, porta ou ObraControl foi alterado.
Git permanece manual; R06 não iniciada.

O Chromium real consulta o documento e os favicons via GET, sem cache, verifica
HTTP 200, dimensões, pixels transparentes, ausência de pixels opacos neutros e
frames ICO idênticos aos PNGs. Uma prancha renderiza os tamanhos efetivos sobre
fundos claros/escuros para inspeção visual. Essa prancha não é uma captura da
barra de abas do navegador; não se afirma inspeção manual de browser chrome.

Resultados e logs completos: `evidence/transparent-favicon/qa-results.json`.
Receita e hashes: `evidence/transparent-favicon/manifest.json`.
As **30 verificações** do Chromium passaram. A prancha nos tamanhos nativos foi
aberta e inspecionada: somente o símbolo verde, sem tile preto, nos fundos claro
e escuro. Todos os PNGs possuem cantos com alpha zero; os três frames ICO são
idênticos aos PNGs servidos. Os quatro assets também estão íntegros no build.
A suíte Vitest não foi repetida neste ajuste de HTML/assets; seus resultados
anteriores permanecem históricos. Não houve mudanças em componentes/handlers.

Se uma aba já aberta continuar mostrando o ícone antigo, atualize com
**Ctrl+Shift+R** ou feche e reabra a aba para renovar o cache do navegador.
