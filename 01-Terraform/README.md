# 01 — Terraform: Infrastructure as Code para IA

Módulo prático da **Aula 2** da disciplina **AI-Devops-CICD**. Aqui você, AI DevOps Engineer da **Quantum Commerce**, escreve em código a infraestrutura em que o **Quantum Commerce AI Customer Assistant** (o Assistente de Rastreio e Políticas, Projeto 2) vai rodar: a primeira EC2 com a página do assistente, a rede compartilhada `fiap-lab`, a camada web atrás de um load balancer, o state remoto no S3 e os ambientes `dev` e `prod`.

São **5 demos guiadas em sala** e **2 exercícios** que você resolve sozinho.

## Aula 2 na Quantum Commerce

> **Segunda-feira, 8h30. Sua primeira semana como AI DevOps Engineer da Quantum Commerce, antes da primeira demo.**
> Os protótipos de IA aprovados pelo Comitê de Tecnologia funcionam, mas nenhum está pronto para produção. A infraestrutura foi criada à mão, no console da AWS, por pessoas diferentes em momentos diferentes. **Helena Marques**, Head de Engenharia de IA e Entrega Contínua, abre a reunião:
>
> > *— "Ninguém aqui sabe recriar o ambiente do assistente. Se a conta cair ou alguém apagar uma rede, levamos dias para voltar, e nem temos certeza de que volta igual. Quero essa infraestrutura como **código versionado**: que qualquer pessoa do time destrua e recrie idêntica, enxergando antes o que vai mudar."*
>
> **Diego Tavares**, AI SRE sênior, completa: *— "E sem depender de quem clicou. Hoje o ambiente de um responde diferente do ambiente de outro, e o deploy só acontece quando uma pessoa específica está disponível. Antes de falar em modelo, a gente precisa de uma base que se reproduz."*

A pergunta central da disciplina é:

> **Como transformar aplicações experimentais de IA em sistemas reproduzíveis, testáveis e continuamente entregáveis, sem perder qualidade, segurança e controle operacional?**

Neste módulo respondemos à parte **reproduzível**, e usamos uma pergunta concreta como medidor: **quanto tempo a Quantum Commerce leva para recriar do zero, de forma confiável, a infraestrutura do assistente?** No começo da semana a resposta é "dias, na mão, e ninguém tem certeza". No fim do módulo, é um `terraform apply`.

## O que este módulo entrega e o que ainda não entrega

| Entrega | Ainda não entrega (e quando chega) |
|---------|-------------------------------------|
| Infraestrutura AWS do assistente descrita em código Terraform, criada e destruída por comando | **IA integrada.** Nenhum modelo, inferência ou RAG roda neste módulo. O assistente que busca nas políticas e cita a fonte (RAG extrativo, sem LLM) chega na Aula 4 (`03-CICD`); os quality gates bloqueantes e a observabilidade de GenAI, na Aula 5 (`04-GenAIOps`). |
| Uma página **estática** do AI Customer Assistant servida pelo Nginx, que diz qual máquina respondeu | **API de pedidos.** Status de pedido vem do sistema de origem e entra na Aula 4. |
| State remoto compartilhado com lock e ambientes `dev`/`prod` isolados por workspace | **Deploy automático.** Aqui o `apply` ainda roda no seu terminal; o pipeline que roda por você chega na Aula 4. |

## As 5 demos

As demos contam a mesma semana em sequência: cada uma resolve uma dor que a anterior deixou exposta.

