# Guia do professor — Demo 5.2: O assistente foi publicado; quem percebe que ele piorou?

Roteiro de condução da segunda demo da Aula 5 (`04-GenAIOps/02-Operacao-Observabilidade-Qualidade-Continua/README.md`). Os números citados são os do ensaio no devcontainer da disciplina (QA, rodada 2, 02/11/2026: `--duration 30`, 60 probes por cenário). Este arquivo é para quem conduz: sequência dramática, o que mostrar em cada tela, perguntas de decisão, onde a turma trava, como interromper uma falha planejada e o que fazer se a AWS não estiver disponível. O aluno não precisa dele.


## Em uma frase

Esta demo prova que **um sistema de IA pode estar tecnicamente saudável e perigoso para o negócio**, e que a saída é medir qualidade de propósito, diagnosticar pelo trace e devolver o aprendizado ao pipeline. Ela fecha a disciplina: a seta de volta do Second Way para o First Way.

## Preparar antes da aula (20 min, na véspera)

| O quê | Como | Por quê |
|---|---|---|
| Rodar a demo inteira uma vez no Codespaces do professor | `bash ops/bootstrap-demo52.sh` até o passo 24, sempre com `ops/run <script>` (nunca `python3`) | Confere imagens Docker em cache, portas e tempos reais do dia. O ensaio de referência foi no devcontainer local; no Codespaces real ainda **não validado** |
| Capturar os prints `NOVA-IMAGEM` que ainda faltam | Grafana lado a lado (baseline e incidente), trace no Jaeger, alerta FIRING, arquivo de alerta | O README usa placeholders comentados; descomentar depois de salvar em `img/` |
| Confirmar o estado do runner | `bash shared/runner/runner-preflight.sh` (somente leitura) | Saber se a Parte 9 vai destruir algo `stopped` ou `running`; não ligar hoje |
| Decidir se a Parte 9 roda em sala | Depende de a turma ter credenciais renovadas | Se não, vira dever de casa com o passo 27 |
| Deixar dois terminais e três abas (3000, 16686, 9090) abertos no projetor | Aba PORTS do Codespaces | A demo alterna entre eles o tempo todo; não perder tempo achando janela |

Não precisa da Demo 5.1 rodando, nem do runner ligado, nem da API na AWS. Se a API da 5.1 estiver de pé, o `bootstrap-demo52.sh --from-live <URL>` compara versões e dá um momento bom ("é o mesmo conjunto que está publicado"): no ensaio contra a API real do Learner Lab, as seis versões vieram `igual` e o `release_id` publicado era `rel-...` (do pipeline), diferente do `local-<sha>`. O `--from-live` é um GET público, sem credencial: funciona mesmo com a sessão do Learner Lab vencida, porque Lambda e API Gateway sobrevivem entre sessões.

## Sequência dramática (90-120 min de aula)

Tempos de condução, não de execução. Execução pura cabe em ~25 min; o resto é observar, perguntar e discutir. Se a aula tiver 90 min, cortar a Parte 6 para "mostrar e comentar" (10 min em vez de 15) e levar a Parte 9 para casa.

