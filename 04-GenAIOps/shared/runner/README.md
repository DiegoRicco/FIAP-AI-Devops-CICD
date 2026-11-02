# Runner do GitLab: conferir e recuperar antes da aula

Entre uma aula e outra, a EC2 do runner pode ter sido desligada, a sessão do AWS Academy pode ter expirado ou o serviço pode ter parado. Estes três scripts respondem, na ordem, a três perguntas: **o runner ainda serve?**, **qual o menor passo para ele voltar?** e **ele executa um job de verdade?**.

Execute tudo no terminal do Codespaces, a partir da raiz do repositório.

## Ritual do dia da aula (5 minutos)

1. **Atualize as credenciais.** No Learner Lab: *Start Lab* → *AWS Details* → cole o bloco em `~/.aws/credentials`. Confira com `aws sts get-caller-identity`.
2. **Diagnostique (somente leitura):**
   ```bash
   bash 04-GenAIOps/shared/runner/runner-preflight.sh
   ```
   A última linha mostra `diagnosis=...`. Com `reuse`, pule para o passo 4.
3. **Veja o plano e depois execute:**
   ```bash
   bash 04-GenAIOps/shared/runner/runner-recover.sh            # só mostra o plano
   bash 04-GenAIOps/shared/runner/runner-recover.sh --apply    # executa
   ```
4. **Prove com um job real:**
   ```bash
   GITLAB_REPO_SSH=git@gitlab.com:SEU-USUARIO/primeiro-projeto.git \
     bash 04-GenAIOps/shared/runner/runner-test.sh
   ```
   Resultado esperado: `result=Job succeeded`. O job roda na branch `runner-smoke`, então a `main` e o pipeline do projeto não são afetados.

## O que cada diagnóstico significa

| `diagnosis` | Situação | O que o `--apply` faz |
|---|---|---|
| `reuse` | EC2 ligada, SSM Online, serviço `active`, `verify` válido, 1 runner no `config.toml` | nada |
| `restart` | registro válido, mas serviço parado | `systemctl restart gitlab-runner` via SSM |
| `start` | EC2 desligada (disco e registro preservados) | liga a EC2 e espera o SSM (~1 min) |
| `reconfigure` | EC2 existe, mas falta o runner ou há token novo no SSM | roda o playbook do Módulo 02 |
| `recreate` | não existe EC2 do runner | `terraform plan`/`apply` do Módulo 02 e depois o playbook |
| `blocked` | credencial expirada, SSM offline, token revogado, runner duplicado | não age e mostra a dica |

O `--apply` roda o preflight de novo depois de cada ação e só para em `reuse`. Rodar duas vezes é seguro: com o runner saudável, a segunda execução não muda nada.

## Reboot, stop/start e outros sustos

- **Reboot** da EC2: o IP público é o mesmo.
- **Stop/start**: o IP público muda. **Isso não quebra nada**, porque o runner não recebe conexões: ele busca jobs no gitlab.com (conexão de saída). Por isso não usamos Elastic IP.
- **Terminate**: a EC2 e o disco somem. O diagnóstico é `recreate`.
- **Codespaces parado** ou **sessão do AWS Academy expirada**: o runner continua igual. O que expira são as credenciais do seu terminal (`ExpiredToken`).

## Mensagens comuns

| Mensagem | Causa | Correção |
|---|---|---|
| `ExpiredToken` | a sessão do Learner Lab acabou (~4 h) | cole credenciais novas (passo 1) |
| `ssm_ping=ConnectionLost` / `ssm_offline` | a EC2 acabou de ligar ou está sem `LabInstanceProfile` | espere 1 min e rode o preflight de novo |
| job `Pending` / `stuck` | nenhum runner online com a tag `shell` no projeto | confira em *Settings > CI/CD > Runners* a bolinha verde e as tags `shell,terraform` |
| `token_revoked` | o runner foi apagado ou o token revogado na UI | crie um runner novo na UI, grave o `glrt-` no SSM com `--overwrite` e rode `--apply` |

## Segurança

- Nenhum script imprime o token. O `gitlab-runner list` **não é usado** porque mostra o token na tela; para contar runners, contamos os blocos `[[runners]]` do `config.toml` sem exibi-lo.
- Nada usa `terraform destroy`, Elastic IP ou porta 22. Um plano Terraform que apague recurso AWS é abortado.
- O Terraform roda numa cópia temporária da pasta do Módulo 02 (o state fica no S3), então o repositório não ganha arquivos novos.
