# CSRF Origin Check Specification

## Problem Statement

O painel é protegido só por Basic Auth no nginx. O navegador reenvia essas credenciais automaticamente, então um site malicioso aberto no mesmo navegador consegue disparar POSTs em `/api/jobs`, `/api/live`, `/api/discovery/run` e afins. O app não verifica a origem da requisição.

## Goals

- [ ] Nenhuma requisição de escrita vinda de outra origem chega a um handler.
- [ ] Painel, tela de LIVE e worker do Windows continuam funcionando sem alteração no frontend nem no `worker.ps1`.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Login/autenticação dentro do app | Mudança maior; nginx segue sendo a fronteira de autenticação |
| Token anti-CSRF em formulários | Checagem de Origin cobre o caso sem tocar no frontend |
| Rotação do token do worker / token fora do `worker.ps1` | Achado separado da avaliação; não é CSRF |
| Rate limiting | Não pedido |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Como comparar origem | `urlparse(Origin).netloc` deve ser igual ao header `Host` recebido pelo app | O nginx repassa `Host $host`; sem porta em ambos no deploy atual | n |
| Requisição sem `Origin` | Permitida, salvo se `Sec-Fetch-Site: cross-site` | Worker (curl/Invoke-RestMethod) não envia `Origin`; navegadores modernos sempre enviam `Origin` ou `Sec-Fetch-Site` em POST cross-site | n |
| Onde aplicar | Middleware HTTP único no `app.py` | Cobre todas as rotas atuais e futuras num ponto só | n |
| Código e mensagem de bloqueio | HTTP 403, JSON `{"detail": "Origem não permitida"}` | Mesmo formato de erro do FastAPI usado no resto da API | n |

**Open questions:** none - all resolved or logged above.

---

## User Stories

### P1: Bloquear escrita cross-origin ⭐ MVP

**User Story**: Como operador do painel, quero que requisições de escrita de outros sites sejam recusadas, para que uma aba maliciosa não crie jobs, lives ou varreduras em meu nome.

**Why P1**: É a vulnerabilidade explorável hoje.

**Acceptance Criteria**:

1. WHEN uma requisição POST, PUT, PATCH ou DELETE traz `Origin` cujo netloc difere do header `Host` THEN o sistema SHALL responder 403 com `{"detail": "Origem não permitida"}` sem executar o handler.
2. IF uma requisição POST, PUT, PATCH ou DELETE traz `Origin: null` THEN o sistema SHALL responder 403 com `{"detail": "Origem não permitida"}`.
3. IF uma requisição POST, PUT, PATCH ou DELETE não traz `Origin` e traz `Sec-Fetch-Site: cross-site` THEN o sistema SHALL responder 403 com `{"detail": "Origem não permitida"}`.
4. The system SHALL não aplicar a checagem a GET, HEAD e OPTIONS.

**Independent Test**: `TestClient` faz POST em `/api/discovery/run` com `Origin: https://evil.example` e recebe 403; o handler não é chamado.

---

### P1: Clientes legítimos seguem funcionando ⭐ MVP

**User Story**: Como operador, quero que o painel, a tela de LIVE e o worker continuem funcionando sem mudanças.

**Why P1**: Um bloqueio que quebra o próprio painel não pode ir para produção.

**Acceptance Criteria**:

1. WHEN uma requisição de escrita traz `Origin` cujo netloc é igual ao header `Host` THEN o sistema SHALL repassá-la ao handler normalmente.
2. WHEN uma requisição de escrita não traz `Origin` nem `Sec-Fetch-Site` THEN o sistema SHALL repassá-la ao handler (a autenticação Bearer das rotas de worker continua valendo).

**Independent Test**: POST em `/api/jobs/{id}/retry` com `Origin` igual ao `Host` retorna o mesmo status de antes; POST do worker sem `Origin` com Bearer válido retorna 202.

---

## Edge Cases

- WHEN `Origin` difere do `Host` só na porta THEN o sistema SHALL responder 403.
- WHEN `Sec-Fetch-Site` é `same-origin` ou `none` e não há `Origin` THEN o sistema SHALL repassar a requisição.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| CSRF-01 | P1: Bloquear escrita cross-origin (AC1, borda porta) | Execute | Verified |
| CSRF-02 | P1: Bloquear escrita cross-origin (AC2) | Execute | Verified |
| CSRF-03 | P1: Bloquear escrita cross-origin (AC3) | Execute | Verified |
| CSRF-04 | P1: Bloquear escrita cross-origin (AC4) | Execute | Verified |
| CSRF-05 | P1: Clientes legítimos (AC1) | Execute | Verified |
| CSRF-06 | P1: Clientes legítimos (AC2, borda Sec-Fetch-Site) | Execute | Verified |

**Coverage:** 6 total, 0 mapped to tasks (Tasks pulado: escopo Medium, 1 arquivo + 1 teste).

---

## Success Criteria

- [ ] Os 6 requisitos têm teste em `tests/test_csrf.py` passando.
- [ ] Suíte existente continua verde.
