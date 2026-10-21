# Edivo

Editor de vídeo web **local-first**, com React/TypeScript, FastAPI/Python e
FFmpeg. A **R04 — Textos, Áudio e Geração de Prévia** implementa textos, música
MP3, EditSpec v2 e prévias MP4 curtas processadas pelo worker independente.
Projetos, importações, player HTML5 privado, timeline e revisões não destrutivas
das R01–R03 permanecem na aplicação. **A R04 está concluída tecnicamente**, com
os critérios essenciais validados e as limitações registradas no relatório.

O produto evoluirá para um editor visual orientado principalmente por prompts.
A R04 estabelece a composição local reutilizável; não inclui exportação final,
campo de prompt ou integração de IA.

O contrato atual está na [especificação R04](docs/R04_EDIVO_ESPECIFICACAO_OPERACIONAL.md).
Consulte o [aceite R04](docs/r04/acceptance.md), o [QA R04](docs/r04/qa-results.json)
e a [compatibilidade R04](docs/r04/compatibility.md). O [aceite R03](docs/r03/acceptance.md),
seu [QA](docs/r03/qa-results.json) e o [relatório R02](docs/r02/acceptance.md)
e o [aceite R01](docs/r01/acceptance.md) permanecem como histórico.
Os documentos originais de planejamento em `docs/` foram
preservados. Consulte também
[arquitetura](docs/architecture.md), [decisões](docs/adr/0001-r01-foundation.md),
[roadmap](docs/roadmap.md) e [convenções para agentes](AGENTS.md).

## Requisitos

- Ubuntu 24.04 LTS, inclusive via WSL2; prefira `~/edivo-editor` no filesystem
  Linux em vez de `/mnt/c` para permissões e desempenho de watch.
- Docker Engine ou Docker Desktop com integração ativada para a distribuição
  WSL2 e Docker Compose com suporte a condições de dependência.
- Python 3.12 no host para os scripts de preparo/aceite.
- Duas portas locais disponíveis: padrões 5173 e 8000, configuráveis pelo `.env`.
  PostgreSQL não publica porta no host.
- Rede para baixar imagens e dependências no primeiro build; o produto não
  depende de serviços externos de IA, GPU ou assinatura.
- Para desenvolver fora dos containers: Node 24 LTS, npm 11 e uv. As versões
  usadas e os patches das imagens estão registrados no relatório de aceite.

## Preparar e iniciar

Na raiz do repositório:

```bash
python3 scripts/setup-local.py
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps -a
```

O preparo cria `.env` com senha aleatória, permissões 0600 e UID/GID locais,
além dos diretórios de storage com acesso privado ao usuário. Se `.env` ou
arquivos já existirem, são preservados. Não publique nem envie esse arquivo.
Use `config --quiet`: a saída completa de `docker compose config` contém
variáveis interpoladas, inclusive credenciais.

O serviço `migrate` deve terminar com código zero. Os serviços `api` e `worker`
aguardam banco saudável e migração concluída; o frontend aguarda a API saudável.
Na primeira inicialização, aguarde os healthchecks antes de abrir a interface.

| Serviço | Função | Acesso padrão no host |
| --- | --- | --- |
| `frontend` | React e servidor Vite de desenvolvimento | http://127.0.0.1:5173 |
| `api` | FastAPI/Uvicorn | http://127.0.0.1:8000 |
| `db` | PostgreSQL 16 | Nenhuma porta publicada |
| `worker` | Diagnóstico, fila PostgreSQL e codificação de prévias curtas | Nenhuma porta publicada |
| `migrate` | Alembic, execução única | Nenhuma porta publicada |

As portas externas usam `EDIVO_API_PORT` e `EDIVO_FRONTEND_PORT`. Para coexistir
com o ObraControl, o `.env` local deste ambiente usa **8001** para a API e **5174**
para a interface: http://127.0.0.1:8001/docs e http://127.0.0.1:5174. Não é preciso
parar o outro aplicativo. O bind permanece em `127.0.0.1`; as portas internas
continuam 8000 e 5173, e o proxy continua `http://api:8000`.

Exemplo de configuração local para essa coexistência:

```dotenv
EDIVO_API_PORT=8001
EDIVO_FRONTEND_PORT=5174
EDIVO_CORS_ORIGINS='["http://127.0.0.1:5174","http://localhost:5174"]'
```

Ao alterar a porta do frontend, ajuste também as origens locais do CORS. Recrie
os containers com `docker compose up -d --build`. Os exemplos HTTP abaixo usam
os padrões; substitua `8000` por `8001` neste ambiente.

Em `docker compose ps`, entradas como `5432/tcp` ou `8000/tcp` sem mapeamento
`127.0.0.1:...->` representam portas internas/metadados da imagem, sem publicação
no host. O worker não inicia servidor HTTP.

Swagger local: http://127.0.0.1:8000/docs. O navegador utiliza `/api`, encaminhado
pelo Vite para `http://api:8000` dentro do Docker. O frontend exibe disponibilidade
da API. O processamento das prévias é acompanhado pelo estado persistente de
cada job; o status da API não comprova que o worker esteja em execução.

```bash
curl -fsS http://127.0.0.1:8000/api/v1/system/health
curl -fsS http://127.0.0.1:8000/api/v1/system/ready
curl -fsS http://127.0.0.1:8000/api/v1/system/capabilities
docker compose logs --tail=50 worker
```

## Configuração e persistência