| # | Demo | O que você faz | Depende de | Tempo |
|---|------|----------------|------------|-------|
| **01.1** | **[Plan e Apply: o primeiro recurso da Quantum Commerce em código](demos/01-Plan-Apply/README.md)** | Ciclo `init` → `plan` → `apply` → `destroy` com uma EC2 vazia; depois a EC2 `quantum-ai-web` com Nginx, provisionada **via SSM** (sem chave SSH, sem porta 22), servindo a página inicial do assistente. Lê o plano (alteração in-place vs. recriação), vê o Terraform **detectar drift** e inspeciona o **estado**. | Setup do ambiente | ~65 min |
| **01.2** | **[Módulos: a rede compartilhada da Quantum Commerce](demos/02-Modules/README.md)** | Cria a VPC `fiap-lab` (10.0.0.0/16, uma subnet pública `Tier=Public` por zona, Internet Gateway e route table) chamando os módulos `VPC` e `RouteTables` pelas pastas `vpc-call` e `RT-call`. **Adota** um Security Group criado à mão com `import` e o renomeia sem destruir com `moved`. | 01.1 | ~45 min |
| **01.3** | **[Count: a camada web do assistente escala mudando um número](demos/03-Count/README.md)** | Sobe 2 EC2 `t3.micro` atrás do ALB `quantum-ai-alb`, registradas no target group `quantum-ai-tg` e provisionadas via SSM em paralelo. Escala para 3 e volta para 2 mudando só o `count`, com o `check` block `alb_esta_no_ar` conferindo o HTTP 200. O rodapé da página mostra qual máquina respondeu. | 01.2 (lê a rede) | ~35 min |
| **01.4** | **[State remoto: dois engenheiros da Quantum Commerce na mesma infra sem se atropelar](demos/04-State/README.md)** | Liga o versionamento do bucket `base-config-<SEU-RM>`, move o state para o S3 (key `demo-state/quantum/terraform.tfstate`) com locking nativo (`use_lockfile`), recupera o state depois de apagar a cópia local, vê o **lock** barrar o seu `apply` concorrente com o do Diego, faz cirurgia de estado (`state list/show/mv/rm`), restaura uma versão anterior e vê segredos em texto claro no state. | 01.2 (lê a rede) e o bucket do setup | ~50 min |
| **01.5** | **[Workspaces: experimentando sem tocar em produção na Quantum Commerce](demos/05-Workspaces/README.md)** | Cria os workspaces `dev` e `prod` sobre o mesmo código (key `demo-workspaces/terraform.tfstate`, que vira `env:/dev/...` e `env:/prod/...` no bucket), com uma EC2 dimensionada por ambiente (`quantum-ai-dev` em `t3.micro`, `quantum-ai-prod` em `t3.small`). Valida com `terraform test` sem criar infraestrutura e discute o que workspace **não** isola. | 01.2 (lê a rede) e 01.4 | ~35 min |

Os tempos são de aula (ler, executar, observar), somando as partes do mapa de cada README. Faça as demos **na ordem**: cada uma assume conceitos e recursos da anterior.

## A rede `fiap-lab` é permanente

![Arquitetura do Lab 01.2: as pastas vpc-call e RT-call chamam os módulos VPC e RouteTables, que criam a VPC fiap-lab 10.0.0.0/16 em us-east-1 com uma subnet pública por zona, o Internet Gateway e a route table com a rota padrão; a faixa no rodapé avisa que a rede é persistente e que vpc-call e RT-call não devem ser destruídas.](demos/02-Modules/images/arquitetura-modules.png)

A demo 01.2 cria a rede **uma vez**, e ela fica de pé até o fim do curso. Quem usa essa rede só a **lê**, procurando a VPC pela tag `Name=fiap-lab` e as subnets pela tag `Tier=Public`: as demos 01.3, 01.4 e 01.5, o exercício 01.B, o GitLab Runner do módulo 02 (Ansible), os pipelines do módulo 03 (CI/CD) e o Trabalho Final. Cada lab destrói apenas os recursos que ele mesmo criou.

> [!CAUTION]
> **Nunca rode `terraform destroy` nas pastas `vpc-call` e `RT-call`**, nem no fim de uma demo, nem para "limpar" depois de um erro, nem no fim do módulo. Sem a VPC `fiap-lab`, os labs seguintes param no primeiro `plan`. VPC, subnets, Internet Gateway e route tables **não cobram por hora**: manter a rede de pé não custa nada.

## Os 2 exercícios

