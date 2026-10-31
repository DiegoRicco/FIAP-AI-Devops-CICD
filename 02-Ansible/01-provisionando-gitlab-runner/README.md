# 02.1 - Provisionando um GitLab Runner com Ansible

> **Segunda-feira, 10h. Aula 3 na Quantum Commerce: a máquina que vai entregar o AI Customer Assistant.**
> Você é AI DevOps Engineer na **Quantum Commerce**. Na Aula 2 a infraestrutura do **AI Customer Assistant** (o Assistente de Rastreio e Políticas) virou código: rede `fiap-lab`, EC2 com a página, ALB, state no S3 e workspaces. **Helena Marques**, Head de Engenharia de IA e Entrega Contínua, te chama:
>
> > *— "Os servidores ainda são configurados à mão, cada um de um jeito, e quem roda o `terraform apply` é o laptop de alguém. Vamos automatizar o deploy do assistente, mas o runner compartilhado do GitLab não serve: precisamos de um runner **nosso**, dentro da nossa conta AWS. Configurar esse servidor à mão (instalar pacote, registrar o runner, ajustar o serviço) é um documento de 30 passos que ninguém lembra. Quero subir um runner novo rodando **um playbook**."*
>
> **Diego Tavares**, AI SRE sênior, completa: *— "Faz uma vez com calma para entender cada peça. Depois, rodar o playbook de novo tem que dar exatamente a mesma máquina."*

Esse laboratório é o que vamos fazer juntos durante a aula: criar o projeto `primeiro-projeto` no GitLab, provisionar uma EC2 com Terraform e rodar um playbook Ansible que a transforma num **GitLab Runner registrado** e dedicado a esse projeto. Nenhuma IA roda nesta máquina: ela é a esteira que, no módulo 03, vai publicar a **API de pedidos** que o assistente consulta.

Nunca usou Ansible ou um terminal? Tudo bem: cada passo diz o que fazer, onde rodar, o que deve aparecer e o que fazer se aparecer outra coisa. Copie o bloco cinza inteiro, cole no terminal indicado e aperte Enter. Se aparecer `Error:`, `ERRO:` ou `FAILED!`, pare no passo em que está e abra o bloco **⚠ Se der erro** logo abaixo dele.

> Os comandos deste lab rodam em **dois lugares**: o **terminal do Codespaces** (onde estão Terraform e Ansible) e o **console web do GitLab** (onde você cria o projeto e gera o token). Cada passo indica onde executar. A chave SSH que você vai gerar serve **só** para autenticar o `git push` no GitLab — o acesso à EC2 do runner é feito via AWS Systems Manager (SSM), sem chave e sem porta 22.
>
> O **token de registro do runner** (um segredo) **não** é colado dentro de nenhum arquivo do projeto: você o guarda uma vez no **AWS SSM Parameter Store** (como `SecureString`) e o Ansible o lê em tempo de execução. Assim nenhum segredo entra no Git.