`.env.example` documenta as variáveis sem conter credenciais reais. O Compose
constrói o DSN a partir de `POSTGRES_USER`, `POSTGRES_PASSWORD` e `POSTGRES_DB`;
`EDIVO_DATABASE_URL` é usado pelo Python fora do Compose. Senhas geradas em
hexadecimal não precisam de URL encoding. Este Compose exige senha URL-safe
(prefira o hexadecimal gerado): ele usa o mesmo valor diretamente no PostgreSQL
e na montagem do DSN. Não codifique apenas `POSTGRES_PASSWORD`, pois isso mudaria
a senha do banco. Um DSN nativo configurado separadamente precisa de URL encoding
quando suas credenciais contêm caracteres reservados.

| Variável | Uso |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` | Credenciais locais do banco |
| `EDIVO_UID`, `EDIVO_GID` | Identidade do backend e permissões do bind mount |
| `EDIVO_API_PORT`, `EDIVO_FRONTEND_PORT` | Portas externas, padrões 8000 e 5173; bind somente loopback |
| `EDIVO_ENV`, `EDIVO_LOG_LEVEL` | Ambiente e nível de logging |
| `EDIVO_DATABASE_URL` | URL `postgresql+psycopg://...` para SQLAlchemy/Alembic |
| `EDIVO_DATABASE_CONNECT_TIMEOUT` | Timeout de conexão em segundos |
| `EDIVO_STORAGE_ROOT` | Storage; no Compose é `/data/storage` |
| `EDIVO_CORS_ORIGINS` | Lista JSON de origens locais permitidas |
| `EDIVO_FFMPEG_BIN`, `EDIVO_FFPROBE_BIN` | Executáveis para diagnóstico |
| `EDIVO_FFMPEG_TIMEOUT_SECONDS` | Timeout por subprocesso de inventário |
| `EDIVO_CAPABILITIES_CACHE_SECONDS` | Prazo do cache de capacidades |
| `API_PROXY_TARGET` | Destino do proxy Vite; não é segredo nem URL do browser |

Limites específicos da importação, configurados no `.env` e consumidos pela API:

| Variável | Padrão | Uso |
| --- | --- | --- |
| `EDIVO_UPLOAD_MAX_BYTES` | `524288000` | Até 500 MiB de bytes reais por arquivo |
| `EDIVO_UPLOAD_MULTIPART_OVERHEAD_BYTES` | `65536` | 64 KiB adicionais para o envelope multipart; o body inteiro é limitado durante a entrada |
| `EDIVO_MEDIA_MAX_DURATION_SECONDS` | `600` | Duração máxima de 10 minutos |
| `EDIVO_AUDIO_UPLOAD_MAX_BYTES` | `31457280` | Limite MP3 independente, 30 MiB; pode ser reduzido |
| `EDIVO_AUDIO_MAX_DURATION_SECONDS` | `600` | Duração máxima MP3, até 10 minutos |
| `EDIVO_FFPROBE_TIMEOUT_SECONDS` | `15` | Prazo de inspeção por subprocesso |
| `EDIVO_FFPROBE_MAX_OUTPUT_BYTES` | `1048576` | Limite combinado de stdout/stderr, 1 MiB |
| `EDIVO_STORAGE_MIN_FREE_BYTES` | `67108864` | Reserva mínima de disco livre, 64 MiB |

O Compose repassa os limites de arquivo/duração à interface pelas variáveis
públicas `VITE_UPLOAD_MAX_BYTES`, `VITE_MEDIA_MAX_DURATION_SECONDS`,
`VITE_AUDIO_UPLOAD_MAX_BYTES` e `VITE_AUDIO_MAX_DURATION_SECONDS`. No fluxo
nativo do Vite, configure esses valores públicos se alterar os defaults.
A validação do navegador fornece feedback; a API é responsável pela proteção.

Settings rejeitam outros drivers de banco e origens CORS externas. Configuração
inválida falha com mensagem sanitizada. Nunca use variáveis `VITE_*` para segredos.
O DSN não aceita query; configure os timeouts pelas variáveis indicadas.

PostgreSQL usa o volume nomeado `edivo_postgres_data`; API e worker compartilham
`./storage`. A baseline Alembic cria somente o registro de versão, sem tabelas
de negócio. A revisão incremental **`0002_r02_projects_media`**, posterior à
**`0001_r01_baseline`**, cria `projects` e `media_assets`, preservando a baseline
e os dados existentes. A **`0003_r03_edit_spec_revisions`** acrescenta somente
revisões de EditSpec, índices, constraints e proteção contra alteração/exclusão
das revisões. **`0004_r04_text_audio_previews`** acrescenta `audio_assets` e
`preview_jobs` e amplia somente a constraint de schema para 1/2, sem regravar
snapshots nem remover a trigger. Para aplicar/repetir e consultar a revisão:

```bash
docker compose run --rm migrate
docker compose exec api alembic current
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version_num FROM alembic_version"'
```

As credenciais do entrypoint PostgreSQL são aplicadas quando o volume é criado;
alterar somente `.env` não modifica uma senha em banco já inicializado.
Não remova o volume para resolver isso sem avaliar os dados existentes.

Para encerrar preservando os dados:

```bash
docker compose stop
```

Para retomar, use `docker compose up -d`. Não execute `down -v`: ele apaga o
volume. O script de aceite não remove volumes nem dados existentes.

## Projetos, biblioteca e API

Na interface, crie um projeto, abra seu detalhe, selecione ou arraste um MP4 e
clique em **Importar MP4**. A transferência mostra percentual somente quando
o navegador informa seu tamanho. Depois, **Analisando vídeo…** é um estado
indeterminado até a resposta da API. A biblioteca apresenta duração, resolução,
FPS racional, tamanho, codecs, presença de áudio, data e SHA-256. Vídeos sem áudio
são aceitos. O tema claro/escuro continua persistido no navegador.