| Bloco | Min | Momento do storyboard | O que você mostra | Pergunta antes de revelar | O que o aluno deve concluir |
|---|---|---|---|---|---|
| Abertura | 0-5 | Cena de Diego e Helena | Só a leitura do blockquote, sem tela | "Tudo verde e produto errado: onde você começa?" (ouvir 3 respostas; a maioria dirá "olho o log" ou "reinicio") | Ninguém sabe ainda; o painel atual não responde |
| Parte 0 | 5-15 | Bootstrap | Terminal: `bootstrap-demo52.sh`, as versões, `model=none`; abrir as 3 abas | "Por que `model=none` não é defeito?" | Ambiente `local-demo`, mesmo core, sem LLM; não é a Lambda |
| Parte 1 | 15-20 | 1. Health verde | `curl /health` e uma resposta de política com citação | **R1:** "O 200 prova a resposta certa?" Peça para escreverem uma frase | O 200 prova processo vivo, não conteúdo correto |
| Parte 2 | 20-35 | 2. OTel | Jaeger: trace de pedido com o span do serviço de pedidos **dentro**; depois os 4 spans de RAG | **R2:** "Se citasse política vencida, em qual span você olharia?" | Causalidade por trace; propagação de contexto é o que une dois serviços |
| Parte 3 | 35-50 | 3. Baseline | Simulador 30 s (60 probes, `pass_rate=1.0`); monitor `[OK] n=60`; Grafana: **Visão do negócio** com semáforo **Confiável** e 100% com a política vigente; **Visão técnica** com 0,0% 5xx, p95 de poucos ms, `n` ~60 | **R3:** "Qual número representa o negócio? Por que ele precisa de gabarito?" | SLI sintético ≠ qualidade do cliente real; `insufficient_data` existe |
| Parte 4 | 50-65 | 4. Incidente + 5. Alerta | `ops/run inject.py stale_policy` (corpus `7e012a21c8b8`); simulador (`pass_rate=0.8167`, 11 falhas); `curl` com `200` e citação `politica-trocas@v2` (15 dias); Visão do negócio vira **Não confiável** (87% com a política vigente, tabela `Citou uma política obsoleta · 7e012a21c8b8`, faixa HTTP em 100% e faixa de resposta certa caindo) enquanto a Visão técnica não se mexe; monitor `[CRITICAL] stale_source_served obsoletas=11` + arquivo em `ops/alerts/`; Prometheus com `StaleSourceServed` (critical) e `QualityProbeDegraded` (warning) | **R4:** "Dois alertas para um incidente: qual você aciona primeiro? E se `n` fosse 8?" | Qualidade cai com HTTP verde; alerta por medição, com janela, n e 3 estados; ocorrência × tendência |
| Parte 5 | 65-85 | 6. Diagnóstico + 7. Mitigação | **Investigação · Incidente**: tabela de versões (corpus `7e012a21c8b8`, probes errados do intervalo), linha do tempo com `Fonte obsoleta servida`, log `probe fail` → **Ver este trace (Jaeger) no painel**; no trace: `release_id` igual, `rag.filter_documents` **sem** `blocked_status` (era 2) e `chunks_allowed` 24→26; `/admin/reload` **não** resolve; `ops/run restore.py`; replay de `stale_policy` com `--seed 42` passa (`pass_rate=1.0`); Prometheus segue FIRING 2-3 min (resolve pela janela) | "Reiniciaria? Por quê?" (deixe alguém defender o reinício, depois mostre o reload) | Mitigar o artefato apontado; placebo descartado com evidência; recuperação provada, não presumida |
| Parte 6 | 85-100 | 8. Cenário B | `ops/run inject.py order_api_degradation` (250 ms, 70% 5xx); resposta `order_api_unavailable`; monitor `[OK] fallback=19 p95=3120ms`; `OrderApiDegraded` (team sre) em FIRING; Jaeger com 3 `orders.get` 503 dentro do `order_api.lookup` | **R5:** "Mesma resposta operacional? Reindexar ajuda aqui?" | Dois alertas, duas mitigações; fallback honesto vale mais que resposta inventada |
| Parte 7 | 100-110 | 9. Aprendizagem | Postmortem preenchido ao vivo (só causa raiz, hipótese errada e ação preventiva); caso golden novo; `run_eval` passa, e reprova sob `stale_policy` | "Como impedir recorrência sem culpar quem reindexou?" | Incidente vira gate; blameless é método |
| Parte 8 | 110-118 | Fechamento | O mermaid do ciclo e a tabela dos 4 módulos | **R6:** "Um painel para a liderança de CX: qual, e o que ele não mostra?" | Three Ways com a dimensão de qualidade da IA |
| Parte 9 | 118-120 | Limpeza | `restore.py` + `stop-demo52.sh` (o stop não desfaz o overlay); anunciar o destroy do runner (em sala ou em casa) | "Por que agora pode destruir e antes não podia?" | Fim do ciclo de AWS; o Trabalho Final sobe runner próprio |

## Telas e mensagens a esperar