Os exercícios são **desafios, não tutoriais**: o README diz o que construir e como provar que funcionou, mas o código Terraform é seu, escrito a partir das demos e da documentação oficial. As pistas ficam colapsadas, em níveis, e você decide quando abrir. Os dois terminam com um `DECISION.md` curto, em que você defende a escolha técnica para a Helena, e com a limpeza do que você criou.

| # | Exercício | O que você constrói | Depende de | Tempo |
|---|-----------|---------------------|------------|-------|
| **01.A** | **[Count na prática: filas para o catálogo da Quantum Commerce](exercicios/count/README.md)** | Para o Projeto 3 (Categorização do Catálogo), um único `aws_sqs_queue` com `count` ligado a uma variável numérica cria as filas `qc-catalog-classification-01`, `-02`, `-03`. Você prova pelo `plan` que subir para 5 só acrescenta 2 e voltar para 3 só remove 2. Nenhuma IA roda: é só a infraestrutura das filas, na pasta `~/exercicio-count`. | 01.3 | ~20-25 min |
| **01.B** | **[State remoto e Workspaces: dev e prod do AI Customer Assistant](exercicios/State-e-workspace/README.md)** | Na pasta `~/exercicio-state`, um único código cria 2 EC2 `t3.micro` por workspace (`quantum-ai-dev-01`, `-02`, `quantum-ai-prod-01`, `-02`), com AMI Amazon Linux 2023 descoberta por `data "aws_ami"`, a rede `fiap-lab` só lida e o state no seu bucket (key `exercises/quantum-ai-workspaces/terraform.tfstate`, com `use_lockfile`). Você prova que os estados são separados, que o state volta em outra sessão e que destruir `dev` não toca em `prod`. | 01.2, 01.4 e 01.5 | ~30-35 min |

O 01.A já pode ser feito logo depois da demo 01.3; o 01.B fecha o módulo. Os exercícios trabalham em pastas suas, fora do clone do curso, e cada um usa uma key de state própria: nenhum deles sobrescreve o state de uma demo.

## Principais pontos de aprendizagem do módulo

- o ciclo de vida do Terraform (`init`, `plan`, `apply`, `destroy`) e como **ler o plano** antes de aplicar: alteração in-place vs. recriação
- detectar e reconciliar **drift**, a mudança feita fora do código
- descrever recursos AWS em HCL, com `validation` que falha cedo e `data source` que descobre AMI, rede e zonas em vez de IDs fixos
- escrever a rede uma vez como **módulo** e reutilizá-la; adotar infraestrutura existente (`import`) e refatorar sem destruir (`moved`)
- escalar com `count` atrás de um Application Load Balancer e conferir a saúde com um `check` block
- trabalhar em time com **state remoto** no S3 (lock nativo, versionamento, cirurgia de estado) e saber que segredos ficam em texto claro no state
- isolar `dev` e `prod` com workspaces, testar a configuração com `terraform test` e reconhecer o que workspace não isola (conta, credenciais, raio de impacto)

## Pré-requisitos do módulo

> [!WARNING]
> Antes de começar **qualquer** demo deste módulo:
>
> - [ ] Codespaces da disciplina aberto, com terminal funcionando ([setup do ambiente](../00-create-codespaces/README.md))
> - [ ] Credenciais AWS do Academy atualizadas no Codespaces ([Início de aula](../00-create-codespaces/Inicio-de-aula.md)); a sessão do Learner Lab expira
> - [ ] Bucket `base-config-<SEU-RM>` criado no setup do ambiente (usado pela 01.4, pela 01.5 e pelo exercício 01.B)
> - [ ] Terraform, AWS CLI e `jq` disponíveis: o devcontainer já entrega os três (Terraform 1.10.5); a AWS CLI e o `jq` enviam os scripts via SSM nas demos 01.1 e 01.3
>
> **Valide rapidamente** (terminal do Codespaces):
>
> ```bash
> aws sts get-caller-identity --query Account --output text
> terraform -version
> aws s3 ls | awk '/base-config-/{print $3}'
> ```
>
> Saída esperada: o número da sua conta (12 dígitos), uma linha começando com `Terraform v1.10` e o nome do seu bucket `base-config-...`. `ExpiredToken` no primeiro comando quer dizer credencial vencida: refaça o [Início de aula](../00-create-codespaces/Inicio-de-aula.md). Se o bucket não aparecer, volte ao [setup do ambiente](../00-create-codespaces/README.md).

