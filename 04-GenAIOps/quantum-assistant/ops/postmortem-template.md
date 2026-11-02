# Postmortem (blameless) — <título curto do incidente>

> Sem culpados: o objetivo é entender como o sistema permitiu a falha, e não quem errou. Escreva fatos com horário e evidência (trace, JSONL, consulta).
> Ambiente: `local-demo` (Demo 5.2) | Sinais de qualidade: proxy sintético (probes rotulados).

## 1. Resumo

| Campo | Valor |
|---|---|
| Data e duração | <início> → <fim> (<min>) |
| Severidade | warning / critical |
| Alerta que detectou | `QualityProbeDegraded` / `StaleSourceServed` / `OrderApiDegraded` / `QualityNoData` |
| `release_id` | <do /health> |
| `corpus_version` antes → durante → depois | <...> → <...> → <...> |
| `index_version` antes → durante → depois | <...> → <...> → <...> |
| Impacto simulado | <n probes com falha / n total, slices afetados> |

## 2. Linha do tempo

| Hora | Evento | Evidência |
|---|---|---|
| hh:mm | Mudança de conteúdo/dependência aplicada | `ops/inject.py` / commit / `/admin/faults` |
| hh:mm | Primeiro probe com falha | `trace_id` do JSONL |
| hh:mm | Alerta FIRING | `http://127.0.0.1:9090/alerts` + `ops/alerts/<ts>.json` |
| hh:mm | Hipótese inicial | (ver seção 4) |
| hh:mm | Mitigação aplicada | `ops/run restore.py` / rollback do corpus |
| hh:mm | Recuperação confirmada | replay com a mesma seed + monitor `OK` |

## 3. Detecção

- Que sinal disparou primeiro: técnico ou de qualidade? O `/health` e o p95 estavam verdes?
- Quanto tempo se passou entre a mudança e o alerta? A janela (2 min) e o n mínimo (20) ajudaram ou atrasaram?

## 4. Hipóteses e decisão

| Hipótese | Por que parecia plausível | Evidência que a confirmou ou descartou | Decisão |
|---|---|---|---|
| Reiniciar o serviço | "algo travou" | `/health` 200, p95 normal, nenhum deploy de código; reiniciar recarrega o mesmo corpus | **Descartada** (ação placebo) |
| Conteúdo/índice mudou | `corpus_version` diferente do baseline com o mesmo `release_id` | trace: `rag.filter_documents` deixou de bloquear a política obsoleta; JSONL: `cited` com `@v2` | **Confirmada**: reindexar ou fazer rollback do corpus |

## 5. Causa raiz

<o que mudou, por que nenhum gate impediu, por que o sinal técnico não viu>

## 6. Mitigação e recuperação

- Ação: <reindexação/rollback do corpus | reset dos faults | circuit breaker>
- Artefato revertido: <corpus/índice (não o código)>
- Prova: replay `--seed <n> --scenario <x>` → `pass_rate` <antes> → <depois>, `obsoletas` <antes> → 0.

## 7. Ações preventivas

| Ação | Tipo | Dono | Prazo |
|---|---|---|---|
| Promover `<case_id>` a caso do golden set (ou `must_not_cite`) para o gate da 5.1 barrar a regressão | detecção mais cedo | ai-quality | <data> |
| <ex.: mudança de corpus passa pelo pipeline como qualquer release> | processo | <time> | <data> |

## 8. O que aprendemos

- <o que funcionou bem, o que foi sorte, o que fica mais fácil da próxima vez>
