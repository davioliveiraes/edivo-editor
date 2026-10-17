# ADR — consolidação das decisões executáveis R01

**Origem:** decisões aprovadas em R00 e fechadas na especificação operacional
R01. Esta consolidação não altera os documentos de origem.

- Monólito modular Python 3.12, FastAPI/Pydantic v2 e SQLAlchemy 2 síncrono
  com psycopg 3; API e worker em processos separados na mesma imagem.
- PostgreSQL 16, volume persistente e Alembic baseline sem tabelas de negócio.
  Migrações coordenadas por serviço único `migrate`, sem concorrência no startup.
- React/TypeScript strict/Vite, Node 24, Tailwind e componentes shadcn usados
  de forma mínima. Proxy `/api` evita configurar URLs internas no browser.
- Dependências Python travadas com uv; JavaScript com npm. Imagens usam tags
  explícitas; digests efetivamente executados são evidências do aceite, sem
  assumir imutabilidade de tags de distribuição.
- Storage local compartilhado, privado e gerenciado pelo usuário local; banco
  sem porta no host, frontend/API somente em loopback.
- Worker ocioso, diagnóstico de capacidades real e cacheado; nenhum render,
  job, domínio ou adaptador especulativo nesta etapa.

Essas escolhas priorizam a reprodução local e a separação das responsabilidades.
Fila PostgreSQL, imutabilidade das revisões e integrações externas continuam
decisões de evolução da R00, sem implementação antecipada.
