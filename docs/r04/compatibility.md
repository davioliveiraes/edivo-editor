# Edivo — R04.0: auditoria e compatibilidade

Auditoria de 18–19/10/2026, concluída **antes de editar a implementação**.
Os 14 documentos obrigatórios foram lidos integralmente. O checkout contém
somente a especificação R04 ainda não rastreada; os fontes R03 estão preservados.
Modelos, schemas, serviços, repositórios, streaming, storage, configurações,
worker, migrações, frontend e testes reais foram inspecionados. Nenhuma
incompatibilidade essencial impede a evolução.

A autorização expressa do usuário libera a R04. O status documental de
planejamento da especificação e o limite R03 anterior do AGENTS são históricos;
os documentos de origem e seus relatórios não serão reescritos.

| Contrato encontrado | Evolução compatível R04 |
| --- | --- |
| EditSpec v1 estrito, GET devolve seu snapshot | União discriminada v1/v2; GET v1 não recebe campos novos nem migração de dados |
| CHECK schema_version = 1 em modelo e 0003 | Nova 0004 amplia somente essa constraint para IN (1,2); 0001–0003 e trigger append-only intactas |
| FOR UPDATE Project, expected_revision, no-op e 409 | Mesma ordem de validação e mesmo contrato para ambas as versões |
| MediaAsset restrito a MP4/source_video | AudioAsset independente; sem alterar constraints ou significado dos vídeos |
| Upload MP4 limitado na entrada/parser/staging | Limite MP3 próprio de 30 MiB nas três camadas; MP4 conserva 500 MiB |
| Originais privados, promoção exclusiva e 0400 | Namespace originals-audio separado, mesmos princípios de compensação e COMMIT incerto |
| Reader dir_fd/O_NOFOLLOW e FileResponse adaptada | Reusar descritores fixos e Range para artefatos autorizados; nenhum StaticFiles de storage |
| Worker independente ocioso | Acrescentar fila PostgreSQL mínima com claim/lease/heartbeat/fencing, sem codificação HTTP |
| Hash routing e draft/baseline locais | Normalizar v1 para v2 em memória nos dois; sem autosave ou dirty apenas por abrir/seek |
| Player baseado em currentTime nativo | Overlays CSS seguem esse tempo; ganho/música/fades definitivos são avaliados no MP4 de prévia |
| Portas externas 8001/5174 em loopback | Manter rede, mounts, volume e ObraControl; banco/worker sem publicação |
| Locks e dependências existentes | Reutilizar bibliotecas instaladas; mudanças só quando tecnicamente necessárias e verificadas |

## Contratos fechados

- PUT mantém expected_revision no topo, fora do snapshot. v1 e v2 são
  discriminados por inteiro estrito 1/2, rejeitando bool/float/coerções e extras.
  Mesmo snapshot/revisão esperada atual é no-op; versão antiga sempre é 409.
- V2 adiciona até três textos UUID distintos, conteúdo aparado de 1–120
  caracteres, até duas linhas, sem markup/controles invisíveis; tempos inteiros
  absolutos dentro do trim e tokens de posição/estilo/tamanho da especificação.
  Texto é dado UTF-8; aspas, acentos, dois-pontos e barras não viram filtros.
- Áudio usa source_volume=1, background_audio_id=null, background_volume=0.15,
  background_loop=false e fades=0 como defaults de conversão v1. Volumes finitos
  0–2/0–1, fades inteiros 0–3000 cuja soma não excede o trim. Música do mesmo
  projeto ativo, duração até 600 s, sem vídeo/capa embutida.
- Preview recebe revisão salva, início absoluto e duração 1–8000 ms; duração
  efetiva é truncada ao fim do trim. Música/fades conservam o relógio do trim
  completo, inclusive em janela intermediária.
- Perfis: 540×960, 540×540, **544×680**, 960×540; H.264/libx264, yuv420p,
  24 fps, SAR=1, faststart e AAC quando houver áudio. 544×680 mantém 4:5 exato
  com dimensões pares, conforme correção expressa do usuário para 540×675.
- PreviewJob registra janela efetiva, revisão, perfil, hash determinístico,
  status/stage, tentativas, lease/heartbeat e claim_token UUID interno. Token
  funciona como barreira contra publicação por worker que perdeu o lease.
- Solicitações equivalentes reutilizam queued/running (202) ou completed
  íntegro (200). Criação é serializada por Project; até dois jobs ativos. Falhas
  terminais permitem nova solicitação explícita, sem retry automático infinito.
  Cada job admite no máximo duas tentativas. Política detalhada de reconciliação
  será documentada e comprovada no lote da fila.
- Worker processa um job por vez, commit antes do subprocesso, heartbeat e
  recuperação de lease, publicação exclusiva sob validação de ownership.
  Workspace por job/tentativa; SIGTERM/timeout encerram o grupo de processos.
- Arquivo pendente é 409 preview_not_ready; projeto/job/revisão de outro projeto
  ou projeto excluído é 404. Ausência/corrupção de artefato é erro sanitizado,
  nunca sucesso aparente. Nenhum caminho físico no JSON.
- Tipografia de overlay usa fonte local licenciada, sem download automático,
  margem 7%, centro horizontal e ajuste de largura sem truncamento silencioso.
  CSS é referência de edição; FFmpeg produz a prévia real.

## Riscos e validação

