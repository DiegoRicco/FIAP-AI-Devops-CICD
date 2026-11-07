# 04 — GenAIOps e RAG na Prática

Módulo prático da **Aula 5**. O Assistente de Políticas que você publicou no módulo 03 vira o **Quantum Commerce AI Customer Assistant**: responde sobre trocas, devoluções, entrega e garantia **citando a política vigente**, consulta o **status do pedido na API de pedidos** (nunca em documento) e passa a ser **publicado, promovido, observado e corrigido** como um produto de IA em operação.

São **2 demos separadas e executáveis**, com a mesma história e o mesmo núcleo de software (`quantum-assistant/`):

| # | Demo | Pergunta que ela responde | Tempo de aula |
|---|------|---------------------------|---------------|
| **04.1** | **[Demo 5.1: do RAG ao release confiável](01-Release-RAG-Quality-Gates/README.md)** | *"Como sabemos que uma mudança no comportamento do assistente é segura **antes** de publicar?"* Golden set com slices, gates que **barram** o deploy, release com manifesto de versões, promoção `candidate → live` e rollback. | 90-120 min |
| **04.2** | **[Demo 5.2: o assistente foi publicado; quem percebe que ele piorou?](02-Operacao-Observabilidade-Qualidade-Continua/README.md)** | *"Como saberemos se ele **piorou depois** de publicado, sem commit, sem pipeline e sem erro 500?"* OpenTelemetry, traces distribuídos, métricas técnicas e de qualidade lado a lado, probes sintéticos, alerta, diagnóstico, mitigação e postmortem. | 90-120 min |

As duas podem acontecer no mesmo dia ou em dias diferentes: a **5.2 tem bootstrap próprio** e não depende de a 5.1 ter rodado naquela máquina. Se as duas vierem no mesmo dia, **a infra da 5.1 fica de pé** entre uma e outra (a 5.2 compara o que observa com o release publicado).

> Tempos acima são **tempo de aula** (ler, executar, observar, discutir), não o tempo de desenvolver o material. Execução pura de cada demo fica em ~25 min; o resto é entendimento, e é o que importa.

## Aula 5 na Quantum Commerce

> **Quinta-feira, 8h50. Três semanas depois do pipeline com quality gates entrar no ar.**
> **Helena Marques**, Head de Engenharia de IA e Entrega Contínua, chega com o celular na mão e um print de conversa:
>
> > *— "Ontem o marketing publicou a política de trocas da Black Friday. O pipeline passou, a API respondeu 200 o dia inteiro, e o assistente passou a dizer que a troca de eletrônicos tem 15 dias. A política vigente diz 30. Nenhum alarme tocou. Eu só descobri porque uma cliente postou a conversa no Instagram. Quero duas coisas: que **nenhuma mudança** chegue ao cliente sem a gente saber que ela é segura, e que, se ele piorar **depois** de publicado, a gente descubra antes da cliente."*
>
> **Diego Tavares**, AI SRE sênior, completa: *— "O gate do módulo passado pega prazo errado em documento que já conhecemos. Ele não pega documento antigo que voltou para o topo da busca, não pega a API de pedidos lenta, e não olha nada depois do `apply`. Hoje de manhã o dashboard estava verde e o assistente estava errado. Verde de infraestrutura não é verde de resposta."*

A pergunta que guia a aula inteira:

> **Como sabemos que uma mudança no comportamento da IA é segura antes de publicar, e como saberemos se ela piora após a publicação?**

A Demo 5.1 responde a primeira metade; a Demo 5.2, a segunda. Juntas, fecham o arco da disciplina (Three Ways de *The DevOps Handbook*: **fluxo** com o pipeline, **feedback** com a telemetria e a avaliação em operação, **aprendizagem** com o postmortem).

## Antes vs depois

