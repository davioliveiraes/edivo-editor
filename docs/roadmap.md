# Edivo — continuidade após a fundação

O roadmap aprovado permanece em `R00_EDIVO_PLANEJAMENTO_TECNICO.md`. A
implementação atual abrange a R01, R02 e R03 autorizadas. Consulte
`r01/acceptance.md`, `r02/acceptance.md` e `r03/acceptance.md` antes do aceite;
código implementado não substitui validação operacional.

| Etapa | Entrega autorizável |
| --- | --- |
| R01 | Ambiente reproduzível, banco/baseline, API/worker, diagnóstico e shell web |
| R02 | Projetos, upload seguro e metadados de mídia |
| R03 | Streaming privado/Range, player real, trim não destrutivo, timeline de uma faixa, presets e EditSpec versionado |
| R04 | Texto, áudio simples e prévia |
| R05 | Render, estado/cancelamento, exportação e histórico |
| R06 | Robustez, acessibilidade e testes de ponta a ponta |
| R07 | Validação isolada do heditor e desenho do executor |
| R08 | Edição assistida com plano revisável e QA |
| R09 | Pesquisa e prova de conceito Higgsfield |

Cada avanço exige autorização do usuário. A R03 foi expressamente autorizada;
seu aceite depende das evidências de regressão, HTTP, PostgreSQL e navegador.
R04 não foi iniciada e exige nova autorização. O limite atual termina nas
marcações e configurações salvas: sem renderização, exportação, edição de áudio,
texto, filas, heditor ou Higgsfield. Git permanece sob gestão manual.
