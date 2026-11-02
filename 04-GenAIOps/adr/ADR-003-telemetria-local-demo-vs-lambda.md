# ADR-003 — Telemetria: OpenTelemetry no runtime local da demo, fachada no-op na Lambda

**Status:** aceito (Aula 5, módulo 04).
**Decide:** onde o OpenTelemetry roda de verdade, o que a Lambda publicada exporta e como o core continua sem dependências.

## Contexto

A Demo 5.2 precisa de **traces distribuídos reais** (assistente → API de pedidos), métricas de qualidade em janela e logs correlacionados por `trace_id`, visíveis em Jaeger, Prometheus e Grafana. A Demo 5.1 publica o assistente numa Lambda no Learner Lab.

Restrições verificadas nesta conta: **X-Ray, Application Signals, Synthetics, RUM e Evidently estão bloqueados**. Funcionam CloudWatch Logs (inclusive formato EMF), alarmes, SNS e EventBridge Scheduler. Exportar OTLP da Lambda exigiria um Collector acessível pela internet (custo, exposição e um IP a cuidar), ou uma Lambda Layer com SDK que não podemos validar com o X-Ray bloqueado.

Ao mesmo tempo, o core precisa rodar **sem o pacote `opentelemetry` instalado** no runner (Python 3.10, `/opt/venv` enxuto) e na Lambda (bundle pequeno, sem camadas).

## Decisão

1. O core fala com a telemetria por uma **fachada** (`core/telemetry.py`) com nomes fixos de etapas: `route_intent`, `rag.retrieve`, `rag.filter_documents`, `rag.compose_deterministic`, `order_api.lookup`. O adapter abre `http.request`; os probes abrem `quality.evaluate`; o deploy abre `release.verify`.
2. A fachada só ativa o **SDK OpenTelemetry quando `OTEL_EXPORTER_OTLP_ENDPOINT` existe e o pacote está instalado**; caso contrário é **no-op** e o comportamento do assistente não muda em nada. É isso que mantém o core stdlib.
3. **Onde o OTel roda de verdade:** no **runtime local da Demo 5.2** (`adapters/http_server.py` na porta 8080 + `adapters/orders_service.py` na 8081, no Codespaces), com `requirements-ops.txt` pinado e o stack `ops/docker-compose.yml` (OTel Collector contrib → Jaeger + Prometheus + Grafana, versões pinadas, limite de memória, Grafana anônimo somente leitura em `localhost`). O `traceparent` W3C é propagado entre os dois serviços: o trace distribuído é real.
4. **O que a Lambda publicada faz:** usa a mesma fachada em modo no-op para spans e grava **logs JSON em CloudWatch Logs** com `request_id`, `trace_id` (gerado localmente), versões e `release_id`, sem PII. É o suficiente para investigar um release na AWS; **não é tracing OTel da Lambda em produção, e nenhum material diz que é**.
5. Métricas (`requests_total`, `request_latency_ms`, `retrieval_hit_at_k`, `retrieval_mrr`, `citation_presence_rate`, `abstention_rate`, `transaction_lookup_failure_rate`, `synthetic_quality_pass_rate`, `source_is_current_rate`, `quality_probe_latency_ms`) usam labels de **baixa cardinalidade** (`route`, `environment`, `release_id`, `corpus_version`, `scenario`, `slice`). `order_id` e `trace_id` **nunca** viram label.
6. Sem LLM, atributos `gen_ai.*` não são usados (ou aparecem anotados como N/A). Métricas vindas de probes são rotuladas **"proxy sintético"**.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Exportar OTLP direto da Lambda para um Collector público | Precisa de endpoint exposto na internet, custo e segurança a explicar; sem X-Ray, não há backend gerenciado para validar. |
| X-Ray / Application Signals / ADOT Layer | Bloqueados na conta do Learner Lab (verificado). |
| Só CloudWatch Logs, sem OTel em lugar nenhum | Perderia o objetivo da 5.2: ver um trace distribuído, spans por etapa e métricas de qualidade lado a lado com as técnicas. |
| OTel obrigatório no core (import direto) | Quebraria a Lambda e o runner, que não têm o pacote; obrigaria dependência onde não há benefício. |

## Consequências

- A 5.2 tem **bootstrap independente** (`ops/bootstrap-demo52.sh`): sobe o stack, constrói o índice do repositório e mostra as versões; com `--from-live URL`, compara com o release publicado na 5.1. Funciona mesmo que a 5.1 não tenha rodado naquele dia.
- O que o aluno observa no Jaeger é a **instância que atendeu as requisições da demo** (o runtime local). Os READMEs rotulam "ambiente de demonstração local" em todo gráfico e print.
- Na AWS, o diagnóstico de um release fica em CloudWatch Logs (JSON, filtrável por `release_id`); isso é dito como limite, não escondido.
- O mesmo `release_id` e as mesmas versões aparecem nos dois ambientes, o que permite comparar "o que foi publicado" com "o que estou observando".
- Qualquer componente de telemetria que não exista no modo no-op (ex.: span sem SDK) **não aparece como funcionando** em material nenhum.

## Referências

- [OpenTelemetry Python: instrumentação](https://opentelemetry.io/docs/languages/python/instrumentation/).
- [OpenTelemetry Collector: configuração](https://opentelemetry.io/docs/collector/configuration/).
- [Convenções semânticas do OpenTelemetry](https://opentelemetry.io/docs/concepts/semantic-conventions/) e [GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (experimentais; não usadas sem LLM).
- *The DevOps Handbook* (Kim et al.): telemetria como base do feedback (a Segunda Via). Transposição: os autores não tratam de telemetria de RAG.
