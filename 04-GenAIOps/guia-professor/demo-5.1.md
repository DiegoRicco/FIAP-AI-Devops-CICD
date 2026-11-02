# Guia do professor — Demo 5.1: do RAG ao release confiável

Material de bastidor para conduzir a [Demo 5.1](../01-Release-RAG-Quality-Gates/README.md). O aluno não precisa dele para executar o lab; quem estuda sozinho pode consultar aqui o gabarito das perguntas R1-R3 e do mini-desafio. Ele contém a sequência dramática, as perguntas de decisão com o que a turma costuma responder, a falha planejada (como provocar, como interromper se der errado), a alternativa sem cloud, o gabarito do mini-desafio e as respostas das perguntas R1-R3.

Nenhuma saída de comando está reproduzida aqui ou no README como se fosse real sem ter sido medida. As saídas locais (Partes 2, 3, 5 e mini-desafio) foram executadas e são exatas; as de pipeline e AWS (Partes 4 e 6) mostram a forma e dizem explicitamente o que varia por execução (`release_id`, hashes do commit, números de versão da Lambda).

## Antes da aula (15 min, na véspera ou de manhã)

| # | O quê | Por quê |
|---|-------|---------|
| 1 | Renovar as credenciais do Academy no Codespaces (passo A do ritual). | Credenciais expiram por sessão; sem elas nada funciona e o aluno culpa o lab. |
| 2 | `runner-preflight.sh`. Se `diagnosis=start`, `runner-recover.sh --apply` **antes** de os alunos chegarem. | O start leva ~1 min até o SSM e o serviço voltarem; feito na frente da turma vira tempo morto. Medido na construção: `running` em 19 s, SSM Online em 29 s, serviço ativo em 48 s. |
| 3 | No GitLab, confirmar runner **online** com tag `shell`. | Os comandos de push do README descobrem a branch com `git branch --show-current` (`master` nos módulos 02/03, `main` em projeto novo), então não há o que ajustar; só confira que o GitLab roda pipeline nessa branch. |
| 4 | Rodar localmente `run_eval.py` no baseline e com `inject_regression.py --apply`, e anotar: exit code, quais slices caem, qual regra dura reprova, quantos `<failure>` no JUnit. | É o gabarito do passo 19. Sem isso você não consegue dizer "compare com a previsão". |
| 5 | Ter o `release_id` do `live` anotado se a infra já estiver de pé de uma aula anterior. | Se o `live` já existe, o passo 17 vai mostrar um `release_id` **novo** e o rollback do passo 25 vai voltar para o **antigo** da aula anterior, o que confunde se você não avisar. |
| 6 | Abrir, em abas: GitLab Pipelines, GitLab Settings → CI/CD → Runners, AWS Console → Lambda `qc-assistant-api` → Aliases. | As "telas que a turma vai ver" prontas. |

**Decida antes:** a aula terá a Demo 5.2 no mesmo dia? Se sim, diga no começo que a infra fica de pé e que o `cleanup.sh` só roda em `--dry-run` (passo 29). Se não, idem; o cleanup real fica para o fim do módulo.

## Sequência dramática (tempo de aula: 90-120 min)

A demo tem oito atos e **três pipelines**: verde (passo 14), vermelho (passo 20) e verde de novo (passo 22), mais o `rollback`/`promote` manuais da Parte 6. O que dá ritmo é a **previsão antes da revelação**: em cada ato marcado, pare, pergunte, espere respostas em voz alta, e só então rode.