| Momento | Tela | O que precisa aparecer para seguir | Se não aparecer |
|---|---|---|---|
| Passo 1 | Terminal | Cinco etapas `== N/5` e versões `72532257bea3` / `4f0ea92022d2`, `model_version none` | Erro de porta: `stop-demo52.sh` e repetir; erro de Docker: `sudo service docker start` |
| Passo 7 | Jaeger | Span do `quantum-orders` aninhado sob `order_api.lookup` do `quantum-assistant` | Se vierem dois traces separados, a propagação falhou: conferir `OTEL_EXPORTER_OTLP_ENDPOINT` nos dois processos (o bootstrap exporta para ambos) |
| Passo 9 | Terminal | `[OK] baseline n=60 pass_rate=1.0` e `Prometheus FIRING: nenhum` | `INSUFFICIENT_DATA`: o simulador ainda não completou 20 amostras; esperar e repetir (bom momento para explicar o estado). `QualityNoData` em FIRING antes do primeiro probe é normal |
| Passo 10 | Grafana | **Visão do negócio**: semáforo **Confiável**, 100% com a política vigente, rodapé "probes sintéticos, não clientes reais"; **Visão técnica**: 0,0% 5xx, `n` ~60, 0 alertas | `No data`: intervalo de tempo errado (usar Last 15 min) ou Prometheus ainda sem scrape (esperar 15 s) |
| Passo 13 | Terminal + Grafana | `200` em milissegundos (p95 do runtime 7,65 ms no ensaio), citação `politica-trocas@v2#prazo-para-troca` e "15 dias", metade direita caindo | Se a citação continuar `v3`/"30 dias", o inject não fez reload: conferir a saída do passo 11 e repetir |
| Passo 14 | Prometheus Alerts | `StaleSourceServed` (critical) e `QualityProbeDegraded` (warning) em FIRING | `QualityProbeDegraded` em PENDING: esperar os 30 s do `for:`; `StaleSourceServed` acende sem `for`. Se nenhuma acender, a janela de 2 min ainda não tem 20 probes |
| Passo 16 | Terminal | `index_version` **igual** ao do passo 11 após o reload | Se mudar, o restore já tinha sido rodado por engano; refazer inject e repetir |
| Passo 18 | Terminal | replay de `stale_policy`: `pass_rate=1.0 obsoletas=0`, monitor `[OK]`; Prometheus ainda FIRING por 2-3 min | Duas linhas `stale_policy` (CRITICAL e OK): faltou o `--results`; vale a mais recente |
| Passo 20 | Terminal | `abstain_reason: order_api_unavailable`, `order: null`, ~1,4 s (3 tentativas de 250 ms + backoff) | A taxa de erro é sorteada (70%): repetir o `curl` 2-3 vezes, ou usar `Q-5000` (503 garantido) |
| Passo 24 | Terminal | `exit=0` com corpus restaurado; `exit=1` sob `stale_policy`, com `REGRA DURA fonte_obsoleta` | Se `exit=1` com corpus restaurado, o caso novo está mal formado: comparar com `head -1`. Se `exit=0` sob `stale_policy`, faltou `--target http` (o `--target local` ignora o overlay) |

## Falhas planejadas: como interromper e como desfazer

Tudo é reversível e nada toca nos arquivos versionados: o `inject.py` trabalha num overlay em `ops/.runtime/` e o `restore.py` (sem argumento) desfaz **tudo** de uma vez e confere o `corpus_version`:

| Falha | Como ligar | Como desligar | Se o aluno esqueceu de desligar |
|---|---|---|---|
| Política obsoleta | `ops/run inject.py stale_policy` | `ops/run restore.py` | `stop-demo52.sh` **não** reverte o overlay (de propósito); rodar `restore.py` antes do `stop`, como manda o passo 25 |
| API de pedidos degradada | `ops/run inject.py order_api_degradation` | `ops/run restore.py` | idem; os faults vivem só em memória do `:8081` e em `ops/.runtime/faults.json` |
| Injeção documental (extensão, opcional) | `ops/run inject.py document_injection` | `ops/run restore.py` | idem |

Se a turma estiver atrasada, o cenário `document_injection` fica como leitura: ele mostra que um trecho de documento com "ignore as instruções" é tratado como **dado** pelo composer extrativo e vira `injection_blocked` quando não sobra evidência legítima. Vale dizer em voz alta o limite: isso prova que o fluxo **sem LLM** não executa instruções; não prova nada sobre um LLM futuro.

## Perguntas de decisão e respostas-guia