Migração deve preservar snapshots/trigger; MP3 deve ter ingress independente;
parser e staging não podem ampliar limites; arquivo e banco não são transação
única. Crash após promoção exige reconciliação sem substituir outro artefato.
Lease exige fencing, não apenas timestamp. Unicode exige textfile/expansion=none
e fonte existente. Rotação/SAR devem preceder crop; música/fades não podem
reiniciar no meio do trim. Ganho acima de 100% não será simulado como suportado
pelo volume nativo HTML5. Fontes e diferenças CSS/FFmpeg serão documentadas.

## Gates

R04.0 auditada e liberada. Sequência: R04.1 banco/v1-v2/MP3 → R04.2 textos/áudio
React → R04.3 fila/worker → R04.4 FFmpeg/API/player de prévia → R04.5 regressões,
QA real e documentação. Resultados serão registrados após execução; contagens
históricas 315/75 são referência, não aprovação antecipada.

### R04.1 — validação operacional

Migração 0004 aplicada no PostgreSQL real e repetida; `alembic check` não
detectou operações pendentes. Uma revisão v1 criada com a imagem R03 antes
da migração conservou a resposta GET e o no-op. V2 foi salva explicitamente,
com textos literais e música do projeto; expected_revision antigo retornou 409.
UPDATE e DELETE de revisões continuam bloqueados com SQLSTATE 55000, em
provas revertidas. Os MP3 importados com Content-Length e transferência HTTP
chunked preservaram SHA-256 e modo 0400, sem temporários restantes.

A auditoria encontrou e corrigiu um desvio de limite por Info/Xing forjado:
o FFprobe real podia declarar 12,024 s para 661,344 s de frames MP3. A análise
agora soma duração racional dos frames, confronta cabeçalhos/stream e aplica
o limite independentemente do índice declarado, com tolerância máxima de
dois frames para atraso/padding. A prova real rejeitou os arquivos forjados e
aceitou o original sintético. Não houve alteração de documentos históricos,
linhas antigas ou dos 30 originais registrados no baseline.

Evidências: [compatibilidade HTTP](evidence/edit-spec-audio.json),
[storage e trigger](evidence/audio-storage-database.json),
[duração MP3](evidence/mp3-duration-security.json),
[preservação](evidence/preservation.json) e
[29 verificações Range R03](evidence/r03-streaming-regression.json).

Limites do importador MP3: framing integral mais decodificação do prefixo de
até dois segundos; isso não equivale a decodificação integral do original.
Free-format, tags finais APE e arquivos com capa/vídeo não são aceitos.

### Gate R04.3 — fila validada

A fila e o supervisor passaram em 32 testes unitários e oito testes opt-in no PostgreSQL real: claim concorrente, SKIP LOCKED, token/lease, heartbeat, recuperação com até duas tentativas, projeto excluído, relógio de parede e timeout persistido. A codificação e a publicação reais pertencem ao gate R04.4. Os limites do worker estão em `evidence/worker-runtime-limits.json`. Nenhum original foi removido.

### Decisões de prévia R04.4

A chave de idempotência considera perfil 1, revisão imutável, início e duração efetiva truncada pelo trim. Equivalente pendente retorna 202; artefato concluído só retorna 200 após verificação de tamanho, modo e SHA-256. Artefato ausente/corrompido é preservado e seu job passa a failed apenas ao receber nova solicitação explícita; o novo job recebe outro UUID. GET de artefato inválido retorna 503. Publicação interrompida com diretório canônico existente nunca adota nem sobrescreve esse arquivo; falha sanitizada e nova solicitação explícita são necessárias. Prévias usam 544×680 em 4:5, mantendo dimensões pares e proporção exata.

### R04.4/R04.5 — validação final

Compositor, API, streaming e worker foram executados com mídia sintética real.
A matriz produziu 25 prévias e passou 43 verificações; a prova HTTP/worker
passou 30, incluindo concorrência/idempotência, limite ativo, revisão antiga,
Range e persistência após reiniciar db/api/worker. Música e fades mantiveram o
relógio do trim na janela intermediária. O artefato de demonstração continuou
com a mesma revisão, tamanho e SHA após os reinícios: sete verificações
Chromium finais, sem POST ou PUT. Nenhum original foi sobrescrito.

Backend: 692 casos padrão aprovados e oito PostgreSQL opt-in aprovados em
separado, totalizando 700 casos distintos. Frontend: 149 casos, ESLint,
Prettier, TypeScript e Vite aprovados. O roteiro R01 interrompido no Vitest é
preservado como tentativa falha; duas reexecuções sem mudança de fonte passaram.
O [resumo conciliado](evidence/final-regression-summary.json) identifica cada
comando realmente executado, sem transformar essa tentativa em exit zero.

Auditoria final: 83 MP4, seis MP3 e cinco prévias consistentes, SHA e 0400,
nenhum temporário ou canônico desconhecido. Os 66 arquivos históricos, 30
originais do baseline e linhas anteriores permanecem inalterados. Os cinco
containers ObraControl mantêm IDs, estados, imagens e portas. Ver
[storage](evidence/storage-final.json), [preservação](evidence/preservation.json)
e [coexistência](evidence/coexistence-final.json).

Limitações preservadas: quantização a 24 fps em até um quadro mais 1 ms,
máximo absoluto de 8.001 s; diferenças CSS/FreeType e glifos não disponíveis;
sem retenção automática de resíduos de publicação incerta. Somente Chromium
desktop/mobile emulado foi validado. Áudio tem análise objetiva sintética,
sem escuta humana ou avaliação subjetiva de inteligibilidade. R05 não iniciada.
