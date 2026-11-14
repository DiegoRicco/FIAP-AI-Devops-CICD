#!/usr/bin/env python3
"""Gera as arquiteturas da Aula 5 na versao ECS Fargate + Neo4j + ALB.

Uso:     python3 gerar_aula5_ecs.py
Saida:   arquitetura-5-1.excalidraw         (Demo 5.1: onde mora cada evidencia)
         arquitetura-5-2.excalidraw         (Demo 5.2: do push ao cliente, e de volta)
         arquitetura-modulo-04.excalidraw   (modulo: duas demos, a mesma base)
Render:  bash ~/.claude/skills/excalidraw-aulas/scripts/render.sh \
             arquitetura-5-1.excalidraw ../01-Release-RAG-Quality-Gates/img/arquitetura-5-1.png
         (idem para os outros dois; o PNG da 5.2 vai para
         ../02-Operacao-Observabilidade-Qualidade-Continua/img/)

O gerador antigo (gerar.py, arquitetura Lambda/local-demo) continua intacto
porque os LEGADO-*.md usam as saidas dele. Daqui so se importa a classe
Diagrama e a paleta, para os dois conjuntos terem o mesmo estilo.

Cada imagem responde UMA pergunta, nao e um mural de tudo. Fonte da verdade:
os mermaids dos READMEs do modulo e o codigo real (infra-ecs/*.tf,
infra-obs/*.tf, .gitlab-ci.yml, ops/cloud/docker-compose.yml,
ops/cloud/incident.sh, web/app/serve.py). Nao existe LLM: nenhum icone de
modelo gerativo; o modelo de embeddings roda na CPU do conteiner app.

Icones novos em assets/ (SVG oficial + PNG 160px):
  - elb, fargate, ecr, ecs, ssm, ssm-parameter-store, vpc: AWS Architecture
    Icons, pacote oficial 04/2026 (https://aws.amazon.com/architecture/icons/)
  - neo4j: avatar oficial da organizacao neo4j no GitHub (github.com/neo4j)
  - fastapi: favicon oficial do repositorio fastapi/fastapi (docs/en/docs/img)
  - python: marca oficial Python (vectorlogo.zone/logos/python)
  - huggingface: huggingface.co/front/assets/huggingface_logo-noborder.svg
  - loki: logo oficial do repositorio grafana/loki (docs/sources/logo.png)

Licoes de UX aplicadas (skill excalidraw-aulas): L1 acentuacao, L3 verbo com
sujeito na origem, L5 rotulo explicito, L6 empresa ficticia, L8 apoio
tracejado, L9/L18 rotulo com fundo branco, L10 so o caminho de erro/incidente
em cor de alerta, L11 fan-out de um ponto, L12 papel em destaque, L13 magenta
so no titulo, L19 ponta de seta fora da borda de container, L20 tamanho
minimo de fonte 15 (o PNG precisa ser legivel num notebook 1366x768).
"""
import os

from gerar import (ALERTA, AWS, CINZA, DIR, FUNDO, GITLAB, MAGENTA, OTLP,
                   TEXTO, Diagrama)

AZUL = "#1971C2"      # selo "Demo 5.1"
VERDE = "#2F9E44"     # selo "Demo 5.2"


# L20: no PNG exibido a 1366 px o corpo precisa de >= 13 px. Com ~1800 px
# logicos de largura (escala ~0,75), isso exige fonte >= 17 no corpo.
PISO = {14: 16, 15: 17, 16: 17, 17: 18}