A navegação usa `#/projects` e `#/projects/{uuid}`. Projetos e mídias têm
paginação de 20 itens na interface. A API aceita `limit` de 1 a 100 e `offset`
não negativo; o padrão é 20/0. Ambas as listagens usam `created_at DESC, id DESC`
e retornam `{ "items": [], "total": 0, "limit": 20, "offset": 0 }` para uma
lista vazia.

| Método | Endpoint | Sucesso |
| --- | --- | --- |
| `POST` | `/api/v1/projects` | `201`, projeto criado |
| `GET` | `/api/v1/projects` | `200`, projetos ativos paginados |
| `GET` | `/api/v1/projects/{project_id}` | `200`, detalhe de projeto ativo |
| `PATCH` | `/api/v1/projects/{project_id}` | `200`, atualização parcial |
| `DELETE` | `/api/v1/projects/{project_id}` | `204`, exclusão lógica sem corpo |
| `POST` | `/api/v1/projects/{project_id}/media` | `201`, MP4 importado e metadados persistidos |
| `GET` | `/api/v1/projects/{project_id}/media` | `200`, mídias paginadas |
| `GET` | `/api/v1/projects/{project_id}/media/{media_id}` | `200`, metadados de mídia do projeto |
| `POST` | `/api/v1/projects/{project_id}/audio` | `201`, MP3 validado e preservado |
| `GET` | `/api/v1/projects/{project_id}/audio` | `200`, músicas paginadas |
| `GET` | `/api/v1/projects/{project_id}/audio/{audio_id}` | `200`, metadados do MP3 associado |

`Project` tem UUID gerado no servidor, `name` obrigatório após trim com 1–120
caracteres, `purpose` opcional de até 500 caracteres, `target_preset` opcional
(`9:16`, `1:1`, `4:5`, `16:9`), `created_at`, `updated_at` e `deleted_at`
interno. O preset do cadastro sugere apenas a primeira edição; mudar esse campo
não transforma originais nem altera as revisões de edição já salvas.
Nomes podem se repetir. Um PATCH vazio, nome nulo ou campos desconhecidos são
rejeitados. Finalidade e preset podem ser limpos com `null`.

`MediaAsset` tem UUID, FK de projeto, tipo `source_video`, nome original saneado,
chave de storage interna única, SHA-256, tamanho real, MIME confirmado,
`duration_ms`, dimensões, `fps_num`/`fps_den`, codecs, presença de áudio,
metadados internos filtrados e data UTC. Constraints validam os valores
positivos, a FK e a chave única. O checksum não é único: duas importações do
mesmo arquivo geram registros e destinos independentes.

Os JSON públicos de mídia contêm os metadados de exibição e integridade;
`storage_key`, paths locais e o JSON bruto do FFprobe não são devolvidos.
A R03 acrescenta streaming privado por projeto/mídia; o storage permanece
sem publicação estática ou endpoint genérico de arquivos.

Exemplos com arquivo sintético ou próprio:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Minha história","purpose":"Organizar os originais","target_preset":"9:16"}'
curl -fsS 'http://127.0.0.1:8000/api/v1/projects?limit=20&offset=0'
# Substitua PROJECT_UUID pelo id devolvido pela API; um único campo file.
curl -fsS -X POST http://127.0.0.1:8000/api/v1/projects/PROJECT_UUID/media \
  -F 'file=@amostra.mp4'
curl -fsS http://127.0.0.1:8000/api/v1/projects/PROJECT_UUID/media
```

Erros seguem o envelope R01 `{ "code": "...", "message": "...", "request_id": "..." }`,
com mensagens sanitizadas. Swagger apresenta os contratos em `/docs`.

| HTTP | Situações |
| --- | --- |
| `400` | Multipart malformado/incompleto, campos extras, mais de um arquivo, Content-Length inválido |
| `404` | Projeto inexistente/excluído, mídia ausente ou pertencente a outro projeto; DELETE repetido; edição ainda não salva |
| `409` | Revisão esperada desatualizada, limite de jobs ativos ou prévia ainda não pronta |
| `413` | Body total ou arquivo real acima dos limites |
| `415` | Extensão/container incompatível; MP3 com vídeo/capa |
| `416` | Range de streaming inválido, múltiplo ou insatisfatível |
| `422` | Dados/UUID/paginação/janela inválidos; mídia vazia, corrompida, sem stream útil, duração excessiva ou metadados inconsistentes |
| `503` | Banco/storage indisponíveis, FFprobe ausente, timeout de análise ou artefato de prévia indisponível |

A exclusão de projeto exige confirmação na interface e preenche `deleted_at`.
Oculta o projeto e bloqueia novas importações/consultas de mídia por ele, mas
**preserva registros e arquivos originais**. Restauração e exclusão física não
fazem parte desta entrega.

## Editor, streaming e EditSpec v1/v2

Abra **Abrir editor** na biblioteca do projeto. A rota
`#/projects/{uuid}/editor` mantém a navegação por hash existente. Selecione uma
fonte importada, reproduza/pause e busque no vídeo. O player usa `<video>` nativo,
`preload="metadata"` e URLs relativas; não baixa o MP4 inteiro em JavaScript.
Não há autoplay com áudio. Tempo e playhead seguem eventos e tempo reais do vídeo.