| | Módulo 03 (Aula 4) | Módulo 04 (Aula 5) |
|---|---|---|
| **O que o assistente faz** | Busca a seção mais parecida nas políticas e devolve o trecho com a fonte. | O mesmo, mas **só entre documentos vigentes, do tenant certo e com permissão**; pergunta de pedido vai para a **API de pedidos** com autorização no servidor; texto de documento é tratado como **dado**, nunca como instrução. |
| **O que o pipeline testa** | Golden set (hit@3, MRR, trechos obrigatórios, recusa, injeção, PII, citação). Checkov com `\|\| true`. | Golden set com **slices** e **regras duras** (ACL, fonte obsoleta, recusa obrigatória, contrato de pedido, injeção) que reprovam **independentemente da média**. Checkov **bloqueante**, com exceções documentadas. |
| **Como publica** | `terraform apply` troca a Lambda, e o cliente já recebe a nova versão. | `apply` publica uma **versão numerada** no alias `candidate`; smoke e golden rodam contra ela; só então **`promote-live`** move o alias `live` e grava o registro do release no S3. **Rollback** é um job manual (botão play, ou pipeline com `QC_ACTION=rollback`) que move o alias de volta em ~12 s. |
| **O que é versionado** | Código e políticas, juntos no zip. | Seis versões por resposta: `application`, `prompt`, `corpus`, `index`, `eval_set`, `model` (= `none`), e um **manifesto de release** com Git SHA, `bundle_sha256` e aprovador. |
| **Depois do deploy** | Um stage `avaliacao` roda uma vez e acaba. | **Operação contínua**: traces OpenTelemetry por etapa, métricas de qualidade em janela, probes com gabarito, alerta, runbook e postmortem (Demo 5.2). |
| **LLM** | Não há. | Continua não havendo no caminho da turma, e a resposta diz isso (`mode = deterministic`, `model_version = none`). |

O que **não** muda: Terraform, GitLab, o runner do módulo 02 e o repositório `primeiro-projeto`. Nada é reconstruído do zero; o módulo evolui o que você já tem.

## Pré-requisitos do módulo

> [!WARNING]
> Este módulo **depende dos módulos 02 e 03**. Antes de qualquer demo daqui:
>
> - [ ] **Módulo 03 concluído**: o pipeline do `primeiro-projeto` ficou verde pelo menos uma vez, e você sabe ler a aba **Tests** do GitLab.
> - [ ] **GitLab Runner** do módulo 02 **existe** (ligado ou desligado; o ritual abaixo liga e confere). Tags `shell` e `terraform`.
> - [ ] **Cópia local do projeto** em `/home/vscode/environment/primeiro-projeto`, com o remote do GitLab configurado e a **chave SSH** em `/home/vscode/.ssh/gitlab`.
> - [ ] **Credenciais AWS do Academy atualizadas** no Codespaces (elas expiram a cada sessão do Learner Lab).
>
> **Valide rapidamente** (terminal do Codespaces):
>
> ```bash
> aws sts get-caller-identity --query Account --output text
> ls /home/vscode/environment/primeiro-projeto/.gitlab-ci.yml
> ```
>
> Saída esperada: o número da sua conta (12 dígitos) e o caminho do `.gitlab-ci.yml`. Se faltar qualquer item, volte ao [módulo 03](../03-CICD/README.md).

## Ritual do dia: credenciais → runner → demo

Toda aula deste módulo começa igual, e os READMEs das demos assumem que você passou por aqui. São três passos (A, B e C), sempre nesta ordem:

**A. Atualize as credenciais do Academy no Codespaces.** Siga o [ritual de início de aula](../00-create-codespaces/Inicio-de-aula.md). Credenciais velhas dão `ExpiredToken` em qualquer comando `aws`.

**B. Rode o preflight do runner.** Ele é **somente leitura**: encontra a EC2 do runner, diz se está `running` ou `stopped`, se o SSM a enxerga e se o serviço `gitlab-runner` está ativo. Não liga, não desliga, não registra nada.

```bash
bash /workspaces/FIAP-AI-Devops-CICD/04-GenAIOps/shared/runner/runner-preflight.sh
```

> Saída esperada: o progresso de cada checagem vai para o terminal com o prefixo `[preflight]` (seis etapas: credenciais, state do runner, parâmetro do token sem ler o valor, EC2, SSM, serviço via SSM); o **resultado** são linhas `chave=valor`, e a última é o diagnóstico. Com o runner ligado e online (medido na construção do lab, 17 s):
>
> ```text
> aws_account=****<4 dígitos da sua conta>
> aws_credentials=ok
> region=us-east-1
> state_bucket=base-config-<SEU RM>
> state=present
> state_instance_id=i-<id da sua EC2>
> token_param=present
> instance_count=1
> instance_id=i-<id da sua EC2>
> instance_state=running
> public_ip=<muda a cada stop/start>
> ssm_ping=Online
> runner_binary=present
> runner_service=active
> runner_config=present
> runners_in_config=1
> runner_verify=valid
> token_newer_than_config=no
> reason=runner_online
> diagnosis=reuse
> ```

`diagnosis=reuse` (exit `0`) quer dizer "o runner da aula passada ainda serve". Os outros diagnósticos são `start` (EC2 desligada), `restart` (serviço parado), `reconfigure`, `recreate` e `blocked` (algo que o script não resolve, com a dica do que fazer).

