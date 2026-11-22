# Dead Code Cleanup Specification

## Problem Statement

`farmcortes/longcuts.py` e `farmcortes/scan.py` são stubs que só levantam `NotImplementedError` e não são importados por ninguém. A conversão de segmentos do faster-whisper em legendas está duplicada em `pipeline.transcribe` e `live.LiveTranscriber.__call__`; qualquer ajuste precisa ser feito duas vezes.

## Goals

- [ ] Nenhum módulo stub sem uso no pacote.
- [ ] Uma única implementação da conversão segmentos → legendas, com o comportamento atual preservado.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| `livecut.py` e `titles.py` | Estão em uso (pipeline GTA de Twitch/Kick → fila de revisão); a avaliação anterior os classificou como mortos por engano |
| Unificar `livecut.make_subtitles` | Gera karaokê palavra a palavra com estilo próprio; é outro formato |
| Unificar o carregamento do modelo Whisper | Batch e live têm políticas diferentes (download permitido vs. `local_files_only`, cache de modelo) |
| Reformatar estilo de código | Não pedido |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Onde fica a função compartilhada | `pipeline.captions_from_segments(segments) -> list[Caption]` | `live.py` já importa de `pipeline`; `Caption` e `apply_caption_glossary` moram lá | n |
| Regra de agrupamento | A atual: fecha o grupo quando a duração do grupo chega a 3,3 s ou a palavra termina em `.`, `!` ou `?`; segmento sem palavras vira uma legenda com o texto do segmento; glossário aplicado em cada legenda | As duas cópias já são equivalentes; é refatoração sem mudança de comportamento | n |
| Confiança de idioma abaixo de 0,55 | Permanece fora da função: batch levanta `PipelineError`, live retorna `[]` | Comportamentos diferentes de propósito | n |
| Segmento sem palavras e com texto vazio | Ignorado nos dois caminhos | Hoje o batch ignora e o live grava legenda vazia; legenda vazia não tem valor | n |

**Open questions:** none - all resolved or logged above.

---

## User Stories

### P1: Remover stubs sem uso ⭐ MVP

**User Story**: Como mantenedor, quero que o pacote só tenha módulos que fazem algo.

**Why P1**: Menor superfície de leitura e de manutenção.

**Acceptance Criteria**:

1. The system SHALL não conter `farmcortes/longcuts.py` nem `farmcortes/scan.py`.
2. The system SHALL não ter nenhuma referência a `longcuts` ou `scan` como módulo em `farmcortes/`, `tests/`, `deploy/` ou `docs/`.

**Independent Test**: `grep -rn "longcuts\|farmcortes.scan\|from .scan"` não retorna nada e a suíte passa.

---

### P1: Conversão de legendas única ⭐ MVP

**User Story**: Como mantenedor, quero uma só implementação da conversão de segmentos em legendas, para que um ajuste valha para batch e live.

**Why P1**: É a duplicação mais arriscada do código.

**Acceptance Criteria**:

1. The system SHALL implementar a conversão segmentos → `Caption` somente em `pipeline.captions_from_segments`, usada por `pipeline.transcribe` e `LiveTranscriber.__call__`.
2. WHEN as palavras `["Oi", " tudo", " bem."]` com tempos 0,0–0,4 / 0,4–0,8 / 0,8–1,2 são convertidas THEN o sistema SHALL produzir uma única legenda `Caption(0.0, 1.2, "Oi tudo bem.")`.
3. WHEN um grupo de palavras sem pontuação atinge 3,3 s do início do grupo THEN o sistema SHALL fechar a legenda nessa palavra e começar outra na palavra seguinte.
4. WHEN um segmento não tem palavras e tem texto `" Olá "` THEN o sistema SHALL produzir `Caption(segment.start, segment.end, "Olá")`.
5. IF um segmento não tem palavras e seu texto está vazio THEN o sistema SHALL não produzir legenda para ele.
6. WHEN o texto de uma legenda contém um termo do `caption-glossary.json` THEN o sistema SHALL aplicar a substituição do glossário.

**Independent Test**: teste unitário de `captions_from_segments` com segmentos falsos cobrindo AC2–AC6; os testes atuais de `LiveTranscriber` continuam passando.

---

## Edge Cases

- WHEN a lista de segmentos é vazia THEN o sistema SHALL retornar lista vazia.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| DEAD-01 | P1: Remover stubs (AC1) | Execute | Pending |
| DEAD-02 | P1: Remover stubs (AC2) | Execute | Pending |
| DEAD-03 | P1: Conversão única (AC1) | Execute | Pending |
| DEAD-04 | P1: Conversão única (AC2) | Execute | Pending |
| DEAD-05 | P1: Conversão única (AC3) | Execute | Pending |
| DEAD-06 | P1: Conversão única (AC4) | Execute | Pending |
| DEAD-07 | P1: Conversão única (AC5) | Execute | Pending |
| DEAD-08 | P1: Conversão única (AC6) | Execute | Pending |
| DEAD-09 | Edge: lista vazia | Execute | Pending |

**Coverage:** 9 total; Tasks pulado (3 arquivos de código + 1 teste).

---

## Success Criteria

- [ ] Duas cópias da lógica viram uma; suíte existente verde.
