# 03 — CI/CD e Deployment de Aplicações de IA

Módulo prático da **Aula 4** sobre **integração e entrega contínua de uma aplicação de IA** com **GitLab CI/CD**, rodando no **GitLab Runner** que você provisionou com Ansible no módulo 02. O pipeline publica o **Assistente de Políticas da Quantum Commerce**, que responde perguntas sobre trocas, devoluções, entrega e garantia citando a política de onde tirou a resposta. Depois, ganha os **quality gates de IA**: testes de comportamento que reprovam o pipeline quando o assistente passa a responder errado.

São **2 labs sequenciais**, os dois sobre o repositório `primeiro-projeto` no GitLab e o runner registrado no módulo anterior.

## Aula 4 na Quantum Commerce

> **Segunda-feira, 10h. Uma semana depois do runner ficar online.**
> **Helena Marques**, Head de Engenharia de IA e Entrega Contínua, abre a reunião com uma reclamação impressa na mão:
>
> > *— "Um cliente perguntou ao atendimento qual era o prazo para devolver um fone. Ouviu 30 dias. A política diz 7. Ele devolveu no dia 20, a gente recusou, e virou reclamação no Procon. Agora o jurídico quer saber de onde saiu a resposta. Quero o Assistente de Políticas no ar, respondendo **só** o que está na política, dizendo **qual** política usou. E nenhuma mudança na política ou no código vai para produção sem passar pelos testes."*
>
> **Diego Tavares**, AI SRE sênior, completa: *— "E sem ninguém rodando `terraform apply` do laptop. Todo push na `master` testa, publica e confere o assistente no ar. Se o teste reprovar, nada sobe."*

A pergunta central da disciplina é: **como transformar aplicações experimentais de IA em sistemas reproduzíveis, testáveis e continuamente entregáveis, sem perder qualidade, segurança e controle operacional?** Neste módulo ela ganha uma resposta concreta: **um `git push` que testa o comportamento do assistente antes e depois de publicar**.

## Os 2 labs deste módulo

| # | Lab | O que você faz | Tempo estimado |
|---|-----|----------------|----------------|
| **03.1** | **[Primeiro pipeline](01-Primeiro-pipeline/README.md)** | Cria o `.gitlab-ci.yml` com `plan` (CI), `apply` (CD) e um teste de fumaça, e vê o pipeline publicar o Assistente de Políticas sozinho. No fim, você faz perguntas reais com `curl` e lê a política citada na resposta. | 40-55 min |
| **03.2** | **[Quality gates de IA](02-Validando-e-gerando-relatorios/README.md)** | Adiciona os gates: `fmt`/`validate`, TFLint, Checkov, `terraform test` e **pytest** com o *golden set* (perguntas com resposta conhecida). Inclui uma avaliação contra o endpoint real depois do deploy. Você **quebra uma política de propósito**, vê o pipeline reprovar e conserta. | 50-70 min |

Faça na ordem: 03.1 → 03.2. O 03.2 evolui o mesmo `.gitlab-ci.yml` do 03.1.

![Arquitetura do módulo 03: um git push no primeiro-projeto do GitLab dispara o pipeline no runner próprio (EC2 com LabRole); o pipeline testa, roda terraform plan e apply com o state no S3, publica a API Gateway e a Lambda do Assistente de Políticas, que busca nas políticas versionadas e consulta o status do pedido no DynamoDB, e por fim avalia o endpoint publicado.](01-Primeiro-pipeline/img/arquitetura-rag-pipeline.png)

## O que o assistente faz e o que ainda não faz

O Assistente de Políticas é um **RAG extrativo**: recebe a pergunta, **busca** os trechos mais parecidos nas políticas versionadas no repositório (trocas, devoluções, entrega, garantia, Black Friday e status do pedido) e **devolve o trecho encontrado, com a fonte**. Quando a pergunta é sobre um pedido específico, o status vem da **API de pedidos** (DynamoDB), não de um documento.

