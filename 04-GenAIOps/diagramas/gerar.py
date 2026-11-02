#!/usr/bin/env python3
"""Gera as arquiteturas das duas demos do modulo 04 (Aula 5).

Uso:     python3 gerar.py
Saida:   arquitetura-release-rag.excalidraw  (Demo 5.1)
         arquitetura-operacao.excalidraw     (Demo 5.2)
Render:  abra cada .excalidraw em https://excalidraw.com e exporte como PNG
         (largura entre 2000 e 2400 px) para:
         ../01-Release-RAG-Quality-Gates/img/arquitetura-release-rag.png
         ../02-Operacao-Observabilidade-Qualidade-Continua/img/arquitetura-operacao.png

Os diagramas mostram CAPACIDADES (o que cada parte faz), nao um catalogo de
logos. Icones OFICIAIS em assets/ (SVG fonte + PNG 160px embutido, porque SVG
embutido nao rasteriza no render headless). Origem de cada icone:
  - AWS (apigateway, aws-cloud, dynamodb, ec2, lambda, s3) e icones
    genericos (users, server, alert, metrics, gear, user): AWS Architecture
    Icons, pacote oficial 04/2026,
    https://aws.amazon.com/architecture/icons/
  - gitlab: marca oficial do GitLab (mesmo asset do modulo 03-CICD).
  - terraform: marca oficial HashiCorp (mesmo asset do modulo 03-CICD).
  - otel, jaeger, prometheus: CNCF artwork oficial,
    https://github.com/cncf/artwork/tree/main/projects/<projeto>/icon/color
  - grafana: repositorio oficial, grafana/grafana public/img/grafana_icon.svg
  - github: Primer Octicons oficial (mark-github-24.svg); a GitHub nao publica
    icone separado para Codespaces, por isso o rotulo diz "Codespaces".
  - docker: press kit oficial, docker.com/app/uploads/2024/01/icon-docker-square.svg

Licoes de UX aplicadas: L1 acentuacao, L3 verbo com sujeito na origem,
L5 rotulo de aresta explicito, L6 empresa ficticia visivel, L8 fluxo de apoio
tracejado, L9/L18 rotulo de seta com fundo branco opaco, L10 so o caminho de
reprovacao/alerta em cor de alerta, L11 fan-out de um ponto so, L12 papel em
destaque quando o icone se repete, L13 magenta so no titulo, L19 ponta de
seta nunca sobre borda de container.
"""
import base64
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(DIR, "assets")

MAGENTA = "#B80D47"     # magenta-deep Quantum Commerce: so no titulo
TEXTO = "#1e1e1e"
CINZA = "#495057"
AWS = "#232F3E"
GITLAB = "#E24329"
ALERTA = "#C92A2A"      # so reprovacao e alerta (L10)
OTLP = "#3B5BDB"        # telemetria: tracejado azul, distinto do controle
FUNDO = "#f8f9fa"