Se o diagnóstico não for `reuse`, o `runner-recover.sh` age sobre ele. Ele roda em **`--dry-run` por padrão**, mostrando o plano sem mudar nada; só com `--apply` ele liga a instância (ou reinicia o serviço) e espera o runner voltar:

```bash
bash /workspaces/FIAP-AI-Devops-CICD/04-GenAIOps/shared/runner/runner-recover.sh --dry-run
bash /workspaces/FIAP-AI-Devops-CICD/04-GenAIOps/shared/runner/runner-recover.sh --apply
```

<details>
<summary><b>⚠ Se der erro de rede: <code>no matching EC2 VPC found</code> (ou <code>Invalid index</code>, <code>nao ficou online no SSM</code>) no <code>runner-recover.sh</code></b></summary>
<blockquote>

Só acontece no diagnóstico `recreate`: o `runner-recover.sh` recria a EC2 do runner com o Terraform do Módulo 02, e o runner nasce dentro da rede `fiap-lab`. Os erros saem no meio da saída do script:

- **`no matching EC2 VPC found`**: não há VPC `fiap-lab` nesta conta.
- **`Invalid index`** em `eligible_subnet_ids`: a VPC existe, mas não tem subnets públicas.
- **`ERRO: a instancia i-... nao ficou online no SSM a tempo`**: falta a rota para a internet.

A rede `fiap-lab` (VPC, subnets públicas e route table), criada no [Lab 01.2](../01-Terraform/demos/02-Modules/README.md), não existe nesta conta ou está incompleta, por exemplo porque o lab do AWS Academy foi resetado. Você não precisa refazer o Lab 01.2: um script sobe a mesma rede com o mesmo Terraform, criando só o que estiver faltando.

**Onde:** terminal do Codespaces.

```bash
bash /workspaces/FIAP-AI-Devops-CICD/01-Terraform/scripts/recriar-rede.sh
```

> Saída esperada (leva cerca de 1 minuto quando precisa recriar e menos de 20 s quando a rede já existe; o id muda):
>
> ```text
> REDE_OK vpc-0abc... 6
> ```

Depois, rode de novo `bash /workspaces/FIAP-AI-Devops-CICD/04-GenAIOps/shared/runner/runner-recover.sh --apply` e continue o ritual daqui. Se o script disser que existem duas VPCs `fiap-lab` (erro `multiple EC2 VPCs matched`), não apague nada: chame o professor.

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: reboot, stop/start, terminate, Codespaces parado e sessão do Academy expirada não são a mesma coisa</b></summary>
<blockquote>

Cinco fenômenos diferentes que os alunos costumam misturar:

| O que aconteceu | O que muda | O runner sobrevive? |
|---|---|---|
| **EC2 reboot** | Nada de identidade: mesmo ID, mesmo IP público, disco intacto. | Sim. O serviço volta sozinho em ~1 min. |
| **EC2 stop/start** (o que acontece entre aulas) | O **IPv4 público autoatribuído geralmente muda**. ID, disco, `config.toml` e token continuam. | Sim. O runner **não precisa do IP**: é ele quem chama o GitLab (saída pela 443), não o contrário. IP novo não é runner perdido. |
| **EC2 terminate** | A instância e o disco somem. | Não. Só o Terraform + Ansible do módulo 02 recriam, com registro novo. |
| **Codespaces parado** | Só o seu ambiente de trabalho parou. A AWS e o GitLab nem notam. | Sim. Mas o terminal perde as variáveis exportadas e o `ssh-agent`: refaça o `ssh-add`. |
| **Sessão do Learner Lab expirada** | As credenciais AWS do Codespaces viram `ExpiredToken`. A EC2 **continua** no estado em que estava (se o Lab foi encerrado, o Academy **para** as instâncias). | Sim, mas você não consegue falar com a AWS até atualizar as credenciais (passo A). O GitLab mostra o runner offline enquanto a EC2 estiver parada. |

A regra prática: **`ExpiredToken` = passo A; runner `stopped` = `runner-recover.sh --apply`; runner `running` mas offline no GitLab = serviço parado, o `recover` reinicia via SSM**. Nunca registre o runner de novo sem olhar o `config.toml`: registro duplicado cria dois runners no projeto.

