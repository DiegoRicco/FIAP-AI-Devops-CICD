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

import sys

from gerar import (ALERTA, AWS, CINZA, DIR, FUNDO, GITLAB, MAGENTA, OTLP,
                   TEXTO, Diagrama, log)

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
# ---------------------------------------------------------------------------
ESCURO = "#343a40"    # L32: texto secundario escuro, nunca cinza medio
CINZA2 = ESCURO       # nome usado pela Demo 5.2 para o mesmo tom


def folgas(d, minimo=15, ponta=40):
    """L36: rotulo a >= 15 px de qualquer borda e ponta de seta longe de borda
    intermediaria. Devolve a lista de violacoes (vazia = ok)."""
    caixas = [e for e in d.elements if e["type"] == "rectangle"
              and e["strokeColor"] != "transparent"]
    rotulos = [e for e in d.elements if e["type"] == "rectangle"
               and e["strokeColor"] == "transparent"]
    ruins = []
    for r in rotulos:
        rl, rr, rt, rb = r["x"], r["x"] + r["width"], r["y"], r["y"] + r["height"]
        for c in caixas:
            cl, cr, ct, cb = c["x"], c["x"] + c["width"], c["y"], c["y"] + c["height"]
            # distancia do retangulo do rotulo a cada uma das 4 bordas
            for nome, dist, sobrepoe in (
                    ("esq", min(abs(rl - cl), abs(rr - cl)) if not rl < cl < rr else 0, rt < cb and rb > ct),
                    ("dir", min(abs(rl - cr), abs(rr - cr)) if not rl < cr < rr else 0, rt < cb and rb > ct),
                    ("topo", min(abs(rt - ct), abs(rb - ct)) if not rt < ct < rb else 0, rl < cr and rr > cl),
                    ("base", min(abs(rt - cb), abs(rb - cb)) if not rt < cb < rb else 0, rl < cr and rr > cl)):
                if sobrepoe and dist < minimo:
                    ruins.append(f"rotulo@({rl:.0f},{rt:.0f}) a {dist:.0f}px da borda {nome} de caixa@({cl:.0f},{ct:.0f})")
    for e in d.elements:
        if e["type"] != "arrow":
            continue
        px = e["x"] + e["points"][-1][0]
        py = e["y"] + e["points"][-1][1]
        perto = []
        for c in caixas:
            cl, cr, ct, cb = c["x"], c["x"] + c["width"], c["y"], c["y"] + c["height"]
            for dist, dentro in ((abs(px - cl), ct <= py <= cb), (abs(px - cr), ct <= py <= cb),
                                 (abs(py - ct), cl <= px <= cr), (abs(py - cb), cl <= px <= cr)):
                if dentro and dist < ponta:
                    perto.append(dist)
        perto.sort()
        # a borda mais proxima (<= 12) e o destino; qualquer outra < 40 e ruim
        extras = perto[1:] if perto and perto[0] <= 12 else perto
        for dist in extras:
            ruins.append(f"ponta de seta em ({px:.0f},{py:.0f}) a {dist:.0f}px de outra borda")
    return ruins


