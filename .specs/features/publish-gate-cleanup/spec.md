# Publish Gate Cleanup Specification

## Problem Statement

`youtube.publish_file` exige o sentinela `_REVIEW_AUTHORIZATION`, que só `review.publish_approved` passa. Mesmo assim, pipeline, original, compilations, source_compilations e backfill chamam `publish_file` diretamente: essas chamadas sempre falham, marcam a publicação do job como `failed` e o README descreve uma publicação automática que não existe. Decisão AD-003: remover essas chamadas.

## Goals

- [ ] `review.publish_approved` é o único chamador de `publish_file` no pacote.
- [ ] Nenhum job, Short original ou compilação termina com publicação `failed` por causa do gate.
- [ ] README e units do systemd refletem o fluxo real.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Enfileirar Shorts/compilações em `review_queue` | AD-003: feature futura; exige migrar o schema de `review_queue` |
| Remover a coluna `auto_publish` ou `publication_json` | Mudança de schema sem ganho; dados antigos continuam legíveis |
| Mudar `review.py`, `telegram.py` ou `youtube.publish_file` | Caminho aprovado continua igual |
| Remover os drop-ins `pause-publish.conf` | Decisão operacional do servidor |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Remover ou rotear | Remover | Escolhido pelo usuário em 2026-11-22 | y |
| `backfill.py` | Apagar o módulo inteiro | Sua única função é publicar/agendar cortes já renderizados | n |
| `youtube.publish()` (função por job) | Apagar; manter `publish_file`, `settings`, `metadata_for`, `schedule_slots` e helpers usados por outros módulos | Sem chamadores após a remoção | n |
| Detecção de "fonte já publicada" em `_finish` | Apagar | Só servia para evitar publicação duplicada | n |
| Flag `--publish` nos CLIs | Remover; passar a flag passa a ser erro de argumento (exit 2) | Uma flag que nunca funciona engana o operador | n |
| Estágio final de um job concluído | `"Cortes prontos para revisão"` | Reflete o fluxo real; texto curto no padrão atual | n |
| Status do manifesto de Short original | Sempre `"rendered"` | Não há mais caminho `published` nesse módulo | n |
| Jobs antigos com `publication` gravada | Mantidos como estão; UI continua exibindo | Sem migração de dados | n |

**Open questions:** none - all resolved or logged above.

---

## User Stories

### P1: Jobs de corte terminam sem tentar publicar ⭐ MVP

**User Story**: Como operador, quero que um job concluído fique pronto para revisão em vez de exibir uma falha de publicação que nunca poderia dar certo.

**Why P1**: Hoje todo job com YouTube habilitado termina com `publication.status = failed`.

**Acceptance Criteria**:

1. WHEN um job de corte termina de renderizar THEN o sistema SHALL gravar `status="done"`, `progress=100`, `stage="Cortes prontos para revisão"` e `publication={}`.
2. WHILE `FARMCORTES_YOUTUBE_ENABLED=true` o sistema SHALL concluir o job sem chamar `publish_file` nem a API do YouTube.
3. WHEN um job é recuperado de cortes já renderizados THEN o sistema SHALL aplicar o mesmo estado final do AC1.

**Independent Test**: com `publish_file` substituído por um mock que falha o teste se chamado e YouTube habilitado, `Processor.process` termina com o estado do AC1.

---

### P1: Um único caminho de publicação ⭐ MVP

**User Story**: Como operador, quero que só a fila de revisão publique, para que a regra "nada publica sem aprovação" seja garantida pelo código.

**Why P1**: É a política vigente.

**Acceptance Criteria**:

1. The system SHALL conter exatamente um ponto de chamada de `publish_file` no pacote `farmcortes`, em `review.publish_approved`.
2. The system SHALL não conter o módulo `farmcortes/backfill.py` nem a função `youtube.publish`.
3. IF `original`, `compilations` ou `source_compilations` for executado com `--publish` THEN o sistema SHALL sair com código 2 (argumento desconhecido).
4. WHEN `original.generate` termina THEN o sistema SHALL gravar o manifesto com `status="rendered"` e `publication={}`.
5. The system SHALL não passar `--publish` em nenhuma unit de `deploy/` nem de `deploy/producao-atual/`.

**Independent Test**: `grep` acha um único `publish_file(` em `farmcortes/`; `python -m farmcortes.original --publish` sai com 2.

---

### P2: Documentação fiel

**User Story**: Como quem faz o deploy, quero que o README descreva o fluxo real de publicação.

**Why P2**: Não afeta o comportamento, mas hoje induz a erro.

**Acceptance Criteria**:

1. The system SHALL ter README sem instruções de publicação automática nem exemplos com `--publish`, e com uma seção dizendo que a publicação ocorre só após aprovação na fila de revisão (`review.publish_approved` / Telegram).

**Independent Test**: `grep -n "\-\-publish\|agendados nos próximos horários" README.md` não retorna nada.

---

## Edge Cases

- IF `compilations.run_once` encontra um manifesto antigo com `publication` preenchida THEN o sistema SHALL mantê-lo intacto e não tentar publicar.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| PUB-01 | P1: Jobs sem publicar (AC1) | Execute | Implementing |
| PUB-02 | P1: Jobs sem publicar (AC2) | Execute | Implementing |
| PUB-03 | P1: Jobs sem publicar (AC3) | Execute | Implementing |
| PUB-04 | P1: Único caminho (AC1) | Execute | Pending |
| PUB-05 | P1: Único caminho (AC2) | Execute | Implementing |
| PUB-06 | P1: Único caminho (AC3) | Execute | Pending |
| PUB-07 | P1: Único caminho (AC4) | Execute | Pending |
| PUB-08 | P1: Único caminho (AC5) | Execute | Pending |
| PUB-09 | P2: Documentação (AC1) | Execute | Pending |
| PUB-10 | Edge: manifesto antigo | Execute | Pending |

**Coverage:** 10 total; `tasks.md` será criado no Execute se a listagem passar de 5 passos.

---

## Success Criteria

- [ ] Zero ocorrências de publicação `failed` causadas pelo gate em jobs novos.
- [ ] Suíte existente verde, sem testes enfraquecidos.