class D(Diagrama):
    def texto(self, t, x, y, w, size=18, cor=TEXTO, align="center"):
        return super().texto(t, x, y, w, size=PISO.get(size, size), cor=cor,
                             align=align)

    def no_s(self, icone, rotulo, cx, cy, s=72, w=240, size=16, cor=TEXTO):
        """No com icone de tamanho s e rotulo centralizado abaixo."""
        self.imagem(icone, cx - s / 2, cy - s / 2, s)
        if rotulo:
            self.texto(rotulo, cx - w / 2, cy + s / 2 + 8, w, size=size,
                       cor=cor)
        return {"l": cx - s / 2, "r": cx + s / 2, "t": cy - s / 2,
                "b": cy + s / 2, "cx": cx, "cy": cy}

    def ret(self, x, y, w, h, cor=TEXTO, bg="#ffffff", tracejada=False,
            largura=2):
        self._base("rectangle", x, y, w, h, strokeColor=cor,
                   backgroundColor=bg, strokeWidth=largura,
                   strokeStyle="dashed" if tracejada else "solid")

    def cartao(self, x, y, w, h, titulo, corpo, bg="#ffffff", cor=TEXTO,
               tsize=17, csize=15, tracejada=False, ccor=CINZA):
        self.ret(x, y, w, h, cor=cor, bg=bg, tracejada=tracejada)
        self.texto(titulo, x + 12, y + 9, w - 24, size=tsize, cor=cor,
                   align="left")
        if corpo:
            self.texto(corpo, x + 12, y + 13 + tsize * 1.25 * (
                titulo.count("\n") + 1), w - 24, size=csize, cor=ccor,
                align="left")

    def selo(self, x, y, t, cor):
        """Selo de qual demo usa o componente (5.1 / 5.2)."""
        w = len(t) * 9 + 18
        self._base("rectangle", x, y, w, 26, strokeColor=cor,
                   backgroundColor="#ffffff", strokeWidth=2, roughness=0,
                   roundness={"type": 3})
        self.texto(t, x, y + 4, w, size=15, cor=cor)
        return w

    def linha(self, pontos, cor=CINZA, tracejada=False, largura=2):
        self.seta(pontos, cor=cor, tracejada=tracejada, largura=largura,
                  ponta=False)