Nunca usou Terraform, um terminal ou Git? Cada README de demo diz, em todo passo, o que fazer, onde rodar, o que deve aparecer e o que fazer se aparecer outra coisa.

## Modernização do código

O HCL deste módulo segue o Terraform atual e as restrições do Learner Lab:

- `required_version = ">= 1.10"` e provider `hashicorp/aws ~> 6.0` declarados no `versions.tf` de cada raiz
- referências diretas (`var.x`), sem a interpolação solta do estilo 0.11 (`"${var.x}"`); a sintaxe `${...}` aparece só dentro de textos que juntam valores
- AMI Amazon Linux 2023 descoberta por `data "aws_ami"`, em vez de ID fixo que expira; as demos 01.3, 01.4 e 01.5 também consultam quais zonas ofertam o tipo de instância, porque a `us-east-1e` não oferta `t3.micro`
- `default_tags` no provider (tags de governança em todos os recursos) e `validation` nas variáveis-chave (região, tipo de instância, ambiente e CIDR da rede)
- **provisionamento via AWS Systems Manager (SSM)** nas demos 01.1 e 01.3, no lugar do antigo `provisioner` por SSH: a EC2 sobe com o `LabInstanceProfile`, e um `terraform_data` com `local-exec` envia o script com `aws ssm send-command`, mostra o log no próprio `apply` e aborta se o script falhar, **sem chave SSH e sem porta 22 aberta**
- recursos modernos exercitados nas demos: `import` e `moved` blocks, `check` block, backend S3 com `use_lockfile` (sem tabela DynamoDB) e `terraform test`

## Custo do módulo

As demos criam recursos pagos por hora. São baratos e de vida curta, **desde que você destrua os recursos de cada demo ao final dela**.

| Recurso | Custo aproximado | Onde aparece |
|---------|------------------|--------------|
| EC2 `t3.micro` | ~US$ 0,01/h cada | 01.1, 01.4, 01.5 (`dev`), até 3 na 01.3 e 4 no exercício 01.B |
| EC2 `t3.small` | ~US$ 0,02/h | 01.5 (`prod`) |
| Application Load Balancer | ~US$ 0,0225/h + uso | só na 01.3; esquecer a 01.3 ligada por um dia consome cerca de US$ 1,20 |
| VPC `fiap-lab`, subnets, Internet Gateway, route tables | sem custo por hora | criadas na 01.2 e **mantidas** até o fim do curso |
| Filas SQS, state e versões no S3 | centavos ou zero | exercício 01.A, demos 01.4 e 01.5, exercício 01.B |

> [!CAUTION]
> **Ao final de cada demo, rode o `terraform destroy -auto-approve` que o README dela indica, na pasta dela** (na 01.5 e no 01.B, em cada workspace). Isso remove só o que aquela demo criou. A rede fica: **nunca destrua `vpc-call` nem `RT-call`**. Na 01.1, o passo 31 também fecha a regra da porta 80 que a AWS CLI abriu no Security Group `default`, fora do Terraform.

## Próximo módulo

Depois das 5 demos e dos 2 exercícios, siga para **[02 — Ansible](../02-Ansible/README.md)** (Aula 3). A infraestrutura virou código, mas os servidores ainda são configurados à mão e o `apply` ainda sai do laptop de alguém: a Helena pede uma máquina de build da Quantum Commerce, criada com Terraform **dentro da rede `fiap-lab`** e configurada por um playbook Ansible idempotente até virar o GitLab Runner que vai executar os pipelines do assistente.