| Tempo | Ato | Pergunta de decisão para a turma | O que a turma costuma responder | O que mostrar |
|-------|-----|----------------------------------|----------------------------------|---------------|
| 0-10 | Abertura (cena da Helena) | "Me deem **duas** respostas: uma convincente sem fonte, outra com a política vigente citada. Qual vocês publicam?" | Todos escolhem a com fonte. Então: "e se a fonte citada for a política de 2024?" Silêncio. É o gancho da regra dura de vigência. | O print do Instagram (narrado). Pergunta guia no quadro. |
| 10-20 | Parte 1 (setup) | Nenhuma; é ritual. Aproveite para explicar **por que** a key de state é separada (passo 3). | — | `preflight.sh` verde. |
| 20-30 | **Ato 1 e 2** (passos 5-7) | Antes do passo 5: "Ele vai inventar um programa de cashback, dizer não sei, ou devolver código?" | Metade diz "inventa", pela experiência com chatbots. A recusa com `abstain_reason` surpreende. | `abstained: true`, `insufficient_evidence`, `citations: []`. **Bônus de debate:** rode a pergunta do sofá (está no README como contraexemplo): o BM25 responde "Troca de presente" com fonte. Pergunte: "isso é melhor ou pior que inventar?" (é pior: erro com cara de certo). Depois: `corpus_version` mudando com uma linha. |
| 30-50 | **Ato 3 e 4** (passos 8-12) | Antes do passo 8: "Tem duas versões de trocas no corpus. Qual vai aparecer na citação, e por quê?" Antes do passo 10: "Sem token, o que acontece? Com token de outro cliente?" | No passo 8, alguns dizem "a mais parecida com a pergunta" (é a intuição de busca, e é **errada** sem filtro: por isso o filtro existe). No passo 10, alguns esperam `order_not_found` em vez de `forbidden`. Explique: não revelar se o pedido existe também é segurança. | Citação v3 com `effective_date`; os três `route=order_status` com `forbidden`/`forbidden`/`order`; relatório com slices e regras duras. |
| 50-75 | Parte 4 (pipeline verde, passos 13-17) | Antes do passo 17: "Se o `postdeploy` reprovar, o que o cliente está vendo neste instante?" | Costumam acertar: "a versão antiga". Reforce: no módulo 03 a resposta seria "a versão nova e errada". | 10 stages verdes; aba Tests, suíte `evaluate`; `/health` em `live` com `release_id`. **Peça para anotarem o `release_id`.** |
| 75-95 | **Ato 5 e 6** (passos 18-22) | **A falha planejada.** Antes do passo 19: "Hit@3 e MRR vão cair, subir ou ficar? Qual regra dura reprova? Quais slices?" Faça cada um escrever. | A maioria diz "Hit@3 cai muito". **Não cai** (a busca continua achando a seção de trocas, só que a v2 agora disputa o topo). Quem reprova é `fonte_obsoleta`. E repita: **nenhum código mudou**. É o momento de maior aprendizado da aula: **a média não pega, a regra dura pega**. | Exit `1`, `<failure>` no JUnit; pipeline com `evaluate` vermelho e `deploy` **skipped**; `/health` em `live` com o **mesmo** `release_id`. Depois o revert e o verde. |
| 95-110 | **Ato 7** (passos 23-26) | Antes do passo 25: "Qual `release_id` o `/health` vai mostrar depois do rollback?" | Acertam se anotaram no passo 17. Quem não anotou entende por que o manifesto existe. | Manifesto no S3 (e a ausência do manifesto do pipeline vermelho); aliases no Console; `live` voltando e indo de novo. |
| 110-120 | **Ato 8** (passos 27-29) | R1-R3 (abaixo). Fechar com a frase da Helena: "sem commit, sem pipeline e sem erro 500, quem percebe?" | — | `cleanup.sh --dry-run` e a nota de que a infra fica de pé. |

Se estiver atrasado: o mini-desafio (passo 28) é cortável; o Ato 7 **não** é (é a resposta ao pedido do Diego na abertura). O Ato 2 pode virar fala sem comando (mostre só o `templates.json` e explique os três problemas do texto estático).

## A falha planejada, em detalhe

**O que ela é:** `scripts/inject_regression.py --kind obsolete-priority --apply` troca **uma linha** de `corpus/trocas-v2.md`: `status: obsolete` vira `status: active`. Ou seja, alguém republicou a política de trocas v2 (15 dias) como vigente, sem tocar em código nem em configuração. A alternativa `--kind remove-active-filter` muda `config/settings.json` (`require_active: false`), desligando o filtro de vigência para todo o corpus; o efeito no gate é parecido, com mais casos reprovando. Use `obsolete-priority` na aula: é a mudança "inocente" de **conteúdo** que alguém do atendimento faria de boa-fé, e é a que mais surpreende ("mas eu não mexi em código!"). `--status` mostra o estado atual das duas sem alterar nada; o `--revert` devolve o arquivo byte a byte.

