# Edivo — continuidade após a fundação

O roadmap aprovado permanece em `R00_EDIVO_PLANEJAMENTO_TECNICO.md`. A
implementação aceita abrange R01, R02 e R03. A R04 autorizada está **concluída
tecnicamente, com os critérios essenciais validados**. Consulte
`r01/acceptance.md`, `r02/acceptance.md`, `r03/acceptance.md` e `r04/acceptance.md`;
código implementado não substitui validação operacional.

| Etapa | Entrega autorizável |
| --- | --- |
| R01 | Ambiente reproduzível, banco/baseline, API/worker, diagnóstico e shell web |
| R02 | Projetos, upload seguro e metadados de mídia |
| R03 | Streaming privado/Range, player real, trim não destrutivo, timeline de uma faixa, presets e EditSpec versionado |
| R04 | Até três textos, MP3, volume/loop/fades, EditSpec v2 e prévia local curta no worker |
| R05 | Render, estado/cancelamento, exportação e histórico |
| R06 | Robustez, acessibilidade e testes de ponta a ponta |
| R07 | Validação isolada do heditor e desenho do executor |
| R08 | Edição assistida com plano revisável e QA |
| R09 | Pesquisa e prova de conceito Higgsfield |

Cada avanço exige autorização do usuário. R04.0–R04.4 entregam compatibilidade
v1/v2, importação segura de MP3, controles de texto/áudio, fila PostgreSQL com
lease/heartbeat e compositor FFmpeg. A R04.5 concluiu regressões, navegador,
persistência, auditoria e evidências, conforme a
[conciliação final das verificações](r04/evidence/final-regression-summary.json).
O harness interrompido e as reexecuções aprovadas permanecem documentados.

O limite implementado é a prévia local de até oito segundos solicitados, H.264,
24 fps e áudio opcional. Há quantização de até um quadro, mais arredondamento do
MP4; 4:5 usa 544×680 para dimensões pares. Música e fades seguem o relógio do
trim completo. QA de navegador cobre somente Chromium; áudio sintético foi
medido objetivamente, sem escuta humana. Consulte as limitações no aceite.

**R05 não foi iniciada** e exige nova autorização. Render completo, exportação,
histórico de exportações e cancelamento avançado permanecem para essa etapa.
heditor, agente, prompt e Higgsfield continuam futuros. Git permanece sob gestão
manual do usuário.
