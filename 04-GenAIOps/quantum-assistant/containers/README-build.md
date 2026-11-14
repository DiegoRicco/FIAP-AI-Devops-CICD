# Como a imagem do assistente é construída

A imagem `qc-assistant-app` **é o release**. Ela leva o código, o corpus, o seed do grafo, o modelo de embedding e o cache de embeddings. O digest dela no ECR (`sha256:...`) identifica o release: o deploy, a promoção e o rollback usam esse digest, nunca uma tag.

## Quem builda e onde

| Quem | Comando | Onde roda o `docker build` |
|---|---|---|
| Aluno (Codespaces, sem Docker) | `make image` | no GitLab Runner (EC2 amd64), via SSM |
| Pipeline | job de build no runner | no próprio runner (`scripts/cloud/build-push.sh`) |

Os dois caminhos chamam o mesmo `scripts/cloud/build-push.sh`. O build é sempre `linux/amd64`, a arquitetura das tasks Fargate (`X86_64`), e usa `--provenance=false` para gerar uma imagem só, sem o índice de atestados, de modo que o digest do push seja o mesmo que a task baixa.

## Camadas (do que menos muda para o que mais muda)

1. **Dependências**: `requirements-engine.txt`, `requirements-web.txt` e `requirements-ops.txt`, todas pinadas com `==`. Só são refeitas quando um desses arquivos muda.
2. **Modelo de embedding**: `python -m vector.encoder --download`. Baixa do Hugging Face o `paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0, 384 dimensões) na revisão pinada `e8f8c211`, em ONNX, e confere o sha256 de cada arquivo. Fica em `/app/models/...` (`QC_EMBEDDING_MODEL_DIR`).
3. **Código e dados**: `core/`, `corpus/`, `graph/`, `vector/`, `lab/`, `web/` e as demais pastas.
4. **Cache de embeddings**: `python -m vector.build_cache`. Usa o mesmo modelo da camada 2, e assim a task não precisa calcular embeddings ao subir.

Quando o commit muda só o corpus, as camadas 1 e 2 vêm do cache e o rebuild termina em segundos.

## Números medidos (runner t3.small, 2 vCPU, 1,9 GB de RAM + 2 GB de swap)

| Medida | Valor |
|---|---|
| Build frio de referência (deps + modelo, cache limpo) | 73 s |
| Memória disponível mínima durante o build | 699 MB (swap usado: 79 MB) |
| Tamanho da imagem de referência | 573 MB |
| Rebuild com cache | 1 s |
| 1º build real do app (`make image`, deps e modelo do cache da medição) | 74 s de build + 2 s de push |
| Rebuild real só de código ou corpus | 7 a 18 s de build + 1 a 2 s de push |
| Imagem do app no ECR (comprimida) | 219 MB |

Na AWS (Fargate, 1 vCPU, 3 GB), o pull da imagem levou 15,5 s, e o app ficou pronto (`/ready` 200) 21,6 s depois de o processo subir.

## Risco: o Hugging Face fora do ar no dia da aula

O modelo é baixado **no build**, não na task. Se o huggingface.co estiver fora do ar ou lento, o build quebra na camada 2. A task que já está no ar continua funcionando, porque o modelo está dentro da imagem.

**Mitigação, na própria aula:** o primeiro `make image` (Parte 1 da Demo 5.1) já deixa as camadas 1 e 2 no cache do BuildKit do runner; os builds seguintes do dia reaproveitam essas camadas e não acessam o Hugging Face. Se o Hugging Face estiver fora do ar nesse primeiro build, ou se o cache do runner tiver sido perdido (EC2 recriada), há dois caminhos: trocar o `FROM` por uma imagem base do ECR com as camadas 1 e 2 (pinada por digest) ou implantar o último digest já aprovado com `make deploy DIGEST=sha256:...`, sem build novo.

Um download corrompido ou adulterado não entra na imagem: o `vector/encoder.py` recusa arquivo com sha256 diferente do pinado, e o build falha nesse ponto.

## Segurança

- A imagem roda como usuário sem privilégio (UID 10001) e só escreve em `/tmp`.
- Nenhum segredo entra na imagem. As senhas do Neo4j e a chave admin chegam na task pelo SSM (`secrets` da task definition).
- A imagem base está pinada por digest (`python:3.12.16-slim@sha256:...`), e o ECR escaneia cada push (`scan_on_push`).