**Por que ela é boa didaticamente:** é uma mudança de **conteúdo**, não de código. O aluno acabou de ver, no passo 7, que uma linha a mais num documento muda `corpus_version`; agora vê que uma linha a mais num documento muda a **resposta ao cliente** (15 dias em vez de 30) e que o pipeline só pega isso porque reavalia a cada push, mesmo sem `.py` alterado. E as métricas de média **não caem**, o que derruba a intuição "se o número está alto, está bom". A versão com `remove-active-filter` é o complemento: mesma reprovação, causa em configuração; vale citar em sala como "os dois caminhos para o mesmo erro".

**Gabarito do passo 19** (medido no corpus/golden atuais; refaça na véspera se o corpus mudar): exit `1`; Hit@3 **1,0 → 1,0**, MRR **1,0 → 0,9**; `fonte_vigente` 1,0 → 0,8333; `case_pass_rate` 1,0 → 0,8929; slices: `vigencia` 0,5, `politicas` 0,8333, `autorizacao` 0,75. Regra dura `fonte_obsoleta` violada em **3 casos**: `acl-outro-tenant` (autorizacao), `troca-como-solicitar` (politicas) e `troca-politica-obsoleta` (vigencia, duas violações: citou `@v2` e `apareceu: ['15 dias']`). JUnit com **7 `<failure>`** (3 casos + 4 gates). Ponto de fala: a média ficou a 0,007 do limiar; com `case_pass_rate.min = 0.85` ela passaria e só a regra dura seguraria.

**Como interromper se der errado no meio da aula:**

| Sintoma | Causa provável | Saída rápida |
|---|---|---|
| Passo 19 sai com exit `0` (regressão não reprova) | `--apply` não aplicou (arquivo editado à mão: `nao tem o trecho esperado`), ou `thresholds.json` foi afrouxado. | `python3 scripts/inject_regression.py --status` deve dizer `applied` para `obsolete-priority`, e `grep '^status:' corpus/trocas-v2.md` deve mostrar `active`. Se mostra e ainda passa, abra `reports/eval.json` → `hard_rule_violations`; se vazio, confira `thresholds.json` (`source_is_current_rate.min` tem que ser `1.0`). Plano B: `--kind remove-active-filter --apply`. |
| Pipeline vermelho, mas o `deploy` **rodou** | `allow_failure: true` ou `\|\| true` no job `evaluate`. | Pare o pipeline (GitLab → Cancel). Compare o `.gitlab-ci.yml` da raiz com `quantum-assistant/.gitlab-ci.yml` (`diff`). Rode `rollback.sh` se o `live` mudou. É um erro que vale a aula: mostre o `diff`. |
| Job `stuck` | Runner offline (sessão do Academy expirou e a EC2 parou, ou serviço caiu). | `runner-preflight.sh`; se `start` ou `restart`, `runner-recover.sh --apply`. Enquanto espera (~1 min), avance a Parte 2 ou 3 localmente: elas não precisam do runner. |
| `ExpiredToken` em qualquer `aws` | Credenciais do Academy expiraram. | Passo A do ritual. Se o Learner Lab encerrou, as EC2 pararam: depois dele, `runner-recover.sh --apply`. |
| `postdeploy` reprova no pipeline **verde** (passo 17) | Lambda ainda em cold start ou o `release_id` do `/candidate/health` não bate. | Leia o log do job: ele diz qual dos dois. Retry do job resolve o cold start; `release_id` diferente significa que o alias `candidate` não foi atualizado, confira o `apply`. |
| Precisa **voltar o projeto ao estado limpo** a qualquer momento | — | `python3 scripts/inject_regression.py --revert` (sem `--kind`, reverte as duas) e, se já houver commit, `git -C /home/vscode/environment/primeiro-projeto checkout -- quantum-assistant/config quantum-assistant/corpus`; antes do primeiro commit, recopie as pastas do repositório do curso. Nada disso toca a AWS. |

**Nunca** use `terraform destroy` como saída de emergência no meio da aula: o `live` da turma some. O rollback de alias é a emergência prevista.

## Alternativa sem cloud (quando a AWS ou o GitLab não cooperam)

A Demo 5.1 tem **três camadas**, e só a terceira precisa de nuvem:

