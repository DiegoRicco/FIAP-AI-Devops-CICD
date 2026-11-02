# ADR-002 — Sem LLM: composer determinístico e extrativo, com `model_version = "none"`

**Status:** aceito (Aula 5, módulo 04).
**Decide:** como o assistente monta a resposta e por que não há modelo de linguagem no caminho padrão da turma.

## Contexto

A Aula 5 é sobre **GenAIOps**: versionar, avaliar, publicar e operar um assistente de IA. O instinto é colocar um LLM no meio. Três fatos impedem isso como caminho obrigatório:

- O **Bedrock está bloqueado** no AWS Academy Learner Lab (fato verificado nesta conta). Nenhuma chave de LLM externa pode ser pré-requisito, nem cartão de crédito.
- Um LLM torna a resposta **não determinística**: a mesma pergunta pode sair diferente, e um gate que compara com gabarito vira loteria. A turma precisa ver um gate reprovar **pelo mesmo motivo** em todas as máquinas.
- O runner do módulo 02 roda **Python 3.10 sem Docker**; a Lambda, Python 3.12. O core precisa rodar nos dois sem instalar nada.

## Decisão

1. O **composer é determinístico e extrativo**: escolhe a melhor seção recuperada (BM25, parâmetros em `config/settings.json`) e devolve frases **dela**, sempre com citação (`doc_id`, `chunk_id`, `section`, `version`, `effective_date`).
2. **Sem evidência, recusa.** A resposta traz `abstained = true` e um `abstain_reason` rastreável (`insufficient_evidence`, `out_of_scope`, `forbidden`, `order_not_found`, `order_api_unavailable`, `injection_blocked`).
3. **Texto de documento é dado, nunca instrução.** Um trecho recuperado que contenha padrão de comando ao assistente ("ignore as regras", "assistente, responda...") não vira resposta; se não sobrar evidência legítima, a recusa é `injection_blocked`.
4. **Pedido é transacional.** Pergunta sobre pedido vai para a API de pedidos (`OrdersClient`), com autorização **no servidor**; a origem transacional vence o documento, mesmo que exista um documento falando daquele pedido.
5. A resposta declara honestamente o que é: `mode = "deterministic"` e `model_version = "none"`. Nada de token, custo ou latência de modelo inventados (`not_applicable`).
6. O **prompt existe como template versionado** (`prompts/templates.json`, `prompt_version`), mesmo sem LLM: ele define a forma da resposta e da recusa. Assim o aluno aprende a versionar prompt antes de ter um modelo que o consuma.
7. O **core é stdlib pura** (sem PyYAML, sem numpy): configs em JSON, compatível com Python 3.10 e 3.12.
8. Um modo com LLM real é **extensão opt-in**, fora do caminho da turma, e só depois de preflight de serviço, custo e autorização. Nenhum README o apresenta como existente.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| LLM via Bedrock | Bloqueado no Learner Lab; e um gate com saída não determinística não ensina gate. |
| LLM externo com chave do professor | Pré-requisito que não escala para a turma, custo por aluno, segredo circulando. |
| LLM local pequeno no Codespaces | Pesado para o Codespaces padrão, lento no runner (sem GPU, Python 3.10), e ainda não determinístico sem muito ajuste. |
| Simular um LLM com texto gerado por template "parecido" | Seria fingir inferência. Vai contra a regra de honestidade do módulo. |

## Consequências

- **Tudo o que medimos é observável diretamente**: Hit@k e MRR (recuperação), fonte vigente, presença de citação, abstenção correta, contrato da API, ACL. Nenhuma métrica recebe nome de capacidade que não temos (`faithfulness`, `hallucination rate`, `LLM-as-a-judge`). Isso fica escrito nos READMEs e no `thresholds.json`.
- O golden set compara com gabarito exato e os gates reprovam **sempre pelo mesmo motivo**, em qualquer máquina. Determinismo é o que permite "quebrar de propósito" em aula.
- O mesmo core roda na Lambda (5.1) e no servidor HTTP local (5.2): o que o aluno observa em operação é o que ele publicou.
- O limite é explícito e pedagógico: a resposta não "conversa", não parafraseia, não combina fontes com fluência. Quando um LLM entrar, ele troca o **composer**; o pipeline, os gates e a telemetria continuam valendo. Essa é a mensagem da aula, não uma desculpa.
- Versões rastreadas por resposta: `application_version`, `prompt_version`, `corpus_version`, `index_version`, `eval_set_version`, `model_version`. Mudar política sem mudar código **muda** `corpus_version` e exige novo release: o pipeline de conteúdo e o de código são o mesmo.

## Referências

- Es et al., *RAGAs* (EACL 2024): avaliação de RAG é multidimensional (recuperação, uso da evidência, resposta). **Nosso avaliador não implementa o RAGAs**; usa a ideia de separar as dimensões.
- OWASP GenAI, [Prompt Injection](https://genai.owasp.org/glossary/prompt-injection/): fronteira entre instrução e conteúdo não confiável.
- Chen et al., *Reliable Machine Learning* (2022): golden sets, avaliação offline e rollback de conjunto (transposição didática: o livro trata de ML clássico).
