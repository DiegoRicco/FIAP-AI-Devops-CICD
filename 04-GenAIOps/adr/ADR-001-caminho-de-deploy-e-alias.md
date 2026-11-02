# ADR-001 — Caminho de deploy: Lambda com versões publicadas e aliases `candidate` / `live`

**Status:** aceito (Aula 5, módulo 04).
**Decide:** como o Assistente da Quantum Commerce é publicado pelo pipeline e como a promoção e o rollback acontecem.

## Contexto

O módulo 03 publicava o assistente com um `terraform apply` direto: o `apply` trocava a Lambda e, no mesmo instante, o que a API respondia. Isso bastava para ensinar CI/CD, mas deixa duas perguntas sem resposta:

- **"O que está no ar agora é o que foi testado?"** O teste pós-deploy do 03.2 rodava contra a versão já publicada. Se reprovasse, o cliente já estava recebendo a resposta errada.
- **"Como volto para a versão anterior em um minuto?"** Voltar exigia um novo commit, um novo pipeline e um novo `apply` (minutos, com o gate inteiro no caminho).

Além disso, o Learner Lab limita o que podemos usar: `LabRole` fixa, sem criar roles; sem ECS/EKS; sem serviços de deploy progressivo pagos. Precisamos de um caminho **simples de explicar** a quem nunca fez deploy, e que caiba no runner `shell` do módulo 02.

## Decisão

1. A Lambda `qc-assistant-api` é criada com **`publish = true`**: cada `apply` que muda o pacote gera uma **versão numerada e imutável** da função.
2. Dois **aliases** apontam para versões:
   - **`candidate`**: recebe a versão nova logo após o `apply`. É contra ele que rodam o smoke e o subconjunto do golden set no stage `postdeploy`.
   - **`live`**: é o que a rota pública do cliente usa. Só muda no job `promote`, depois do `postdeploy` passar.
3. A API Gateway HTTP `qc-assistant` expõe os dois caminhos: `POST /assistant/ask` e `GET /health` vão para `live`; `/candidate/assistant/ask` e `/candidate/health` vão para `candidate`.
4. O alias `live` tem `lifecycle { ignore_changes = [function_version] }` no Terraform: **só `promote` e `rollback` movem o `live`**, nunca o `apply`. Isso impede que um `apply` qualquer troque a versão em produção por acidente.
5. **Rollback** = apontar `live` para o release **promovido anterior**, registrado em `s3://<bucket>/quantum-assistant/releases/` (`scripts/rollback.sh`, executado pelo job manual `rollback` do pipeline, ou por um pipeline disparado com `QC_ACTION=rollback`). Não precisa de novo `apply` nem de passar pelos gates. Validado na conta do Learner Lab: `live` 2 → 1 em 12 s.
6. O bundle da Lambda carrega **core + corpus + prompts + config + adapter** num zip só. Uma versão da Lambda é, portanto, um **conjunto coerente de artefatos**: voltar a versão volta código, políticas e templates juntos (ver ADR-002 para o porquê do conjunto).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| `apply` direto trocando a função (como no módulo 03) | Não separa "publicado" de "em uso pelo cliente"; teste pós-deploy chega tarde; rollback lento. |
| Duas funções (`blue`/`green`) com troca de rota na API Gateway | Dobra recursos e explicação; a troca de integração na API GW é mais opaca para o aluno do que mover um alias. |
| Deploy canário com pesos no alias (`routing_config`) | Funciona, mas introduz estatística de tráfego numa turma que ainda está aprendendo o que é alias. Fica como extensão comentada no README. |
| CodeDeploy / serviços de deploy progressivo | Não validados no Learner Lab; mais um serviço para explicar sem ganho didático neste ponto. |

## Consequências

- O pipeline ganha os stages `deploy → postdeploy → promote`, e o aluno **vê** a diferença entre "subiu" e "entrou em produção".
- O smoke e a avaliação pós-deploy rodam contra `/candidate/...` e conferem o `release_id` da resposta antes de promover.
- O `terraform plan` nunca mostra mudança no `live` (por causa do `ignore_changes`); isso precisa ser explicado no README para não parecer bug.
- Versões antigas da Lambda se acumulam; o `cleanup.sh` lista e remove as que não estão em nenhum alias (sempre com `--dry-run` primeiro).
- Rollback pelo alias **não** reverte infraestrutura (tabela, rotas); reverte comportamento do assistente, que é o que a Aula 5 quer ensinar.

## Referências

- [AWS Lambda: versões](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html) e [aliases](https://docs.aws.amazon.com/lambda/latest/dg/configuration-aliases.html).
- Kief Morris, *Infrastructure as Code* (3ª ed.): entrega de mudanças em pequenos passos com verificação em cada etapa (transposição: aplicamos a ideia a um alias de função, não é prescrição do livro).