1. **Local (Partes 2, 3 e o mini-desafio):** tudo roda no Codespaces com `python3`. Recusa, RAG com citação e vigência, API de pedidos em memória, `run_eval.py` com exit `0`/`1`, JUnit gravado, regressão e revert. **Isso cobre os Atos 1 a 6 sem nenhuma credencial.**
2. **GitLab sem AWS:** se a AWS falhou mas o GitLab e o runner estão de pé, o pipeline roda `preflight → validate → test → evaluate` e para no `package`/`deploy` por falta de credencial. O gate vermelho (Ato 5) e o verde (Ato 6) **ainda aparecem na UI** até o `evaluate`; só o deploy não acontece. Diga isso à turma em vez de fingir.
3. **AWS (Ato 7):** manifesto no S3, aliases, rollback. Sem AWS, mostre o `manifest.json` gerado localmente por `python3 release/build_bundle.py` (ele grava o manifesto em `release/`) e explique o rollback com o diagrama de aliases do README e os prints do Console (quando existirem em `img/`). Deixe claro: **"isto é o que aconteceria; hoje não conseguimos executar na conta"**.

Roteiro condensado sem cloud (60 min): Partes 2 e 3 completas (30 min) → passo 18-19 e 22 **locais** (o `run_eval.py` com exit `1` e depois `0` é o próprio gate; 15 min) → leitura do `.gitlab-ci.yml` com a tabela do passo 13 e o `💡` de report-only × gate (10 min) → Ato 8 (5 min). O aluno sai sabendo o que o pipeline faria e tendo visto o avaliador barrar de verdade.

## Perguntas R1-R3: respostas esperadas

**R1.** Duas coisas que mudam sem commit e estragam a mesma resposta: (a) alguém **reindexa** o corpus ou edita um documento no ambiente publicado fora do pipeline (ou a vigência de um documento vira com a data: a política de Black Friday **entra em vigor** sozinha num dia, e o golden set de ontem não a via); (b) a **API de pedidos** fica lenta ou devolve 5xx, e o assistente passa a responder `order_api_unavailable` para todo mundo. Nenhuma das duas passa pelo `evaluate`. Boa resposta adicional: a **distribuição das perguntas** muda (Black Friday traz perguntas que o golden set não cobre).

**R2.** O `200` do `/health` prova que a Lambda está no ar, respondendo, com o `release_id` esperado. **Não prova** que a resposta a qualquer pergunta está certa, que a fonte citada é vigente, nem que a API de pedidos está acessível. É disponibilidade, não qualidade. (Esta é a frase que abre a 5.2.)

**R3.** Continuam valendo sem mudança: fonte vigente, ACL/tenant, contrato de pedido (são filtros e roteamento, antes do composer), presença de citação, smoke e `release_id`, aliases e rollback, manifesto. Precisam ser repensados: os casos com **texto esperado exato** (um LLM parafraseia), a **regra de injeção** (hoje provamos que o composer não executa instrução; com LLM é o teste mais difícil e mais importante) e a própria ideia de **determinismo** (seed não fixa a saída de um modelo; entra avaliação por juiz, com suas próprias limitações). Fechar: "o pipeline é o mesmo; o que muda é o que você mede no meio dele".

## Mini-desafio: gabarito

Números medidos no corpus/golden atuais (cópia temporária do projeto, `run_eval.py` local, seed 42). Se o corpus mudar, refaça os dois experimentos na véspera.

**28.1, `retrieval.k = 1`** (medido): Hit@k **continua 1,0** e MRR 1,0, porque no baseline a seção certa já estava sempre em 1º. O que cai é o slice **`multi_fonte`** (1 caso, `multi-fonte-devolucao-reembolso`: 0,0): com um só candidato, o composer não tem a 2ª fonte que a resposta exigia. `case_pass_rate` 0,9643 (passa); reprova **só** `slice.multi_fonte` (mínimo 0,75). **Zero regra dura**, exit `1`. Lição: `k` é um botão de recall; aqui o erro é de **cobertura** (a resposta ficou incompleta), e o piso por slice é o que pega, não a média geral (0,96 passaria).