ICON = 80


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Diagrama:
    def __init__(self):
        self.elements = []
        self.files = {}
        self._n = 0

    def _id(self, p):
        self._n += 1
        return f"{p}{self._n}"

    def _base(self, tipo, x, y, w, h, **kw):
        el = {
            "id": self._id(tipo[:3]), "type": tipo, "x": x, "y": y,
            "width": w, "height": h, "angle": 0, "strokeColor": TEXTO,
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
            "seed": 1000 + self._n * 7, "version": 1,
            "versionNonce": 2000 + self._n * 11, "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
        }
        el.update(kw)
        self.elements.append(el)
        return el

    def texto(self, t, x, y, w, size=18, cor=TEXTO, align="center"):
        linhas = t.count("\n") + 1
        h = linhas * size * 1.25
        self._base("text", x, y, w, h, strokeColor=cor, strokeWidth=1,
                   fontSize=size, fontFamily=2, text=t, originalText=t,
                   textAlign=align, verticalAlign="top", containerId=None,
                   lineHeight=1.25, baseline=int(size * 0.8))
        return h

    def rotulo(self, t, cx, y, w, size=15, cor=CINZA):
        """Rotulo de seta: retangulo branco opaco atras do texto (L18)."""
        linhas = t.count("\n") + 1
        h = linhas * size * 1.25
        self._base("rectangle", cx - w / 2 - 6, y - 4, w + 12, h + 8,
                   strokeColor="transparent", backgroundColor="#ffffff",
                   strokeWidth=1, roughness=0)
        self.texto(t, cx - w / 2, y, w, size=size, cor=cor)

    def imagem(self, nome, x, y, s):
        fid = self._id("file")
        with open(os.path.join(ASSETS, f"{nome}.png"), "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        self.files[fid] = {"mimeType": "image/png", "id": fid,
                           "dataURL": f"data:image/png;base64,{b64}",
                           "created": 1}
        self._base("image", x, y, s, s, strokeColor="transparent",
                   roughness=0, status="saved", fileId=fid, scale=[1, 1])

    def no(self, icone, rotulo, cx, cy, w=280, size=16):
        self.imagem(icone, cx - ICON / 2, cy - ICON / 2, ICON)
        if rotulo:
            self.texto(rotulo, cx - w / 2, cy + ICON / 2 + 10, w, size=size)
        return {"x": cx - ICON / 2, "y": cy - ICON / 2, "cx": cx, "cy": cy}

    def caixa(self, x, y, w, h, titulo, cor, bg="transparent", icones=(),
              tracejada=False, size=18):
        self._base("rectangle", x, y, w, h, strokeColor=cor,
                   backgroundColor=bg,
                   strokeStyle="dashed" if tracejada else "solid")
        tx = x + 14
        for ic in icones:
            self.imagem(ic, tx - 6, y + 6, 36)
            tx += 40
        if titulo:
            self.texto(titulo, tx + 4, y + 13, w - (tx - x) - 14, size=size,
                       cor=cor, align="left")

    def seta(self, pontos, cor=CINZA, tracejada=False, largura=2, ponta=True):
        x0, y0 = pontos[0]
        rel = [[px - x0, py - y0] for px, py in pontos]
        xs = [p[0] for p in rel]
        ys = [p[1] for p in rel]
        self._base("arrow" if ponta else "line", x0, y0,
                   max(xs) - min(xs), max(ys) - min(ys),
                   strokeColor=cor, strokeWidth=largura,
                   strokeStyle="dashed" if tracejada else "solid",
                   points=rel, lastCommittedPoint=None,
                   startBinding=None, endBinding=None, startArrowhead=None,
                   endArrowhead="arrow" if ponta else None)

    def losango(self, cx, cy, w, h, t, cor=ALERTA, size=15):
        self._base("diamond", cx - w / 2, cy - h / 2, w, h, strokeColor=cor,
                   backgroundColor="#fff5f5")
        self.texto(t, cx - w / 2, cy - size * 1.25, w, size=size, cor=cor)

    def etapa(self, x, y, w, h, titulo, corpo, icone=None, tracejada=False):
        self._base("rectangle", x, y, w, h, strokeColor=TEXTO,
                   backgroundColor="#ffffff",
                   strokeStyle="dashed" if tracejada else "solid")
        tx = x + 14
        if icone:
            self.imagem(icone, x + 10, y + (h - 40) / 2, 40)
            tx = x + 60
        self.texto(titulo, tx, y + 9, w - (tx - x) - 10, size=17,
                   align="left")
        self.texto(corpo, tx, y + 33, w - (tx - x) - 10, size=14, cor=CINZA,
                   align="left")

    def checar(self):
        """Sobreposicao entre textos e entre texto e icone (largura estimada
        pelo glifo, nao pela caixa declarada). Vazio = nada colidindo."""
        caixas = []
        for e in self.elements:
            if e["type"] == "text":
                lw = max(len(l) for l in e["text"].split("\n"))
                w = lw * e["fontSize"] * 0.52
                if e["textAlign"] == "center":
                    x = e["x"] + (e["width"] - w) / 2
                else:
                    x = e["x"]
                caixas.append(("txt", e["text"][:28], x, e["y"], w,
                               e["height"]))
            elif e["type"] == "image":
                caixas.append(("img", e["fileId"], e["x"], e["y"],
                               e["width"], e["height"]))
        ruins = []
        for i, a in enumerate(caixas):
            for b in caixas[i + 1:]:
                if a[0] == "img" and b[0] == "img":
                    continue
                if (a[2] < b[2] + b[4] and b[2] < a[2] + a[4]
                        and a[3] < b[3] + b[5] and b[3] < a[3] + a[5]):
                    ruins.append((a[1], b[1]))
        return ruins

    def salvar(self, caminho):
        doc = {"type": "excalidraw", "version": 2,
               "source": "quantum-commerce-gerador",
               "elements": self.elements,
               "appState": {"gridSize": None,
                            "viewBackgroundColor": "#ffffff"},
               "files": self.files}
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        for a, b in self.checar():
            log(f"  SOBREPOSICAO: {a!r} x {b!r}")
        log(f"gerado: {caminho} ({len(self.elements)} elementos)")


