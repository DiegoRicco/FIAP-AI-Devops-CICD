# Runbook — Quantum Commerce AI Customer Assistant (Demo 5.2, ambiente `local-demo`)

> **Escopo:** runtime local da Demo 5.2 (`adapters/http_server.py` na porta 8080, `adapters/orders_service.py` na 8081, stack `ops/docker-compose.yml`). Não é a Lambda publicada na 5.1: lá a investigação usa CloudWatch Logs filtrando por `release_id`.
> **Comandos:** rode os scripts de `ops/` pelo wrapper `ops/run` (usa o venv do bootstrap e liga a telemetria). Sem ele, o script avisa `telemetria DESLIGADA` e os probes não chegam ao Grafana.
> **Sinais de qualidade são proxy sintético:** vêm de probes rotulados (`ops/simulator.py`), não de tráfego humano.

## Telas e consultas

| O quê | Onde |
|---|---|
| Painel técnico × qualidade | Grafana `http://127.0.0.1:3000` → "Quantum Commerce - Demo 5.2 - Tecnico x Qualidade" |
| Alertas | Prometheus `http://127.0.0.1:9090/alerts` (sem Alertmanager: nada é enviado para fora) |
| Traces | Jaeger `http://127.0.0.1:16686` → serviço `quantum-assistant` |
| Versões do que está atendendo | `curl -s http://127.0.0.1:8080/health` |
| Alerta local e janelas | `ops/run quality_monitor.py --prometheus http://127.0.0.1:9090` → `ops/alerts/<ts>.json` |

## Passos comuns a qualquer alerta

1. **Confirme o impacto.** Qual sinal mudou: o técnico (5xx, p95, falha de pedidos) ou o de qualidade (pass rate, fonte vigente)? Os dois juntos contam outra história.
2. **Capture a identidade.** Anote `release_id`, `corpus_version` e `index_version` do `/health` e do alerta. Mesmo `release_id` com outro `corpus_version` significa que o conteúdo mudou sem deploy de código.
3. **Escolha um probe que falhou.** O alerta local traz `sample_trace_ids`; o JSONL em `ops/results/` traz `trace_id`, `case_id` e `reason`.
4. **Abra o trace no Jaeger** (ou pela API: `curl -s http://127.0.0.1:16686/api/v3/traces/<trace_id>`). Leia `rag.filter_documents` (quantos trechos passaram e quantos foram bloqueados por `status`, `expired`, `tenant` e `access_level`), `rag.retrieve` (`top_doc_id`, `top_score`) e `order_api.lookup` → `orders.get` (`http.response.status_code`, `fault.fate`).
5. **Mitigue a causa, não o sintoma.** Use a seção do alerta abaixo.
6. **Prove a recuperação** repetindo o MESMO cenário com a MESMA seed e rodando o monitor de novo.

## QualityProbeDegraded

`synthetic_quality_pass_rate < 0.90` com `n >= 20` na janela de 2 min. Severidade **warning**, time `ai-quality`.

- **Hipótese que parece certa e está errada:** "o serviço está estranho, vamos reiniciar". O `/health` responde 200, o p95 está normal e nenhum código mudou. Reiniciar recarrega o **mesmo** corpus e o **mesmo** índice, e o pass rate continua baixo. É uma ação placebo: consome tempo e não toca na causa.
- **O que fazer:** olhe `fail_reasons` no alerta local. `fonte_obsoleta` leva para a seção StaleSourceServed. `campos_esperados` ou `rota` sem `fonte_obsoleta` apontam para conteúdo ou configuração alterados: compare `index_version` com o do último baseline verde.

## StaleSourceServed

Qualquer probe citou uma política não vigente. Severidade **critical**: não há média que compense informar ao cliente o prazo errado.

1. No trace de um probe com falha, `rag.filter_documents` mostra `blocked_status` **menor** que no baseline, porque o documento obsoleto deixou de ser filtrado. Em `rag.retrieve`, o `top_doc_id` é a política certa, mas o JSONL mostra `cited: ["politica-trocas@v2"]`.
2. Confira o corpus ativo: `ops/run inject.py --status`. Se aparecer `corpus_source: overlay`, há uma mudança de conteúdo fora do repositório.
3. **Mitigação:** reindexar a partir do corpus versionado. Não reinicie o processo.
   ```bash
   ops/run restore.py           # remove o overlay, chama POST /admin/reload e confere o corpus_version
   ```
   Em produção, o equivalente é o **rollback do corpus e do índice** para a versão do último release aprovado, ou seja, outro artefato além do código.
4. **Recuperação:** `ops/run simulator.py --seed 42 --scenario stale_policy --rate 2 --duration 25` seguido de `ops/run quality_monitor.py` precisa voltar para `OK`, com `obsoletas=0`.

## OrderApiDegraded

`transaction_lookup_failure_rate > 0.20` com `n >= 10` consultas de pedido em 2 min. Severidade **warning**, time `sre`. É um sinal **técnico**, de dependência.

- O assistente **não inventa status**: devolve `abstain_reason=order_api_unavailable` (fallback honesto). Os probes de pedido aparecem como `fallback` e ficam fora do pass rate de qualidade. Por isso este alerta é **distinto** do alerta de qualidade documental.
- No trace, `order_api.lookup` mostra as tentativas (retry com backoff só para 5xx e timeout) e `orders.get` mostra `fault.fate=error|timeout`.
- **Mitigação local:** `ops/run restore.py` zera os faults. Em produção, as opções seriam circuit breaker, aumento de capacidade da API de pedidos ou acionar o time dono dela. Reindexar o corpus aqui seria a ação errada.

## QualityAclViolation

Um documento de outro tenant ou interno foi recuperado ou citado para um cliente. Severidade **critical**. Congele mudanças de corpus e de configuração, verifique `filters.enforce_tenant` e `filters.enforce_access_level` em `config/settings.json` e o `tenant`/`access_level` no front matter do documento.

## QualityNoData

Nenhum probe nos últimos 5 minutos. **Ausência de dado não é sucesso:** um painel verde sem amostras é um painel mudo. Verifique se o simulador está rodando e se o Collector recebe dados (`docker compose -f ops/docker-compose.yml logs otel-collector`).

## Depois do incidente

Preencha `ops/postmortem-template.md` e **promova o caso que falhou a teste de regressão no pipeline da 5.1**: um novo caso em `evals/golden_v1.jsonl` (ou um ajuste em `must_not_cite`) faz o próximo release ser barrado no gate antes de chegar à produção.