def demo_51():
    d = D()
    ESC = "#343a40"   # L32: texto secundario escuro
    d.texto("Quantum Commerce · Demo 5.1: onde mora cada evidência da "
            "resposta?", 30, 24, 1780, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Não há LLM: a resposta é montada a partir do "
            "trecho recuperado e citado.", 30, 72, 1780, size=20, cor=ESC,
            align="left")

    # --- Voce (no papel do cliente, fora da AWS) <-> ALB ---
    YA = 600
    d.no_s("user", "Você\n(no papel do\ncliente)", 75, YA, s=72, w=150,
           size=18)
    d.seta([(117, YA), (288, YA)], cor=TEXTO, largura=3)
    d.texto("HTTP :80", 126, YA - 40, 90, size=18,
            align="left")

    # --- AWS e VPC (L28: folga >= 27 px; L44: vaos >= 50 px nas travessias)
    d.caixa(220, 130, 1580, 1470, "", AWS)
    d.imagem("aws-cloud", 234, 138, 36)
    d.texto("AWS · us-east-1", 278, 146, 600, size=18, cor=AWS, align="left")
    d.ret(252, 186, 1258, 1152, cor=AWS, bg="transparent", largura=1)
    d.texto("VPC fiap-lab\nsubnets públicas", 264, 196, 190, size=18,
            cor=AWS, align="left")

    alb = d.no_s("elb", "Application\nLoad Balancer", 336, YA, s=80, w=170,
                 size=18)

    # --- service candidate ---
    d.caixa(470, 228, 990, 92, "ECS Fargate · service "
            "qc-assistant-candidate", TEXTO, bg=FUNDO, icones=("fargate",),
            size=18)
    d.texto("a próxima imagem, em teste, com app + Neo4j próprios; nunca "
            "lê nem escreve o grafo do live", 484, 274, 920, size=18,
            cor=ESC, align="left")

    # --- service live ---
    d.caixa(470, 350, 990, 960, "ECS Fargate · service qc-assistant-live: "
            "a task que atende você", TEXTO, bg=FUNDO,
            icones=("fargate",), size=18)
    BX = (814, 1010, 1206)   # colunas das buscas = colunas dos armazenamentos
    BW = 184
    # conteiner neo4j
    YN = 410
    d.ret(492, YN, 946, 180, bg="#ffffff")
    d.imagem("neo4j", 504, YN + 8, 34)
    d.texto("contêiner neo4j · Community 5.26", 546, YN + 15, 500, size=18,
            align="left")
    d.texto("só aceita conexão de\ndentro da task; dados\nrefeitos da "
            "imagem a\ncada subida", 508, YN + 56, 230, size=18, cor=ESC,
            align="left")
    d.cartao(BX[1], YN + 44, BW, 88, "índice vetorial",
             "vetores dos\ntrechos", csize=18, ccor=ESC)
    d.cartao(BX[2], YN + 44, BW, 88, "grafo",
             "liga produto,\npolítica e trecho", csize=18, ccor=ESC)

    # conteiner app (vao de 50 px ate o neo4j: as setas "consulta" cruzam)
    YAPP = 640
    d.ret(492, YAPP, 946, 646, bg="#ffffff")
    d.imagem("fastapi", 504, YAPP + 7, 34)
    d.imagem("python", 542, YAPP + 7, 34)
    d.texto("contêiner app · FastAPI · porta 8000", 586, YAPP + 14, 400,
            size=18, align="left")
    # grupo das tres buscas em paralelo
    GX, GY, GR = 770, 836, 1414
    d.ret(GX, GY, GR - GX, 150, cor=CINZA, bg="#ffffff", largura=1)
    d.texto("busca em paralelo", GX + 14, GY + 9,
            160, size=18, cor=ESC, align="left")
    YB = GY + 44          # topo dos cartoes de busca
    YF = 1036             # filtro (vao de 50 px ate o grupo)
    YCP = 1166            # composer e pedido
    YE = YB + 46          # altura da seta "/" e do roteador
    RX, RW = 540, 150     # coluna do roteador e do pedido
    RC = RX + RW / 2

    # setas antes dos cartoes: o inicio fica escondido sob a origem e o
    # ultimo segmento tem >= 50 px, entao toda ponta sai do mesmo tamanho
    JX, YC = 416, 274
    d.seta([(alb["r"] + 6, YA), (JX, YA), (JX, YC), (464, YC)], cor=TEXTO,
           largura=3)
    d.seta([(JX, YA), (JX, YE), (RX - 6, YE)], cor=TEXTO, largura=3)
    d.texto("/candidate", 312, 440, 90, size=18, align="left")
    d.texto("/ (padrão)", 312, 760, 90, size=18, align="left")
    for i, bx in enumerate(BX):
        ax = bx + 154
        topo = (YAPP + 130 if i == 0 else YN + 132) + 6
        d.seta([(ax, YB + 40), (ax, topo)], cor=TEXTO, largura=3)
        d.seta([(bx + BW / 2, YB + 40), (bx + BW / 2, YF - 6)], cor=TEXTO,
               largura=3)
    d.texto("consulta", BX[0] + 66, YAPP + 150, 76, size=18, cor=ESC,
            align="left")
    for bx in BX[1:]:
        d.texto("consulta", bx + 66, YAPP + 150, 76, size=18, cor=ESC,
                align="left")
    d.seta([(RX + 100, YE), (GX - 6, YE)], cor=TEXTO, largura=3)
    d.texto("não", 712, YE - 32, 40, size=18, align="left")
    d.seta([(RC, YE), (RC, YCP - 6)], cor=TEXTO, largura=3)
    d.texto("sim", RC + 17, YE + 110, 40, size=18, align="left")
    d.seta([(1010, YF + 30), (1010, YCP - 6)], cor=TEXTO, largura=3)
    d.seta([(RX + 100, YCP + 38), (808, YCP + 38)], cor=TEXTO, largura=3)

    d.cartao(BX[0], YAPP + 54, BW, 76, "índice BM25", "na memória do app",
             csize=18, ccor=ESC)
    d.cartao(RX, YE - 48, RW, 96, "roteador", "a pergunta é\nsobre pedido?",
             csize=18, ccor=ESC)
    d.cartao(BX[0], YB, BW, 92, "BM25", "por palavras", csize=18, ccor=ESC)
    d.cartao(BX[1], YB, BW, 92, "Vetorial", "por significado\n(vetor na CPU)",
             csize=18, ccor=ESC)
    d.cartao(BX[2], YB, BW, 92, "Grafo", "por relações\n(Cypher)", csize=18,
             ccor=ESC)
    d.cartao(BX[0], YF, GR - 14 - BX[0], 76, "filtro único, igual para as "
             "três", "vigência (data), loja (tenant) e permissão (ACL)",
             csize=18, ccor=ESC)
    d.cartao(RX, YCP, RW, 96, "pedido", "consulta a API\nde pedidos",
             csize=18, ccor=ESC)
    d.seta([(BX[0] + 300, YCP + 38), (1250, YCP + 38)], cor=TEXTO,
           largura=3)
    d.cartao(BX[0], YCP, 400, 76, "composer (sem LLM)", "trecho + citação, "
             "ou status do pedido", csize=18, ccor=ESC)
    # a resposta volta pelo mesmo caminho (ALB) ate voce
    d.texto("resposta volta a\nvocê pelo ALB", 1258, YCP + 16, 160,
            size=18, align="left")

    # pedido -> API de pedidos (fora da VPC, embaixo)
    YP = 1490
    d.seta([(RC, YCP + 102), (RC, YP - 36)], cor=TEXTO, largura=3)
    d.texto("GET /orders/{id}", RC + 19, 1362, 170, size=18, cor=ESC,
            align="left")

    # --- fora da VPC: ECR e SSM alimentam as DUAS tasks (L22, L45, L46) ---
    BUS = 1560
    d.seta([(BUS, 274), (1468, 274)], tracejada=True)
    d.seta([(BUS, 380), (1468, 380)], tracejada=True)
    d.linha([(BUS, 274), (BUS, 940)], cor=CINZA, tracejada=True)
    d.texto("ECR e SSM\nfornecem a cada\ntask (live e\ncandidate),\nquando "
            "ela sobe", 1578, 296, 220, size=18, cor=ESC, align="left")
    d.no_s("ecr", "Amazon ECR\nimagem da release:\ncódigo, políticas,\n"
           "grafo e o modelo\nque gera os vetores", 1690, 560, s=64, w=230,
           size=18)
    d.no_s("ssm-parameter-store", "SSM Parameter Store\nsenhas do Neo4j "
           "e\nendereço da API\nde pedidos", 1690, 940, s=64, w=230,
           size=18)
    d.linha([(1652, 560), (BUS, 560)], cor=CINZA, tracejada=True)
    d.linha([(1652, 940), (BUS, 940)], cor=CINZA, tracejada=True)

    # --- API de pedidos (fora da VPC) ---
    d.caixa(470, YP - 82, 1300, 172, "", TEXTO, bg=FUNDO)
    d.texto("API de pedidos (fictícia), fora da VPC", 650, YP - 70, 560,
            size=18, align="left")
    gw = d.no_s("apigateway", "Amazon API Gateway", RC, YP, s=56, w=200,
                size=18)
    lam = d.no_s("lambda", "AWS Lambda", 1100, YP, s=56, w=160, size=18)
    ddb = d.no_s("dynamodb", "Amazon DynamoDB\npedidos fictícios", 1580,
                 YP, s=56, w=200, size=18)
    d.seta([(gw["r"] + 10, YP), (lam["l"] - 10, YP)], cor=TEXTO, largura=3)
    d.seta([(lam["r"] + 10, YP), (ddb["l"] - 10, YP)], cor=TEXTO,
           largura=3)
    d.seta([(RC, YCP + 102), (RC, YP - 36)], cor=TEXTO, largura=3)

    # --- Terraform (no Codespaces) grava o state no S3 (L31) ---
    d.no_s("terraform", "Terraform\n(no seu\nCodespaces)", 75, 1440, s=56,
           w=150, size=18)
    s3 = d.no_s("s3", "Amazon S3\nstate do\nTerraform", 340, 1440, s=56,
                w=150, size=18)
    d.seta([(110, 1440), (s3["l"] - 10, 1440)], tracejada=True)
    d.texto("grava o\nstate", 118, 1376, 80, size=18, cor=ESC,
            align="left")

    # --- Legenda ---
    d.caixa(30, 1630, 1770, 60, "", TEXTO, bg=FUNDO)
    y = 1660
    d.seta([(50, y), (110, y)], cor=TEXTO, largura=3)
    d.texto("caminho da pergunta", 122, y - 11, 260, size=18, align="left")
    d.seta([(380, y), (440, y)], tracejada=True)
    d.texto("apoio, fora do caminho da pergunta", 452, y - 11, 360,
            size=18, cor=ESC, align="left")
    d.texto("A telemetria (traces, métricas e logs) entra na Demo 5.2.",
            860, y - 11, 900, size=18, cor=ESC, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-5-1.excalidraw"))


# ---------------------------------------------------------------------------
# Demo 5.2: do push ao cliente, e de volta
# ---------------------------------------------------------------------------


def demo_52():
    d = D()
    d.texto("Quantum Commerce · Demo 5.2: do push ao cliente, e de volta",
            30, 20, 1840, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Mudança de código só chega ao live pelos "
            "gates. O incidente de laboratório muda o grafo em runtime, fora "
            "do pipeline. Probes = perguntas de teste rotuladas.",
            30, 62, 1860, size=20, cor=CINZA2, align="left")

    # --- faixa de cima: Codespaces -> GitLab -> pipeline ---
    cs = d.no_s("github", "Você no\nCodespaces", 60, 250, s=56, w=130,
                size=19)
    gl = d.no_s("gitlab", "GitLab\nquantum-ai-\nassistant", 225, 250, s=56,
                w=160, size=19)
    d.seta([(cs["r"] + 6, 250), (gl["l"] - 6, 250)], cor=TEXTO, largura=3)
    d.texto("git push", 92, 208, 100, size=19)
    d.seta([(gl["r"] + 6, 250), (342, 250)], cor=TEXTO, largura=3)
    d.texto("dispara", 262, 208, 76, size=19)

    d.caixa(350, 130, 1570, 295, "Pipeline .gitlab-ci.yml · os jobs rodam no "
            "GitLab Runner (EC2 na VPC, desenhada abaixo)", TEXTO,
            icones=("gitlab",), size=23)
    d.texto("executor shell + Docker · credencial = LabRole · nenhuma chave "
            "AWS guardada no GitLab", 408, 174, 1300, size=19, cor=CINZA2,
            align="left")
    st = [
        (368, 310, "1 · gates", "security · contrato API\nretrieval · "
         "business-policy\ngraph-policy (Neo4j efêmero)"),
        (731, 240, "2 · package", "docker build + push\nno ECR: o digest\n"
         "vira o release"),
        (1024, 280, "3 · deploy candidate", "o service candidate\nrecebe o "
         "digest novo"),
        (1357, 220, "4 · smoke", "testa /candidate\npelo ALB real"),
        (1630, 255, "5 · promote live", "o live recebe o\nmesmo digest "
         "testado;\nmanifesto vai ao S3"),
    ]
    for x, w, t, c in st:
        d.cartao(x, 210, w, 135, t, c, tsize=23, csize=19, ccor=CINZA2)
    for i in range(len(st) - 1):
        x0 = st[i][0] + st[i][1]
        d.seta([(x0 + 6, 278), (st[i + 1][0] - 8, 278)], cor=TEXTO,
               largura=3)
    d.texto("gate reprovou: o pipeline para e o live\nsegue no release "
            "anterior", 368, 356, 560, size=19, cor=CINZA2, align="left")

    # --- AWS: regiao; ECR, S3 e SSM moram fora da VPC ---
    d.caixa(30, 415, 1890, 810, "", AWS)
    d.imagem("aws-cloud", 42, 423, 34)
    d.texto("AWS · região us-east-1", 84, 427, 320, size=23, cor=AWS,
            align="left")

    ecr = d.no_s("ecr", "", 851, 515, s=56)
    d.texto("Amazon ECR · qc-assistant-app\n(imagem = release)", 470, 493,
            340, size=19, align="right")
    d.seta([(851, 319), (851, ecr["t"] - 8)], cor=TEXTO, largura=3)
    d.texto("push da imagem", 866, 422, 170, size=19, align="left")
    s3 = d.no_s("s3", "Amazon S3\nreleases-ecs/\n(manifestos)", 1820, 505,
                s=56, w=150, size=19)
    d.seta([(1820, 319), (1820, s3["t"] - 8)], cor=TEXTO, largura=3)

    # --- VPC ---
    d.caixa(50, 590, 1690, 615, "", TEXTO)
    d.imagem("vpc", 60, 598, 34)
    d.texto("VPC fiap-lab", 102, 602, 220, size=23, align="left")

    # cluster ECS: titulo entre as setas de deploy e promote
    d.ret(930, 650, 790, 265, bg=FUNDO)
    d.imagem("fargate", 1300, 656, 36)
    d.texto("ECS Fargate · cluster", 1344, 662, 280, size=22, align="left")
    d.cartao(946, 705, 408, 160, "service qc-assistant-candidate",
             "app + Neo4j próprios:\nrecebe a mudança\nantes do cliente",
             tsize=23, csize=19, ccor=CINZA2)
    d.cartao(1370, 705, 334, 160, "service qc-assistant-live",
             "app :8000 (via ALB)\nadmin :8001 (só runner)\nNeo4j: grafo "
             "refeito da\nimagem a cada task", tsize=23, csize=19, ccor=CINZA2)
    # os dois services puxam a imagem do ECR pelo digest: a seta chega no
    # cluster, que agrupa os dois
    d.seta([(ecr["r"] + 8, 530), (1060, 530), (1060, 642)], cor=TEXTO,
           largura=3)
    d.texto("os dois services puxam pelo digest", 896, 492, 350, size=19,
            align="left")
    d.seta([(1262, 319), (1262, 697)], cor=TEXTO, largura=3)
    d.texto("deploy", 1274, 440, 100, size=19, align="left")
    d.seta([(1660, 319), (1660, 697)], cor=TEXTO, largura=3)
    d.texto("promote\n(mesmo digest)", 1486, 440, 150, size=19,
            align="right")

    # ALB: / -> live, /candidate* -> candidate, /grafana* -> Grafana
    alb = d.no_s("elb", "", 980, 1030, s=72)
    d.seta([(980, alb["t"] - 6), (980, 873)], cor=TEXTO, largura=3)
    d.texto("/candidate/*", 994, 935, 140, size=19, align="left")
    d.seta([(alb["r"] + 6, 1030), (1390, 1030), (1390, 873)], cor=TEXTO,
           largura=3)
    d.texto("/ (padrão)", 1222, 998, 140, size=19, align="right")
    d.texto("ALB qc-assistant-alb\nHTTP :80 público", 1030, 1048, 240,
            size=19, align="left")

    cli = d.no_s("users", "", 980, 1290, s=56)
    d.seta([(980, cli["t"] - 6), (980, alb["b"] + 6)], cor=TEXTO, largura=3)
    d.texto("Clientes · HTTP :80", 1020, 1278, 220, size=19, align="left")

    # --- observabilidade (EC2 com Docker Compose) ---
    d.ret(70, 650, 820, 535, bg=FUNDO)
    d.imagem("ec2", 84, 658, 34)
    d.imagem("docker", 122, 658, 34)
    d.texto("EC2 qc-assistant-obs · Docker Compose (sem SSH)", 164, 662,
            720, size=22, align="left")
    col = d.no_s("otel", "OTel Collector\nrecebe e distribui", 715, 805,
                 s=56, w=200, size=19)
    d.seta([(926, 805), (col["r"] + 8, 805)], cor=OTLP, tracejada=True,
           largura=2)
    d.texto("OTLP de\ncandidate\ne live", 754, 712, 100, size=19, cor=OTLP)
    d.ret(96, 712, 480, 180, cor=CINZA, bg="transparent", largura=1)
    d.texto("guardam a telemetria", 108, 720, 300, size=19, cor=CINZA2,
            align="left")
    for cx, ic, t in ((180, "jaeger", "Jaeger\ntraces"),
                      (336, "prometheus", "Prometheus\nmétricas"),
                      (492, "loki", "Loki\nlogs")):
        d.no_s(ic, t, cx, 795, s=46, w=140, size=19)
    d.seta([(col["l"] - 8, 805), (584, 805)], cor=OTLP, tracejada=True)
    gf = d.no_s("grafana", "", 520, 1000, s=46)
    d.seta([(520, 900), (520, gf["t"] - 8)], cor=TEXTO, largura=3)
    d.texto("Grafana\n(com login)", 360, 978, 124, size=19, align="right")
    d.seta([(alb["l"] - 6, 1000), (gf["r"] + 8, 1000)], cor=TEXTO,
           largura=3)
    d.texto("/grafana/*", 680, 964, 130, size=19)
    pr = d.no_s("python", "qc-probe:\nperguntas de\nteste rotuladas", 185,
                1058, s=50, w=170, size=19)
    d.seta([(pr["r"] + 8, 1058), (alb["l"] - 6, 1058)], cor=TEXTO,
           largura=3)
    d.texto("o probe pergunta pelo ALB", 300, 1070, 300, size=19,
            align="left")

    # --- incidente: fora do pipeline (unico traco vermelho tracejado) ---
    ssm = d.no_s("ssm", "", 1830, 1060, s=56)
    d.texto("AWS Systems\nManager\nRun Command", 1755, 955, 150, size=19)
    run = d.no_s("ec2", "GitLab Runner\n(EC2) roda jobs\ne incident.sh",
                 1630, 1060, s=56, w=170, size=19)
    d.seta([(ssm["l"] - 8, 1060), (run["r"] + 8, 1060)], cor=ALERTA,
           tracejada=True, largura=3)
    d.seta([(1630, run["t"] - 8), (1630, 873)], cor=ALERTA, tracejada=True,
           largura=3)
    d.texto("admin :8001 + HMAC:\naltera o grafo\n(nunca pelo ALB)", 1410,
            935, 200, size=19, cor=ALERTA, align="right")
    inc = d.no_s("github", "", 1830, 1290, s=50)
    d.seta([(1830, inc["t"] - 6), (1830, ssm["b"] + 8)], cor=ALERTA,
           tracejada=True, largura=3)
    d.texto("Você no Codespaces: incident.sh\napply / restore (sem commit,"
            " sem pipeline)", 1295, 1268, 500, size=19, cor=ALERTA,
            align="right")

    # --- de volta: ciclo de aprendizado (esquerda -> direita) ---
    y0 = 1390
    d.seta([(pr["l"] - 8, 1058), (100, 1058), (100, y0 - 8)], cor=CINZA,
           largura=2)
    d.texto("Depois do incidente: ciclo de aprendizado, da esquerda para a "
            "direita", 160, 1340, 1200, size=22, align="left")
    passos = ["o probe acusa o erro;\no trace no Grafana\nmostra onde",
              "restore e replay:\nvolta o grafo e\nrepete os probes",
              "postmortem:\no que falhou e\npor quê",
              "caso novo no golden set\n(perguntas que\nos gates cobram)",
              "próximo git push:\nprotegido pelo\n1 · gates"]
    bw, gap = 334, 50
    xs = [30 + i * (bw + gap) for i in range(len(passos))]
    for x, t in zip(xs, passos):
        n = t.count("\n") + 1
        d.ret(x, y0, bw, 100, bg=FUNDO)
        d.texto(t, x, y0 + (100 - n * 25) / 2, bw, size=20)
    for i in range(len(passos) - 1):
        d.seta([(xs[i] + bw + 6, y0 + 50), (xs[i + 1] - 8, y0 + 50)],
               cor=CINZA, largura=2)

    # --- Legenda: vaos iguais entre itens ---
    y = 1540
    x = 30
    for cor, trac, larg, t in ((TEXTO, False, 3, "mudança e requisição"),
                               (OTLP, True, 2, "telemetria OTLP"),
                               (ALERTA, True, 3, "incidente de laboratório "
                                "(fora do pipeline)"),
                               (CINZA, False, 2, "ciclo de aprendizado")):
        d.seta([(x, y), (x + 60, y)], cor=cor, tracejada=trac, largura=larg)
        w = len(t) * 19 * 0.47
        d.texto(t, x + 72, y - 12, w + 10, size=19,
                cor=TEXTO if cor == TEXTO else cor, align="left")
        x += 72 + w + 50
    d.texto("Rollback: job manual do pipeline que volta o live ao último "
            "release aprovado registrado no S3.", 30, y + 28, 1500, size=19,
            cor=TEXTO, align="left")

    # o cabecalho do pipeline ganhou uma linha: os cartoes desceram 30 px e
    # tudo da regiao AWS para baixo desce 40 px; setas que saem dos cartoes
    # comecam 30 px mais abaixo
    for e in d.elements:
        if e["type"] in ("arrow", "line") and 300 <= e["y"] < 400:
            ab = [(e["x"] + px, e["y"] + py) for px, py in e["points"]]
            ab = [(px, py + (30 if py < 400 else 40)) for px, py in ab]
            e["x"], e["y"] = ab[0]
            e["points"] = [[px - ab[0][0], py - ab[0][1]] for px, py in ab]
            e["height"] = max(p[1] for p in e["points"]) - min(
                p[1] for p in e["points"])
        elif e["y"] >= 400:
            e["y"] += 40

    # abre 45 px entre o subtitulo e o pipeline para o laco de volta passar
    # longe das bordas (L47)
    for e in d.elements:
        if e["y"] >= 100:
            e["y"] += 45

    # "e de volta": o proximo git push fecha o laco no Codespaces; ponta no
    # meio do trecho vertical para mostrar o sentido, traco sem rugosidade
    n0 = len(d.elements)
    d.seta([(1906, 1525), (1978, 1525), (1978, 820)], cor=CINZA, largura=2)
    d.seta([(1978, 830), (1978, 128), (60, 128), (60, 259)], cor=CINZA,
           largura=2)
    for e in d.elements[n0:]:
        e["roughness"] = 0
    # margem direita: o render corta rente ao ultimo elemento
    d._base("rectangle", 2000, 1500, 20, 20, strokeColor="transparent",
            backgroundColor="transparent", strokeWidth=1, roughness=0)
    d.texto("e de volta:\no próximo push", 76, 140, 170, size=19,
            cor=CINZA2, align="left")
    d.salvar(os.path.join(DIR, "arquitetura-5-2.excalidraw"))


# ---------------------------------------------------------------------------
# Modulo 04: duas demos, a mesma base
# ---------------------------------------------------------------------------


def modulo():
    d = D()
    from PIL import ImageFont
    fonte = {}

    def larg(t, size):
        """Largura real do glifo (Helvetica = fontFamily 2 do Excalidraw)."""
        if size not in fonte:
            fonte[size] = ImageFont.truetype(
                "/System/Library/Fonts/Helvetica.ttc", size)
        return max(fonte[size].getlength(l) for l in t.split("\n"))

    def card(x, y, w, h, icone, titulo, corpo="", s=44, size=18):
        d.ret(x, y, w, h, bg="#ffffff")
        d.imagem(icone, x + 12, y + 12, s)
        tx = x + 24 + s
        d.texto(titulo, tx, y + 12, w - (tx - x) - 8, size=size,
                align="left")
        d.texto(corpo, tx, y + 14 + size * 1.25, w - (tx - x) - 8,
                size=size, cor=ESCURO, align="left")
        return {"l": x, "r": x + w, "t": y, "b": y + h}

    def rot(t, cx, y, cor=ESCURO, size=18):
        d.rotulo(t, cx, y, larg(t, size) + 4, size=size, cor=cor)

    def rot_esq(t, x, y, cor=ESCURO, size=18):
        """Rotulo comecando em x (ao lado direito de uma seta vertical)."""
        w = larg(t, size) + 4
        d.rotulo(t, x + w / 2, y, w, size=size, cor=cor)

    def rot_dir(t, x, y, cor=ESCURO, size=18):
        """Rotulo terminando em x (ao lado esquerdo de uma seta vertical)."""
        w = larg(t, size) + 4
        d.rotulo(t, x - w / 2, y, w, size=size, cor=cor)

    # --- titulo -----------------------------------------------------------
    d.texto("Quantum Commerce · Módulo 04 (Aula 5): duas demos sobre a mesma "
            "base na AWS", 30, 22, 1760, size=32, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Sem LLM: a resposta é montada do trecho "
            "recuperado e citado. A Parte 0 (preparação da Demo 5.1) cria esta base AWS "
            "uma vez.",
            30, 68, 1760, size=20, cor=ESCURO, align="left")

    # --- as duas demos: so texto; o selo de cada uma reaparece no desenho --
    def demo(x, selo, cor, titulo, linhas):
        d.ret(x, 112, 870, 188, cor=cor, bg="#ffffff", largura=3)
        sw = d.selo(x + 16, 126, selo, cor)
        d.texto(titulo, x + 30 + sw, 126, 800 - sw, size=22, align="left")
        d.texto(linhas, x + 18, 166, 834, size=21, align="left")

    demo(30, "Demo 5.1", AZUL, "A evidência por trás da resposta",
         "Pergunta: qual evidência autoriza um release?\n"
         "· a mesma pergunta em BM25, vetorial e grafo, com citação\n"
         "· o mesmo filtro de vigência, tenant e ACL nas três\n"
         "· na Central de Evidências, servida pelo service live")
    demo(940, "Demo 5.2", VERDE, "Operação e qualidade contínua",
         "Pergunta: como barrar a evidência errada no deploy?\n"
         "· pipeline: gates → package → candidate → smoke → promote\n"
         "· EC2 de observabilidade: qc-probe e Grafana medem a qualidade\n"
         "· incidente: muda o grafo do live sem passar pelo pipeline")

    # --- fora da AWS: o GitLab (SaaS) e voce no Codespaces -------------------
    XI = 1045  # vertical do incidente: Codespaces -> runner -> live
    gl = card(540, 330, 340, 84, "gitlab", "GitLab (SaaS)",
              "projeto quantum-ai-assistant")
    d.selo(735, 339, "Demo 5.2", VERDE)
    d.no_s("user", "Você (Codespaces)", XI, 356, s=48, w=200, size=18)
    d.seta([(XI - 32, 356), (gl["r"] + 8, 356)], cor=TEXTO, largura=2)
    rot("git push", 946, 322)

    # --- regiao e VPC (molduras com >= 25 px de folga, L28) -----------------
    d.caixa(265, 490, 1565, 775, "", AWS, bg="#ffffff")
    d.imagem("aws-cloud", 279, 498, 36)
    d.texto("AWS · região us-east-1", 323, 505, 400, size=18, cor=AWS,
            align="left")
    VR = 1385
    d.ret(295, 545, VR - 295, 690, cor=CINZA, bg=FUNDO, largura=1)
    d.texto("VPC fiap-lab (do Módulo 01, reutilizada)", 311, 556, 400,
            size=18, cor=ESCURO, align="left")

    rn = card(720, 600, 400, 135, "ec2", "GitLab Runner (EC2)",
              "shell, Docker e LabRole (papel IAM)\n"
              "executa os jobs do pipeline e,\n"
              "fora dele, o incident.sh (via SSM)", s=48)
    d.seta([(770, gl["b"] + 8), (770, rn["t"] - 8)], cor=TEXTO, largura=2)
    rot_esq("roda os jobs\ndo pipeline", 786, 436)
    d.seta([(XI, 418), (XI, rn["t"] - 8)], cor=ALERTA, tracejada=True)
    rot_esq("incident.sh via SSM Run Command\n(não é job do pipeline)", XI + 16,
            436, cor=ALERTA)

    # servicos regionais: fora da VPC, dentro da regiao (L21); vaos de 40
    RX, RW = 1435, 375
    s3 = card(RX, 570, RW, 80, "s3", "Amazon S3",
              "state do Terraform e manifestos")
    ecr = card(RX, 690, RW, 100, "ecr", "Amazon ECR",
               "imagem = release\n(por digest)")
    ssm = card(RX, 830, RW, 80, "ssm-parameter-store", "SSM Parameter Store",
               "segredos lidos pelas tasks")
    MV = (rn["r"] + VR) / 2   # meio do vao runner -> borda da VPC
    d.seta([(rn["r"] + 8, 615), (RX - 8, 615)], tracejada=True)
    rot("grava state e manifestos", MV, 583)
    d.seta([(rn["r"] + 8, 705), (RX - 8, 705)], cor=TEXTO, largura=2)
    rot("build e push da imagem", MV, 673)

    # ECS: dois services, cada um com app + Neo4j proprios (r4 item 4);
    # cartoes recuados 50 px para a ponta da seta do ALB nao cair na borda
    E = {"l": 720, "r": 1210, "t": 860, "b": 1200}
    d.ret(720, 860, 490, 340, bg="#ffffff")
    d.imagem("fargate", 732, 870, 40)
    d.texto("ECS Fargate · 2 services", 782, 870, 300, size=18,
            align="left")
    d.texto("cada task: app + Neo4j", 782, 896, 300, size=18, cor=ESCURO,
            align="left")
    CL = 770
    live = {"l": CL, "r": 1190, "t": 925, "b": 1065}
    d.ret(CL, 925, 420, 140, bg="#ffffff")
    d.texto("service live: Central de Evidências", CL + 12, 937, 300,
            size=18, align="left")
    d.selo(1086, 934, "Demo 5.1", AZUL)
    d.imagem("fastapi", CL + 14, 971, 36)
    d.texto("app: BM25 e embeddings", CL + 62, 978, 300, size=18,
            cor=ESCURO, align="left")
    d.imagem("neo4j", CL + 12, 1015, 40)
    d.texto("Neo4j: grafo e índice vetorial", CL + 62, 1024, 340, size=18,
            cor=ESCURO, align="left")
    cand = {"l": CL, "r": 1190, "t": 1080, "b": 1185}
    d.ret(CL, 1080, 420, 105, bg="#ffffff")
    d.texto("service candidate: próximo release", CL + 12, 1092, 400,
            size=18, align="left")
    d.imagem("fastapi", CL + 14, 1128, 36)
    d.imagem("neo4j", CL + 56, 1126, 40)
    d.texto("app e Neo4j próprios,\ncom a próxima imagem", CL + 108, 1126,
            300, size=18, cor=ESCURO, align="left")

    # runner -> ECS: deploy por digest (candidate, depois promote no live)
    d.seta([(770, rn["b"] + 8), (770, E["t"] - 8)], cor=TEXTO, largura=2)
    rot_dir("deploy e\npromote\n(Demo 5.2)", 754, 758)
    # incidente: runner -> porta admin 8001 do live (mesma vertical XI)
    d.seta([(XI, rn["b"] + 8), (XI, live["t"] - 8)], cor=ALERTA,
           tracejada=True)
    rot_dir("porta admin 8001", XI - 16, 785, cor=ALERTA)
    # pull em L, 65 abaixo da chegada do push (pontas separadas, r4 item 1)
    d.seta([(RX - 8, 770), (1165, 770), (1165, E["t"] - 8)], cor=TEXTO,
           largura=2)
    rot_esq("pull ao subir", 1181, 790)

    MD = (E["r"] + VR) / 2    # meio do vao ECS -> borda da VPC
    d.seta([(RX - 8, 890), (E["r"] + 8, 890)], tracejada=True)
    rot("segredos", MD, 852)

    # API de pedidos: fora da VPC, chamada pelos dois services (sai do ECS)
    d.ret(RX, 950, RW, 210, bg="#ffffff")
    d.texto("API de pedidos (status e entrega)", RX, 962, RW, size=18)
    for nome, rotulo, cx in (("apigateway", "Amazon API\nGateway", RX + 65),
                             ("lambda", "AWS\nLambda", RX + 188),
                             ("dynamodb", "Amazon\nDynamoDB", RX + 310)):
        d.no_s(nome, rotulo, cx, 1060, s=56, w=120, size=18)
    d.seta([(RX + 101, 1060), (RX + 152, 1060)], cor=TEXTO)
    d.seta([(RX + 224, 1060), (RX + 274, 1060)], cor=TEXTO)
    d.seta([(E["r"] + 8, 1060), (RX - 8, 1060)], cor=TEXTO, largura=2)
    rot("GET /orders\n(os 2 services)", MD, 1000)

    # EC2 de observabilidade (so na 5.2), Grafana e qc-probe dentro
    d.ret(320, 610, 290, 300, cor=VERDE, bg="#ebfbee")
    d.selo(334, 622, "Demo 5.2", VERDE)
    d.imagem("ec2", 558, 620, 40)
    d.texto("EC2 de observabilidade", 334, 658, 270, size=18, align="left")
    d.texto("OTel Collector, Jaeger,\nPrometheus e Loki", 334, 686, 270,
            size=18, cor=ESCURO, align="left")
    d.ret(332, 770, 120, 85, bg="#ffffff")
    d.imagem("grafana", 374, 780, 36)
    d.texto("Grafana", 332, 822, 120, size=18)
    d.ret(472, 770, 126, 85, bg="#ffffff")
    d.imagem("python", 517, 780, 36)
    d.texto("qc-probe", 472, 822, 126, size=18)
    d.seta([(E["l"] - 8, 890), (618, 890)], tracejada=True)
    rot("OTLP", 665, 852)

    # ALB e clientes (ALB a 50 px da VPC: a ponta do HTTP :80 nao cai na borda)
    alb = {"l": 345, "r": 570, "t": 1000, "b": 1180}
    d.ret(345, 1000, 225, 180, bg="#ffffff")
    d.imagem("elb", 429, 1014, 56)
    d.texto("Application Load\nBalancer (ALB)\nHTTP :80 público", 345,
            1078, 225, size=18)
    d.no_s("user", "Você e os clientes\n(navegador)", 95, 1090, s=56,
           w=180, size=18)
    d.seta([(131, 1090), (alb["l"] - 8, 1090)], cor=TEXTO, largura=2)
    rot("HTTP :80", 197, 1058)
    MA = (alb["r"] + E["l"]) / 2
    d.seta([(alb["r"] + 8, 1040), (live["l"] - 8, 1040)], cor=TEXTO,
           largura=2)
    rot("/ (padrão)", MA, 1004)
    d.seta([(alb["r"] + 8, 1150), (cand["l"] - 8, 1150)], cor=TEXTO,
           largura=2)
    rot("/candidate", MA, 1114)
    d.seta([(392, alb["t"] - 8), (392, 863)], cor=TEXTO, largura=2)
    rot_esq("/grafana", 404, 935)
    d.seta([(535, 863), (535, alb["t"] - 8)], cor=TEXTO, largura=2)
    rot_esq("perguntas com\ngabarito ao live", 547, 932)

    # legenda: toda seta desenhada cabe numa linha daqui (L35)
    y = 1300
    d.seta([(40, y + 11), (100, y + 11)], cor=TEXTO, largura=2)
    d.texto("requisição, entrega e deploy", 112, y, 280, size=18,
            align="left")
    d.seta([(410, y + 11), (470, y + 11)], tracejada=True)
    d.texto("apoio (state, segredos, telemetria)", 482, y, 340, size=18,
            align="left")
    d.seta([(820, y + 11), (880, y + 11)], cor=ALERTA, tracejada=True)
    d.texto("incidente de laboratório (Demo 5.2): muda o grafo do live, "
            "fora do pipeline", 892, y, 900, size=18, cor=ALERTA,
            align="left")

    # desce tudo da AWS para baixo (y >= 470) para os rotulos de duas linhas
    # entre o GitLab e a regiao terem folga (L36); setas que cruzam a borda
    # so estendem o trecho de baixo
    DY = 24
    for e in d.elements:
        if e["type"] in ("arrow", "line"):
            pts = [(e["x"] + px, e["y"] + py) for px, py in e["points"]]
            pts = [(px, py + DY if py >= 470 else py) for px, py in pts]
            x0, y0 = pts[0]
            e["x"], e["y"] = x0, y0
            e["points"] = [[px - x0, py - y0] for px, py in pts]
            e["height"] = max(p[1] for p in e["points"]) - min(
                p[1] for p in e["points"])
        elif e["y"] >= 470:
            e["y"] += DY

    for r in folgas(d):
        log("  FOLGA: " + r)
    d.salvar(os.path.join(DIR, "arquitetura-modulo-04.excalidraw"))


if __name__ == "__main__":
    demo_51()
    demo_52()
    modulo()