> [!WARNING]
> **Pré-requisitos obrigatórios antes de começar:**
>
> - [ ] **Módulo 01 (Terraform) concluído** — você entende `init/plan/apply` e state remoto no S3
> - [ ] A VPC `fiap-lab` do [Lab 01.2](../../01-Terraform/demos/02-Modules/README.md) existe: a EC2 do runner nasce nela (este lab só **lê** a rede, nunca a destrói)
> - [ ] Bucket de state remoto existe no S3 (`base-config-<SEU-RM>`, criado no [setup inicial](../../00-create-codespaces/README.md)) — este mesmo bucket é reutilizado pelo Ansible-via-SSM para transferir arquivos
> - [ ] Credenciais AWS do Academy atualizadas no Codespaces (o acesso à EC2 é via SSM, então as credenciais AWS no ambiente são o que autentica a conexão)
> - [ ] Ferramentas do Ansible-via-SSM instaladas no Codespaces (`session-manager-plugin`, `boto3`, collections `community.aws` e `amazon.aws`) — você instala no **passo 3** mais abaixo
> - [ ] Conta no [GitLab](https://gitlab.com/) (gratuita)
>
> **Valide rapidamente:**
>
> ```bash
> aws sts get-caller-identity --query Account --output text
> aws ec2 describe-vpcs --region us-east-1 --filters "Name=tag:Name,Values=fiap-lab" --query "Vpcs[0].VpcId" --output text
> ```
>
> Saída esperada: o número da sua conta (12 dígitos) e um `vpc-...`. Se a segunda linha vier `None`, a rede não existe: volte ao [Lab 01.2](../../01-Terraform/demos/02-Modules/README.md) e suba a VPC antes de seguir.

## O que você vai fazer

Tempo estimado: **70–100 min** (execução pura ~25 min: o `terraform apply` provisiona a EC2 e roda o bootstrap, e o `ansible-playbook` leva alguns minutos; o resto é o tempo para ler, copiar comandos, observar os resultados e entender cada peça).

Vamos separar com clareza **quem faz o quê**: o **Terraform** cria a EC2 e prepara o sistema operacional mínimo; o **Ansible** entra depois e configura essa EC2 de forma declarativa até ela virar um GitLab Runner registrado. Esse é o padrão clássico de IaC + config management que você vai usar pelo resto da carreira.

## Principais pontos de aprendizagem

- instalar e usar Ansible a partir de um ambiente isolado (virtualenv)
- preparar o Codespaces para conectar via SSM (`session-manager-plugin`, `boto3`, collection `community.aws`)
- gerar e registrar uma chave SSH para autenticar o `git push` no GitLab
- criar um projeto no GitLab e gerar um token de registro de runner
- guardar esse token como segredo no **AWS SSM Parameter Store** (`SecureString`) em vez de hardcode no código
- provisionar a EC2 do runner com Terraform usando state remoto no S3
- conectar o Ansible ao host criado via inventário (`hosts`), usando o connection plugin `community.aws.aws_ssm` (sem chave SSH, sem porta 22)
- rodar um playbook que instala, configura e **registra** o GitLab Runner — lendo o token direto do SSM
- conferir as ferramentas instaladas no runner e provar a **idempotência** rodando o playbook pela segunda vez
- deixar o runner de pé para o módulo 03 (a destruição da infra acontece ao final do Lab 03.2)

## O que você terá ao final

Ao final deste laboratório, você terá uma EC2 `quantum-ai-gitlab-runner` rodando como **GitLab Runner registrado** (executor `shell`, tags `shell` e `terraform`), com Terraform, TFLint, Checkov, Python, `jq`, AWS CLI e git instalados, visível no painel do projeto `primeiro-projeto` e configurada inteiramente por um playbook Ansible. **Helena vai querer ver o runner aparecendo "online" no painel de CI/CD do projeto**: esse é o entregável simbólico do lab.

> [!TIP]
> Sempre que encontrar um bloco com o título **💡 Clique para entender**, abra esse trecho. Ele traz a anatomia do comando, o contexto prático e links oficiais para aprofundamento.

## Mapa do lab

| Parte | O que você faz | Passos | Tempo |
|-------|----------------|--------|-------|
| [Parte 1](#parte-1---preparando-o-ambiente) | Atualizar o repositório e entrar na pasta | [1](#passo-1) · [2](#passo-2) | ~3 min |
| [Parte 2](#parte-2---instalando-ansible-python-e-virtualenv) | Instalar Python, Ansible, virtualenv e as ferramentas de SSM | [3](#passo-3) (3.1·3.2·3.3·3.4·3.5) | ~12 min |
| [Parte 3](#parte-3---configurando-o-acesso-ao-gitlab) | Conta GitLab e chave SSH | [4](#passo-4) · [5](#passo-5) (5.1–5.6) | ~10 min |
| [Parte 4](#parte-4---criando-o-primeiro-projeto-no-gitlab) | Criar o projeto e subir o código | [6](#passo-6) · [7](#passo-7) · [8](#passo-8) · [9](#passo-9) (9.1–9.6) | ~10 min |
| [Parte 5](#parte-5---gerando-o-token-do-runner-e-guardando-no-ssm) | Gerar o token e guardá-lo no SSM | [10](#passo-10) · [11](#passo-11) · [12](#passo-12) · [13](#passo-13) · [14](#passo-14) · [15](#passo-15) · [16](#passo-16) | ~10 min |
| [Parte 6](#parte-6---provisionando-a-ec2-do-runner-com-terraform) | Provisionar a EC2 com Terraform | [17](#passo-17) · [18](#passo-18) · [19](#passo-19) · [20](#passo-20) · [21](#passo-21) · [22](#passo-22) | ~15 min |
| [Parte 7](#parte-7---configurando-a-ec2-como-runner-com-ansible) | Rodar o playbook Ansible | [23](#passo-23) · [24](#passo-24) · [25](#passo-25) · [26](#passo-26) | ~10 min |
| [Parte 8](#parte-8---conferindo-o-runner-e-provando-a-idempotência) | Conferir as ferramentas e rodar o playbook de novo | [27](#passo-27) · [28](#passo-28) | ~10 min |

> [!TIP]
> Se travou em algum passo, você pode pular direto: clique no número do passo na coluna **Passos** acima.

<details>
<summary><b>💡 O que é um GitLab Runner (e por que Ansible) em 3 parágrafos</b></summary>
<blockquote>

Um **GitLab Runner** é o processo que efetivamente **executa** os jobs de um pipeline de CI/CD. Quando você dá um push e o pipeline roda `terraform plan`, é o runner que pega esse job, executa os comandos numa máquina e devolve o resultado para o GitLab. Você pode usar os runners compartilhados do GitLab, mas empresas que rodam infraestrutura preferem um runner **próprio**, dentro da própria conta de nuvem, com acesso às próprias credenciais. É o caso da Quantum Commerce.

O runner é só um programa, mas para funcionar ele precisa de um servidor configurado: o pacote `gitlab-runner` instalado, o Terraform disponível (porque os jobs vão rodar Terraform), um serviço `systemd` ativo e o runner **registrado** no projeto via um token. Fazer isso na mão é o "documento de 30 passos" que a Helena reclamou.

É aí que entra o **Ansible**: em vez de um humano seguir um wiki, descrevemos o estado desejado da máquina em arquivos YAML (os *playbooks* e *tasks*) e o Ansible aplica esse estado de forma **idempotente** — rodar de novo não duplica nada. Neste lab o Ansible não conecta por SSH: usa o connection plugin `community.aws.aws_ssm`, que fala com a EC2 através do AWS Systems Manager (sem chave, sem porta 22). O Terraform cria a máquina; o Ansible a configura. Essa separação "provisionar vs. configurar" é um dos padrões mais importantes de DevOps.

Documentação oficial:
- [GitLab Runner](https://docs.gitlab.com/runner/)
- [Ansible — Getting Started](https://docs.ansible.com/ansible/latest/getting_started/index.html)

</blockquote>
</details>

## Contexto

A Quantum Commerce está montando o pipeline de entrega contínua do AI Customer Assistant. O passo de hoje é ter um runner **dedicado**, dentro da conta AWS da empresa, capaz de rodar Terraform com as credenciais certas. Em vez de configurar esse servidor à mão toda vez que precisar de um runner novo, vamos codificar a configuração com Ansible: um playbook que qualquer pessoa do time roda e obtém exatamente o mesmo runner. Esse runner é a peça que o módulo 03 (CI/CD) usa para automatizar `plan`/`apply` da API de pedidos.

![Arquitetura do lab 02.1: Codespaces roda Terraform e Ansible; Terraform cria a EC2 do runner na VPC fiap-lab; Ansible configura a EC2 via SSM; o Ansible lê o token no SSM Parameter Store e registra o runner no GitLab](img/arquitetura-ansible-runner.png)

*O Terraform (1) cria a EC2 e o Security Group na VPC `fiap-lab`; o Ansible (2) configura a EC2 pelo SSM, sem SSH; a EC2 (3) se registra no GitLab. No módulo 03, cada `git push` no `primeiro-projeto` dispara um pipeline que roda nesta EC2.*

## Onde o Ansible entra

| Peça | O que faz neste lab | Em uma frase |
|------|---------------------|--------------|
| **Terraform** | Cria a EC2 `quantum-ai-gitlab-runner` e o Security Group (só saída, sem porta de entrada) | O **que existe** |
| **Ansible** | Instala Terraform, TFLint, Checkov, git e o `gitlab-runner` dentro da EC2, ajusta o serviço e registra o runner | **Como a máquina está** por dentro |
| **SSM (AWS Systems Manager)** | Leva os comandos do Codespaces até a EC2 | O canal: sem SSH, sem chave e sem porta 22 |
| **GitLab** | Guarda o código do `primeiro-projeto` e entrega os jobs ao runner | Quem **dispara** o runner a cada `git push` |

Em linguagem de negócio: o Terraform compra e instala o "galpão"; o Ansible monta as prateleiras e as máquinas lá dentro, sempre na mesma disposição; o GitLab manda os pedidos de trabalho para esse galpão.

**Por que a idempotência importa:** rodar o playbook duas vezes dá o mesmo resultado. Na primeira execução o Ansible instala e configura o que falta; na segunda, ele confere cada item, vê que já está no estado desejado e não muda nada (`changed=0`). É isso que permite rodar o playbook de novo sem medo depois de um erro, e reconstruir um runner idêntico quando precisar. Você vai provar isso na [Parte 8](#parte-8---conferindo-o-runner-e-provando-a-idempotência).

```mermaid
flowchart LR
    TF[Terraform<br/>cria a EC2 + bootstrap via SSM] --> EC2[(EC2 nua<br/>Ubuntu)]
    EC2 --> ANS[Ansible<br/>configura via SSM]
    ANS --> RUN[GitLab Runner<br/>registrado e online]
    GL[GitLab<br/>gera o token do runner] -.você copia e grava.-> PS[(SSM Parameter Store<br/>/fiap/gitlab-runner/token)]
    PS -.Ansible lê o token.-> ANS
    RUN -.aparece no painel.-> GL
```

---

## Parte 1 - Preparando o ambiente

### Resultado esperado desta parte

Ao final desta etapa, você estará com o repositório atualizado e dentro da pasta do laboratório, no terminal do Codespaces.

---

<a id="passo-1"></a>

**1.** No **terminal do Codespaces**, entre na pasta principal do repositório e garanta que está com a versão mais atualizada do exercício:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/
git reset --hard && git pull origin master
```

> [!IMPORTANT]
> O `git reset --hard` descarta alterações locais não commitadas. Se você tiver trabalho em andamento que queira manter, faça commit antes.

---

<a id="passo-2"></a>

**2.** Entre na pasta do laboratório:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/
```

### Checkpoint

- o repositório está atualizado
- você está dentro de `02-Ansible/01-provisionando-gitlab-runner/`

---

## Parte 2 - Instalando Ansible, Python e virtualenv

### Resultado esperado desta parte

Ao final desta etapa, você terá Python nativo confirmado, Ansible instalado, um virtualenv ativo e as ferramentas de conexão via SSM (`session-manager-plugin`, `boto3`, collections `community.aws` e `amazon.aws`) prontas no terminal do Codespaces.

O Codespaces da disciplina já vem com o Ansible no `~/venv` (o devcontainer instala na criação). Mesmo assim, rodamos os sub-passos abaixo: quem já tem tudo vê os comandos terminarem sem instalar nada, e quem recriou o ambiente fica com tudo pronto. Cada sub-passo tem uma validação rápida, para que, se algo falhar, você saiba exatamente onde travou.

---

<a id="passo-3"></a>

**3.** Vamos instalar e preparar o ambiente do Ansible. Execute os sub-passos abaixo **na ordem**, no terminal do Codespaces.

**3.1.** Atualize o sistema, confirme o Python 3 nativo e instale o pacote de virtualenv:

```bash
sudo apt update -y
sudo apt install -y python3 python3-pip python3-venv jq
python3 --version
```

Deve imprimir `Python 3.10.x` ou mais recente. É esse Python nativo que vamos usar — não precisamos de PPA nem de versões antigas.

**3.2.** Crie um ambiente Python isolado (virtualenv) com o Python nativo:

```bash
python3 -m venv ~/venv
```

**3.3.** Ative o virtualenv:

```bash
source ~/venv/bin/activate
```

O prompt do terminal deve passar a exibir o prefixo `(venv)`. Esse prefixo confirma que o ambiente está ativo.

<details>
<summary><b>💡 Clique para entender: por que virtualenv (e por que instalar o Ansible dentro dele)</b></summary>
<blockquote>

Um **virtualenv** é um diretório isolado com sua própria cópia do Python e dos pacotes. Em vez de instalar dependências "globalmente" no sistema (onde elas podem conflitar com outras ferramentas), você as mantém presas a um ambiente que pode ser ativado e desativado.

O prefixo `(venv)` no prompt é o sinal de que tudo o que você instalar com `pip` daqui em diante fica dentro desse ambiente. Se você abrir um novo terminal, precisa rodar `source ~/venv/bin/activate` de novo — o ambiente não se ativa sozinho.

**Por que instalamos o Ansible aqui dentro (via `pip`) e não pelo `apt`?** Porque o connection plugin do SSM e o *lookup* que lê o token precisam do `boto3` **no mesmo Python que executa o Ansible**. Se o Ansible viesse do `apt` (Python do sistema) e o `boto3` fosse instalado no venv, eles ficariam em Pythons diferentes e o Ansible reclamaria `Failed to import boto3`. Mantendo Ansible **e** boto3 dentro do venv, tudo roda no mesmo interpretador.

Documentação oficial:
- [venv — Python](https://docs.python.org/3/library/venv.html)

</blockquote>
</details>

**3.4.** Com o `(venv)` ativo, instale o Ansible, o `boto3`/`botocore` e as collections — tudo no mesmo Python do venv:

```bash
pip install ansible boto3 botocore
ansible-galaxy collection install community.aws amazon.aws
```

Confirme a versão (o caminho deve apontar para dentro de `~/venv`):

```bash
ansible --version
```

**3.5.** Instale o `session-manager-plugin`, que o Ansible usa para abrir a sessão SSM com a EC2 (o AWS CLI e o `jq` já vêm do devcontainer):

```bash
command -v session-manager-plugin >/dev/null || {
  curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/smp.deb
  sudo dpkg -i /tmp/smp.deb
}
session-manager-plugin --version
```

Deve imprimir um número de versão.

<details>
<summary><b>💡 Clique para entender: por que estas ferramentas</b></summary>
<blockquote>

Neste lab o Ansible não acessa a EC2 por SSH — ele usa o connection plugin `community.aws.aws_ssm`, que conversa com a máquina através do **AWS Systems Manager**. Para isso funcionar, o Codespaces precisa destas peças:

- **`aws` CLI e `jq`** — o devcontainer já instala estas duas; o comando acima só garante que `jq` existe (é usado pelo bootstrap do Terraform também).
- **`session-manager-plugin`** — é o binário que o plugin `aws_ssm` chama por baixo para abrir a sessão SSM. Não vem no devcontainer; é específico deste lab.
- **`boto3` / `botocore`** — SDK Python da AWS que o plugin `aws_ssm` usa para falar com a API do SSM e com o bucket S3.
- **collection `community.aws`** — onde vive o próprio connection plugin `aws_ssm`. Sem ela, o `ansible_connection=community.aws.aws_ssm` do inventário não resolve.
- **collection `amazon.aws`** — traz o *lookup* `ssm_parameter`, que o playbook usa para ler o token do runner do Parameter Store na hora de registrar (Parte 7).

Tudo isso é redundante de propósito: se você pulou algum lab anterior, os comandos com `command -v ... ||` só instalam o que faltar.

Documentação oficial:
- [community.aws.aws_ssm connection plugin](https://docs.ansible.com/ansible/latest/collections/community/aws/aws_ssm_connection.html)
- [Install the Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

</blockquote>
</details>

### Checkpoint

- `python3 --version` responde 3.10.x ou mais recente
- `ansible --version` responde sem erro
- o prompt mostra `(venv)`
- `session-manager-plugin --version` responde sem erro
- `ansible-galaxy collection list community.aws amazon.aws` lista as duas collections instaladas

---

## Parte 3 - Configurando o acesso ao GitLab

### Resultado esperado desta parte

Ao final desta etapa, você terá uma conta GitLab e uma chave SSH registrada e ativa na sessão, capaz de autenticar seus `git push`.

---

<a id="passo-4"></a>

**4.** Antes de criar o runner, você precisa de uma conta no GitLab. Acesse o [GitLab](https://gitlab.com/) e crie uma conta — ou, se já tiver, apenas faça login.

---

<a id="passo-5"></a>

**5.** Para fazer `git push` para o GitLab via SSH, você vai criar e registrar uma chave de conexão. Siga os sub-passos abaixo.

**5.1.** No **terminal do Codespaces**, crie a chave SSH:

```bash
ssh-keygen -t rsa -b 2048 -C "gitlab key" -f /home/vscode/.ssh/gitlab
```

**5.2.** Pressione **Enter duas vezes** para indicar que não quer senha para a chave.

<!-- TROCAR-IMAGEM: img/gitlab-1.png | motivo: prompt mostra o caminho antigo /workspaces/FIAP-Platform-Engineering | capturar: saída do ssh-keygen no Codespaces em /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner -->
![](img/gitlab-1.png)

**5.3.** Abra a parte **pública** da chave no editor do Codespaces e copie todo o conteúdo para a área de transferência do seu computador:

```bash
code /home/vscode/.ssh/gitlab.pub
```

<!-- TROCAR-IMAGEM: img/gitlab-1.1.png | motivo: terminal mostra o caminho antigo FIAP-Platform-Engineering | capturar: gitlab.pub aberto no editor do Codespaces do repositório FIAP-AI-Devops-CICD -->
![](img/gitlab-1.1.png)

**5.4.** Acesse a página de chaves SSH do seu GitLab: [Chaves SSH do GitLab](https://gitlab.com/-/user_settings/ssh_keys) e clique em **"Add new key"**.

![](img/gitlab-1.2.png)


**5.5.** Cole o conteúdo copiado no campo destacado e clique em **Add New Key**.

![](img/gitlab-2.png)

**5.6.** De volta ao **terminal do Codespaces**, ative a chave na sessão atual do terminal:

```bash
eval $(ssh-agent -s)
ssh-add -k /home/vscode/.ssh/gitlab
```

<details>
<summary><b>⚠ Se der erro: <code>Could not open a connection to your authentication agent</code> no ssh-add</b></summary>
<blockquote>

O `ssh-agent` não está rodando na sessão atual. Rode o `eval` antes do `ssh-add`, na mesma linha de raciocínio:

```bash
eval $(ssh-agent -s)
ssh-add -k /home/vscode/.ssh/gitlab
```

Lembre que o agent vive **por sessão de terminal**: se você abrir um novo terminal, precisa rodar o `eval` + `ssh-add` de novo.

</blockquote>
</details>

### Checkpoint

- a chave `gitlab` existe em `/home/vscode/.ssh/`
- a chave pública foi adicionada ao seu GitLab
- `ssh-add` confirmou a identidade adicionada

---

## Parte 4 - Criando o primeiro projeto no GitLab

### Resultado esperado desta parte

Ao final desta etapa, você terá um projeto `primeiro-projeto` no GitLab com o código de exemplo já versionado.

---

<a id="passo-6"></a>

**6.** No **console web do GitLab**, crie um novo projeto. Acesse [Novo projeto](https://gitlab.com/projects/new) e clique em **Create blank project**.

<!-- NOVA-IMAGEM: img/gitlab-3.0.png | capturar: página https://gitlab.com/projects/new com o cartão "Create blank project" destacado -->

---

<a id="passo-7"></a>

**7.** Selecione seu nome de usuário, dê o nome de `primeiro-projeto` (exatamente assim: os labs do módulo 03 usam esse nome), marque como **Public** e **desmarque** a opção **Initialize repository with a README**. Com o README marcado, o projeto nasce com um commit e o `git push` do passo 9.6 é recusado.

![](img/gitlab-3.1.png)

<!-- TROCAR-IMAGEM: img/gitlab-3.png | motivo: mostra "Initialize repository with a README" MARCADO, o contrário do que o passo 7 pede; mantida sem referência até a troca | capturar: não precisa, a img/gitlab-3.1.png já mostra o estado certo -->

---

<a id="passo-8"></a>

**8.** Clique em **Create project**.

---

<a id="passo-9"></a>

**9.** De volta ao **terminal do Codespaces**, você vai subir o código de exemplo para esse projeto. Siga os sub-passos, cuidando dos pontos onde precisa colocar suas informações.

**9.1.** Configure seu nome e e-mail do GitLab no git:

```bash
git config --global user.name "SEU NOME"
git config --global user.email "SEU EMAIL DO GITLAB"
```

**9.2.** Copie o código do projeto para outra pasta, para poder inicializar um repositório git separado:

```bash
mkdir -p /home/vscode/environment
cp -frv /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/primeiro-projeto/ /home/vscode/environment/
ls /home/vscode/environment/primeiro-projeto
```

O `mkdir -p` garante que a pasta de destino existe (sem ele, o `cp` criaria a cópia com outro nome). O `ls` deve listar `apigw.tf`, `dynamodb.tf`, `lambda.tf`, `main.tf`, `outputs.tf`, `src`, `state.tf`, `tests` e `versions.tf`. É neste caminho, `/home/vscode/environment/primeiro-projeto`, que os labs do módulo 03 trabalham.

**9.3.** Entre na pasta copiada (este é o projeto da **API de pedidos** da Quantum Commerce, que o módulo 03 vai publicar pelo pipeline):

```bash
cd /home/vscode/environment/primeiro-projeto
```

**9.4.** Inicialize o repositório e aponte para o seu projeto no GitLab. O comando abaixo **descobre seu usuário do GitLab automaticamente** (a partir da chave SSH que você carregou no passo 5.6) e monta a URL do remote:

```bash
git init
GLUSER=$(ssh -o StrictHostKeyChecking=accept-new -T git@gitlab.com 2>&1 | sed -n 's/.*@\([A-Za-z0-9._-]*\)!.*/\1/p')
git remote add origin git@gitlab.com:$GLUSER/primeiro-projeto.git
git remote -v
```

O `git remote -v` deve mostrar `git@gitlab.com:SEU-USUARIO/primeiro-projeto.git` já com o seu usuário.

<details>
<summary><b>⚠ Se o <code>git remote -v</code> mostrar a URL sem o usuário (<code>git@gitlab.com:/primeiro-projeto.git</code>)</b></summary>
<blockquote>

A chave SSH não está carregada na sessão (o `ssh -T` não conseguiu se identificar). Refaça o passo 5.6 (`eval $(ssh-agent -s)` + `ssh-add`) **neste terminal** e então corrija o remote:

```bash
GLUSER=$(ssh -T git@gitlab.com 2>&1 | sed -n 's/.*@\([A-Za-z0-9._-]*\)!.*/\1/p')
git remote set-url origin git@gitlab.com:$GLUSER/primeiro-projeto.git
git remote -v
```

</blockquote>
</details>

**9.5.** Adicione e faça o commit inicial:

```bash
git add .
git commit -m "Initial commit"
git branch -M master
```

**9.6.** Faça o push para o GitLab:

```bash
git push -uf origin master
```

<!-- TROCAR-IMAGEM: img/gitlab-4.png | motivo: fluxo antigo (prompt voclabs, só sqs.tf e state.tf, push sem -f) | capturar: git commit + git push -uf origin master do primeiro-projeto atual no Codespaces -->
![](img/gitlab-4.png)

![](img/gitlab-5.png)

<details>
<summary><b>⚠ Se der erro: <code>Permission denied (publickey)</code> no git push</b></summary>
<blockquote>

A chave SSH não está ativa na sessão ou não foi adicionada ao GitLab. Verifique:

1. Você rodou o `eval $(ssh-agent -s)` e `ssh-add` do passo 5.6 **neste mesmo terminal**?
2. A chave pública foi colada no GitLab (passo 5.5)?

Teste a conexão:

```bash
ssh -T git@gitlab.com
```

Deve responder com uma saudação contendo seu usuário. Se não, repita os sub-passos 5.5 e 5.6.

</blockquote>
</details>

### Checkpoint

- o projeto `primeiro-projeto` no GitLab mostra os arquivos do código
- o `git push` terminou sem erro de autenticação

---

## Parte 5 - Gerando o token do runner e guardando no SSM

### Resultado esperado desta parte

Ao final desta etapa, você terá um token de registro de runner gerado no GitLab e guardado com segurança no **AWS SSM Parameter Store** — sem colar segredo em nenhum arquivo do projeto.

---

<a id="passo-10"></a>

**10.** No seu projeto do GitLab, clique em **Settings** na lateral esquerda e depois em **CI/CD**.

![](img/gitlab-6.png)

---

<a id="passo-11"></a>

**11.** Em **Runners**, clique para expandir.

![](img/gitlab-7-1.png)

![](img/gitlab-7.png)

---

<a id="passo-12"></a>

**12.** Na aba **Instance**, desabilite a opção **Turn on instance runners for this project** — assim, o runner que vamos criar será usado apenas por este projeto.

![](img/gitlab-8.png)

---

<a id="passo-13"></a>

**13.** Clique em **Assigned project runners** e em seguida em **Create project runner**, para gerar o token que registrará o runner.

![](img/gitlab-9.png)

---

<a id="passo-14"></a>

**14.** No campo de **Tags**, adicione o valor `shell, terraform` e clique em **Create runner**.

![](img/gitlab-9-1.png)

> [!IMPORTANT]
> As **tags** do runner (`shell`, `terraform`) são definidas **aqui, na interface do GitLab** — e é assim que elas continuam valendo no fluxo atual. Desde o GitLab 16 o runner é criado primeiro na UI (gerando um token que começa com `glrt-`) e a máquina só se **vincula** a ele depois; as tags, o nível de acesso e o "run untagged" pertencem ao runner criado na UI, não ao comando de registro. Por isso é importante preencher as tags corretamente neste passo.

---

<a id="passo-15"></a>

**15.** Copie o token gerado para a área de transferência do seu computador.

![](img/gitlab-9-2.png)

---

<a id="passo-16"></a>

**16.** De volta ao **Codespaces**, guarde o token no **AWS SSM Parameter Store** — não em um arquivo. Rode o comando abaixo trocando `COLE-SEU-TOKEN-AQUI` pelo token que você copiou (mantenha as aspas):

```bash
aws ssm put-parameter \
  --name "/fiap/gitlab-runner/token" \
  --type SecureString \
  --value "COLE-SEU-TOKEN-AQUI" \
  --region us-east-1 \
  --overwrite
```

Você deve ver uma resposta com `"Version": 1` (ou um número maior, se já tinha gravado antes). Confirme que o token ficou guardado:

```bash
aws ssm get-parameter --name "/fiap/gitlab-runner/token" --with-decryption \
  --region us-east-1 --query 'Parameter.Value' --output text
```

Deve imprimir o seu token `glrt-...`. É esse parâmetro que o playbook vai ler automaticamente na hora de registrar o runner.

![](img/ssm-parameter-token.png)

<details>
<summary><b>💡 Clique para entender: por que guardar o token no SSM Parameter Store</b></summary>
<blockquote>

O token de registro é um **segredo**: quem o tiver pode registrar runners no seu projeto. Colá-lo dentro de um arquivo `.yml` é perigoso — ele acabaria no histórico do Git, visível para sempre.

O **SSM Parameter Store** é um cofre de parâmetros da AWS. Guardando o token como `SecureString`, ele fica **criptografado** e fora do código. Na hora de registrar o runner, o Ansible usa um *lookup* (`amazon.aws.ssm_parameter`) que lê o valor direto do Parameter Store — usando as suas credenciais AWS, as mesmas do resto do lab. Nenhum segredo entra no repositório.

Esse é o padrão que você usaria de verdade num time de engenharia: segredos vivem num cofre (Parameter Store, Secrets Manager, Vault), nunca no código. Aqui você pratica isso com a ferramenta nativa da AWS, sem custo no Learner Lab.

- `--type SecureString` — criptografa o valor em repouso.
- `--name "/fiap/gitlab-runner/token"` — o caminho onde o playbook procura o token (definido em `play.yaml`).
- `--overwrite` — permite regravar se você precisar gerar um token novo depois.

Documentação oficial:
- [aws ssm put-parameter](https://docs.aws.amazon.com/cli/latest/reference/ssm/put-parameter.html)
- [amazon.aws.ssm_parameter lookup](https://docs.ansible.com/ansible/latest/collections/amazon/aws/ssm_parameter_lookup.html)

</blockquote>
</details>

### Checkpoint

- o token foi gerado no GitLab (com as tags `shell`, `terraform` na UI)
- o token está guardado em `/fiap/gitlab-runner/token` no SSM Parameter Store (`get-parameter` imprime o seu `glrt-...`)
- **nenhum** arquivo do projeto contém o token

---

## Parte 6 - Provisionando a EC2 do runner com Terraform

### Resultado esperado desta parte

Ao final desta etapa, você terá uma EC2 provisionada (já com Python/pip/awscli instalados pelo bootstrap via SSM) e o **instance id** dela em mãos para o inventário do Ansible.

---

<a id="passo-17"></a>

**17.** O runner será uma EC2 provisionada com Terraform. Entre na pasta com o código:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/terraform-gitlab-runner/
```

---

<a id="passo-18"></a>

**18.** O state remoto deste projeto usa um bucket S3 — o mesmo `base-config-<SEU-RM>` criado no setup inicial do módulo 01. Em vez de editar o arquivo na mão, o comando abaixo **descobre o seu bucket automaticamente** e o grava no `state.tf`:

```bash
BUCKET=$(aws s3 ls | awk '{print $3}' | grep '^base-config' | head -1)
sed -i "s/base-config-SEU-RM/$BUCKET/" state.tf
grep bucket state.tf
```

O `grep` no final deve mostrar `bucket = "base-config-<SEU-RM>"` já com o nome do seu bucket. Confirme que não ficou o placeholder `SEU-RM`. A `key` do state é `gitlab-runner-fleet`: é por ela que o Lab 03.2 e o Trabalho Final reencontram este runner.

<details>
<summary><b>💡 Clique para entender: o que esse comando faz</b></summary>
<blockquote>

- `aws s3 ls` lista seus buckets; `awk '{print $3}'` pega só o nome; `grep '^base-config'` filtra o do curso; `head -1` garante um só.
- `sed -i "s/.../$BUCKET/" state.tf` substitui o placeholder `base-config-SEU-RM` pelo nome real, direto no arquivo.

Fazemos assim porque o backend do Terraform **não aceita variável** no nome do bucket — o valor precisa ser literal no `state.tf`. Descobrir e substituir por comando evita erro de digitação (espaço, maiúscula, RM errado), que é a causa nº 1 de falha no `init`.

</blockquote>
</details>

> [!CAUTION]
> Se você tiver **mais de um** bucket começando com `base-config`, o comando pega o primeiro. Confira o resultado do `grep` e, se pegou o errado, edite o `state.tf` com `code state.tf` e ajuste o nome à mão.

---

<a id="passo-19"></a>

**19.** Inicialize o Terraform (vamos usar a flag `-auto-approve` nos próximos comandos para evitar a confirmação manual):

```bash
terraform init
```

<details>
<summary><b>⚠ Se der erro: <code>NoSuchBucket</code> ou <code>error configuring S3 Backend</code></b></summary>
<blockquote>

O bucket informado em `state.tf` não existe ou o nome está errado (espaços, maiúsculas, RM incorreto). Liste seus buckets para confirmar o nome exato:

```bash
aws s3 ls | grep base-config
```

Corrija o `state.tf` com o nome listado e rode `terraform init` novamente.

</blockquote>
</details>

---

<a id="passo-20"></a>

**20.** Veja o que será criado:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/terraform-gitlab-runner/
terraform plan
```

> Saída esperada (a linha que importa):
>
> ```text
> Plan: 3 to add, 0 to change, 0 to destroy.
> ```

Os 3 recursos são a EC2 `quantum-ai-gitlab-runner` (`aws_instance.runner`), o Security Group `quantum-ai-gitlab-runner` e o `terraform_data.bootstrap`. Nada da rede `fiap-lab` aparece no plano: ela é só lida.

---

<a id="passo-21"></a>

**21.** Provisione a máquina que será o runner. Esse apply, além de criar a EC2, roda um script de bootstrap (`install-python.sh`) na máquina — **via SSM**, não SSH — para preparar tudo o que o Ansible vai precisar (Python, pip, awscli):

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/terraform-gitlab-runner/
terraform apply -auto-approve
```

> Saída esperada (no fim, depois do log do bootstrap):
>
> ```text
> == bootstrap concluido ==
> ...
> Apply complete! Resources: 3 added, 0 changed, 0 destroyed.
> ```

> [!NOTE]
> O apply fica um tempo em "Aguardando a instancia ficar online no SSM..." e depois mostra o log do bootstrap. Isso é esperado: o Terraform espera o SSM Agent reportar `Online`, manda o `install-python.sh` por `aws ssm send-command` e só conclui o apply quando o script termina com sucesso (se falhar, o apply aborta).

<details>
<summary><b>💡 Clique para entender: por que Terraform e Ansible juntos (e tudo via SSM)</b></summary>
<blockquote>

Repare na divisão de responsabilidades: o Terraform **cria** a EC2 (recurso de infraestrutura) e ainda roda um `install-python.sh` mínimo só para garantir que o Python existe — porque o Ansible precisa de Python no host de destino para funcionar.

A novidade é **como** esse bootstrap chega na máquina: não há SSH nem chave. A EC2 sobe **sem `key_name`** e o Security Group só tem regra de **saída**: nenhuma porta de entrada, nem a 22. Em vez disso, o Terraform usa um `terraform_data` com `local-exec` que (1) espera o SSM Agent da instância reportar `Online`, (2) envia o `install-python.sh` por `aws ssm send-command` e (3) aborta o apply se o script falhar. Quem autentica tudo isso é o `LabInstanceProfile` anexado à EC2 mais as credenciais AWS do seu ambiente — por isso `aws` e `jq` são pré-requisitos (passo 3.5).

A configuração de verdade (instalar `gitlab-runner`, Terraform, TFLint, Checkov, ajustar o `systemd`, registrar o runner) fica para o **Ansible**, na próxima parte, que também conecta via SSM (plugin `community.aws.aws_ssm`).

A AMI é resolvida dinamicamente por um `data "aws_ami"` (Ubuntu 22.04 mais recente da Canonical) em vez de um ID fixo — assim o lab não quebra quando a AMI antiga é despublicada.

Documentação oficial:
- [aws ssm send-command](https://docs.aws.amazon.com/cli/latest/reference/ssm/send-command.html)
- [data source aws_ami](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami)

</blockquote>
</details>

---

<a id="passo-22"></a>

**22.** Copie o **instance id** da EC2 (algo como `i-0abc123...`) — vamos usá-lo no inventário do Ansible, já que a conexão é por SSM e não por IP:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/terraform-gitlab-runner/
terraform output -raw instance_id; echo
```

> Saída esperada (o id é o seu):
>
> ```text
> i-0abc123def4567890
> ```

<!-- TROCAR-IMAGEM: img/gitlab-11.png | motivo: prompt mostra o caminho antigo FIAP-Platform-Engineering | capturar: terraform output -raw instance_id no repositório FIAP-AI-Devops-CICD -->
![](img/gitlab-11.png)

### Checkpoint

- o `terraform apply` terminou com `Apply complete!`
- o log do bootstrap via SSM apareceu e terminou com sucesso (sem `ERRO:`)
- `terraform output -raw instance_id` mostra o id da EC2 (`i-...`)

---

## Parte 7 - Configurando a EC2 como runner com Ansible

### Resultado esperado desta parte

Ao final desta etapa, a EC2 estará configurada como GitLab Runner registrado e aparecerá online no painel do projeto.

---

<a id="passo-23"></a>

**23.** Entre na pasta onde o Ansible será executado:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/ansible-gitlab-runner/
```

---

<a id="passo-24"></a>

**24.** O arquivo de **inventário** (`hosts`) diz ao Ansible quais máquinas acessar e como. Ele precisa de **dois** valores: o **instance id** da EC2 (do passo 22) como host, e o nome do **seu bucket S3** (`base-config-<SEU-RM>`) que o plugin SSM usa para transferir arquivos. O comando abaixo **descobre os dois automaticamente** e os grava no `hosts` (o instance id vem do Terraform da pasta ao lado; o bucket, do `aws s3 ls`):

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/ansible-gitlab-runner/
ID=$(terraform -chdir=../terraform-gitlab-runner output -raw instance_id)
BUCKET=$(aws s3 ls | awk '{print $3}' | grep '^base-config' | head -1)
sed -i "s|<INSTANCE ID DO SERVER>|$ID|" hosts
sed -i "s/base-config-SEU-RM/$BUCKET/" hosts
cat hosts
```

O `cat` no final deve mostrar o `hosts` já preenchido — com o `i-...` real embaixo de `[runner]` e o seu bucket em `ansible_aws_ssm_bucket_name`, mais ou menos assim:

```ini
[runner]
i-0abc123def4567890

[all:vars]
ansible_connection=community.aws.aws_ssm
ansible_aws_ssm_bucket_name=base-config-12345
ansible_aws_ssm_region=us-east-1
ansible_become=true
```

<!-- TROCAR-IMAGEM: img/gitlab-12.png | motivo: prompt mostra o caminho antigo FIAP-Platform-Engineering | capturar: o mesmo bloco do passo 24 rodado no repositório FIAP-AI-Devops-CICD, com o cat hosts preenchido -->
![](img/gitlab-12.png)

<details>
<summary><b>💡 Clique para entender: o inventário do Ansible via SSM</b></summary>
<blockquote>

O arquivo `hosts` é o **inventário**: ele diz ao Ansible quais máquinas existem e como conectar a elas. Como aqui a conexão é via SSM (não SSH), o conteúdo muda:

- `[runner]` é um **grupo** de hosts; abaixo dele o host é o **instance id** da EC2 (`i-...`), não um IP. O SSM endereça a máquina pelo id, então não precisamos de DNS nem de IP público.
- `[all:vars]` define variáveis válidas para todos os hosts:
  - `ansible_connection=community.aws.aws_ssm` — usa o connection plugin do SSM em vez de SSH. **Não há `ansible_ssh_private_key_file`**: nenhuma chave é usada.
  - `ansible_aws_ssm_bucket_name` — o plugin SSM copia os arquivos do playbook por um bucket S3 intermediário; reutilizamos o `base-config-<RM>` do setup.
  - `ansible_aws_ssm_region` — região onde estão a EC2 e o bucket (`us-east-1`).
  - `ansible_become=true` — elevação de privilégio (vira root para instalar pacotes).

É o equivalente Ansible do "para quais servidores eu aplico essa configuração e como chego neles". Quem autoriza a conexão são as credenciais AWS do seu ambiente + o `LabInstanceProfile` da EC2 — sem chave, sem porta 22.

Documentação oficial:
- [Ansible — Inventário](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html)
- [community.aws.aws_ssm connection plugin](https://docs.ansible.com/ansible/latest/collections/community/aws/aws_ssm_connection.html)

</blockquote>
</details>

---

<a id="passo-25"></a>

**25.** Execute o playbook que configura a EC2 como GitLab Runner. Como a conexão é via SSM, **não há `-u ubuntu` nem chave privada** — o Ansible usa as credenciais AWS já presentes no seu ambiente. O **token do runner é lido automaticamente do SSM** (o que você gravou no passo 16); você não passa o token aqui:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/ansible-gitlab-runner/
ansible-playbook -i hosts --extra-vars 'gitlab_runner_name=quantum-ai-gitlab-runner' play.yaml
```

> Saída esperada (última linha, o `PLAY RECAP`; o id é o seu):
>
> ```text
> i-0abc123def4567890        : ok=30   changed=16   unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
> ```

O que importa é `failed=0` e `unreachable=0`. O `changed=16` diz que o Ansible **mudou** 16 coisas na máquina, porque ela estava nua. Guarde esse número: na Parte 8 você roda o mesmo comando e ele vai cair para zero.

> [!NOTE]
> Logo no começo do registro, o playbook executa a task **"Ler o token do runner do SSM Parameter Store"**. Se você não gravou o token (passo 16), o playbook **para** com uma mensagem clara dizendo exatamente qual comando rodar — é o comportamento esperado (falhar cedo, não registrar um runner quebrado).

<!-- TROCAR-IMAGEM: img/gitlab-14.png | motivo: caminho antigo FIAP-Platform-Engineering, nome gitlab-runner-fleet-001 e recap antigo (ok=35 changed=18) | capturar: fim do ansible-playbook com gitlab_runner_name=quantum-ai-gitlab-runner e o recap ok=30 changed=16 failed=0 -->
![](img/gitlab-14.png)

<details>
<summary><b>💡 Clique para entender: o comando sem chave SSH</b></summary>
<blockquote>

Compare com a versão antiga, que tinha `ANSIBLE_HOST_KEY_CHECKING=False` e `-u ubuntu`: aquilo só fazia sentido quando o Ansible conectava por SSH (desligar a verificação de *fingerprint* e dizer qual usuário SSH usar). Com o connection plugin `community.aws.aws_ssm` nada disso existe — não há fingerprint de SSH para confiar nem usuário SSH para informar.

O que sobra no comando: `-i hosts` aponta o inventário (que já declara a conexão SSM, o instance id e o bucket) e `--extra-vars` passa o nome do runner para o playbook. A autenticação vem das **credenciais AWS no ambiente** (as mesmas que o `aws sts get-caller-identity` valida) somadas ao `LabInstanceProfile` da EC2. O `become` (virar root) está declarado no próprio inventário (`ansible_become=true`).

Documentação oficial:
- [community.aws.aws_ssm connection plugin](https://docs.ansible.com/ansible/latest/collections/community/aws/aws_ssm_connection.html)

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>UNREACHABLE</code> / falha ao abrir a sessão SSM</b></summary>
<blockquote>

O Ansible não conseguiu chegar na EC2 via SSM. Verifique, na ordem:

1. O **instance id no `hosts`** é exatamente o que `terraform output -raw instance_id` retornou (passo 22)?
2. A EC2 já está `Online` no SSM? Logo após o `apply` ela costuma estar (o próprio bootstrap esperou o SSM ficar online), mas confirme com `aws ssm describe-instance-information --filters "Key=InstanceIds,Values=i-SEU-ID" --query 'InstanceInformationList[0].PingStatus'`.
3. O `session-manager-plugin` está instalado? Confira com `session-manager-plugin --version` (passo 3.5).
4. As collections `community.aws` e `amazon.aws` e o `boto3` estão instalados no `(venv)` ativo (passo 3.5)?
5. O `ansible_aws_ssm_bucket_name` no `hosts` é um bucket **seu** e existente (`base-config-<RM>`)?
6. Suas credenciais AWS estão válidas? Valide com `aws sts get-caller-identity`.

</blockquote>
</details>

---

<a id="passo-26"></a>

**26.** Volte à mesma página do GitLab onde você pegou o token (**Settings → CI/CD → Runners**, aba **Assigned project runners**). Agora há um runner **Online**, com as tags `shell` e `terraform`.

<!-- TROCAR-IMAGEM: img/gitlab-13.png | motivo: dono aparece como primeiro-projeto-test, não primeiro-projeto | capturar: aba Assigned project runners do primeiro-projeto com o runner Online e as tags shell, terraform -->
![](img/gitlab-13.png)

<details>
<summary><b>⚠ Se der erro: o playbook termina sem falha, mas o runner não aparece registrado no GitLab</b></summary>
<blockquote>

Quase sempre é o **token**. Verifique:

1. O token guardado no SSM está correto? Confira com `aws ssm get-parameter --name "/fiap/gitlab-runner/token" --with-decryption --region us-east-1 --query 'Parameter.Value' --output text` — deve imprimir o seu `glrt-...` (passo 16).
2. O token foi gerado para **este** projeto (passo 13-15)? Tokens de runner são por projeto.
3. Se o token estava errado, regrave no SSM com o mesmo `put-parameter --overwrite` do passo 16 e rode o playbook de novo (passo 25) — o registro é idempotente.

</blockquote>
</details>

### Checkpoint

- o playbook terminou com `failed=0`
- o runner aparece registrado e online no painel CI/CD do projeto GitLab

---

## Parte 8 - Conferindo o runner e provando a idempotência

### Resultado esperado desta parte

Você confirma, de dentro da EC2, que as ferramentas que o módulo 03 usa estão lá, e prova que rodar o playbook de novo não muda nada.

---

<a id="passo-27"></a>

**27. Confira as ferramentas instaladas no runner**

No **terminal do Codespaces**, use o próprio Ansible (mesmo inventário, mesmo canal SSM) para rodar um comando de checagem dentro da EC2. Isso é um comando *ad hoc*: uma tarefa avulsa, sem playbook.

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/ansible-gitlab-runner/
ansible runner -i hosts -m shell -a "terraform version | head -1; tflint --version | head -1; /opt/venv/bin/checkov --version; python3 --version; jq --version; aws --version; git --version; gitlab-runner --version | head -1; gitlab-runner verify 2>&1 | grep -o 'is valid'; grep -cE '^\[\[runners\]\]' /etc/gitlab-runner/config.toml; grep -E '^ +(name|executor) =' /etc/gitlab-runner/config.toml"
```

> Saída esperada (versões do teste real; o id e os números de build variam):
>
> ```text
> i-0abc123def4567890 | CHANGED | rc=0 >>
> Terraform v1.10.5
> TFLint version 0.65.0
> 3.4.0
> Python 3.10.12
> jq-1.6
> aws-cli/2.39.3 Python/3.14.8 Linux/6.8.0-1066-aws exe/x86_64.ubuntu.22
> git version 2.34.1
> Version:      19.5.2
> is valid
> 1
>   name = "quantum-ai-gitlab-runner"
>   executor = "shell"
> ```

As três últimas linhas são o registro: `is valid` é o GitLab confirmando que conhece este runner, `1` é a quantidade de runners nesta máquina (precisa ser exatamente 1) e `executor = "shell"` é o modo que os pipelines do módulo 03 esperam.

> [!WARNING]
> Não use `gitlab-runner list` para essa conferência: ele imprime o token `glrt-...` inteiro na tela. Quem tem o token consegue se passar pelo seu runner. Se ele aparecer num print ou num chat, gere outro token no GitLab e grave de novo no SSM (passo 16).

O `CHANGED` na primeira linha é normal: o módulo `shell` sempre se declara "mudou" porque o Ansible não sabe o que um comando livre faz. São essas ferramentas que os labs 03.1 e 03.2 chamam no pipeline: `terraform`, `tflint`, o Checkov em `/opt/venv`, `python3` (validação da Lambda), `jq` e `aws`.

<details>
<summary><b>⚠ Se der erro: <code>command not found</code> para alguma ferramenta</b></summary>
<blockquote>

O playbook não chegou a instalar aquela ferramenta (por exemplo, parou no meio por credencial expirada). Rode de novo o [passo 25](#passo-25): o playbook é idempotente, então ele só instala o que falta. Se o erro for `UNREACHABLE`, siga o troubleshoot do passo 25.

</blockquote>
</details>

---

<a id="passo-28"></a>

**28. Rode o playbook uma segunda vez**

Mesmo comando do passo 25, sem mudar nada:

```bash
cd /workspaces/FIAP-AI-Devops-CICD/02-Ansible/01-provisionando-gitlab-runner/ansible-gitlab-runner/
ansible-playbook -i hosts --extra-vars 'gitlab_runner_name=quantum-ai-gitlab-runner' play.yaml
```

> Saída esperada (`PLAY RECAP`):
>
> ```text
> i-0abc123def4567890        : ok=27   changed=0    unreachable=0    failed=0    skipped=4    rescued=0    ignored=0
> ```

`changed=0` é a prova da idempotência: o Ansible conferiu cada item (pacotes, binários, serviço, registro), viu que já estavam no estado desejado e não mexeu em nada. O registro também não foi refeito: o playbook vê que esta máquina já está vinculada ao token atual e pula o `register` (por isso aparecem tasks `skipped`). No GitLab continua existindo **um** runner, não dois.

Se você gerar um token novo no GitLab e regravar o parâmetro no SSM (passo 16), a próxima execução volta a registrar, porque o estado desejado mudou.

### Checkpoint

- as ferramentas responderam no passo 27 e o `verify` disse `is valid`
- a segunda execução do playbook terminou com `changed=0` e `failed=0`
- o painel do GitLab mostra um único runner Online para o `primeiro-projeto`

---

## Conclusão

> [!CAUTION]
> **NÃO destrua o runner agora.** A EC2 que você acabou de configurar é o **mesmo runner que o módulo 03 (CI/CD) vai usar** para rodar os pipelines. Sem ela, os jobs do próximo módulo ficam parados em "stuck". Ela custa enquanto existir: a `t3.small` sai por ~US$ 0,02/h (~US$ 0,50 por dia, ~US$ 3,50 por semana), mais ~US$ 0,08/dia do disco de 30 GB. O `terraform destroy` do runner está no **[passo 11 do Lab 03.2](../../03-CICD/02-Validando-e-gerando-relatorios/README.md#passo-11)**: rode lá, ao terminar o CI/CD, ou quando o curso acabar. A rede `fiap-lab` não é destruída em nenhum desses momentos.

**O que você precisa ter para entrar no módulo 03:**

- o projeto **`primeiro-projeto`** no seu GitLab, com o código da API de pedidos versionado (Parte 4)
- a chave SSH do GitLab em **`/home/vscode/.ssh/gitlab`**, cadastrada no GitLab (Parte 3); em terminal novo, rode de novo o `eval $(ssh-agent -s)` + `ssh-add` do passo 5.6
- a cópia local do projeto em **`/home/vscode/environment/primeiro-projeto`** (passo 9)
- o runner **Online** no `primeiro-projeto`, executor **`shell`**, tags **`shell,terraform`** (passos 14 e 26)
- no runner: `terraform`, `tflint`, `checkov` em `/opt/venv`, `python3`, `jq`, `aws` e `git` (passo 27)

Se você chegou até aqui, então já executou:

- instalação de Python, Ansible, virtualenv e as ferramentas de SSM num ambiente isolado
- geração de chave SSH e registro no GitLab (para o `git push`)
- criação de projeto no GitLab e push do código via SSH
- geração do token de registro do runner e armazenamento seguro no SSM Parameter Store (sem hardcode)
- provisionamento da EC2 com Terraform usando state remoto no S3, com bootstrap via SSM
- configuração da EC2 como GitLab Runner via playbook Ansible, conectando via SSM (sem chave, sem porta 22)

- conferência das ferramentas no runner e prova de idempotência (`changed=0` na segunda execução)

**Mensagem para Helena**: o runner da Quantum Commerce agora sobe por código. O "documento de 30 passos" virou um `terraform apply` seguido de um `ansible-playbook`: qualquer pessoa do time sobe um runner idêntico, e rodar de novo não muda nada. Está pronto para automatizar o deploy da API de pedidos no módulo de CI/CD.

Este laboratório forma a base para o próximo módulo: com o runner registrado, o GitLab pode executar pipelines automatizados.

---

## Próximo passo

Abra o próximo módulo: **[03 — CI/CD com GitLab](../../03-CICD/README.md)**.

Lá Diego Tavares volta com uma demanda: *"Temos o runner. Agora quero que todo push na master rode `plan` e `apply` sozinho, com um gate de segurança que barre configuração insegura antes de chegar na nuvem."*

---

<details>
<summary><b>💡 Glossário rápido — termos que aparecem neste lab</b></summary>
<blockquote>

| Termo | O que é |
|-------|---------|
| **Ansible** | Ferramenta de configuração de servidores. Descreve o estado desejado em YAML (playbooks/tasks) e aplica de forma idempotente. Normalmente conecta por SSH; neste lab, por SSM. |
| **Playbook** | Arquivo YAML que orquestra uma sequência de tasks Ansible contra um conjunto de hosts. Aqui é o `play.yaml`. |
| **Task** | Unidade de trabalho do Ansible (instalar pacote, copiar arquivo, rodar comando). As tasks deste lab estão em `tasks/`. |
| **Inventário (`hosts`)** | Arquivo que lista quais máquinas o Ansible gerencia e como conectar. Aqui: o instance id da EC2 e a conexão via SSM (sem IP, sem chave SSH). |
| **SSM (AWS Systems Manager)** | Serviço da AWS que permite acessar e rodar comandos numa EC2 sem SSH, sem chave e sem porta 22. O acesso à EC2 do runner (bootstrap do Terraform e conexão do Ansible) é todo via SSM. |
| **`community.aws.aws_ssm`** | Connection plugin do Ansible que conecta na EC2 através do SSM em vez de SSH. Usa um bucket S3 para transferir arquivos. |
| **`session-manager-plugin`** | Binário local que o plugin `aws_ssm` usa para abrir a sessão SSM com a EC2. Instalado no passo 3.5. |
| **Idempotência** | Propriedade de rodar a mesma operação várias vezes sem efeitos colaterais. Aplicar o playbook duas vezes não duplica nada (`changed=0` na segunda, passo 28). |
| **Comando ad hoc** | Tarefa avulsa do Ansible (`ansible <grupo> -m <módulo> -a ...`), sem playbook. Usado no passo 27 para conferir o runner. |
| **GitLab Runner** | Processo que executa os jobs de um pipeline de CI/CD. Aqui, dedicado a um único projeto. |
| **Token de registro** | Segredo gerado pelo GitLab que autentica e vincula um runner a um projeto. No GitLab 16+ começa com `glrt-` e é criado junto com o runner na UI. |
| **SSM Parameter Store** | Cofre de parâmetros da AWS. Guardamos o token do runner como `SecureString` (criptografado) e o Ansible o lê em runtime, sem hardcode no código. |
| **virtualenv** | Ambiente Python isolado, com pacotes próprios, ativado por `source ~/venv/bin/activate`. |
| **State remoto (S3)** | Arquivo de estado do Terraform guardado num bucket S3, para colaboração sem corromper o estado local. |
| **Bootstrap (Terraform via SSM)** | Etapa em que o Terraform envia o `install-python.sh` para a EC2 por `aws ssm send-command`, garantindo Python/pip/awscli antes do Ansible. Configuração de verdade fica no Ansible. |

</blockquote>
</details>

<details>
<summary><b>💡 Como pedir ajuda se travou</b></summary>
<blockquote>

Antes de abrir issue/perguntar, colete estas 4 informações — elas reduzem o tempo de resposta em 10×:

1. **Em que passo você está** (ex: "passo 25, rodando o `ansible-playbook`")
2. **Mensagem de erro literal** (copia-cola completo do terminal, não screenshot — texto é pesquisável)
3. **Saída de** `terraform output -raw instance_id` **e do arquivo** `hosts` (mostra o instance id que o Ansible está usando)
4. **O que você já tentou**

Canais (em ordem de prioridade):

- **Issues do repositório**: [github.com/vamperst/FIAP-AI-Devops-CICD/issues](https://github.com/vamperst/FIAP-AI-Devops-CICD/issues)
- **E-mail do professor**: `Rafael@rfbarbosa.com`
- **LinkedIn**: [rafael-barbosa-serverless](https://www.linkedin.com/in/rafael-barbosa-serverless/)
- **Antes de tudo**: confira se o instance id em `hosts` bate com o `terraform output -raw instance_id` e se o `session-manager-plugin` está instalado (passo 3.5) — são as causas mais comuns de `UNREACHABLE` na conexão via SSM.

</blockquote>
</details>
