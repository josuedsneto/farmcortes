# Farmcortes — State

## Decisions

### AD-001
- **Decision**: O projeto passa a ser um repositório git local; o primeiro commit é o código atual, antes de qualquer feature.
- **Reason**: Sem histórico não há como reverter nem fazer commits atômicos por tarefa (item 1 da avaliação, escopo Small, executado inline).
- **Trade-off**: Nenhum; push para remoto exige autorização à parte.
- **Scope**: Projeto inteiro.
- **Date**: 2026-11-22
- **Status**: active

### AD-002
- **Decision**: `dados-iniciais/farmcortes.sqlite3` não é versionado (entra no `.gitignore`); continua no pacote de deploy.
- **Reason**: É o banco de produção (URLs, jobs, métricas); não pertence ao histórico do código.
- **Trade-off**: Quem clonar o repositório começa com banco vazio.
- **Scope**: repo-bootstrap.
- **Date**: 2026-11-22
- **Status**: active

### AD-003
- **Decision**: Toda publicação no YouTube passa exclusivamente por `review.publish_approved` (aprovação no Telegram); as chamadas diretas a `publish_file` em pipeline, original, compilations, source_compilations e backfill são removidas, não roteadas.
- **Reason**: Elas já falham sempre por causa do gate `_approval`; a política vigente (drop-in `pause-publish.conf`) é "nada publica sem aprovação no Telegram". Remover é o menor diff.
- **Trade-off**: Compilações e Shorts originais deixam de ter caminho de publicação até existir uma feature que os enfileire em `review_queue`.
- **Scope**: publish-gate-cleanup.
- **Date**: 2026-11-22
- **Status**: active