# ---------------------------------------------------------------------------
# Demo 5.1: onde mora cada evidencia?
# ---------------------------------------------------------------------------
def demo_51():
    d = D()
    d.texto("Quantum Commerce · Demo 5.1: onde mora cada evidência da "
            "resposta?", 30, 24, 1760, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Não há LLM: a resposta é montada a partir do "
            "trecho recuperado e citado. A imagem no ECR é o release.",
            30, 72, 1760, size=20, cor=CINZA, align="left")

    # Voce, fora da AWS
    d.no_s("user", "Você (navegador\ne Codespaces)", 115, 440, s=72, w=200,
           size=17)
    d.seta([(160, 440), (322, 440)], cor=TEXTO, largura=3)
    d.rotulo("HTTP :80", 205, 408, 80)

    # AWS
    d.caixa(260, 130, 1520, 1020, "", AWS)
    d.imagem("aws-cloud", 274, 138, 36)
    d.texto("AWS · us-east-1", 318, 146, 900, size=18, cor=AWS,
            align="left")
    d.ret(275, 178, 1180, 724, cor=AWS, bg="transparent", largura=1)
    d.texto("VPC fiap-lab\n(subnets públicas,\nsem NAT)", 285, 188, 220,
            size=16, cor=AWS, align="left")

    alb = d.no_s("elb", "Application Load\nBalancer · HTTP :80\n"
                 "qc-assistant-alb", 370, 440, s=80, w=220, size=16)

    # --- service live ---
    d.caixa(520, 190, 920, 560, "ECS Fargate · service qc-assistant-live: "
            "1 task, a que o cliente usa", TEXTO, bg=FUNDO,
            icones=("fargate",), size=18)
    # conteiner app
    d.ret(545, 245, 530, 485, bg="#ffffff")
    d.imagem("fastapi", 557, 253, 34)
    d.imagem("python", 595, 253, 34)
    d.texto("contêiner app · FastAPI · porta 8000", 637, 260, 430, size=17,
            align="left")
    X, W = 575, 480
    d.cartao(X, 292, W, 70, "① BM25 · palavras",
             "índice em memória do próprio app", csize=16)
    d.cartao(X, 374, W, 84, "② Vetorial · significado",
             "o modelo de embeddings roda na CPU do app (só\n"
             "gera vetores) e consulta o índice no Neo4j", csize=16)
    d.cartao(X, 470, W, 84, "③ Grafo · relações",
             "consulta Cypher que percorre o grafo\nno Neo4j",
             csize=16)
    d.cartao(X, 568, W, 70, "mesmo filtro nas três: vigência, tenant, ACL",
             "composer determinístico: trecho + citação (sem LLM)",
             bg="#f1f3f5", csize=16)
    d.cartao(X, 650, W, 66, "④ Pergunta sobre pedido: outro caminho",
             "status e entrega vêm da API de pedidos, não do grafo",
             csize=16, bg="#fff9db")
    # colchete: as tres estrategias passam pelo mesmo filtro
    d.seta([(X - 4, 327), (X - 18, 327), (X - 18, 603), (X - 6, 603)],
           cor=TEXTO, largura=2)
    d.linha([(X - 18, 416), (X - 4, 416)], cor=TEXTO)
    d.linha([(X - 18, 512), (X - 4, 512)], cor=TEXTO)

    # conteiner neo4j
    d.ret(1135, 245, 285, 405, bg="#ffffff")
    d.imagem("neo4j", 1147, 253, 34)
    d.texto("contêiner neo4j\nCommunity 5.26", 1189, 253, 220, size=17,
            align="left")
    d.texto("aceita conexão só de\ndentro da task", 1149, 306, 260,
            size=15, cor=CINZA, align="left")
    d.cartao(1150, 374, 255, 84, "índice vetorial",
             "chunk_embedding\n(cosseno, 384 dimensões)")
    d.cartao(1150, 470, 255, 84, "grafo",
             "produto → categoria →\npolítica → versão → trecho")
    d.texto("reconstruídos a partir\nda imagem a cada\nsubida da task",
            1150, 562, 255, size=15, cor=CINZA, align="left")
    d.seta([(X + W + 6, 416), (1142, 416)], cor=TEXTO, largura=2)
    d.seta([(X + W + 6, 512), (1142, 512)], cor=TEXTO, largura=2)

    # ALB -> live e -> candidate (cada rota com o proprio rotulo)
    d.seta([(alb["r"] + 8, 440), (512, 440)], cor=TEXTO, largura=3)
    d.rotulo("/ (padrão)", 465, 406, 84, size=15)
    d.seta([(370, 565), (370, 835), (512, 835)], cor=TEXTO, largura=3)
    d.rotulo("/candidate*", 445, 803, 96, size=15)

    # --- service candidate ---
    d.caixa(520, 775, 920, 120, "ECS Fargate · service "
            "qc-assistant-candidate", TEXTO, bg=FUNDO,
            icones=("fargate",), size=18)
    d.texto("a mesma estrutura (app + Neo4j próprios) com a próxima imagem."
            "\nNunca lê nem escreve o grafo do live.", 540, 820, 880,
            size=16, cor=CINZA, align="left")

    # --- API de pedidos ---
    d.caixa(1475, 560, 285, 555, "API de pedidos\n(fictícia, infra própria)",
            TEXTO, bg=FUNDO, size=17)
    gw = d.no_s("apigateway", "Amazon API Gateway", 1617, 700, s=64, w=260)
    lam = d.no_s("lambda", "AWS Lambda\nqc-assistant-orders", 1617, 860,
                 s=64, w=260)
    ddb = d.no_s("dynamodb", "Amazon DynamoDB\npedidos fictícios", 1617,
                 1020, s=64, w=260)
    d.seta([(X + W + 6, 683), (gw["l"] - 8, 683)], cor=TEXTO, largura=3)
    d.rotulo("GET /orders/{id}", 1290, 698, 140, size=15)
    d.seta([(1617, 774), (1617, lam["t"] - 8)], cor=TEXTO, largura=2)
    d.seta([(1617, 952), (1617, ddb["t"] - 8)], cor=TEXTO, largura=2)

    # --- linha de baixo: de onde a task nasce ---
    s3 = d.no_s("s3", "Amazon S3\nbase-config-<SEU RM>\nstate do Terraform"
                "\n(manifestos: Demo 5.2)", 430, 1000, s=64, w=230, size=16)
    d.seta([(115, 560), (115, 1000), (s3["l"] - 8, 1000)], tracejada=True)
    d.rotulo("o Terraform\n(Parte 0)\ngrava o state", 115, 706, 130,
             size=15)
    ecr = d.no_s("ecr", "Amazon ECR · qc-assistant-app\nimagem = release "
                 "(por digest): código,\ncorpus, seed do grafo, pesos do\n"
                 "modelo e embeddings", 760, 1000, s=64, w=310, size=16)
    ssm = d.no_s("ssm-parameter-store", "SSM Parameter Store\nsenhas do "
                 "Neo4j, chave HMAC\nda porta admin, URL de pedidos",
                 1160, 1000, s=64, w=290, size=16)
    d.seta([(760, ecr["t"] - 6), (760, 903)], tracejada=True)
    d.rotulo("cada task (live e\ncandidate), ao nascer,\npuxa a imagem", 900,
             912, 200, size=15)
    d.seta([(1160, ssm["t"] - 6), (1160, 903)], tracejada=True)
    d.rotulo("cada task, ao nascer,\nlê os segredos", 1310, 912, 200,
             size=15)

    # --- Legenda ---
    d.caixa(30, 1170, 1750, 64, "", TEXTO, bg=FUNDO)
    y = 1202
    d.seta([(50, y), (110, y)], cor=TEXTO, largura=3)
    d.texto("requisição", 122, y - 10, 140, size=16, align="left")
    d.seta([(270, y), (330, y)], tracejada=True)
    d.texto("apoio, fora do caminho da pergunta", 342, y - 10, 340,
            size=16, cor=CINZA, align="left")
    d.texto("A telemetria (traces, métricas e logs) entra na Demo 5.2.",
            700, y - 10, 1060, size=16, cor=CINZA, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-5-1.excalidraw"))


# ---------------------------------------------------------------------------
# Demo 5.2: do push ao cliente, e de volta
# ---------------------------------------------------------------------------
def demo_52():
    d = D()
    d.texto("Quantum Commerce · Demo 5.2: do push ao cliente, e de volta",
            30, 24, 1840, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Mudança de código só chega ao live pelos "
            "gates. O incidente de laboratório muda o grafo em runtime e "
            "não passa pelo pipeline.", 30, 72, 1840, size=20, cor=CINZA,
            align="left")

    # --- faixa de cima: Codespaces -> GitLab -> pipeline ---
    cs = d.no_s("github", "Você no\nCodespaces", 110, 250, s=60, w=130,
                size=16)
    gl = d.no_s("gitlab", "GitLab\nquantum-ai-assistant", 260, 250, s=60,
                w=170, size=15)
    d.seta([(cs["r"] + 6, 250), (gl["l"] - 6, 250)], cor=TEXTO, largura=3)
    d.rotulo("git push", 185, 206, 62, size=15)
    d.seta([(gl["r"] + 6, 250), (372, 250)], cor=TEXTO, largura=3)
    d.rotulo("dispara", 322, 200, 52, size=15)

    d.caixa(360, 140, 1500, 255, "Pipeline .gitlab-ci.yml · roda no GitLab "
            "Runner (EC2 com shell, Docker e LabRole: nenhuma chave AWS no "
            "GitLab)", TEXTO, icones=("gitlab",), size=17)
    st = [
        (380, 300, "1 · gates", "security · contrato da API\nretrieval · "
         "business-policy\ngraph-policy: Neo4j efêmero"),
        (714, 250, "2 · package", "docker build + push no\nECR: o digest "
         "vira o\nrelease"),
        (998, 250, "3 · deploy candidate", "o service candidate\nrecebe o "
         "digest novo"),
        (1282, 250, "4 · smoke", "testa /candidate\npelo ALB real"),
        (1566, 250, "5 · promote live", "o live recebe o\nmesmo digest "
         "testado"),
    ]
    for x, w, t, c in st:
        d.cartao(x, 190, w, 120, t, c, csize=16)
    for i in range(len(st) - 1):
        x0 = st[i][0] + st[i][1]
        d.seta([(x0 + 6, 250), (st[i + 1][0] - 8, 250)], cor=TEXTO,
               largura=3)
    d.seta([(400, 316), (400, 336)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("gate reprovou: o pipeline para;\no live segue no release "
            "anterior", 418, 330, 290, size=15, cor=ALERTA, align="left")
    d.texto("rollback (manual): o live volta ao digest\nanterior, lido do "
            "manifesto no S3", 1282, 330, 340, size=15, cor=CINZA,
            align="left")

    # --- AWS ---
    d.caixa(260, 440, 1515, 720, "", AWS)
    d.imagem("aws-cloud", 274, 448, 36)
    d.texto("AWS · us-east-1 · VPC fiap-lab", 318, 456, 400, size=18,
            cor=AWS, align="left")

    ecr = d.no_s("ecr", "Amazon ECR\nqc-assistant-app\n(imagem = release)",
                 839, 560, s=64, w=190, size=15)
    d.seta([(839, 316), (839, ecr["t"] - 8)], cor=TEXTO, largura=2)
    d.rotulo("push da imagem", 920, 404, 110, size=15)
    s3 = d.no_s("s3", "Amazon S3 · releases-ecs/\n(manifestos)",
                1660, 515, s=64, w=240, size=15)
    d.seta([(1660, 316), (1660, s3["t"] - 8)], cor=TEXTO, largura=2)
    d.rotulo("grava o manifesto", 1750, 404, 130, size=15)

    # cluster ECS: candidate em cima, live embaixo; o promote leva o MESMO
    # digest do candidate para o live (etapa 5)
    d.caixa(940, 500, 500, 320, "ECS Fargate · cluster", TEXTO, bg=FUNDO,
            icones=("fargate",), size=17)
    d.seta([(1230, 316), (1230, 544)], cor=TEXTO, largura=3)
    d.rotulo("etapa 3: deploy no candidate", 1375, 404, 250, size=15)
    d.cartao(960, 552, 460, 84, "service qc-assistant-candidate",
             "app + Neo4j próprios: recebe a\nmudança antes do cliente",
             csize=16)
    d.seta([(1300, 642), (1300, 672)], cor=TEXTO, largura=3)
    d.rotulo("etapa 5: promote (mesmo digest)", 1150, 646, 250, size=15)
    d.cartao(960, 680, 460, 130, "service qc-assistant-live",
             "app :8000 (só pelo ALB)\nadmin :8001 (só do runner, HMAC)\n"
             "Neo4j: grafo reconstruído da\nimagem a cada subida da task",
             csize=16)

    alb = d.no_s("elb", "ALB qc-assistant-alb\nHTTP :80 público", 1660,
                 760, s=72, w=200, size=16)
    d.seta([(alb["l"] - 8, 748), (1448, 600)], cor=TEXTO, largura=3)
    d.seta([(alb["l"] - 8, 772), (1448, 760)], cor=TEXTO, largura=3)
    d.rotulo("/candidate/*", 1530, 650, 96, size=15)
    d.rotulo("/ (padrão)", 1500, 730, 80, size=15)

    cli = d.no_s("users", "Clientes", 1900, 760, s=64, w=100, size=16)
    d.seta([(cli["l"] - 6, 760), (alb["r"] + 8, 760)], cor=TEXTO, largura=3)
    d.rotulo("HTTP :80", 1830, 716, 62, size=15)

    # --- observabilidade ---
    d.ret(800, 880, 945, 250, bg=FUNDO)
    d.imagem("ec2", 985, 886, 36)
    d.imagem("docker", 1025, 886, 36)
    d.texto("EC2 qc-assistant-obs · Docker Compose (sem SSH)", 1069, 893,
            520, size=17, align="left")
    col = d.no_s("otel", "OTel Collector\nrecebe e distribui", 960, 1010,
                 s=60, w=160, size=15)
    d.seta([(960, 826), (960, col["t"] - 8)], cor=OTLP, tracejada=True,
           largura=2)
    d.rotulo("OTLP: traces, métricas e logs (live e candidate)", 1170, 842,
             370, size=15, cor=OTLP)
    d.ret(1095, 955, 340, 150, bg="#ffffff")
    for cx, ic, t in ((1150, "jaeger", "Jaeger\ntraces"),
                      (1265, "prometheus", "Prometheus\nmétricas"),
                      (1380, "loki", "Loki\nlogs")):
        d.no_s(ic, t, cx, 1000, s=50, w=105, size=15)
    d.seta([(col["r"] + 8, 1010), (1087, 1010)], cor=OTLP, tracejada=True)
    gf = d.no_s("grafana", "Grafana\ncom login", 1590, 1010, s=60, w=90,
                size=15)
    d.seta([(1443, 1010), (gf["l"] - 8, 1010)], cor=TEXTO)
    d.seta([(1610, 864), (1610, gf["t"] - 8)], cor=TEXTO, largura=3)
    d.texto("/grafana/*", 1500, 930, 96, size=15, cor=CINZA,
            align="right")
    pr = d.no_s("python", "qc-probe:\nprobes\nrotulados", 1705,
                1010, s=52, w=110, size=15)
    d.seta([(1700, pr["t"] - 8), (1700, 864)], cor=TEXTO, largura=2)

    # --- incidente: fora do pipeline (L10: unico caminho em cor de alerta)
    ssm = d.no_s("ssm", "AWS Systems\nManager\n(send-command)", 420, 1010,
                 s=60, w=160, size=15)
    run = d.no_s("ec2", "GitLab Runner\n(EC2)", 660, 1010, s=60, w=150,
                 size=15)
    d.seta([(110, 345), (110, 1010), (ssm["l"] - 8, 1010)], cor=ALERTA,
           tracejada=True, largura=3)
    d.texto("incident.sh\napply / restore\n(sem commit,\nsem pipeline)",
            122, 640, 132, size=15, cor=ALERTA, align="left")
    d.seta([(ssm["r"] + 8, 1010), (run["l"] - 8, 1010)], cor=ALERTA,
           tracejada=True, largura=3)
    d.seta([(660, run["t"] - 8), (660, 745), (950, 745)], cor=ALERTA,
           tracejada=True, largura=3)
    d.rotulo("porta 8001 + HMAC\n(nunca pelo ALB)", 800, 758, 150, size=15,
             cor=ALERTA)

    # --- de volta: do incidente ao proximo push (direita -> esquerda) ---
    d.seta([(1705, 1112), (1705, 1187)], cor=CINZA, largura=2)
    passos = ["probe e trace\nmostram o erro", "restore\ne replay",
              "postmortem", "caso novo no\ngolden set",
              "próximo git push:\no gate novo protege"]
    bw, gap, y0 = 300, 60, 1195
    xs = [1560 - i * (bw + gap) for i in range(len(passos))]
    for x, t in zip(xs, passos):
        d.ret(x, y0, bw, 72, bg=FUNDO)
        d.texto(t, x, y0 + 12 + (0 if "\n" in t else 11), bw, size=17)
    for i in range(len(passos) - 1):
        d.seta([(xs[i] - 8, y0 + 36), (xs[i + 1] + bw + 8, y0 + 36)],
               cor=CINZA, largura=2)
    d.seta([(xs[-1] - 8, y0 + 36), (40, y0 + 36), (40, 250),
            (cs["l"] - 8, 250)], cor=CINZA, largura=2)
    d.rotulo("e de\nvolta", 40, 560, 44, size=15)

    # --- Legenda ---
    y = 1312
    d.seta([(40, y), (100, y)], cor=TEXTO, largura=3)
    d.texto("mudança e requisição", 112, y - 10, 210, size=16, align="left")
    d.seta([(330, y), (390, y)], cor=OTLP, tracejada=True)
    d.texto("telemetria OTLP", 402, y - 10, 170, size=16, cor=OTLP,
            align="left")
    d.seta([(590, y), (650, y)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("incidente de laboratório e gate reprovado", 662, y - 10, 400,
            size=16, cor=ALERTA, align="left")
    d.seta([(1070, y), (1130, y)], cor=CINZA, largura=2)
    d.texto("ciclo de aprendizado depois do incidente", 1142, y - 10, 380,
            size=16, cor=CINZA, align="left")
    d.texto("Sem LLM · probes = tráfego sintético", 1540, y - 10, 360,
            size=16, cor=CINZA, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-5-2.excalidraw"))


# ---------------------------------------------------------------------------
# Modulo 04: duas demos, a mesma base
# ---------------------------------------------------------------------------
def modulo():
    d = D()
    d.texto("Quantum Commerce · Módulo 04 (Aula 5): duas demos sobre a mesma "
            "base", 30, 24, 1700, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. A Parte 0 da Demo 5.1 sobe a base uma vez; a "
            "5.1 investiga a evidência e a 5.2 automatiza e opera. Sem LLM.",
            30, 72, 1700, size=20, cor=CINZA, align="left")

    def demo(x, w, selo, cor, titulo, pergunta, itens):
        d.ret(x, 130, w, 400, cor=cor, bg="#ffffff", largura=3)
        sw = d.selo(x + 16, 146, selo, cor)
        d.texto(titulo, x + 28 + sw, 146, w - sw - 44, size=19, cor=TEXTO,
                align="left")
        d.texto(pergunta, x + 16, 190, w - 32, size=18, cor=TEXTO,
                align="left")
        y = 268
        for ic, t, c in itens:
            d.imagem(ic, x + 18, y, 44)
            d.texto(t, x + 76, y + 2, w - 92, size=16, cor=c, align="left")
            y += 64

    demo(30, 800, "Demo 5.1", AZUL, "Release, RAG e quality gates",
         "Pergunta: qual evidência autoriza um release?\n"
         "(de onde veio a resposta, e ela se aplica?)",
         [("user", "Central de Evidências no navegador, pelo ALB", TEXTO),
          ("neo4j", "a mesma pergunta em três estratégias: BM25,\n"
           "vetorial e grafo, com citação e recusa", TEXTO),
          ("metrics", "trocar data e tenant: o mesmo filtro de\n"
           "vigência e ACL vale para as três", TEXTO),
          ("gear", "mexer numa aresta do grafo e ver o\n"
           "graph_version mudar (e restaurar)", TEXTO)])
    demo(910, 800, "Demo 5.2", VERDE, "Operação e qualidade contínua",
         "Pergunta: como barrar a evidência errada e\n"
         "perceber quando ela muda depois do deploy?",
         [("gitlab", "pipeline: gates → package → candidate →\n"
           "smoke → promote (e rollback por digest)", TEXTO),
          ("grafana", "EC2 de observabilidade: OTel Collector,\n"
           "Jaeger, Prometheus, Loki e Grafana", TEXTO),
          ("python", "qc-probe: perguntas com gabarito contra o\n"
           "ALB, marcadas como tráfego sintético", TEXTO),
          ("ssm", "incidente: incident.sh → SSM Run Command →\n"
           "runner → porta admin 8001 do app (fora do pipeline)",
           ALERTA)])
    d.seta([(838, 330), (902, 330)], cor=TEXTO, largura=3)
    d.rotulo("vira\npipeline", 870, 270, 64, size=15)

    # base comum (GitLab e SaaS: fica fora da caixa AWS)
    d.caixa(330, 610, 1380, 480, "", AWS, bg=FUNDO)
    d.imagem("aws-cloud", 344, 618, 36)
    d.texto("A mesma base nas duas demos · AWS us-east-1 · VPC fiap-lab "
            "(criada no Módulo 01)", 388, 626, 1300, size=18, cor=AWS,
            align="left")
    d.seta([(430, 538), (430, 602)], cor=TEXTO, largura=3)
    d.rotulo("sobe a base (Parte 0) e investiga", 600, 556, 250, size=15)
    d.seta([(1310, 538), (1310, 602)], cor=TEXTO, largura=3)
    d.rotulo("automatiza e opera a mesma base", 1475, 556, 240, size=15)

    r1, r2 = 760, 960
    g = d.no_s("gitlab", "GitLab (SaaS)\nquantum-ai-assistant", 160, r1,
               s=64, w=220, size=16)

    rn = d.no_s("ec2", "GitLab Runner (EC2)\nDocker e LabRole", 560, r1,
                s=64, w=220, size=16)
    ecr = d.no_s("ecr", "Amazon ECR\nimagem = release\n(por digest)", 930,
                 r1, s=64, w=200, size=16)
    d.ret(1190, 690, 470, 160, bg="#ffffff")
    d.imagem("fargate", 1204, 700, 40)
    d.texto("ECS Fargate · 2 services: live e candidate", 1252, 708,
            400, size=17, align="left")
    d.imagem("fastapi", 1214, 756, 40)
    d.imagem("neo4j", 1214, 802, 40)
    d.texto("app FastAPI (BM25 e modelo de\nembeddings na CPU)", 1266, 752,
            390, size=16, cor=CINZA, align="left")
    d.texto("Neo4j na mesma task (grafo e\níndice vetorial, só localhost)",
            1266, 800, 390, size=16, cor=CINZA, align="left")
    d.seta([(g["r"] + 8, r1), (rn["l"] - 8, r1)], cor=TEXTO, largura=2)
    d.rotulo("roda os jobs", 400, r1 - 34, 100, size=15)
    d.seta([(rn["r"] + 8, r1), (ecr["l"] - 8, r1)], cor=TEXTO, largura=2)
    d.rotulo("build e push", 745, r1 - 34, 100, size=15)
    d.seta([(ecr["r"] + 8, r1), (1182, r1)], cor=TEXTO, largura=2)
    d.rotulo("pull", 1080, r1 - 34, 40, size=15)

    d.no_s("ssm-parameter-store", "SSM Parameter Store\n(segredos: as "
           "tasks\nleem ao subir)", 1300, r2 + 20, s=56, w=230, size=16)
    d.no_s("s3", "Amazon S3\nstate e manifestos", 560, r2, s=64, w=220,
           size=16)
    d.seta([(560, 852), (560, r2 - 40)], tracejada=True)
    d.rotulo("runner grava", 630, 872, 96, size=15)
    d.imagem("elb", 930 - 32, r2 - 32, 64)
    d.texto("ALB · HTTP :80\n(a 5.2 adiciona\n/grafana)", 700, r2 - 30,
            180, size=16, align="right")
    alb = {"r": 962, "l": 898}
    d.no_s("user", "Você e os clientes (navegador)", 930, 1150, s=56, w=320,
           size=16)
    d.seta([(930, 1114), (930, r2 + 40)], cor=TEXTO, largura=2)
    d.rotulo("HTTP :80", 1000, 1040, 70, size=15)
    d.seta([(alb["r"] + 8, r2 - 10), (1220, 858)], cor=TEXTO, largura=2)
    d.rotulo("/ → live\n/candidate/* → candidate", 1110, 920, 200,
             size=15)
    d.seta([(1300, r2 - 18), (1300, 858)], tracejada=True)
    d.ret(1430, 900, 260, 160, bg="#ffffff")
    d.imagem("apigateway", 1444, 912, 40)
    d.imagem("lambda", 1492, 912, 40)
    d.imagem("dynamodb", 1540, 912, 40)
    d.texto("API de pedidos: API\nGateway, Lambda e\nDynamoDB", 1444, 962,
            240, size=16, align="left")
    d.seta([(1560, 858), (1560, 892)], cor=TEXTO, largura=2)
    d.rotulo("GET /orders", 1640, 864, 90, size=15)

    d.salvar(os.path.join(DIR, "arquitetura-modulo-04.excalidraw"))


if __name__ == "__main__":
    demo_51()
    demo_52()
    modulo()