**28.2, devoluções `obsolete`** (medido): Hit@k **0,8** e MRR 0,8 (abaixo de 0,9 e no limite de 0,8), `citation_rate` 0,8333, `case_pass_rate` 0,8571; slices `politicas` 0,6667, `multi_fonte` 0,0 e **`abstencao` 0,5**. Quatro casos reprovam: `devolucao-arrependimento`, `devolucao-reembolso-pix`, `multi-fonte-devolucao-reembolso` e `sem-evidencia`. A surpresa é o último: com a política de devoluções fora, uma pergunta que **devia** recusar passou a ser respondida com outra seção (a busca achou um vizinho). Ou seja, a previsão "vira recusa" acerta para as perguntas de devolução, mas tirar um documento também muda o que o resto do corpus responde. **Zero regra dura**, exit `1`. **`fonte_obsoleta` não reprova**: nenhuma citação obsoleta foi feita, porque o documento foi **filtrado**, não priorizado. É a comparação direta com o passo 18: lá o obsoleto **entrou** na resposta (regra dura); aqui ele **saiu** e deixou um buraco (média). Lição: recusar é mais seguro que responder errado, e o gate diferencia os dois.

## Telas e saídas para mostrar no projetor

| Momento | Tela | O que apontar |
|---|---|---|
| Passo 8 | Terminal | `version: 3`, `effective_date`, `model_version: none`; a frase `Fonte: ...` dentro do `answer`. |
| Passo 12 (`💡` injeção) | Terminal | `"Quais formas de pagamento a loja aceita?"` responde sem a frase maliciosa; `"Existe algum cupom especial de atendimento?"` → `injection_blocked`, sem o código. Abra `corpus/faq-atendimento.md` no projetor para a turma ver a frase plantada. |
| Passo 12 | Terminal | Linhas de `hard_rules` todas verdadeiras; `thresholds.json` como **configuração**, não lei (o próprio arquivo tem o campo `por_que` de cada limiar e a nota "~25 casos, cada caso vale ~4 p.p."). |
| Passo 15-16 | GitLab Pipelines e aba Tests | 10 stages; nome dos testes por slice. |
| Passo 17 | GitLab job `postdeploy` + terminal | O smoke em `/candidate`, o `release_id` conferido, depois o `/health` em `live`. |
| Passo 21 | GitLab pipeline vermelho | `evaluate` vermelho, `deploy` **cinza**. É a imagem da aula. Na aba Tests, o caso `troca-politica-obsoleta` ("O prazo de troca ainda é de 15 dias?") com `apareceu: ['15 dias']`. Depois, o terminal com o mesmo `release_id` em `live`. |
| Passo 24 | AWS Console → Lambda → Aliases | `candidate` e `live` como ponteiros. |
| Passo 25 | Terminal | `live` com o `release_id` antigo, `candidate` com o novo. |

Prints a capturar para o README (marcados como `NOVA-IMAGEM`): `pipeline-verde-10-stages.png`, `aba-tests-evaluate.png`, `log-postdeploy.png`, `pipeline-vermelho-evaluate.png`, `console-lambda-aliases.png`. Depois de salvar em `01-Release-RAG-Quality-Gates/img/`, descomente a linha de imagem logo abaixo de cada marcador.

## Pontes com o resto da disciplina

- **Com o módulo 03:** o Checkov saiu de `|| true` para bloqueante com exceções escritas; o golden set ganhou slices e regras duras; o `apply` direto virou `candidate → live`. Diga explicitamente "isto é o que o 03.2 não tinha", a turma gosta de ver a evolução.
- **Com a Demo 5.2:** a pergunta R2 é a abertura dela. Se for no mesmo dia, deixe a infra de pé e anote o `release_id` final: a 5.2 compara com ele (`bootstrap-demo52.sh --from-live URL`).
- **Com o Trabalho Final:** o manifesto de release e o rollback por conjunto são critérios que o aluno pode levar para o projeto integrador.
- **Fundamentação (com a ressalva de transposição):** Three Ways (*The DevOps Handbook*) para fluxo e feedback; *Accelerate* para "teste automatizado é o que permite entregar rápido com segurança"; *Reliable Machine Learning* para golden set, rollout e rollback de conjunto. Nenhum desses livros fala de RAG: a transposição é nossa, e vale dizer isso em sala.