- **R1 (200 prova resposta certa?)** Não. Prova que o processo recebeu, roteou, montou e devolveu. A correção depende de qual fonte foi escolhida, e o HTTP não vê isso.
- **R2 (qual span?)** Os três, em ordem: `retrieve` diz se a fonte errada entrou nos candidatos; `filter_documents` diz se ela devia ter sido removida; `compose` diz se foi escolhida. No incidente, a resposta é "o filtro deixou passar porque o metadado mudou".
- **R3 (qual número é o negócio?)** `source_is_current_rate` / `pass_rate`. Precisa de gabarito porque "fonte vigente" só existe em relação a uma referência conhecida; sem rótulo, o tráfego real não tem resposta certa.
- **R4 (warning × critical; n=8)** O `critical` (`StaleSourceServed`) é de **ocorrência**: uma política obsoleta servida já basta, sem média. O `warning` (`QualityProbeDegraded`) é de **tendência**: exige n ≥ 20 e 30 s. O plantonista aciona pelo critical (mais específico, aponta o artefato) e usa o warning para medir o impacto. Com `n=8 < 20`, o `QualityProbeDegraded` **não** dispara e o monitor local etiqueta a janela como `INSUFFICIENT_DATA`; o `StaleSourceServed` dispararia mesmo assim, e o monitor local também: em `quality_monitor.py`, ACL e fonte obsoleta são checadas **antes** do `n` mínimo, de propósito (uma ocorrência grave não espera amostra). Discutir: bom (sem falso alarme de taxa) ou ruim (cego com pouco tráfego)?
- **R5 (mesma resposta operacional?)** Não. Reindexar não muda nada na dependência. Opções: circuit breaker (parar de chamar e recusar rápido), degradação graciosa (responder só políticas), comunicar ao time dono da API e ao cliente. O `corpus_version` inalterado no alerta é a pista de que não é problema documental.
- **R6 (um painel para CX)** Não há resposta única; a defesa é o que vale. Resposta forte: `source_is_current_rate` dos probes, rotulado como proxy, acompanhado do que ele **não** mostra (satisfação do cliente real, perguntas fora do golden set). Resposta fraca: uptime sozinho.

## Onde a turma costuma travar

| Sintoma | Causa provável | O que dizer |
|---|---|---|
| "Não entendi por que não é só rodar o pytest de novo" | Confundiu avaliação offline (gate) com monitoramento online | O gate roda uma vez, antes, sobre o que mudou no Git. O monitor roda sempre, depois, sobre o que mudou **fora** do Git. Mesmo conjunto de perguntas, momentos diferentes |
| "O Grafana está vazio" | Intervalo de tempo ou simulador não rodando | Mostrar `Last 15 minutes` e o segundo terminal |
| "Posso editar o dashboard?" | Grafana anônimo como Viewer | Não (HTTP 403 ao salvar): os dashboards são provisionados do repositório; mudar é editar o JSON em `ops/grafana/dashboards/` e subir de novo, como código |
| "Qual a diferença entre o monitor local e o alerta do Prometheus?" | Dois caminhos para a mesma medição | O monitor lê o JSONL (artefato); o Prometheus lê a métrica exportada. Dois caminhos de propósito: um prova que o alerta é reproduzível sem a pilha; o outro é como fica em produção. `--prometheus http://127.0.0.1:9090` mostra os dois juntos |
| "Posso usar `order_id` como label para filtrar no Grafana?" | Não entendeu cardinalidade | Abrir o bloco 💡 de cardinalidade; a regra é "métrica agrega, trace individualiza" |
| "Isso é produção?" | Esqueceu do `local-demo` | Repetir o `[!IMPORTANT]` da abertura: mesmo core, telemetria real, ambiente local; não é a Lambda |

## Honestidade que precisa ser dita em voz alta

- **Os três dashboards existem e estão provisionados** (`ops/grafana/dashboards/negocio-atendimento.json`, `quantum-demo52.json` = Visão técnica, `investigacao-incidente.json`), com Loki 3.7.9 no compose e datasources Prometheus, Loki e Jaeger. Os prints do README são reais (ensaio do VISUAL, 02:36). O fluxo do momento do Diego na Investigação: **Cenário** `stale_policy` no topo → **Buscar no log** `fonte_obsoleta` → expandir uma linha `probe fail` → **Ver este trace (Jaeger) no painel** ao lado do `trace_id`; o trace abre no painel **Trace da requisição escolhida**, abaixo dos logs. Ensaie esse clique antes da aula; se falhar, o caminho pelo Jaeger com o `sample_trace_ids` do alerta dá o mesmo trace.