A timeline possui uma faixa. Clique/toque para buscar; arraste os marcadores ou
use o teclado e os campos em milissegundos para definir entrada/saída. O intervalo
mínimo é 100 ms; fontes menores permitem somente o trecho completo. A reprodução
do trecho pausa no fim dentro da precisão do navegador. Playhead não é edição.

Escolha `9:16` (1080×1920), `1:1` (1080×1080), `4:5` (1080×1350) ou `16:9`
(1920×1080). Essas dimensões são referências de formato; a prévia R04 usa as
resoluções reduzidas descritas abaixo, preservando a proporção escolhida.
`cover` preenche o quadro e permite focal normalizado em `[0,1]`; `contain`
mostra a fonte inteira com fundo neutro e mantém o focal salvo sem aplicá-lo.
`Project.target_preset` sugere somente a primeira edição; revisões salvas possuem
seu próprio preset.

**Salvar edição** é explícito. A interface distingue alterações locais,
salvamento e revisão salva; confirma descarte ao sair/trocar fonte. Em conflito,
recarregue conscientemente a revisão atual antes de tentar salvar novamente.
Não há sobrescrita automática, autosave ou endpoint de restauração/histórico.

| Método | Endpoint | Resultado |
| --- | --- | --- |
| `GET` / `HEAD` | `/api/v1/projects/{project_id}/media/{media_id}/stream` | `200` completo; `206` Range; HEAD sem corpo |
| `GET` | `/api/v1/projects/{project_id}/edit-spec` | `200` revisão atual; `404 edit_spec_not_found` antes do primeiro save |
| `PUT` | `/api/v1/projects/{project_id}/edit-spec` | `201` primeira revisão; `200` atualização/no-op; `409 edit_spec_conflict` |

O stream aceita **um** Range fechado (`bytes=0-1023`), aberto (`bytes=1024-`)
ou suffix (`bytes=-1024`). Range inválido/múltiplo/insatisfatível retorna `416`,
envelope sanitizado e `Content-Range: bytes */tamanho`. Respostas de mídia usam
`Content-Type: video/mp4`, `Accept-Ranges: bytes`, comprimento correto,
`Cache-Control: private, no-store` e `X-Content-Type-Options: nosniff`.
`If-Range` compara ETag forte derivado do SHA-256 ou Last-Modified: correspondência
permite `206`, divergência/ETag fraca retorna `200`. Range malformado é rejeitado
antes dessa comparação. Acesso a projeto excluído ou mídia de outro projeto
retorna `404`. Arquivos ausentes/inacessíveis retornam erro sanitizado.

A API resolve exclusivamente a chave interna esperada por UUID. Cada componente
é aberto sem seguir symlinks; tamanho e arquivo regular são verificados. O
descritor validado é mantido até finalizar a resposta, sem reabrir um path sujeito
a troca. Starlette fornece streaming de 64 KiB e processamento de Range; o
adaptador fecha o descritor inclusive em HEAD, erro e desconexão. A sessão do
banco é liberada antes da transferência. Ler não altera SHA-256 ou modo 0400.

Verificação manual nas portas deste ambiente (substitua os UUIDs):

```bash
curl -I http://127.0.0.1:8001/api/v1/projects/PROJECT_UUID/media/MEDIA_UUID/stream
curl -sS -D - -o /dev/null -H 'Range: bytes=0-15' \
  http://127.0.0.1:8001/api/v1/projects/PROJECT_UUID/media/MEDIA_UUID/stream
curl -fsS http://127.0.0.1:8001/api/v1/projects/PROJECT_UUID/edit-spec
```

Exemplo de PUT completo (substitua ambos os UUIDs e a duração pela fonte real):

```json
{
  "expected_revision": 0,
  "schema_version": 1,
  "source_media_id": "UUID_DA_MIDIA",
  "trim": { "start_ms": 1000, "end_ms": 3500 },
  "canvas": { "preset": "9:16", "fit": "cover", "focal_x": 0.5, "focal_y": 0.5 }
}
```

Tempos, schema e revisão são inteiros estritos; campos desconhecidos e focais
não finitos são rejeitados. A resposta acrescenta `id`, `project_id`,
`revision_number` e `created_at` ao plano. A próxima gravação envia o número
atual em `expected_revision`. Conteúdo idêntico com a revisão correta é **no-op**
`200`: preserva ID/número/data. Revisão desatualizada sempre causa `409`.

`edit_spec_revisions` armazena UUID, FKs para projeto/fonte, sequência por projeto,
schema 1/2, JSONB controlado e data UTC. Transação curta com lock `FOR UPDATE` no
projeto serializa os saves; unique `(project_id, revision_number)` reforça a
integridade. Revisões anteriores são imutáveis na aplicação e por trigger
PostgreSQL que bloqueia UPDATE/DELETE. Não modifique revisões por SQL.

O HTML5 pode não decodificar todo codec aceito no upload. Erros de codec e rede
são apresentados sem transcoding automático. Rotação/display matrix e pixel
aspect ratio são interpretados pelo navegador; a resolução codificada da
biblioteca pode diferir da dimensão visual. VFR, keyframes e agendamento do
navegador limitam seek e pausa: **não há promessa de precisão por frame** nem
equivalência com uma exportação futura.

### Textos, música e EditSpec v2

GET de revisão v1 continua devolvendo v1. O editor converte apenas a cópia em
memória para v2 com textos vazios e áudio padrão; abrir, reproduzir e buscar
não salvam nem criam estado dirty. Uma gravação explícita envia v2, mantendo
`expected_revision`, conflitos e no-op do contrato anterior.

