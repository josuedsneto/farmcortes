# Worker Binary Pinning Specification

## Problem Statement

O `worker.ps1` roda no PC do operador e baixa `yt-dlp.exe` e o Deno a partir de `releases/latest`, validando só os bytes "MZ" e um tamanho mínimo. Uma release comprometida ou um download adulterado seria executado no PC sem nenhuma checagem. Versão e conteúdo precisam ser fixos e verificados.

## Goals

- [ ] O worker só executa binários cujo SHA-256 bate com um valor fixo no script.
- [ ] A versão do yt-dlp no worker é a mesma do `requirements.txt`.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Verificação de assinatura (GPG/Authenticode) | SHA-256 fixo resolve o risco com o menor diff |
| Atualização automática de versão | Atualizar passa a ser uma edição consciente do script |
| Token do worker embutido no script | Achado separado |
| Mudanças no servidor (`app.py`) | O template é servido sem alteração de rota |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Versão do yt-dlp | `2026.08.19` (mesma de `requirements.txt`) | Servidor e worker usam o mesmo extrator | n |
| Versão do Deno | A última estável publicada há pelo menos 14 dias na data do Execute | Evita uma release recém-saída | n |
| Origem dos hashes | Baixados no Execute e conferidos com `SHA2-256SUMS` (yt-dlp) e `.sha256sum` (Deno) publicados nas releases | Hash conferido em fonte independente do binário | n |
| O que se fixa no Deno | SHA-256 do `.zip` baixado | É o artefato publicado com checksum oficial | n |
| Binário já instalado | Reaproveitado se `--version` informar a versão fixada; senão é baixado de novo | Evita baixar 100 MB toda vez sem exigir um segundo hash | n |
| Mensagem de falha | `"Hash do yt-dlp nao confere"` / `"Hash do Deno nao confere"` (sem acento, padrão atual do script) | O script já evita acentos por causa da codificação do console | n |

**Open questions:** none - all resolved or logged above.

---

## User Stories

### P1: Downloads fixos e verificados ⭐ MVP

**User Story**: Como operador que roda o worker no próprio PC, quero que ele só execute binários verificados, para que um download adulterado não rode na minha máquina.

**Why P1**: É o risco de cadeia de suprimentos apontado na avaliação.

**Acceptance Criteria**:

1. The system SHALL baixar o yt-dlp de `https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe` e o Deno de uma URL `releases/download/v<versão fixa>/`, sem nenhuma URL `latest`.
2. WHEN um download termina THEN o sistema SHALL calcular o SHA-256 do arquivo baixado e compará-lo à constante fixa no script antes de qualquer execução ou extração.
3. IF o SHA-256 do yt-dlp não confere THEN o sistema SHALL apagar o arquivo baixado e lançar `"Hash do yt-dlp nao confere"` sem executá-lo.
4. IF o SHA-256 do zip do Deno não confere THEN o sistema SHALL apagar o zip e lançar `"Hash do Deno nao confere"` sem extraí-lo.

**Independent Test**: com a constante de hash alterada para um valor errado, rodar o bloco de instalação num diretório limpo lança a mensagem do AC3 e não deixa `yt-dlp.exe` no diretório.

---

### P1: Versão instalada coerente ⭐ MVP

**User Story**: Como operador, quero que um binário antigo já instalado seja trocado pela versão fixada.

**Why P1**: Sem isso, PCs com um `latest` antigo nunca recebem a versão verificada.

**Acceptance Criteria**:

1. WHEN o yt-dlp instalado informa em `--version` a versão fixada THEN o sistema SHALL reaproveitá-lo sem baixar.
2. IF o yt-dlp instalado informa outra versão ou não inicia THEN o sistema SHALL apagá-lo e seguir o fluxo de download verificado.
3. IF o Deno instalado não informa a versão fixada em `deno --version` THEN o sistema SHALL apagá-lo e seguir o fluxo de download verificado.

**Independent Test**: um `yt-dlp.exe` falso que imprime `2025.01.01` é substituído; um que imprime `2026.08.19` é mantido.

---

## Edge Cases

- IF o download falha (curl com código diferente de 0) THEN o sistema SHALL apagar o arquivo parcial e lançar o erro atual (`"Falha ao baixar o ... oficial"`).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| BIN-01 | P1: Downloads fixos (AC1) | Execute | Pending |
| BIN-02 | P1: Downloads fixos (AC2) | Execute | Pending |
| BIN-03 | P1: Downloads fixos (AC3) | Execute | Pending |
| BIN-04 | P1: Downloads fixos (AC4) | Execute | Pending |
| BIN-05 | P1: Versão instalada (AC1) | Execute | Pending |
| BIN-06 | P1: Versão instalada (AC2) | Execute | Pending |
| BIN-07 | P1: Versão instalada (AC3) | Execute | Pending |
| BIN-08 | Edge: download falho | Execute | Pending |

**Coverage:** 8 total; Tasks pulado (um arquivo).

---

## Success Criteria

- [ ] `grep -n latest farmcortes/worker.ps1` não retorna nada.
- [ ] Teste estático em `tests/test_worker.py` confirma URLs fixas e constantes SHA-256 de 64 hex; teste manual no Windows confirma os ACs de falha.