📚 Documentação oficial: [ciclo de vida da instância EC2](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html), para a diferença entre reboot, stop/start e terminate; [Stop and start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html), para o comportamento do IP público; [comandos do GitLab Runner](https://docs.gitlab.com/runner/commands/), para `verify` e `status` (o `verify` confirma o token, não que jobs estão rodando).

</blockquote>
</details>

**C. Confirme no GitLab e abra a demo.** Em **Settings → CI/CD → Runners** do `primeiro-projeto`, o runner aparece **online** com a tag `shell`. O `verify` do preflight prova que o token vale; só um job prova que o runner **executa**. Se quiser essa prova (recomendado na primeira aula do módulo), o `runner-test.sh` empurra um commit numa branch própria `runner-smoke` do seu projeto, com um único job `tags: [shell]`, e acompanha o resultado pelo journal da EC2 via SSM (sem consultar a API do GitLab, que limita chamadas):

```bash
GITLAB_REPO_SSH=git@gitlab.com:SEU-USUARIO/primeiro-projeto.git \
  bash /workspaces/FIAP-AI-Devops-CICD/04-GenAIOps/shared/runner/runner-test.sh
```

Troque `SEU-USUARIO` pelo seu usuário do GitLab (o mesmo remote `origin` da sua cópia local). A última linha do resultado é `result=Job succeeded` (exit `0`; na construção do lab, o runner pegou o job em 16 s e o script fechou em 25 s). A branch `runner-smoke` pode ficar no projeto, ela não afeta a sua branch principal. Depois, siga para a demo do dia.

Os três scripts, a tabela de diagnósticos e as mensagens de erro comuns estão documentados em [`shared/runner/README.md`](shared/runner/README.md).

## Arquitetura

![Arquitetura da Demo 5.1: um git push no primeiro-projeto dispara o pipeline no runner (EC2 com LabRole); o pipeline valida, testa, avalia o golden set, empacota e aplica; a Lambda qc-assistant-api é publicada no alias candidate, passa por smoke e avaliação pós-deploy e só então é promovida ao alias live, que a API Gateway expõe ao cliente, com o registro do release gravado no S3; o assistente busca nas políticas versionadas e consulta o status do pedido na API de pedidos (Lambda + DynamoDB); o rollback é um job manual que move o alias live de volta.](01-Release-RAG-Quality-Gates/img/arquitetura-release-rag.png)

Capacidades, não logotipos: `cliente → API → roteamento de intenção → (busca em documentos | API de pedidos) → composer determinístico (sem LLM) → citações e recusas → avaliador → telemetria`. A Demo 5.2 adiciona, à esquerda desse fluxo, o runtime local instrumentado com OpenTelemetry e o stack Collector → Jaeger + Prometheus + Grafana.

| Componente | Onde roda | Papel |
|---|---|---|
| `quantum-assistant/core/` | Lambda (5.1) **e** servidor HTTP local (5.2) | Roteamento, busca BM25 com filtros de vigência e ACL, composer extrativo, versões. Stdlib pura, Python 3.10 e 3.12. |
| `quantum-assistant/corpus/*.md` | Dentro do bundle | Políticas fictícias com `effective_date`, `status`, `tenant`, `access_level`. Mudar uma vírgula muda `corpus_version`. |
| `quantum-assistant/evals/` | Runner (offline) e pós-deploy | Golden set ≥ 20 casos, `adversarial.jsonl`, `thresholds.json`, `run_eval.py` (JSON + JUnit, exit 0/1). |
| Lambda `qc-assistant-api` (aliases `candidate`/`live`) | AWS, `LabRole` | O assistente publicado. Versão numerada por `apply`. |
| Lambda `qc-assistant-orders` + DynamoDB `qc-assistant-orders` | AWS | API de pedidos fictícia com autorização por token de laboratório. |
| API Gateway HTTP `qc-assistant` | AWS | `/assistant/ask`, `/health` → `live`; `/candidate/...` → `candidate`; `/orders/{id}` → pedidos. |
| `s3://base-config-<SEU RM>/quantum-assistant/` | AWS | State do Terraform (`terraform.tfstate`) e manifestos de release (`releases/`). |
| GitLab Runner (módulo 02) | EC2 `t3.small` | Executa o pipeline: Python 3.10, Terraform, TFLint, Checkov, pytest. Sem Docker. |

Os recursos novos usam o prefixo **`qc-assistant-`** e a key de state **`quantum-assistant/terraform.tfstate`**: eles convivem com a API do módulo 03 sem tocar nela. A rede `fiap-lab` do módulo 01 é só **lida**. Tudo isso foi provado na conta do Learner Lab: o `terraform plan` tem **zero** recursos `aws_iam_*` (as Lambdas usam o `LabRole`), as Lambdas ficam fora de VPC, e o `apply` dos 25 recursos leva ~30-40 s (o DynamoDB é o gargalo, ~19 s). Latência quente do assistente, do Codespaces para `us-east-1`: p95 em torno de 0,5 s; a Lambda em si gasta de 2 a 40 ms, o resto é rede.

## Custo do módulo

| Recurso | Custo no volume do lab | Quando cobra |
|---|---|---|
| Lambda (2 funções), API Gateway HTTP, DynamoDB on-demand | Centavos ou zero | Só por chamada. Parados, custam ~US$ 0. Medido na validação deste módulo na conta do Learner Lab: ~200 requisições por aluno ≈ **US$ 0,0005** (turma de 40 ≈ US$ 0,02), antes do free tier, que zera Lambda e Logs nesse volume. |
| S3 (state + manifestos) | ~US$ 0 | Kilobytes. |
| CloudWatch Logs | ~US$ 0 | Volume do lab fica na faixa gratuita. |
| **EC2 do runner (`t3.small`)** | **~US$ 0,02/h, ~US$ 0,50/dia ligada** | **Enquanto estiver `running`**, com ou sem pipeline. É o único vilão. |
| Stack da 5.2 (Jaeger, Prometheus, Grafana) | US$ 0 | Roda em Docker **dentro do Codespaces**; conta nas horas do Codespaces, não na AWS. |

> [!CAUTION]
> **Entre aulas, desligue o runner** (`stop-instances`, como no passo 15 do [Lab 03.2](../03-CICD/02-Validando-e-gerando-relatorios/README.md#passo-15)); o `runner-recover.sh --apply` liga de volta no dia seguinte. **Não destrua o runner nem a API `qc-assistant-` entre a 5.1 e a 5.2 do mesmo dia.** A limpeza segura de cada demo (`cleanup.sh --dry-run` antes de qualquer remoção) está no fim de cada README e só remove o que aquela demo criou. A rede `fiap-lab` nunca é destruída.

## Decisões pedagógicas

- **Por que o mesmo `primeiro-projeto` e o mesmo runner?** Porque o módulo é sobre evoluir um sistema que já está em produção, não sobre começar outro. O `quantum-assistant/` entra como subpasta; os arquivos do módulo 03 continuam na raiz e não são reaplicados.
- **Por que sem LLM, de novo?** Bedrock bloqueado no Learner Lab e, mais importante, **determinismo**: a mesma regressão reprova pelo mesmo motivo em todas as máquinas da turma. Quando um LLM entrar, ele troca o composer; gates, release e telemetria continuam.
- **Por que aliases em vez de `apply` direto?** Para o aluno **ver** a diferença entre "subiu" e "entrou em produção", e para o rollback levar um minuto.
- **Por que o OpenTelemetry roda no Codespaces e não na Lambda?** X-Ray e afins bloqueados; exportar OTLP da Lambda exigiria um Collector exposto na internet. O runtime local usa **o mesmo core e o mesmo release**: o que você observa é o que publicou. Isso é dito como limite em todo print.
- **Por que métricas "modestas"?** Hit@k mede busca, não resposta. Sem LLM não há `faithfulness` nem `hallucination rate` reais, e não fingimos que há. Toda métrica de probe é rotulada *proxy sintético*.

## Fundamentação

- Gene Kim, Jez Humble, Patrick Debois e John Willis, *The DevOps Handbook*: as Three Ways (fluxo, feedback, aprendizagem) estruturam as duas demos. Os autores não tratam de RAG nem de LLM: a transposição é nossa.
- Nicole Forsgren, Jez Humble e Gene Kim, *Accelerate*: entrega frequente e segura depende de teste automatizado e de feedback rápido; aplicamos a ideia a mudanças de **conteúdo** (políticas), não só de código.
- Cathy Chen et al., *Reliable Machine Learning* (2022): golden sets, avaliação offline, rollout controlado, incidentes e rollback de conjunto. O livro trata de ML clássico; a parte de RAG vem de fontes próprias.
- Shahul Es et al., *RAGAs* (EACL 2024): avaliação de RAG é multidimensional. **Nosso avaliador não é o RAGAs**; usa a separação entre recuperação e resposta.
- OWASP GenAI, *Prompt Injection*; NIST, *AI RMF: Generative AI Profile*: a fronteira entre instrução e conteúdo não confiável, e medição como controle de risco.
- D. Sculley et al., *Hidden Technical Debt in Machine Learning Systems* (NeurIPS 2015): dependência de dados como dívida técnica. Aqui, a política versionada é o "dado" que muda sem o código mudar.

## Próximo passo

Comece pela **[Demo 5.1](01-Release-RAG-Quality-Gates/README.md)**. Ao terminar, a pergunta que ela deixa aberta ("e se piorar depois, sem commit?") é a abertura da **[Demo 5.2](02-Operacao-Observabilidade-Qualidade-Continua/README.md)**.