V2 acrescenta `text_overlays` (até três) e `audio`. Cada texto tem UUID, conteúdo
simples de 1–120 caracteres/até duas linhas, início/fim absolutos da fonte dentro
do trim, posição `top|center|bottom`, estilo
`white_outline|blue_outline|white_plain` e tamanho `small|medium|large`.
A visualização CSS acompanha o tempo nativo, centraliza as linhas e usa margem
de 7%. DejaVu Sans local e sua licença estão em `frontend/public/fonts`;
textos longos são reduzidos para caber e símbolos raros podem não ter glifos.
Confirme legibilidade no resultado codificado. Glifos ausentes na fonte local
geram falha explícita; não são omitidos silenciosamente.

O painel de áudio permite importar um MP3, selecionar música opcional e salvar
volume original de 0–200%, música de 0–100%, loop e fades de 0–3000 ms cuja soma
não supera o trim. Defaults: original 100%, música 15%, sem música/loop/fades.
O player da fonte aplica volume até 100%; música, fades do mix e ganho superior
a 100% são avaliados no MP4 da prévia. Não há normalização automática.
Use apenas músicas próprias ou com licença adequada; a biblioteca não concede
direitos de uso.

AudioAsset é independente de MediaAsset. O upload MP3 limita o body na entrada,
o parser e o staging a 30 MiB, calcula SHA-256 em chunks e verifica framing
completo mais FFprobe/decodificação de até dois segundos. A duração estrutural
é confrontada com Info/Xing; um índice forjado não reduz o limite real de 600 s.
Free-format, tags finais APE e capas/vídeo não são aceitos. MP3 não é servido
como conteúdo estático. Originais ficam em namespace privado separado, com
destinos exclusivos e modo 0400. Mesmos nomes/bytes não sobrescrevem importações.

```bash
curl -fsS -X POST http://127.0.0.1:8001/api/v1/projects/PROJECT_UUID/audio \
  -F 'file=@musica.mp3'
curl -fsS http://127.0.0.1:8001/api/v1/projects/PROJECT_UUID/audio
```

## Prévias locais R04

A edição precisa estar salva e sem alterações pendentes. Em **Gerar prévia**,
informe início absoluto na fonte e duração inteira de 1–8000 ms. O fim é limitado
pelo trim salvo. A ação não salva implicitamente e não recebe um plano editável.
O resultado mostra a revisão e janela efetivamente usadas, mesmo após novas
alterações. O polling de aproximadamente dois segundos só acompanha jobs ativos;
a referência persistida localmente é revalidada no backend após recarregar.

| Método e rota relativa a `/api/v1/projects/{project_id}` | Resultado |
| --- | --- |
| `POST /previews` | 202 novo/equivalente pendente; 200 artefato equivalente íntegro |
| `GET /previews/{preview_id}` | Estado, estágio real, revisão, janela, tentativas e URLs relativas |
| `GET /previews/{preview_id}/file` | MP4 privado, 200/206/416 e leitura em streaming |
| `HEAD /previews/{preview_id}/file` | Mesmos cabeçalhos, sem corpo |

```json
{"edit_spec_revision_id":"UUID_DA_REVISAO_SALVA","preview_start_ms":5000,"duration_ms":4000}
```

Há até dois jobs ativos por projeto e um processamento por worker. O terceiro
retorna `409 preview_active_limit`; arquivo ainda pendente/falho retorna
`409 preview_not_ready`; projeto/revisão/job de outro projeto ou excluído retorna
404. Falhas de dependência durante a consulta API geram 503 sanitizado. Uma
falha do worker aparece no GET do job com HTTP 200, `status=failed` e código
seguro. `error_code` não contém stderr nem caminhos. Não há endpoint de download
final nem storage estático.

A chave de idempotência inclui perfil, revisão e janela efetiva. Um artefato
concluído só é reutilizado após tamanho, modo 0400 e SHA-256 conferidos. GET de
artefato inválido retorna 503; um novo POST explícito preserva o arquivo e job
anteriores, marca o job inválido como failed e enfileira outro UUID. Não existe
limpeza automática de prévias na R04; acompanhe o espaço em disco.

O worker usa claim transacional `FOR UPDATE SKIP LOCKED`, lease de 30 s,
heartbeat de 5 s e até duas tentativas. Token e validade do lease são verificados
em cada transição. Recuperação de crash permite uma nova tentativa; falhas de
validação/codificação são terminais e podem ser tentadas por solicitação explícita.
Publicação bloqueia projeto e job, revalida posse antes/depois do hardlink e só
confirma completed após validar todo o MP4. Um destino canônico preexistente após
crash nunca é adotado/sobrescrito: o job falha, o arquivo é preservado e uma nova
solicitação recebe outro UUID. Filesystem e PostgreSQL não formam transação única.

O perfil é libx264/H.264, yuv420p, 24 fps, AAC quando há áudio e MP4 faststart:
9:16 540×960, 1:1 540×540, **4:5 544×680** e 16:9 960×540. O 4:5 usa dimensões
pares exatas, pois 540×675 tem altura ímpar. Autorotação, SAR, framing e PTS são
normalizados. Textos UTF-8 são arquivos privados com expansão desativada; não
compõem expressões FFmpeg. A fonte é DejaVu Sans instalada na imagem e licenciada
junto do asset frontend. Mídia permanece local e os originais são abertos por FD.

