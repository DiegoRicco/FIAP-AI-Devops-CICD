# Exercício 01.B - State remoto e Workspaces: dev e prod do AI Customer Assistant

<!--
CONVENÇÃO DE PRINTS DESTE README (nota para o professor, não aparece renderizada)

Cada bloco "> 📸 Nota do autor" marca o lugar exato onde uma imagem entra. Depois de
capturar e salvar o arquivo em img/, troque o bloco pela linha de imagem comentada logo
abaixo dele. As imagens antigas desta pasta são de uma versão anterior do curso e estão
comentadas com TROCAR-IMAGEM.
-->

> **Segunda-feira, 10h. Quantum Commerce, infraestrutura do AI Customer Assistant.**
> O assistente vai receber mudanças de infraestrutura toda semana, e o time quer testar cada uma antes de levar para a diretoria. **Helena Marques**, Head de Engenharia de IA e Entrega Contínua, não aceita teste no mesmo state de produção:
>
> > *— "Sua tarefa é criar a infraestrutura de desenvolvimento e de produção da Quantum Commerce usando o mesmo código. Quero identificar cada máquina pelo ambiente, recuperar o state mesmo em outra sessão e conseguir destruir dev sem tocar em prod."*
>
> **Diego Tavares**, AI SRE sênior, completa: *— "Nas demos eu te mostrei cada peça. Agora quero ver você montar sozinho: o HCL é seu. Me traz a prova de que dev e prod têm estados separados no S3."*

Os comandos `bash` rodam **no terminal do Codespaces**. A conferência é feita no terminal e, se quiser ver também, **no console da AWS** (EC2 e S3).