- **Todo comando Python da demo é `ops/run <script>`**, nunca `python3`: o `python3` do Codespaces não tem OpenTelemetry e o Grafana fica em `No data`. O script avisa (`AVISO: telemetria DESLIGADA`), e o README tem o bloco de erro; mesmo assim, é a falha mais provável em sala.
- Os números do README vêm do **devcontainer local** (mesma imagem do Codespaces, aarch64); o Codespaces real (amd64, port forwarding) **não foi validado**. Hashes, contadores e vereditos devem bater; milissegundos variam.

- Sem LLM: não existem tokens, custo de inferência, "alucinação" nem *faithfulness*. Onde um painel de produção teria isso, aqui é `not_applicable`. Não deixar o aluno sair achando que mediu alucinação.
- Probes são **proxy sintético**: medem contratos conhecidos, não a satisfação do cliente real. O rótulo está no dashboard de propósito.
- 20 probes por janela é **configuração de exercício**, não dimensionamento estatístico.
- O alerta é `local_alert` e uma regra do Prometheus em FIRING; ninguém foi acionado. Não dizer "PagerDuty".
- `environment=local-demo` em cada span. O que mudaria em produção na AWS é o exporter (ADOT layer, EMF, CloudWatch Alarms), não a instrumentação; o bloco 💡 do passo 27 explica, e X-Ray/Synthetics estão bloqueados no Learner Lab.

## Plano sem AWS

A demo não depende da AWS. Se a turma estiver sem credenciais:

- Parte 0: pular o passo 4 (preflight do runner), explicitamente opcional.
- Parte 9: rodar só o passo 25 (`restore.py` + `stop-demo52.sh`); os passos 26 e 27 viram tarefa para a próxima renovação de credenciais, com o lembrete de que o runner `stopped` custa ~US$ 0,08/dia de disco e o `destroy` zera isso.
- Nada mais muda. Esse é o motivo de a 5.2 ter bootstrap independente.

## Runner: por que o destroy é aqui

Nos labs 02.1, 03.1 e 03.2 o aluno é instruído a **desligar** o runner entre aulas e **não** destruí-lo. A Demo 5.1 ainda usa o `primeiro-projeto` e o runner. A 5.2 é a última demo que o referencia. O Trabalho Final (Aula 6) sobe um runner **novo** com `Trabalho-final/provisionamento/subir-runner.sh`, registrado no projeto `trabalho-final`; ele não reaproveita o `quantum-ai-gitlab-runner`. Por isso o passo 27 desta demo é o `terraform destroy` do módulo 02, com `Resources: 3 destroyed`, e a rede `fiap-lab` fica de pé para a Aula 6. Se um aluno destruir antes da hora (no fim da Aula 4, por exemplo), a Demo 5.1 precisa dele de volta: `shared/runner/runner-recover.sh --dry-run` mostra o caminho (`recreate`), que é o Terraform + Ansible do módulo 02.

## Gabarito do postmortem (passo 23)


- **Causa raiz:** mudança de metadado/corpus fora do pipeline tornou `politica-trocas@v2` (obsoleta, 15 dias) elegível no filtro de vigência, no lugar da v3 (30 dias); `index_version` mudou, `release_id` não.
- **Detecção:** alerta `critical` `stale_source_served` do monitor local e `StaleSourceServed` do Prometheus na janela do passo 12; em produção chegou pelo ticket, antes do alerta, e é isso que a ação preventiva muda.
- **Hipótese inicial errada:** reiniciar/recarregar; o `/admin/reload` manteve `index_version` e citação `v2`.
- **Mitigação:** restaurar o corpus e reindexar (artefato documental). Não é rollback de código.
- **Ação preventiva:** caso `pm-stale-trocas-site` no golden set; regra de que toda reindexação executa a avaliação mesmo sem mudança de código; alerta de qualidade com dono.
- **Evidências:** `trace_id` do passo 15, arquivo em `ops/alerts/`, `pass_rate` dos passos 9 e 18.