Música começa no zero relativo do trim completo. Uma janela intermediária usa o
offset correspondente e os fades continuam no relógio do trim, sem reiniciar.
Sem loop, música esgotada resulta em silêncio; fontes sem áudio são aceitas.
O player original serve à edição visual; o player separado da prévia permite
avaliar o mix produzido. CSS e FFmpeg podem diferir na rasterização dos textos.
As janelas solicitadas têm até 8 s. A saída a 24 fps quantiza os limites em até
um quadro (41,667 ms), mais 1 ms de arredondamento do container: uma solicitação
de 1 ms resulta em um quadro, cerca de 42 ms. O máximo absoluto aceito no MP4 é
8,001 s para acomodar esse arredondamento. A reprodução e o seek não prometem
precisão por frame nem qualidade de exportação final.

Os limites completos estão em `.env.example`. Defaults: timeout global do job
120 s, arquivo 64 MiB, 2 threads, AS por subprocesso 1 GiB, CPU por subprocesso
120 s e saída combinada 256 KiB. O container worker limita 2 CPUs, 1536 MiB sem
swap adicional e 128 PIDs, com Tini e sem privilégios extras. Heartbeat deve ser
menor que metade do lease. SIGTERM/SIGINT encerram o grupo de subprocessos.

## Política de upload e integridade

Cada requisição aceita um único campo multipart `file`, sem campos adicionais.
O middleware limita o body antes/durante o parser com e sem Content-Length,
considerando o overhead configurado. O parser também limita bytes do arquivo,
número de partes e cabeçalhos. Arquivos grandes são spooled no storage privado,
sem depender do `/tmp` de 128 MiB do container.

O fluxo de importação é:

1. Verificar projeto ativo e envelope multipart; o MIME do navegador é apenas
   um indício. O nome é saneado para exibição e nunca compõe um path.
2. Receber pelo spool e copiar em chunks de 1 MiB para staging em `storage/temp`,
   contando bytes, verificando espaço e calculando SHA-256 incrementalmente.
3. Checar extensão `.mp4` sem distinção de maiúsculas, estrutura ISO BMFF,
   marcas MP4, boxes `ftyp`/`moov`/`mdat` e FFprobe real com argumentos seguros,
   protocolos restritos, timeout e saída limitada, sem shell.
4. Selecionar a stream de vídeo padrão e, em empate, a de menor índice,
   desconsiderando capas. Validar duração, resolução e FPS positivo; normalizar
   duração para milissegundos e FPS para uma fração racional. Áudio é opcional.
   Persistir somente um conjunto controlado de metadados, sem tags privadas.
5. Revalidar/travar brevemente o projeto ativo, preparar o registro e publicar
   em `originals/{project_uuid}/{asset_uuid}/source.mp4`. A promoção cria um
   destino exclusivo e um hard link atômico sem substituir arquivos existentes,
   no mesmo filesystem. O original recebe permissão **0400**.
6. Confirmar a transação PostgreSQL e limpar spool/staging. Falhas conhecidas
   compensam somente os artefatos desta tentativa. Um COMMIT com resultado
   incerto preserva o original quando não for possível confirmar o resultado.

Reserve espaço para spool e staging simultâneos, além dos originais e da margem
livre configurada. Diretórios privados usam permissões compatíveis com UID/GID
dos runtimes; não aplique `chmod 777` nem altere originais. A promoção exige que
staging e destino permaneçam no mesmo filesystem. O modo 0400 protege contra
escrita acidental, sem substituir backups ou impedir ações explícitas do
proprietário do filesystem.

A inspeção decodifica somente um **prefixo de aproximadamente 0,1 segundo** para
confirmar um frame de vídeo utilizável. Isso **não prova a integridade completa
dos até 600 segundos**: corrupção posterior pode existir. A R02 não transcodifica
nem garante reprodução de todos os codecs MP4 em navegadores. Rotação, VFR e
aspecto de pixels são tratados na composição da prévia, mas a inspeção do upload
continua parcial. Um erro posterior de decodificação faz a prévia falhar;
nenhum original é modificado para corrigir a mídia.

Não há atomicidade perfeita entre banco e filesystem. Queda entre promoção e
COMMIT, ou compensação impossível, pode deixar um arquivo a reconciliar.
A auditoria somente leitura para esse diagnóstico é:

```bash
docker compose exec -T api python - < scripts/audit-storage.py
```

Ela não autoriza apagar originais ou dados existentes automaticamente. Consulte
o relatório R02 para a execução e o resultado observado dessa auditoria.

## Desenvolvimento e qualidade