Este é um **desafio**, não uma demo: o README diz **o que** construir e **como provar** que funcionou, mas não traz o código Terraform pronto. Os comandos de procedimento (`init`, `workspace`, `plan`, `apply`, `destroy`, `aws ...`) estão aqui para você copiar; os arquivos `.tf` você escreve. Se travar, as [pistas](#pistas) abrem em três níveis, do mais leve ao mais direto.

> [!WARNING]
> **Pré-requisitos obrigatórios antes de começar:**
>
> - [ ] Credenciais AWS do Academy atualizadas no Codespaces, ver [Preparando Credenciais](../../../00-create-codespaces/Inicio-de-aula.md)
> - [ ] [Demo 01.2 — Módulos](../../demos/02-Modules/README.md) aplicada: a VPC `fiap-lab` com subnets `Tier=Public` precisa existir
> - [ ] [Demo 01.4 — State remoto](../../demos/04-State/README.md) e [Demo 01.5 — Workspaces](../../demos/05-Workspaces/README.md) concluídas
> - [ ] O bucket `base-config-<SEU-RM>` criado no [setup da disciplina](../../../00-create-codespaces/README.md)
>
> **Valide rapidamente (credencial, VPC e bucket de uma vez):**
>
> ```bash
> aws sts get-caller-identity --query Account --output text
> aws ec2 describe-vpcs --region us-east-1 --filters "Name=tag:Name,Values=fiap-lab" --query "Vpcs[0].VpcId" --output text
> aws s3 ls | awk '/base-config-/{print $3}'
> ```
>
> Saída esperada: três linhas, nesta ordem: o número da conta (12 dígitos), um `vpc-...` e o nome do seu bucket `base-config-...`. Se a segunda linha vier `None`, a rede não existe nesta conta (por exemplo, depois de um reset do AWS Academy). Você não precisa refazer o [Lab 01.2](../../demos/02-Modules/README.md): no terminal do Codespaces, rode `bash /workspaces/FIAP-AI-Devops-CICD/01-Terraform/scripts/recriar-rede.sh`. Em cerca de 1 minuto ele recria a rede, e a última linha é `REDE_OK vpc-0abc... 6`. Depois, rode a validação de novo.
>
> **Tempo estimado: 30–35 min** (execução pura ~8 min; o resto é escrever o HCL, ler a documentação e conferir as provas).

## Sua missão

Entregar, num projeto Terraform **seu**, na pasta `~/exercicio-state` (fora do clone do curso):

- **um único código** que cria **2 EC2 por workspace**, nomeadas pelo ambiente e pelo índice: `quantum-ai-dev-01`, `quantum-ai-dev-02`, `quantum-ai-prod-01`, `quantum-ai-prod-02`;
- o state guardado no **seu** bucket S3, numa key só deste exercício, com lock;
- a **prova** de que `dev` e `prod` têm estados separados, de que o state é recuperado em outra sessão e de que destruir `dev` não encosta em `prod`;
- um `DECISION.md` curto, escrito por você, defendendo (ou não) workspaces para a Helena.

Limites: a rede é **só lida**. Nada de criar VPC, subnet, internet gateway ou route table; o projeto cria apenas as EC2.

## O que você já sabe

| Peça do desafio | Onde você aprendeu | O que reaproveitar de lá |
|---|---|---|
| Descobrir a VPC `fiap-lab` e uma subnet pública por tag | [Demo 01.2 — Módulos](../../demos/02-Modules/README.md) | as tags `Name=fiap-lab` e `Tier=Public` que a demo grava na rede |
| Várias instâncias iguais com `count` e nome por índice | [Demo 01.3 — Count](../../demos/03-Count/README.md) | o padrão de nome com número formatado |
| Backend S3 com `use_lockfile` e o `init` que recupera o state | [Demo 01.4 — State remoto](../../demos/04-State/README.md) | o bloco `backend "s3"` e a prova do "0 added" |
| `terraform.workspace`, `env:/<ws>/...` no S3 e destroy por ambiente | [Demo 01.5 — Workspaces](../../demos/05-Workspaces/README.md) | o ciclo `new`/`select`/`show` e a listagem do bucket |

Abra os arquivos `.tf` das demos para consultar a **estrutura**. Não copie a pasta de uma demo para cá: ela aponta para outra key de backend e outros nomes, e você misturaria estados.

## Mapa do exercício

| Parte | O que você faz | Passos | Tempo |
|-------|----------------|--------|-------|
| [Parte 1](#parte-1) | Criar a pasta, escrever o HCL e inicializar o backend | [1](#passo-1) · [2](#passo-2) · [3](#passo-3) · [4](#passo-4) | ~15 min |
| [Parte 2](#parte-2) | Subir `dev` e `prod` e conferir na AWS e no S3 | [5](#passo-5) · [6](#passo-6) · [7](#passo-7) · [8](#passo-8) | ~7 min |
| [Parte 3](#parte-3) | Provar recuperação do state e isolamento entre ambientes | [9](#passo-9) · [10](#passo-10) · [11](#passo-11) · [12](#passo-12) | ~6 min |
| [Parte 4](#parte-4) | Escrever o `DECISION.md` e limpar | [13](#passo-13) · [14](#passo-14) · [15](#passo-15) · [16](#passo-16) | ~7 min |

---

## Requisitos obrigatórios

O que o seu código precisa **fazer**. Como escrever é com você.

| # | Requisito | Como você vai saber que cumpriu |
|---|-----------|----------------------------------|
| R1 | A AMI é **Amazon Linux 2023**, descoberta por `data "aws_ami"` com `owners = ["amazon"]`, filtro de nome da família AL2023 e a mais recente. Nenhum nome completo nem ID de AMI escrito à mão: a AWS publica imagem nova a cada poucos dias e o ID muda. | Passo 7: o nome da imagem começa com `al2023` |
| R2 | Tipo de instância **`t3.micro`**. | Passos 5 e 7 |
| R3 | A VPC `fiap-lab` é **lida** por tag (`Name=fiap-lab`) e a instância vai para uma subnet dela com tag `Tier=Public`, numa AZ que oferte `t3.micro`. Nenhum recurso de rede é criado. | Passo 5 (só `aws_instance` no plan) e passo 7 (mesmo `vpc-...` da validação inicial) |
| R4 | **2 EC2 por workspace** com `count`, e a tag `Name` montada a partir de `terraform.workspace` e do índice: `quantum-ai-<workspace>-01` e `-02`. | Passo 5: os nomes aparecem no plan |
| R5 | Backend S3 no bucket `base-config-<SEU-RM>`, com a key **`exercises/quantum-ai-workspaces/terraform.tfstate`**, região `us-east-1`. | Passo 8 |
| R6 | Lock nativo no S3: **`use_lockfile = true`** no backend. | Passo 4 |
| R7 | Workspaces **`dev`** e **`prod`**, os dois aplicados com o mesmo código. | Passos 5 e 6 |
| R8 | Os states são separados: `env:/dev/...` e `env:/prod/...` no mesmo bucket. | Passos 8 e 9 |
| R9 | Destruir `dev` não altera `prod`; depois `prod` também é destruído. | Passos 11, 12 e 14 |

A key `exercises/quantum-ai-workspaces/terraform.tfstate` é **só deste exercício**. As demos usam `demo-state/quantum/terraform.tfstate` (Demo 01.4) e `demo-workspaces/terraform.tfstate` (Demo 01.5); com outra key, você nunca sobrescreve o state de uma demo.

---

<a id="pistas"></a>

## Pistas graduais

Abra só quando precisar, na ordem. O **Nível 1** dá a direção, o **Nível 2** aponta a página oficial exata, o **Nível 3** ajuda a depurar o erro mais provável.

### Pista A — AMI Amazon Linux 2023 (R1)

<details>
<summary><b>💡 Nível 1: conceito</b></summary>
<blockquote>

Você não **cria** uma AMI, você a **descobre**: por isso é um bloco `data`, não `resource`. As Demos 01.4 e 01.5 já descobrem a Amazon Linux 2023 exatamente como este exercício pede. Repare no padrão de nome usado no filtro e no `most_recent`.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 2: documentação oficial</b></summary>
<blockquote>

📚 [Data source `aws_ami`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami): a seção *Argument Reference* explica `owners`, `most_recent` e o bloco `filter`, e o exemplo do topo mostra a forma do filtro por nome.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 3: depuração</b></summary>
<blockquote>

Se o `plan` falhar com `Your query returned no results`, o padrão do filtro `name` não casa com nenhuma imagem. Teste o padrão direto na AWS, fora do Terraform:

```bash
aws ec2 describe-images --region us-east-1 --owners amazon \
  --filters "Name=name,Values=al2023-ami-2023.*-x86_64" \
  --query "sort_by(Images,&CreationDate)[-1].Name" --output text
```

Se esse comando devolve um nome e o seu Terraform não acha nada, compare caractere a caractere o valor do seu filtro com o deste comando. Não copie o nome devolvido para o seu código: ele muda a cada poucos dias, e é por isso que o filtro usa `*` e `most_recent`.

</blockquote>
</details>

### Pista B — VPC, subnet e a armadilha da AZ (R3)

<details>
<summary><b>💡 Nível 1: conceito</b></summary>
<blockquote>

São três descobertas encadeadas: a VPC pela tag `Name`, as subnets daquela VPC com a tag `Tier=Public` e, delas, uma que esteja numa AZ que oferte `t3.micro`. **Armadilha conhecida:** a `us-east-1e` não oferta `t3.micro` (no teste do exercício, as AZs com oferta foram `us-east-1a`, `1b`, `1c`, `1d` e `1f`). Se a sua subnet escolhida cair na `1e`, o `apply` falha. Não escreva a AZ nem o ID da subnet à mão: as Demos 01.3, 01.4 e 01.5 perguntam à AWS quais AZs ofertam o tipo e só então escolhem a subnet.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 2: documentação oficial</b></summary>
<blockquote>

- 📚 [Data source `aws_vpc`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/vpc): achar a VPC pelas `tags`.
- 📚 [Data source `aws_subnets`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/subnets): filtrar por `vpc-id` e por `tag:Tier`; devolve uma lista de IDs.
- 📚 [Data source `aws_ec2_instance_type_offerings`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ec2_instance_type_offerings): com `location_type = "availability-zone"`, devolve as AZs que ofertam o tipo pedido.

Para ver como as três peças se juntam, leia o `instance.tf` da [Demo 01.4](../../demos/04-State/README.md) (pasta `demos/04-State/test`): ele usa também o data source `aws_subnet` (singular, para ler a AZ de cada subnet) e uma expressão [`for`](https://developer.hashicorp.com/terraform/language/expressions/for) para cruzar subnets e AZs. O `data "aws_ami"` dessa demo fica no `vars.tf`.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 3: depuração</b></summary>
<blockquote>

- `Unsupported: Your requested instance type (t3.micro) is not supported in your requested Availability Zone (us-east-1e)`: a subnet escolhida está na AZ errada. Falta cruzar a lista de subnets com as AZs ofertadas.
- `no matching EC2 VPC found`: a VPC `fiap-lab` não existe nesta conta ou região. Rode o segundo comando da validação inicial; se vier `None`, veja o bloco **⚠ Se der erro de rede** do [passo 5](#passo-5).
- Para ver em quais AZs a `t3.micro` é ofertada (a `us-east-1e` não pode aparecer):

```bash
aws ec2 describe-instance-type-offerings --region us-east-1 --location-type availability-zone \
  --filters "Name=instance-type,Values=t3.micro" --query "InstanceTypeOfferings[].Location" --output text
```

- Para ver as subnets públicas e as AZs de cada uma:

```bash
VPC_ID=$(aws ec2 describe-vpcs --region us-east-1 --filters "Name=tag:Name,Values=fiap-lab" --query "Vpcs[0].VpcId" --output text)
aws ec2 describe-subnets --region us-east-1 --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Tier,Values=Public" \
  --query "Subnets[].[SubnetId,AvailabilityZone]" --output table
```

</blockquote>
</details>

### Pista C — `count` e o nome por ambiente (R4)

<details>
<summary><b>💡 Nível 1: conceito</b></summary>
<blockquote>

Um único bloco `aws_instance` com `count = 2` gera duas instâncias, `[0]` e `[1]`. O nome junta três coisas: o prefixo fixo `quantum-ai`, o workspace atual (`terraform.workspace`) e o índice começando em **01**, com dois dígitos. A Demo 01.3 já nomeia a frota com um número formatado com zeros à esquerda.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 2: documentação oficial</b></summary>
<blockquote>

- 📚 [Meta-argumento `count`](https://developer.hashicorp.com/terraform/language/meta-arguments/count): a seção *The count object* explica o `count.index`, que começa em 0.
- 📚 [Função `format`](https://developer.hashicorp.com/terraform/language/functions/format): o verbo `%02d` escreve um inteiro com dois dígitos.
- 📚 [Current workspace interpolation](https://developer.hashicorp.com/terraform/language/state/workspaces#current-workspace-interpolation): como usar `terraform.workspace` dentro de uma expressão.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 3: depuração</b></summary>
<blockquote>

Rode `terraform plan` e procure as linhas `"Name" =` no bloco de `tags`. Se aparecer `quantum-ai-dev-00` e `-01`, faltou somar 1 ao índice. Se aparecer `quantum-ai-default-...`, você está no workspace `default`: rode `terraform workspace show` antes de qualquer `plan`.

</blockquote>
</details>

### Pista D — Backend S3 com lock (R5, R6)

<details>
<summary><b>💡 Nível 1: conceito</b></summary>
<blockquote>

O backend fica num bloco `terraform { backend "s3" { ... } }`, com quatro argumentos: bucket, key, região e o lock nativo. Ele é lido no `terraform init`, **antes** de qualquer variável existir. Por isso o nome do bucket entra escrito por extenso. É o mesmo bloco do `state.tf` da Demo 01.4, só que com a key deste exercício.

</blockquote>
</details>

<details>
<summary><b>💡 Nível 2: documentação oficial</b></summary>
<blockquote>

📚 [Backend S3](https://developer.hashicorp.com/terraform/language/backend/s3): a seção *State Locking* descreve o `use_lockfile`, e a seção *Multiple Workspaces* explica por que cada workspace vira `env:/<nome>/<key>` no bucket (argumento `workspace_key_prefix`, padrão `env:`).

</blockquote>
</details>

<details>
<summary><b>💡 Nível 3: depuração</b></summary>
<blockquote>

- `Variables not allowed`: você usou `var.` dentro do bloco `backend`. Escreva o nome do bucket literal.
- `NoSuchBucket` ou `bucket does not exist`: o nome está diferente do que o `aws s3 ls` mostra.
- `Backend configuration changed`: você editou o backend depois de um `init`. Rode `terraform init -reconfigure` nesta pasta.
- `Error acquiring the state lock`: outro `apply` está rodando ou foi interrompido. Espere ele terminar; não use `force-unlock` sem ter certeza de que nenhum outro terminal está aplicando.

</blockquote>
</details>

---

<a id="parte-1"></a>

## Parte 1 - Preparando a pasta, o HCL e o backend

### Resultado esperado desta parte

A pasta `~/exercicio-state` com os seus `.tf`, conectada ao bucket S3 e validada.

---

<a id="passo-1"></a>

**1. Crie a pasta do seu projeto, fora do clone do curso**

Os seus arquivos ficam fora de `/workspaces/FIAP-AI-Devops-CICD` para não se misturarem com o material do curso (que é um repositório Git) nem com as pastas das demos.

```bash
mkdir -p ~/exercicio-state
cd ~/exercicio-state
pwd
```

> Saída esperada:
>
> ```text
> /home/vscode/exercicio-state
> ```

Para abrir um arquivo no editor do Codespaces, use `code ~/exercicio-state/<nome>.tf` (o arquivo é criado se não existir). Como dividir em arquivos (`versions.tf`, `provider.tf`, `main.tf`, `state.tf`...) é escolha sua; o Terraform lê todos os `.tf` da pasta.

---

<a id="passo-2"></a>

**2. Descubra o nome exato do seu bucket**

```bash
aws s3 ls | awk '/base-config-/{print $3; exit}'
```

> Saída esperada: uma linha, por exemplo `base-config-12345`. Esse é o valor que vai no argumento `bucket` do seu backend (R5).

---

<a id="passo-3"></a>

**3. Escreva o seu HCL**

Agora começa o desafio. Escreva os arquivos `.tf` em `~/exercicio-state` cumprindo de **R1 a R6** da tabela de [requisitos](#requisitos-obrigatórios). Use provider `hashicorp/aws ~> 6.0`, `required_version >= 1.10` (o `use_lockfile` exige 1.10 ou mais nova) e região `us-east-1`, como nas demos. As [pistas](#pistas) estão lá se precisar.

Quando achar que terminou, deixe a formatação padrão e confira que o seu código não cria nada de rede:

```bash
cd ~/exercicio-state
terraform fmt
grep -nE 'resource "aws_(vpc|subnet|internet_gateway|route|route_table)"' *.tf || echo "OK: nenhum recurso de rede"
grep -nE 'use_lockfile|key *=' *.tf
```

> Saída esperada: `OK: nenhum recurso de rede`, seguido das duas linhas do seu backend com `use_lockfile = true` e `key = "exercises/quantum-ai-workspaces/terraform.tfstate"`. O `terraform fmt` imprime o nome dos arquivos que ele reformatou (ou nada, se já estavam formatados).

---

<a id="passo-4"></a>

**4. Inicialize e valide**

```bash
cd ~/exercicio-state
terraform init
terraform validate
```

> Saída esperada no primeiro `init` (trechos; leva menos de 10 s):
>
> ```text
> Successfully configured the backend "s3"! Terraform will automatically
> use this backend unless the backend configuration changes.
> ...
> Terraform has been successfully initialized!
> Success! The configuration is valid.
> ```

No primeiro `init` aparece `Successfully configured the backend "s3"!`; em execuções seguintes basta `Terraform has been successfully initialized!`. Se nem no primeiro `init` aparecer a linha do backend, o Terraform não encontrou o seu bloco de backend e vai guardar o state localmente: revise o R5 antes de seguir. Erros de backend estão no Nível 3 da [Pista D](#pista-d--backend-s3-com-lock-r5-r6).

### Checkpoint

- `terraform validate` respondeu `Success! The configuration is valid.`
- o primeiro `init` configurou o backend `s3`
- o `grep` não achou nenhum recurso de rede

---

<a id="parte-2"></a>

## Parte 2 - Subindo `dev` e `prod`

### Resultado esperado desta parte

Quatro EC2 `t3.micro` na VPC `fiap-lab`, duas por ambiente, e dois arquivos de state no bucket.

---

<a id="passo-5"></a>

**5. Crie o workspace `dev`, confira o plano e aplique**

O `terraform workspace select -or-create` entra no workspace e o cria se ele ainda não existir; assim, repetir o passo não dá erro de `already exists`. O `plan` antes do `apply` é a sua chance de conferir nomes e tipo sem gastar nada.

```bash
cd ~/exercicio-state
terraform workspace select -or-create dev
terraform plan
```

> Saída esperada (trechos; a AMI, a subnet e a ordem dos atributos variam):
>
> ```text
>   # aws_instance.<nome que você deu>[0] will be created
>       + instance_type = "t3.micro"
>           + "Name" = "quantum-ai-dev-01"
>   # aws_instance.<nome que você deu>[1] will be created
>           + "Name" = "quantum-ai-dev-02"
>
> Plan: 2 to add, 0 to change, 0 to destroy.
> ```

Só podem aparecer duas linhas `will be created`, ambas `aws_instance`. Se o plano quiser criar VPC, subnet ou qualquer outra coisa, ou se vier um número diferente de `2 to add`, volte ao passo 3 antes de aplicar.

```bash
cd ~/exercicio-state
terraform apply -auto-approve
```

> Saída esperada (a última linha):
>
> ```text
> Apply complete! Resources: 2 added, 0 changed, 0 destroyed.
> ```

O `-auto-approve` pula a pergunta `Enter a value:`. Você já conferiu o plano no bloco anterior. O `apply` das duas EC2 leva por volta de 20 s (varia por execução).

<details>
<summary><b>⚠ Se der erro de rede: <code>no matching EC2 VPC found</code> (ou <code>Invalid index</code>, <code>Call to function "element" failed</code>, <code>no matching EC2 Subnet found</code>)</b></summary>
<blockquote>

O texto exato depende do código que você escreveu:

- **`no matching EC2 VPC found`**: não há VPC `fiap-lab` nesta conta.
- **`Invalid index`** (se você usou `[0]`) ou **`Call to function "element" failed: cannot use element function with an empty list`** (se você usou `element()`): a lista de subnets públicas veio vazia.
- **`no matching EC2 Subnet found`** (se você usou `data "aws_subnet"`, no singular): nenhuma subnet pública casou com o filtro.

Antes de culpar o seu código, confira a rede com o segundo comando da validação inicial. Se vier `None`, a causa é a rede:

A rede `fiap-lab` (VPC, subnets públicas e route table), criada no [Lab 01.2](../../demos/02-Modules/README.md), não existe nesta conta ou está incompleta, por exemplo porque o lab do AWS Academy foi resetado. Você não precisa refazer o Lab 01.2: um script sobe a mesma rede com o mesmo Terraform, criando só o que estiver faltando.

**Onde:** terminal do Codespaces.

```bash
bash /workspaces/FIAP-AI-Devops-CICD/01-Terraform/scripts/recriar-rede.sh
```

> Saída esperada (leva cerca de 1 minuto quando precisa recriar e menos de 20 s quando a rede já existe; o id muda):
>
> ```text
> REDE_OK vpc-0abc... 6
> ```

Depois, rode de novo o `terraform plan` e o `terraform apply -auto-approve` deste passo. Se a rede existe e o erro continua, a causa é o seu filtro: veja a [Pista 3](#pistas). Se o script disser que existem duas VPCs `fiap-lab` (erro `multiple EC2 VPCs matched`), não apague nada: chame o professor.

</blockquote>
</details>

**6. Crie o workspace `prod` e aplique o mesmo código**

```bash
cd ~/exercicio-state
terraform workspace select -or-create prod
terraform plan
```

> Saída esperada (a última linha):
>
> ```text
> Plan: 2 to add, 0 to change, 0 to destroy.
> ```

Repare: `2 to add` e **`0 to destroy`**. O workspace `prod` começa com um state vazio, então o Terraform não enxerga as EC2 de `dev` e não propõe mexer nelas. Os nomes no plano agora são `quantum-ai-prod-01` e `quantum-ai-prod-02`.

```bash
cd ~/exercicio-state
terraform apply -auto-approve
terraform workspace list
```

> Saída esperada:
>
> ```text
> Apply complete! Resources: 2 added, 0 changed, 0 destroyed.
>   default
>   dev
> * prod
> ```

O `*` marca o workspace atual.

---

<a id="passo-7"></a>

**7. Confira as quatro EC2 e a AMI na AWS**

```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Name,Values=quantum-ai-dev-*,quantum-ai-prod-*" "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].{Name:Tags[?Key=='Name']|[0].Value,Type:InstanceType,AZ:Placement.AvailabilityZone,Vpc:VpcId}" \
  --output table
```

> Saída esperada: quatro linhas, `quantum-ai-dev-01`, `quantum-ai-dev-02`, `quantum-ai-prod-01` e `quantum-ai-prod-02`, todas com `t3.micro`, a mesma `vpc-...` que a validação inicial mostrou e uma AZ diferente de `us-east-1e`. A ordem das linhas varia.

Agora confira que a imagem é Amazon Linux 2023 (R1):

```bash
AMI=$(aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Name,Values=quantum-ai-dev-01" "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[0].Instances[0].ImageId" --output text)
aws ec2 describe-images --region us-east-1 --image-ids "$AMI" --query "Images[0].Name" --output text
```

> Saída esperada: um nome começando com `al2023` e terminando em `-x86_64`, por exemplo `al2023-ami-2023.x.AAAAMMDD.0-kernel-6.18-x86_64`. A versão, a data e o kernel no meio mudam a cada poucos dias, e o ID da AMI também: o critério é só o começo `al2023`.

<!-- TROCAR-IMAGEM: img/ec2-list.png | motivo: mostra so 2 instancias com nomes antigos (ec2-prod, ec2-dev) e o tipo antigo de instancia | capturar: console EC2 em us-east-1 filtrado por quantum-ai-, com as 4 linhas quantum-ai-dev-01/02 e quantum-ai-prod-01/02 em t3.micro, estado Executando -->
<!-- ![](img/ec2-list.png) -->

> 📸 **Nota do autor (não é tarefa sua)** — capturar o console EC2 filtrado por `quantum-ai-` com as quatro instâncias `t3.micro` em execução.

---

<a id="passo-8"></a>

**8. Veja os dois states no S3**

```bash
BUCKET=$(aws s3 ls | awk '/base-config-/{print $3; exit}')
aws s3 ls "s3://$BUCKET" --recursive | grep quantum-ai-workspaces
```

> Saída esperada (data, hora e tamanho variam; o `aws s3 ls` não mostra nenhum `.tflock`, porque o lock só existe enquanto um comando roda):
>
> ```text
> 2026-10-24 10:21:40      14210 env:/dev/exercises/quantum-ai-workspaces/terraform.tfstate
> 2026-10-24 10:23:05      14215 env:/prod/exercises/quantum-ai-workspaces/terraform.tfstate
> ```

Duas keys, uma por workspace, no mesmo bucket. Se aparecer uma terceira linha terminando em `.tflock`, um `apply` estava em andamento: espere terminar e rode de novo. Se não aparecer nada, a key do seu backend é outra: rode `grep -n 'key' ~/exercicio-state/*.tf`.

### Checkpoint

- `dev` e `prod` aplicados, cada um com `2 added`
- quatro EC2 `t3.micro` na VPC `fiap-lab`, com AMI de nome `al2023...`
- dois states em `env:/dev/...` e `env:/prod/...`

---

<a id="parte-3"></a>

## Parte 3 - Provando recuperação e isolamento

### Resultado esperado desta parte

Uma segunda pasta, sem nenhum state local, enxerga as EC2 de `prod` só pelo S3; e destruir `dev` deixa `prod` intacto.

---

<a id="passo-9"></a>

**9. Recupere o state numa pasta nova, como se fosse o Codespaces do Diego**

Copiamos **só os `.tf`** para uma pasta limpa: nenhum `.terraform/`, nenhum state. Se essa pasta enxergar as EC2 de `prod`, foi o S3 que contou. Nesta pasta vamos só **ler**: nada de `apply` ou `destroy` aqui.

```bash
mkdir -p ~/exercicio-state-prova
cp ~/exercicio-state/*.tf ~/exercicio-state-prova/
cd ~/exercicio-state-prova
terraform init
terraform workspace list
```

> Saída esperada (trechos):
>
> ```text
> Terraform has been successfully initialized!
> * default
>   dev
>   prod
> ```

A pasta nova já conhece `dev` e `prod`: a lista de workspaces vem do bucket. Repare no `*`: uma pasta recém-inicializada **sempre começa no `default`**, que neste exercício está vazio. Se você rodar `state list` agora, vem vazio e parece que o state sumiu; ele não sumiu, só falta selecionar o workspace.

```bash
cd ~/exercicio-state-prova
terraform workspace select prod
terraform state list | grep aws_instance
```

> Saída esperada:
>
> ```text
> aws_instance.<nome que você deu>[0]
> aws_instance.<nome que você deu>[1]
> ```

<details>
<summary><b>⚠ Se der erro: a lista de workspaces só mostra <code>default</code> ou o <code>state list</code> vem vazio</b></summary>
<blockquote>

Primeiro rode `terraform workspace show`. Se responder `default`, faltou o `terraform workspace select prod`: rode e repita o `state list`. Se a lista de workspaces só mostra `default`, a pasta de prova não está lendo o mesmo backend. Confira que o `cp` levou o arquivo com o bloco `backend "s3"`:

```bash
grep -n 'backend' ~/exercicio-state-prova/*.tf
```

Se não aparecer nada, copie de novo os `.tf` (passo 9) e rode `terraform init` na pasta de prova. Não rode `apply` nela para "consertar": isso criaria EC2 novas.

</blockquote>
</details>

---

<a id="passo-10"></a>

**10. Volte para a pasta do projeto e confira que `dev` não propõe nada contra `prod`**

A prova de recuperação está feita. Daqui em diante, tudo roda na pasta original. Com as quatro EC2 de pé, o `plan` em `dev` só compara com o state de `dev`: não pode propor criar, alterar nem destruir nada.

```bash
cd ~/exercicio-state
terraform workspace select dev
terraform workspace show
terraform plan
```

> Saída esperada (trechos):
>
> ```text
> dev
> ...
> No changes. Your infrastructure matches the configuration.
> ```

Se o plano propuser qualquer mudança, pare e abra os [pontos de atenção](#pontos-de-atenção) antes de destruir algo. A única mudança esperada fora do roteiro é a [troca de AMI](#ponto-ami-nova).

---

<a id="passo-11"></a>

**11. Destrua `dev`**

Confira o workspace **antes** de destruir: a troca de workspace é silenciosa, e um `destroy` no lugar errado derruba o outro ambiente.

```bash
cd ~/exercicio-state
terraform workspace show
terraform destroy -auto-approve
```

> Saída esperada:
>
> ```text
> dev
> ...
> Destroy complete! Resources: 2 destroyed.
> ```

O `destroy` das duas EC2 leva perto de 40 s (varia por execução). Se a primeira linha não for `dev`, **não siga**: rode `terraform workspace select dev` e repita o bloco.

---

<a id="passo-12"></a>

**12. Prove que `prod` não foi tocado**

```bash
cd ~/exercicio-state
terraform workspace select prod
terraform plan
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Name,Values=quantum-ai-dev-*,quantum-ai-prod-*" "Name=instance-state-name,Values=pending,running" \
  --query "Reservations[].Instances[].Tags[?Key=='Name']|[].Value" --output text
```

> Saída esperada (trechos; a ordem dos dois nomes varia):
>
> ```text
> No changes. Your infrastructure matches the configuration.
> quantum-ai-prod-01      quantum-ai-prod-02
> ```

`No changes` em `prod` e nenhuma `quantum-ai-dev-*` na lista: o `destroy` de `dev` só enxergou o state de `dev`. Se em vez de `No changes` o plano propuser substituir as duas EC2, veja o ponto de atenção da [AMI nova](#ponto-ami-nova).

### Checkpoint

- a pasta de prova listou as duas `aws_instance` de `prod` sem nenhum state local
- o `plan` em `dev` respondeu `No changes.` com os dois ambientes de pé
- `dev` destruído com `2 destroyed`
- `prod` com `No changes` e as duas EC2 ainda em execução

---

<a id="parte-4"></a>

## Parte 4 - Decisão, evidências e limpeza

### Resultado esperado desta parte

O `DECISION.md` escrito, as evidências guardadas, nenhuma EC2 do exercício ligada e a VPC `fiap-lab` intacta.

---

<a id="passo-13"></a>

**13. Escreva o `DECISION.md` e junte as evidências**

Crie `~/exercicio-state/DECISION.md` com as suas palavras, 1 a 3 linhas por item:

- **Contexto:** por que dev e prod precisam de estados separados no AI Customer Assistant.
- **Decisão:** workspaces no mesmo código, ou diretórios separados, ou contas AWS separadas? Por quê, neste caso?
- **O que workspace NÃO isola:** os dois estados estão no mesmo bucket, na mesma conta e são alterados com as mesmas credenciais; as EC2 estão na mesma VPC. Explique o que isso significa para o *blast radius* de um erro.
- **Consequência aceita:** pelo menos uma limitação real que você viu neste exercício (por exemplo, o risco de rodar `destroy` no workspace errado).
- **Pergunta para a Helena:** o que você confirmaria com ela antes de usar esse padrão na produção real.

Guarde, como texto colado num `EVIDENCIAS.md` ao lado do `DECISION.md` ou como print, a saída de:

| Evidência | Passo |
|---|---|
| `Plan: 2 to add, 0 to change, 0 to destroy.` em `dev` e em `prod` | 5 e 6 |
| a tabela das quatro EC2 e o nome `al2023...` da AMI | 7 |
| as duas keys `env:/dev/...` e `env:/prod/...` | 8 |
| o `state list` da pasta de prova com as duas `aws_instance` | 9 |
| `Destroy complete! Resources: 2 destroyed.` em `dev` e o `No changes` em `prod` | 11 e 12 |

> [!CAUTION]
> **Não cole o conteúdo do state** (`terraform state pull`, `terraform show` ou o arquivo baixado do S3) nas evidências: ele fica em texto claro e pode carregar dados sensíveis (Demo 01.4, Parte 5). Também não cole a saída de `aws configure` nem credenciais. As saídas da tabela acima bastam.

---

<a id="passo-14"></a>

**14. Destrua `prod`**

```bash
cd ~/exercicio-state
terraform workspace select prod
terraform workspace show
terraform destroy -auto-approve
```

> Saída esperada:
>
> ```text
> prod
> ...
> Destroy complete! Resources: 2 destroyed.
> ```

Rode o `destroy` **só** em `~/exercicio-state`. Nunca rode `destroy` na pasta de uma demo nem em `demos/vpc-call` ou `demos/RT-call`: a rede `fiap-lab` é usada pelo Ansible, pelo CI/CD e pelo Trabalho Final.

---

<a id="passo-15"></a>

**15. Apague os workspaces**

O Terraform não deixa apagar o workspace em que você está, então voltamos ao `default` primeiro. Ele também recusa apagar um workspace que ainda tem recursos: se isso acontecer, o `destroy` daquele ambiente não terminou.

```bash
cd ~/exercicio-state
terraform workspace select default
terraform workspace delete dev
terraform workspace delete prod
terraform workspace list
```

> Saída esperada (trechos):
>
> ```text
> Deleted workspace "dev"!
> Deleted workspace "prod"!
> * default
> ```

<details>
<summary><b>⚠ Se der erro: <code>Workspace is not empty</code></b></summary>
<blockquote>

O workspace citado na mensagem ainda tem EC2 no state. **Não use `-force`**: o Terraform "esqueceria" máquinas que continuam cobrando. Destrua e apague de novo, trocando `prod` por `dev` se for o caso:

```bash
cd ~/exercicio-state
terraform workspace select prod
terraform destroy -auto-approve
terraform workspace select default
terraform workspace delete prod
```

</blockquote>
</details>

---

<a id="passo-16"></a>

**16. Confira que nada ficou ligado e que a rede continua de pé**

```bash
aws ec2 describe-instances --region us-east-1 \
  --filters "Name=tag:Name,Values=quantum-ai-dev-*,quantum-ai-prod-*" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "length(Reservations[].Instances[])" --output text
aws ec2 describe-vpcs --region us-east-1 --filters "Name=tag:Name,Values=fiap-lab" --query "Vpcs[0].[VpcId,State]" --output text
BUCKET=$(aws s3 ls | awk '/base-config-/{print $3; exit}')
aws s3 ls "s3://$BUCKET/" --recursive | grep exercises || echo "nenhuma key do exercicio"
```

> Saída esperada (o `vpc-...` é o seu):
>
> ```text
> 0
> vpc-0123456789abcdef0   available
> nenhuma key do exercicio
> ```

Você **não precisa** apagar nada no S3: o `workspace delete` do passo 15 remove sozinho a key `env:/<workspace>/exercises/quantum-ai-workspaces/terraform.tfstate`, e com `use_lockfile` também não sobra nenhum `.tflock`. Por isso a última linha deve ser `nenhuma key do exercicio`. As keys das demos (`demo-state/...`, `demo-workspaces/...`) não foram tocadas, e as versões antigas do state continuam guardadas pelo versionamento que você ligou na Demo 01.4, a custo de frações de centavo.

Se ainda aparecer alguma key `exercises/...`, um dos workspaces não foi apagado: rode `terraform workspace list` em `~/exercicio-state` e repita o passo 15 para o que sobrou. **Não apague objetos do bucket à mão**, e nunca use `aws s3 rm --recursive`: o bucket guarda o state de todas as demos e do Trabalho Final.

A pasta `~/exercicio-state-prova` é só uma cópia dos seus `.tf`, sem state; pode apagá-la com `rm -rf ~/exercicio-state-prova`. Mantenha `~/exercicio-state`: ela tem o seu código, o `DECISION.md` e as evidências.

> [!IMPORTANT]
> **Não destrua a rede.** A VPC `fiap-lab` é a fundação compartilhada do curso. Este exercício só a lê. VPC, subnets e route tables são gratuitas; o que custava por hora eram as EC2, que você já destruiu.

### Checkpoint

- `0` EC2 `quantum-ai-dev-*` ou `quantum-ai-prod-*` na conta
- VPC `fiap-lab` em `available`
- só o workspace `default` sobrou
- nenhuma key `exercises/...` no bucket, sem precisar de `aws s3 rm`

---

<a id="criterios-de-aceite"></a>

## Critérios de aceite

O exercício está completo quando **todos** os itens abaixo forem verdadeiros.

| # | Onde | O que deve aparecer |
|---|------|---------------------|
| CA1 | código | `grep` do passo 3 imprime `OK: nenhum recurso de rede`; nenhum ID de AMI fixo nos `.tf` |
| CA2 | código | o backend tem `key = "exercises/quantum-ai-workspaces/terraform.tfstate"` e `use_lockfile = true` |
| CA3 | terminal | `terraform validate` responde `Success! The configuration is valid.`; no primeiro `init` aparece `Successfully configured the backend "s3"!`, e em execuções seguintes basta `Terraform has been successfully initialized!` |
| CA4 | plan | em `dev`: `Plan: 2 to add, 0 to change, 0 to destroy.`, só `aws_instance`, `instance_type = "t3.micro"`, nomes `quantum-ai-dev-01` e `quantum-ai-dev-02` |
| CA5 | plan | em `prod`, antes do primeiro apply: `Plan: 2 to add, 0 to change, 0 to destroy.` com `quantum-ai-prod-01` e `quantum-ai-prod-02` (nenhum destroy de `dev`) |
| CA6 | AWS | quatro EC2 `t3.micro` em execução, todas na VPC `fiap-lab`, nenhuma na `us-east-1e`; a AMI tem nome começando com `al2023` (o ID e o restante do nome variam) |
| CA7 | S3 | exatamente duas keys do exercício: `env:/dev/exercises/quantum-ai-workspaces/terraform.tfstate` e `env:/prod/exercises/quantum-ai-workspaces/terraform.tfstate` |
| CA8 | state | na pasta `~/exercicio-state-prova`, sem state local: `workspace list` mostra `dev` e `prod`, e em `prod` o `state list` mostra duas `aws_instance` (`[0]` e `[1]`) |
| CA9 | plan | com os dois ambientes de pé, `terraform workspace select dev` + `terraform plan` responde `No changes.` (nada proposto contra `prod`) |
| CA10 | terminal | `destroy` em `dev` termina com `Destroy complete! Resources: 2 destroyed.` |
| CA11 | plan e AWS | logo depois, em `prod`: `No changes.`, ou só a troca de AMI explicada no [ponto de atenção](#ponto-ami-nova), e só `quantum-ai-prod-01` e `quantum-ai-prod-02` em execução |
| CA12 | entrega | `~/exercicio-state/DECISION.md` escrito por você, com pelo menos uma limitação real de workspaces |
| CA13 | limpeza | `0` EC2 do exercício, só o workspace `default`, `aws s3 ls s3://base-config-<SEU-RM>/ --recursive \| grep exercises` sem nenhuma linha (nem state, nem `.tflock`), VPC `fiap-lab` em `available`, keys das demos intactas |

---

## Pontos de atenção

<details>
<summary><b>⚠ Apareceram instâncias <code>quantum-ai-default-01</code> e <code>-02</code></b></summary>
<blockquote>

Você aplicou no workspace `default`. Rode `terraform workspace show` para confirmar, depois `terraform destroy -auto-approve` **nesse** workspace e volte ao passo 5. Por isso todo bloco deste README que altera infra confere o workspace antes.

</blockquote>
</details>

<details>
<summary><b>⚠ O plan de <code>prod</code> quer destruir as EC2 de <code>dev</code></b></summary>
<blockquote>

Você não está em `prod`, ou o backend não está separando os estados. Rode `terraform workspace show`. Se mostrar `prod` e mesmo assim o plano propõe destroy, confira se o `init` configurou o backend `s3` (passo 4): com backend local, o comportamento é outro e a prova do S3 não vale.

</blockquote>
</details>

<a id="ponto-ami-nova"></a>

<details>
<summary><b>⚠ O <code>plan</code> em <code>prod</code> (ou <code>dev</code>) propõe substituir as 2 EC2 em vez de <code>No changes</code></b></summary>
<blockquote>

Não é erro do exercício. A AWS publicou uma AMI Amazon Linux 2023 mais nova, e o `most_recent` do seu `data "aws_ami"` passou a apontar para ela. Procure no plano a linha do atributo `ami` com o comentário `# forces replacement`: ela mostra o ID antigo à esquerda e o novo à direita. Um `plan` só mostra, não altera nada; sem `apply`, nenhuma EC2 é destruída. Para o exercício, siga em frente: o critério é que nada de `dev` apareça no plano de `prod` e vice-versa.

</blockquote>
</details>

<details>
<summary><b>⚠ Numa pasta nova (ou sessão nova), o <code>state list</code> vem vazio</b></summary>
<blockquote>

O `terraform init` numa pasta recém-criada sempre deixa você no workspace `default`, que neste exercício não tem nada. O state de `dev` e `prod` continua no S3. Rode `terraform workspace show`, depois `terraform workspace select prod` (ou `dev`) e repita o `state list`. **Não rode `apply`** para "recriar" o que parece perdido: no `default`, isso criaria EC2 novas `quantum-ai-default-*`.

</blockquote>
</details>

<details>
<summary><b>⚠ Copiei uma demo inteira e o plan quer mexer em outra coisa</b></summary>
<blockquote>

Os `.tf` das demos apontam para `demo-state/...` ou `demo-workspaces/...`. Se a sua pasta usa uma dessas keys, você está lendo o state da demo. Corrija a key (R5), rode `terraform init -reconfigure` em `~/exercicio-state` e responda `no` se o Terraform perguntar se quer copiar o state existente.

</blockquote>
</details>

<details>
<summary><b>⚠ Credencial expirou no meio do exercício (<code>ExpiredToken</code>)</b></summary>
<blockquote>

A sessão do Academy expira. Atualize as credenciais pelo [Início de aula](../../../00-create-codespaces/Inicio-de-aula.md) e repita o último bloco. O state está no S3, então nada se perde; o `apply` e o `destroy` podem ser repetidos sem duplicar recurso.

</blockquote>
</details>

> [!CAUTION]
> **Custo:** quatro `t3.micro` custam cerca de US$ 0,04 por hora juntas. Esquecer as quatro ligadas um dia inteiro consome perto de US$ 1 do seu crédito do Academy. Faça os passos 14 a 16 no mesmo dia.

<!-- TROCAR-IMAGEM: img/teste-apply.png | motivo: print da versao anterior do curso, com outro caminho de pasta, data source de outra distribuicao e uma unica instancia | capturar: terminal em ~/exercicio-state-prova no passo 9, com terraform workspace select prod e terraform state list mostrando as duas aws_instance [0] e [1] -->
<!-- ![](img/teste-apply.png) -->

---

## Conclusão

Juntamos as peças das Demos 01.2 a 01.5 num projeto escrito por você: rede só lida por tag, AMI descoberta, `count` com nome por ambiente, state remoto com lock e workspaces. E provamos com evidência que dev e prod têm estados separados, que o state sobrevive a uma sessão nova e que destruir um ambiente não encosta no outro. O `DECISION.md` registra o limite dessa separação: ela é de **estado**, não de conta, credencial ou rede.

## Próximo passo

Volte ao [índice do módulo Terraform](../../README.md) e siga para o módulo de Ansible, que reaproveita a mesma VPC `fiap-lab`.

---

<details>
<summary><b>💡 Glossário rápido</b></summary>
<blockquote>

| Termo | O que é |
|-------|---------|
| **Data source** | Bloco `data` que lê algo que já existe (AMI, VPC, subnet) sem criar nem destruir. |
| **`aws_ec2_instance_type_offerings`** | Data source que lista em quais AZs um tipo de instância está disponível. |
| **`count`** | Meta-argumento que cria N cópias de um recurso; cada uma tem um `count.index` a partir de 0. |
| **`terraform.workspace`** | Expressão que devolve o nome do workspace atual (`dev`, `prod`...). |
| **Backend S3** | Configuração que guarda o state num bucket S3 em vez do disco local. |
| **`use_lockfile`** | Lock nativo do backend S3 (Terraform 1.10+): um objeto `.tflock` impede dois `apply` simultâneos no mesmo state. |
| **`env:/<ws>/<key>`** | Caminho em que o backend S3 grava o state de cada workspace diferente do `default`. |
| **Blast radius** | O tamanho do estrago que um erro pode causar. Workspaces na mesma conta compartilham o mesmo blast radius. |
| **HCL** | *HashiCorp Configuration Language*: a linguagem dos arquivos `.tf`. |
| **AZ** | *Availability Zone*: um datacenter isolado dentro da região, como `us-east-1a`. |
| **AMI** | *Amazon Machine Image*: a imagem do sistema operacional de onde a EC2 nasce. |
| **Workspace** | Um state separado para o mesmo código; aqui, `dev` e `prod`. |
| **State** | O arquivo em que o Terraform registra quais recursos criou e os IDs de cada um na AWS. |
| **ADR** | *Architecture Decision Record*: registro curto de uma decisão técnica e do seu porquê. Aqui, o `DECISION.md`. |

</blockquote>
</details>

<details>
<summary><b>💡 Referências oficiais</b></summary>
<blockquote>

- [Workspaces](https://developer.hashicorp.com/terraform/language/state/workspaces): o que o workspace isola e quando a HashiCorp recomenda outra estratégia.
- [Backend S3](https://developer.hashicorp.com/terraform/language/backend/s3): `use_lockfile`, `workspace_key_prefix` e permissões necessárias.
- [Data source `aws_ami`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami)
- [Resource `aws_instance`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance)
- [Meta-argumento `count`](https://developer.hashicorp.com/terraform/language/meta-arguments/count)
- [Data source `aws_ec2_instance_type_offerings`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ec2_instance_type_offerings)

</blockquote>
</details>

<details>
<summary><b>💡 Como pedir ajuda se travou</b></summary>
<blockquote>

Antes de pedir ajuda, colete: (1) em que passo está, (2) o erro literal do terminal, (3) a saída de `terraform workspace show` e de `aws s3 ls "s3://$BUCKET" --recursive | grep quantum-ai-workspaces`, (4) o que já tentou. Não cole credenciais nem o conteúdo do state.

Canais (em ordem de prioridade):

- **Issues do repositório**: [github.com/vamperst/FIAP-AI-Devops-CICD/issues](https://github.com/vamperst/FIAP-AI-Devops-CICD/issues)
- **E-mail do professor**: `Rafael@rfbarbosa.com`

</blockquote>
</details>