def release_rag():
    """Demo 5.1: release do assistente com quality gates e aliases."""
    d = Diagrama()
    d.texto("Quantum Commerce · Demo 5.1: entregar uma mudança de IA com "
            "quality gates, candidate e live", 40, 28, 2250, size=30,
            cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. Se um gate reprova, o pipeline para e o "
            "cliente continua no release anterior; o live só muda depois "
            "do teste contra o candidate.", 40, 74, 2250, size=19,
            cor=CINZA, align="left")

    # --- Esquerda: desenvolvimento ---
    d.caixa(40, 140, 360, 990, "Desenvolvimento", TEXTO, bg=FUNDO)
    d.no("github", "Você no GitHub Codespaces\n(AI DevOps Engineer)",
         220, 245)
    d.seta([(220, 340), (220, 418)], cor=TEXTO, largura=3)
    d.rotulo("git push", 155, 368, 80)
    repo = d.no("gitlab", "GitLab · primeiro-projeto\nquantum-assistant/ e\n"
                ".gitlab-ci.yml na raiz", 220, 470)
    d.texto("O que entra no commit:\n• core em Python (sem LLM)\n"
            "• corpus de políticas (.md)\n• prompts/templates.json\n"
            "• golden set + limiares\n• infra em Terraform",
            70, 610, 320, size=15, cor=CINZA, align="left")
    d.texto("A aba Tests do GitLab mostra\no JUnit de cada gate, e o\n"
            "manifest.json fica como\nartefato do pipeline.",
            70, 800, 320, size=15, cor=CINZA, align="left")

    # --- Centro: pipeline ---
    d.caixa(450, 140, 910, 990, "Pipeline do GitLab CI/CD", GITLAB,
            icones=("gitlab",))
    d.imagem("ec2", 480, 186, 40)
    d.texto("roda no GitLab Runner: EC2 quantum-ai-gitlab-runner\n"
            "(perfil LabRole: nenhuma chave AWS no GitLab)", 530, 186, 720,
            size=15, align="left")

    X, W = 480, 580
    cx = X + W / 2
    etapas = [
        (250, 56, "1 · preflight", "runner, AWS e ferramentas prontos? "
         "Falha cedo, antes de gastar tempo", None, False),
        (324, 56, "2 · validate · security-gate",
         "terraform fmt · validate · tflint · checkov", "terraform", False),
        (398, 56, "3 · test · api-contract-gate",
         "pytest do core e do contrato da API de pedidos (JUnit)", None,
         False),
        (472, 76, "4 · evaluate · retrieval-quality-gate + "
         "business-policy-gate", "golden set v1 offline: Hit@k, MRR, "
         "citação, fonte vigente,\nrecusa obrigatória, ACL e injeção "
         "(regra dura reprova)", None, False),
    ]
    for y, h, t, c, ic, tr in etapas:
        d.etapa(X, y, W, h, t, c, icone=ic, tracejada=tr)
    for y0 in (306, 380, 454):
        d.seta([(cx, y0 + 3), (cx, y0 + 15)], cor=TEXTO, largura=3)
    # o losango decide se o deploy acontece (L10: so a reprovacao e vermelha)
    d.seta([(cx, 551), (cx, 563)], cor=TEXTO, largura=3)
    d.losango(cx, 611, 190, 90, "os gates\npassaram?")
    d.seta([(cx + 95, 611), (cx + 150, 611)], cor=ALERTA, tracejada=True,
           largura=3)
    d.texto("não: o pipeline para.\nDeploy e promote\nnão rodam; o live\n"
            "segue no release\nanterior.", cx + 160, 563, 200, size=15,
            cor=ALERTA, align="left")
    d.seta([(cx, 656), (cx, 670)], cor=TEXTO, largura=3)
    d.texto("sim", cx + 12, 652, 40, size=14, cor=CINZA, align="left")

    etapas2 = [
        (674, "5 · package + plan", "bundle.zip + manifest.json (5 versões) "
         "· terraform plan", None, False),
        (748, "6 · approve (opcional)", "manual só se "
         "REQUIRE_MANUAL_APPROVAL=true", None, True),
        (822, "7 · deploy candidate", "terraform apply publica a versão "
         "N+1 e aponta o candidate", "terraform", False),
        (896, "8 · postdeploy-synthetic-gate", "smoke + parte do golden "
         "set contra /candidate; confere release_id", None, False),
        (970, "9 · promote live", "move o alias live para a versão "
         "testada", None, False),
        (1044, "10 · evidence", "manifest e relatórios gravados no S3 e "
         "no GitLab", None, False),
    ]
    for y, t, c, ic, tr in etapas2:
        d.etapa(X, y, W, 56, t, c, icone=ic, tracejada=tr)
    for y0 in (730, 804, 878, 952, 1026):
        d.seta([(cx, y0 + 3), (cx, y0 + 15)], cor=TEXTO, largura=3)

    # cada push dispara o pipeline (L19: ponta na etapa, nao na borda)
    d.seta([(repo["x"] + ICON + 8, 470), (472, 280)], cor=TEXTO, largura=3)
    d.rotulo("cada push\ndispara", 365, 345, 70, size=15)

    # --- Direita: cliente e AWS ---
    d.caixa(1410, 140, 880, 190, "", TEXTO, bg=FUNDO)
    d.texto("Cliente da Quantum Commerce (tráfego de aula, fictício)",
            1600, 156, 680, size=17, align="left")
    d.imagem("user", 1480, 196, ICON)
    d.texto("pergunta: “Posso trocar um tênis depois de 20 dias?”\n"
            "resposta: trecho da política vigente + citação\n"
            "(doc_id, versão, vigência)", 1600, 200, 680, size=16,
            align="left")

    d.caixa(1410, 370, 880, 760, "", AWS)
    d.imagem("aws-cloud", 2018, 376, 36)
    d.texto("AWS · us-east-1 (Learner Lab)", 2060, 384, 230, size=16,
            cor=AWS, align="left")

    api = d.no("apigateway", "Amazon API Gateway\nqc-assistant (HTTP)",
               1520, 520, w=220)
    d.seta([(1520, 286), (1520, 470)], cor=TEXTO, largura=3)
    d.rotulo("POST /assistant/ask", 1610, 392, 150, size=15)

    # aliases: o mesmo icone de Lambda aparece so uma vez; o papel fica no
    # texto da caixa (L12)
    d._base("rectangle", 1640, 440, 230, 62, strokeColor=TEXTO,
            backgroundColor="#ffffff")
    d.texto("alias live\nrota /assistant/ask", 1652, 448, 210, size=16,
            align="left")
    d._base("rectangle", 1640, 560, 230, 62, strokeColor=TEXTO,
            backgroundColor="#ffffff", strokeStyle="dashed")
    d.texto("alias candidate\nrota /candidate/*", 1652, 568, 210, size=16,
            align="left")
    # fan-out de um ponto so (L11)
    d.seta([(api["x"] + ICON + 6, 520), (1632, 475)], cor=TEXTO, largura=2)
    d.seta([(api["x"] + ICON + 6, 520), (1632, 588)], cor=TEXTO, largura=2)

    lam = d.no("lambda", "", 2110, 530)
    d.texto("AWS Lambda · qc-assistant-api\n(o mesmo core; cada versão é "
            "um\nbundle imutável)", 1935, 432, 350, size=15)
    d.seta([(1870, 470), (lam["x"] - 8, 520)], cor=TEXTO, largura=2)
    d.seta([(1870, 592), (lam["x"] - 8, 545)], cor=TEXTO, largura=2)
    d.rotulo("versão N", 1955, 476, 70, size=14)
    d.rotulo("versão N+1", 1955, 568, 86, size=14)

    d._base("rectangle", 1960, 990, 310, 110, strokeColor=TEXTO,
            backgroundColor="#ffffff")
    d.texto("Rollback", 1976, 1000, 280, size=17, align="left")
    d.texto("mover o alias live de volta à\nversão anterior do manifesto\n"
            "(sem novo pipeline nem apply)", 1976, 1028, 290, size=15,
            cor=CINZA, align="left")

    orders = d.no("lambda", "AWS Lambda · qc-assistant-orders\n"
                  "(API de pedidos fictícia)", 2110, 860, w=300, size=15)
    d.seta([(2110, 578), (2110, 810)], cor=TEXTO, largura=2)
    d.rotulo("GET /orders/{id}\n(token autorizado)", 2010, 680, 150,
             size=14)
    d.no("dynamodb", "Amazon DynamoDB\nqc-assistant-orders\n"
         "(dados 100% fictícios)", 1760, 860, w=220, size=15)
    d.seta([(orders["x"] - 8, 860), (1808, 860)], cor=TEXTO, largura=2)
    d.rotulo("lê o pedido", 1935, 828, 90, size=14)

    d.imagem("s3", 1480, 1010, ICON)
    d.texto("Amazon S3 · base-config-<SEU RM>\nstate do Terraform +\n"
            "releases/<release_id>/manifest.json", 1572, 1018, 360, size=15,
            align="left")

    # pipeline -> AWS: deploy, postdeploy e promote atuam nos aliases
    d._base("line", 1070, 826, 14, 196, strokeColor=CINZA, strokeWidth=2,
            points=[[0, 0], [14, 0], [14, 196], [0, 196]],
            lastCommittedPoint=None, startBinding=None, endBinding=None,
            startArrowhead=None, endArrowhead=None)
    d.seta([(1086, 924), (1700, 632)], cor=TEXTO, largura=3)
    d.rotulo("etapas 7, 8 e 9 publicam,\ntestam e promovem\nnos aliases",
             1730, 704, 200, size=15)
    d.seta([(1068, 1072), (1470, 1050)], tracejada=True)
    d.rotulo("grava manifest\ne evidência", 1215, 1012, 110, size=14)

    # --- Legenda ---
    d.caixa(40, 1170, 2250, 200, "Legenda", TEXTO, bg=FUNDO)
    d.texto("Sem LLM: o composer é determinístico (extrai e cita trechos do "
            "corpus) e model_version = none. Pedidos e tokens são mocks de "
            "laboratório.", 60, 1208, 2200, size=18, align="left")
    d.texto("Cada release leva 5 versões juntas no manifest.json: app, prompt "
            "(templates), corpus, índice e eval set. Voltar o alias volta as "
            "cinco.", 60, 1240, 2200, size=18, align="left")
    y = 1322
    d.seta([(60, y), (120, y)], cor=TEXTO, largura=3)
    d.texto("fluxo principal", 132, y - 10, 200, size=16, align="left")
    d.seta([(340, y), (400, y)], tracejada=True)
    d.texto("apoio (evidência)", 412, y - 10, 220, size=16, cor=CINZA,
            align="left")
    d.seta([(640, y), (700, y)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("gate reprovado: o pipeline para", 712, y - 10, 320, size=16,
            cor=ALERTA, align="left")
    d._base("rectangle", 1060, y - 14, 60, 28, strokeColor=TEXTO,
            strokeStyle="dashed")
    d.texto("borda tracejada = opcional ou ainda não em produção",
            1132, y - 10, 520, size=16, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-release-rag.excalidraw"))


def operacao():
    """Demo 5.2: operacao local-demo com telemetria e qualidade continua."""
    d = Diagrama()
    d.texto("Quantum Commerce · Demo 5.2: operar o assistente e medir a "
            "qualidade continuamente", 40, 28, 2250, size=30, cor=MAGENTA,
            align="left")
    d.texto("Ambiente simulado (local-demo): tudo roda no Codespaces em "
            "Docker, com tráfego sintético rotulado e sem LLM. Não é tracing "
            "da Lambda em produção.", 40, 74, 2250, size=19, cor=CINZA,
            align="left")

    d.caixa(40, 140, 1880, 960, "GitHub Codespaces · local-demo "
            "(ambiente simulado, Docker)", TEXTO, icones=("github", "docker"))

    # controles do instrutor
    d.caixa(70, 200, 370, 150, "Controles do instrutor", TEXTO, bg=FUNDO,
            icones=("gear",), size=17)
    d.texto("bootstrap-demo52.sh: sobe o stack\ninject.py <cenário>: liga "
            "a falha\nrestore.py: desfaz a falha", 86, 248, 350, size=15,
            cor=CINZA, align="left")

    sim = d.no("users", "Simulador de clientes\nops/simulator.py\n"
               "(seed fixa, cenários, test_sample)", 240, 520, w=300,
               size=15)
    rt = d.no("server", "RUNTIME LOCAL · http_server.py :8080\n"
              "o mesmo core e release da 5.1\n(versões em GET /health)",
              780, 520, w=340, size=15)
    od = d.no("server", "PEDIDOS LOCAL · orders_service.py\n"
              ":8081, faults injetáveis\n(pedidos fictícios)", 1300, 520,
              w=320, size=15)

    d.seta([(sim["x"] + ICON + 8, 520), (rt["x"] - 8, 520)], cor=TEXTO,
           largura=3)
    d.rotulo("POST /assistant/ask\n(probes rotulados)", 510, 462, 170)
    d.seta([(rt["x"] + ICON + 8, 520), (od["x"] - 8, 520)], cor=TEXTO,
           largura=3)
    d.rotulo("GET /orders/{id}\n+ traceparent (W3C)", 1040, 462, 170)

    # inject/restore: um ponto de saida, dois destinos (L11)
    d.seta([(448, 300), (780, 300), (780, 466)], tracejada=True)
    d.seta([(448, 300), (1300, 300), (1300, 466)], tracejada=True)
    d.rotulo("inject / restore: falha controlada e reversível", 1040, 316,
             380, size=15)

    # --from-live: comparacao opcional com o release da 5.1
    d.seta([(448, 250), (2075, 250)], tracejada=True)
    d.rotulo("bootstrap --from-live URL: compara as versões locais com o "
             "live da 5.1 (GET /health)", 1180, 214, 640, size=15)

    # telemetria (L8: apoio tracejado; cor propria para OTLP)
    col = d.no("otel", "OpenTelemetry Collector\n(recebe OTLP e distribui)",
               1040, 820, w=260, size=15)
    d.seta([(780, 666), (1000, 776)], cor=OTLP, tracejada=True)
    d.seta([(1300, 666), (1080, 776)], cor=OTLP, tracejada=True)
    d.seta([(330, 666), (992, 808)], cor=OTLP, tracejada=True)
    d.rotulo("OTLP: traces e métricas", 1040, 700, 180, size=14, cor=OTLP)

    jg = d.no("jaeger", "Jaeger · traces\num trace: probe → runtime\n"
              "→ pedidos", 1480, 740, w=240, size=15)
    pm = d.no("prometheus", "Prometheus\nmétricas + alert rules",
              1480, 950, w=240, size=15)
    d.seta([(col["x"] + ICON + 8, 806), (jg["x"] - 8, 746)], cor=OTLP,
           tracejada=True)
    d.seta([(col["x"] + ICON + 8, 834), (pm["x"] - 8, 944)], cor=OTLP,
           tracejada=True)

    d._base("rectangle", 1640, 630, 270, 400, strokeColor=TEXTO,
            backgroundColor=FUNDO)
    d.imagem("grafana", 1654, 642, 48)
    d.texto("Grafana · dashboard\n(localhost, só leitura)", 1710, 646,
            195, size=15, align="left")
    d.seta([(jg["x"] + ICON + 8, 740), (1630, 740)], cor=TEXTO)
    d.seta([(pm["x"] + ICON + 8, 950), (1630, 950)], cor=TEXTO)
    d.rotulo("alimenta", 1580, 712, 64, size=14)
    d.rotulo("alimenta", 1580, 922, 64, size=14)
    d._base("rectangle", 1654, 712, 120, 160, strokeColor=TEXTO,
            backgroundColor="#ffffff")
    d.texto("sinal\ntécnico\n\nRPS, p95,\n5xx, falha\nde pedidos", 1658,
            722, 112, size=14)
    d._base("rectangle", 1778, 712, 120, 160, strokeColor=TEXTO,
            backgroundColor="#ffffff")
    d.texto("qualidade\nsintética\n\npass rate,\nfonte vigente,\nHit@k",
            1782, 722, 112, size=14)
    d.texto("lado a lado no mesmo\npainel, para comparar\n"
            "infra disponível com\nresposta correta", 1654, 890, 250,
            size=14, cor=CINZA)

    # qualidade continua
    mon = d.no("metrics", "QUALITY MONITOR\nquality_monitor.py\n"
               "(janela e n mínimo)", 240, 820, w=240, size=15)
    d.seta([(240, 666), (240, 772)], cor=TEXTO)
    d.rotulo("grava JSONL\npor amostra", 160, 700, 100, size=14)
    al = d.no("alert", "Alerta local\nops/alerts/ + console", 560, 820,
              w=220, size=15)
    d.seta([(mon["x"] + ICON + 8, 820), (al["x"] - 8, 820)], cor=ALERTA,
           tracejada=True, largura=3)
    d.rotulo("pass rate < 0,90:\nwarning · ACL: critical", 400, 768, 190,
             size=14, cor=ALERTA)
    d.texto("Sem Alertmanager: o alerta\naparece em ops/alerts e\n"
            "como FIRING no Prometheus.", 450, 930, 330, size=14,
            cor=CINZA, align="left")

    # --- AWS: release publicado na 5.1 (opcional) ---
    d.caixa(1960, 140, 330, 400, "", AWS, tracejada=True)
    d.imagem("aws-cloud", 1974, 150, 36)
    d.texto("AWS · Demo 5.1\n(opcional)", 2016, 152, 260, size=16,
            cor=AWS, align="left")
    lam = d.no("lambda", "AWS Lambda\nqc-assistant-api\nalias live",
               2125, 250, w=260, size=15)
    d.texto("Só para comparar\nversões. O Collector\nlocal não recebe\n"
            "telemetria da Lambda.", 1990, 400, 280, size=15, cor=CINZA)

    # --- Faixa inferior: o loop de aprendizagem ---
    passos = [
        ("Release", "pipeline da 5.1\npromove o live"),
        ("Telemetria", "traces, métricas\ne logs JSON"),
        ("Avaliação", "probes rotulados\n(proxy sintético)"),
        ("Alerta", "pass rate < 0,90\nou ACL violada"),
        ("Aprendizagem", "runbook e\npostmortem"),
        ("Novo gate", "caso novo no\ngolden set"),
        ("Próximo release", "volta para a 5.1\ncom o gate novo"),
    ]
    bw, gap, x0, y0 = 258, 50, 70, 1150
    for i, (t, c) in enumerate(passos):
        x = x0 + i * (bw + gap)
        d._base("rectangle", x, y0, bw, 96, strokeColor=TEXTO,
                backgroundColor=FUNDO)
        d.texto(t, x, y0 + 12, bw, size=18)
        d.texto(c, x, y0 + 42, bw, size=14, cor=CINZA)
        if i < len(passos) - 1:
            d.seta([(x + bw + 6, y0 + 48), (x + bw + gap - 8, y0 + 48)],
                   cor=TEXTO, largura=2)
    xl = x0 + 6 * (bw + gap) + bw / 2
    d.seta([(xl, y0 + 100), (xl, y0 + 150), (x0 + bw / 2, y0 + 150),
            (x0 + bw / 2, y0 + 104)], cor=TEXTO, largura=2)
    d.rotulo("o gate novo entra no pipeline da Demo 5.1", 1120, y0 + 138,
             360, size=15)

    # --- Legenda ---
    y = 1350
    d.seta([(70, y), (130, y)], cor=TEXTO, largura=3)
    d.texto("requisição HTTP real", 142, y - 10, 220, size=16, align="left")
    d.seta([(390, y), (450, y)], cor=OTLP, tracejada=True)
    d.texto("telemetria OTLP", 462, y - 10, 200, size=16, cor=OTLP,
            align="left")
    d.seta([(660, y), (720, y)], tracejada=True)
    d.texto("controle do instrutor / comparação opcional", 732, y - 10, 440,
            size=16, cor=CINZA, align="left")
    d.seta([(1200, y), (1260, y)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("alerta", 1272, y - 10, 80, size=16, cor=ALERTA, align="left")
    d.texto("Sem LLM · pedidos fictícios (mock) · tráfego sintético "
            "marcado test_sample", 1390, y - 10, 900, size=16, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-operacao.excalidraw"))


if __name__ == "__main__":
    release_rag()
    operacao()