Os containers incluem dependências de desenvolvimento e as suítes R01–R04:

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
docker compose exec frontend npm run test -- --run
docker compose exec frontend npm run lint
docker compose exec frontend npm run format:check
docker compose exec frontend npm run build
```

As suítes de interface usam respostas HTTP e transporte XHR simulados;
eventos de mídia simulados em unidade não comprovam decodificação real.
Contagens finais, comandos, falhas corrigidas e provas com PostgreSQL, storage e
Chromium reais estão no [QA R04](docs/r04/qa-results.json); resultados históricos
R01–R03 são preservados nos respectivos diretórios.

A [regressão final consolidada](docs/r04/evidence/final-regression-summary.json)
registra 692 testes backend e oito PostgreSQL opt-in aprovados separadamente
(700 casos distintos), além de 149 testes frontend e lint/build aprovados.
O harness bruto parou no Vitest; duas reexecuções passaram sem mudar fontes.
A causa inicial permanece desconhecida e não foi apagada das evidências.

Para repetir os testes PostgreSQL opt-in, aguarde a fila ficar vazia e execute
somente sobre o banco local Edivo de desenvolvimento. O worker é retomado mesmo
se a suíte falhar:

```bash
(
  docker compose stop worker || exit
  trap 'docker compose start worker' EXIT
  docker compose exec -T api sh -c \
    'EDIVO_QA_DATABASE_URL="$EDIVO_DATABASE_URL" EDIVO_QA_QUEUE_POSTGRES=1 pytest -q tests/test_preview_queue_postgres.py'
)
```

A matriz FFmpeg usa somente amostras sintéticas. Ganhos, música, offset, fades
e clipping grosseiro são avaliados por RMS, frequências e picos do sinal; não
foi realizada escuta auditiva humana e isso não comprova inteligibilidade
subjetiva de fala. Quadros das prévias foram inspecionados visualmente. O QA de
navegador utiliza somente Chromium headless no container oficial Playwright;
Firefox, WebKit e dispositivos físicos não foram testados. Mobile e toque são
emulados. As evidências incluem reprodução real e recuperação do mesmo artefato
após reinício, com auditoria final de storage e preservação dos dados anteriores.

Os Dockerfiles copiam o código para a imagem. Após alterações, execute
`docker compose up -d --build`. O build garante instalação por `uv.lock` e
`package-lock.json`; não atualize locks incidentalmente.

Para desenvolvimento nativo, preserve `backend/.venv` se já for Python 3.12.
Com uv instalado fora do ambiente, execute `uv sync --frozen` dentro de `backend`, depois
`uv run pytest`, `uv run ruff check .` e `uv run ruff format --check .`.
Se uv foi instalado no próprio ambiente existente, use
`.venv/bin/uv sync --frozen --inexact` para preservar o gerenciador e o pip;
depois execute `.venv/bin/pytest` e `.venv/bin/ruff` diretamente.
No frontend, execute `npm ci`, `npm run test -- --run`, `npm run lint` e
`npm run build` dentro de `frontend`.

Para iniciar a API nativa, é preciso um PostgreSQL acessível nessa rede e um
`EDIVO_DATABASE_URL` correspondente. O hostname `db` é interno ao Docker;
o Compose padrão não publica o banco. Não abra uma porta pública para contornar
essa separação. FFmpeg/FFprobe também precisam existir no runtime nativo.

```bash
# Em backend/, após configurar ambiente nativo e uv sync --frozen
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
# Em outro terminal, no mesmo ambiente
.venv/bin/python -m app.worker.main
# Em frontend/, proxy padrão para 127.0.0.1:8000
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

## Diagnóstico FFmpeg

`capabilities` verifica versões, encoders `libx264`/AAC e filtros `scale`, `crop`,
`pad`, `drawtext`, com timeout e cache. O endpoint sempre retorna HTTP 200 com o
diagnóstico: ausências aparecem como `available=false` e motivo seguro. Readiness
mede banco e storage; a presença dos binários é informada separadamente.

Execute a mesma prova sintética de cerca de um segundo em ambos os runtimes:

```bash
docker compose exec -T api python - < scripts/ffmpeg-smoke.py
docker compose exec -T worker python - < scripts/ffmpeg-smoke.py
```

O script usa somente cor e seno gerados localmente, exercita os quatro filtros,
codifica H.264/AAC, inspeciona codecs/resolução/duração por FFprobe e verifica
decodificação. Os temporários são removidos ao finalizar. Não aceita mídia de
usuário e não é uma funcionalidade de renderização do produto.

## QA operacional

```bash
python3 scripts/acceptance-compose.py
python3 scripts/acceptance-r02.py
python3 scripts/acceptance-upload-limits.py
python3 scripts/acceptance-r03.py
python3 scripts/acceptance-editspec.py --output docs/r04/evidence/r03-edit-spec-regression.json
python3 scripts/generate-r04-fixtures.py
python3 scripts/acceptance-r04-mp3-security.py
python3 scripts/acceptance-r04-process-control.py
python3 scripts/acceptance-r04-previews.py --restart
docker compose exec -T api python - < scripts/audit-storage-r04.py
```

A matriz FFmpeg roda separadamente, com código/fixtures em leitura e diretório
novo para evidências (não reutilize um diretório histórico):

```bash
docker compose run --rm --no-deps -T \
  -v "$PWD/backend:/validation:ro" \
  -v "$PWD/scripts/acceptance-r04-ffmpeg.py:/qa/acceptance-r04-ffmpeg.py:ro" \
  -v /tmp/edivo-r04/fixtures:/fixtures:ro \
  -v "$PWD/docs/r04/evidence:/evidence" \
  -e PYTHONPATH=/validation -e PYTHONDONTWRITEBYTECODE=1 -w /validation \
  api python /qa/acceptance-r04-ffmpeg.py --fixtures /fixtures \
  --output /evidence/ffmpeg-matrix-local/report.json
```

O script de regressão valida a stack e executa cenários operacionais, inclusive
interrupção temporária do banco e reinício do worker. Execute quando não houver outra
validação usando os mesmos serviços. Consulte seu resultado e o relatório de
aceite; testes de UI com mocks não substituem a comunicação real pelo proxy.

O QA de navegador R04 usa Chromium do container oficial Playwright, separado
das dependências do produto. Com os serviços Edivo saudáveis:

```bash
npm install --prefix /tmp/edivo-browser playwright@1.58.2 --no-audit --no-fund
docker pull mcr.microsoft.com/playwright:v1.58.2-noble
python3 scripts/generate-r04-fixtures.py
python3 scripts/browser-r04.py --stage composition
# Após composição, a referência sintética pode ser reutilizada por --project-id.
python3 scripts/browser-r04.py --stage preview
# Após reiniciar somente os serviços Edivo quando a fila estiver vazia:
python3 scripts/browser-r04.py --stage verify
```