| Faz | Ainda não faz (e por quê) |
|-----|---------------------------|
| Busca nas políticas e devolve o trecho com a fonte citada | **Não gera texto com LLM.** O Bedrock está bloqueado no Learner Lab, e o LLM gerativo é tema da Aula 5. |
| Recusa pergunta fora do escopo das políticas, em vez de inventar | **Não tem avaliação com juiz (LLM-as-a-judge).** Também fica para a Aula 5. |
| Passa por testes de comportamento antes e depois do deploy | Não "conversa": cada pergunta é independente, sem memória de conversa. |

Isso não é uma limitação escondida, é o recorte da aula. O que este módulo ensina vale igual quando o LLM entrar: **se ninguém mede o comportamento, cada deploy é uma aposta**. Trocar a busca por um modelo gerativo muda a peça do meio, não o pipeline que a protege.

## Pré-requisitos do módulo

> [!WARNING]
> Este módulo **depende dos módulos 01 e 02**. Antes de começar **qualquer** lab daqui:
>
> - [ ] **Módulo 02 concluído**: GitLab Runner **online** com as tags `shell` e `terraform`, e o repositório **`primeiro-projeto`** existe no seu GitLab com o código versionado.
> - [ ] **Cópia local do projeto** em `/home/vscode/environment/primeiro-projeto` (passo 9 do módulo 02).
> - [ ] **Chave SSH do GitLab** em `/home/vscode/.ssh/gitlab`, cadastrada no GitLab.
> - [ ] **Credenciais AWS do Academy atualizadas** no Codespaces.
>
> **Valide rapidamente** (no terminal do Codespaces):
>
> ```bash
> aws sts get-caller-identity --query Account --output text
> ls /home/vscode/environment/primeiro-projeto
> ```
>
> Saída esperada: o número da sua conta (12 dígitos) e a lista de arquivos do projeto. Depois confira, no GitLab, em **Settings → CI/CD → Runners**, que existe um runner **online** com a tag `shell`. Se faltar qualquer item, volte ao [módulo 02](../02-Ansible/01-provisionando-gitlab-runner/README.md).

## Decisões pedagógicas

- **Por que GitLab CI/CD e não GitHub Actions?** Você já registrou um runner próprio no módulo 02. Manter o mesmo runner e o mesmo `primeiro-projeto` deixa o arco coeso e mostra o runner self-hosted em uso real.
- **Por que `tags: shell`?** O runner do módulo 02 usa o executor `shell`: os jobs rodam direto na EC2, onde o Ansible já instalou Terraform, TFLint e o `/opt/venv` com Checkov e pytest. A EC2 tem o `LabRole`, então o pipeline acessa a AWS sem nenhuma chave cadastrada no GitLab.
- **Por que um RAG extrativo, sem LLM?** Porque ele é determinístico: a mesma pergunta traz sempre o mesmo trecho. Isso permite um teste com resposta esperada exata, que é o que transforma "achei que estava bom" em gate objetivo. Mais completo para IA, sem ser mais difícil de operar.
- **Por que testar antes e depois do deploy?** O teste offline (pytest) barra a mudança errada antes de chegar na AWS. O teste contra o endpoint real confirma que o que foi publicado é o que foi testado.

## Custo do módulo

O pipeline cria uma **API Gateway HTTP**, uma **Lambda** e uma tabela **DynamoDB** on-demand: todas cobram por uso e ficam em centavos (ou zero) no volume do lab. O que custa de verdade é o **runner** do módulo 02, a EC2 `t3.small` (~US$ 0,02/h, ~US$ 0,50 por dia ligada), que precisa estar ligada para os pipelines rodarem.

> [!CAUTION]
> **Não destrua o runner antes de terminar o módulo 03**: sem ele, os pipelines ficam parados em "stuck". A limpeza (API e runner) está no **fim do Lab 03.2**. Entre uma aula e outra, o runner pode ficar desligado; o 03.2 mostra como desligar e como destruir.

## Próximo passo

Depois dos 2 labs deste módulo, prossiga para o **[Trabalho Final](../Trabalho-final/README.md)**, onde você junta Terraform, Ansible e CI/CD num projeto entregável.