As amostras de aproximadamente doze segundos são geradas por FFmpeg no
runtime da API: H.264/AAC horizontal, vertical sem áudio, quadrada, VFR, SAR,
rotação e codec MPEG-4 Part 2 para testar incompatibilidade real do navegador.
Arquivos ficam em `/tmp`; não use mídia pessoal e não versione MP4 gerado.
O harness conserva seed entre etapas, registra respostas Range e eventos
nativos de mídia e copia JSON/capturas para `docs/r04/evidence/`. Seus containers
auxiliares não montam o socket Docker ou diretórios pessoais. Playwright é
ferramenta de QA externa, sem mudança em `frontend/package-lock.json`.
`acceptance-r02.py` exercita HTTP, integridade e persistência, reiniciando somente
serviços Edivo; `acceptance-upload-limits.py` usa um processo isolado com limites
menores e PostgreSQL/FFprobe reais. Eles criam projetos sintéticos, excluídos
logicamente ao terminar, e preservam os originais de QA. A auditoria apenas lê
registros/arquivos e confere hashes; execute sem uploads simultâneos. Exit 0
significa consistência, 1 exige revisão e 2 indica auditoria indisponível.
Nenhum desses comandos remove volumes ou arquivos preexistentes.

## Problemas comuns no Ubuntu/WSL2

- **Docker não encontrado ou integração desativada:** inicie o Docker Desktop
  e ative a distribuição em Settings → Resources → WSL Integration. Confira
  `docker info` e `docker compose version` no terminal WSL.
- **Acesso negado ao socket Docker:** confira a integração/permissões da
  distribuição. Não use `chmod 666` no socket nem monte esse socket no app.
- **Portas ocupadas:** identifique o processo com `ss -ltn`; pare o processo
  conflitante de forma controlada ou configure `EDIVO_API_PORT` e
  `EDIVO_FRONTEND_PORT` livres, mantendo loopback e ajustando CORS. As portas
  internas e `API_PROXY_TARGET=http://api:8000` permanecem iguais.
- **Storage indisponível:** execute o preparo com seu usuário e confira
  proprietário/UID/GID do diretório. Não aplique `chmod 777` indiscriminadamente.
- **API 503:** confira `checks.database` e `checks.storage` e os logs locais.
  `health` 200 com `ready` 503 é esperado durante indisponibilidade de dependência.
- **Worker encerra ao iniciar:** seu diagnóstico exige banco, storage e as
  capacidades FFmpeg necessárias; corrija a dependência e reinicie o serviço.
- **Interface offline:** confira API, proxy e `docker compose ps -a`; o tema e
  a mensagem de indisponibilidade continuam funcionando sem API.
- **Upload 413/422/415:** confira os limites, a extensão e o conteúdo MP4/MP3;
  renomear um arquivo não o converte. Duração acima do permitido gera 422.
- **Upload 503:** confira espaço/permissões do storage, banco e FFprobe.
  Não aumente timeouts ou limites sem avaliar recursos e o vídeo utilizado.
- **Upload interrompido ou sem confirmação:** recarregue a biblioteca antes de
  reenviar. A desconexão após o servidor receber o arquivo pode ocorrer quando
  ele já concluiu a importação; a R02 não implementa deduplicação automática.
- **Player indisponível:** confira API e acesso ao projeto; codec MP4 não
  suportado pelo navegador exige outra fonte compatível. A R03 não converte mídia.
- **Conflito ao salvar:** outra revisão chegou primeiro. As alterações locais
  continuam na tela; confirme o descarte ao recarregar a revisão atual. Não
  envie cegamente um `expected_revision` novo para sobrescrever o outro plano.
- **Prévia permanece na fila:** confira se o worker Edivo está ativo e seus logs;
  a API saudável não comprova execução do worker. Reinicie apenas serviços Edivo
  quando seguro e aguarde a recuperação pelo lease.
- **Prévia falhou:** consulte o código sanitizado do job e o espaço disponível.
  Confirme a mídia, a fonte tipográfica e os limites do runtime. Salve ajustes
  explicitamente e solicite outra prévia; originais e revisões são preservados.
- **Downloads falham:** confira rede/proxy e acesso aos registries. Não copie
  tokens para manifestos, Dockerfiles ou variáveis do frontend.
- **Watch lento no WSL2:** mantenha o repositório no filesystem Linux. O fluxo
  Docker copia código no build; o fluxo nativo usa o HMR do Vite.

## Limites atuais e próxima etapa

Esta implementação inclui projetos, upload MP4/MP3, metadados, streaming, player,
timeline não destrutiva, presets e EditSpec v1/v2 com textos e controles de áudio.
Geração local de prévias curtas foi validada em integração real e Chromium,
conforme o [aceite R04](docs/r04/acceptance.md).
Não possui exportação final, render completo, IA, heditor, Higgsfield,
autenticação ou recursos SaaS. Não há monitoramento HTTP
do worker. O servidor Vite e o Compose são destinados ao desenvolvimento local.

A próxima etapa prevista após o aceite R04 é **R05**, que exige
nova autorização explícita e não foi iniciada. Nenhuma operação de
gerenciamento de Git é executada pelos scripts do projeto.

Referências de compatibilidade: [Vite](https://vite.dev/guide/),
[ordem de startup do Compose](https://docs.docker.com/compose/how-tos/startup-order/)
e [filtros FFmpeg](https://ffmpeg.org/ffmpeg-filters.html).
