#!/usr/bin/env python3
"""Gera arquitetura-rag-pipeline.excalidraw (modulo 03-CICD).

Uso:  python3 gerar.py
Render: abra arquitetura-rag-pipeline.excalidraw em https://excalidraw.com e
        exporte como PNG para ../01-Primeiro-pipeline/img/arquitetura-rag-pipeline.png

Icones OFICIAIS em assets/ (SVG fonte + PNG 160px embutido, porque SVG
embutido nao rasteriza no render headless).

Licoes de UX aplicadas: L1 acentuacao, L3 verbo com sujeito na origem,
L5 rotulo de aresta explicito, L6 empresa ficticia visivel, L8 fluxo de apoio
tracejado, L9/L18 rotulo de seta com fundo branco opaco, L10 so o caminho de
reprovacao em cor de alerta, L13 magenta so no titulo, L19 ponta de seta
nunca sobre borda de container.
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
ALERTA = "#C92A2A"      # so o caminho de reprovacao (L10)

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

    def rotulo(self, t, cx, y, w, size=16, cor=CINZA):
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

    def no(self, icone, rotulo, cx, cy, w=260, size=18):
        self.imagem(icone, cx - ICON / 2, cy - ICON / 2, ICON)
        self.texto(rotulo, cx - w / 2, cy + ICON / 2 + 10, w, size=size)
        return {"x": cx - ICON / 2, "y": cy - ICON / 2, "w": ICON, "h": ICON,
                "cx": cx, "cy": cy}

    def caixa(self, x, y, w, h, titulo, cor, bg="transparent", icone=None,
              tracejada=False, size=17):
        self._base("rectangle", x, y, w, h, strokeColor=cor,
                   backgroundColor=bg,
                   strokeStyle="dashed" if tracejada else "solid",
                   roundness=None)
        tx = x + 14
        if icone:
            self.imagem(icone, x, y, 40)
            tx = x + 50
        self.texto(titulo, tx, y + 10, w - (tx - x) - 10, size=size, cor=cor,
                   align="left")

    def seta(self, pontos, cor=CINZA, tracejada=False, largura=2):
        x0, y0 = pontos[0]
        rel = [[px - x0, py - y0] for px, py in pontos]
        xs = [p[0] for p in rel]
        ys = [p[1] for p in rel]
        self._base("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
                   strokeColor=cor, strokeWidth=largura,
                   strokeStyle="dashed" if tracejada else "solid",
                   roundness=None, points=rel, lastCommittedPoint=None,
                   startBinding=None, endBinding=None, startArrowhead=None,
                   endArrowhead="arrow")

    def salvar(self, caminho):
        doc = {"type": "excalidraw", "version": 2,
               "source": "quantum-commerce-gerador",
               "elements": self.elements,
               "appState": {"gridSize": None,
                            "viewBackgroundColor": "#ffffff"},
               "files": self.files}
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        log(f"gerado: {caminho} ({len(self.elements)} elementos)")



    def losango(self, cx, cy, w, h, t, cor=TEXTO, size=15):
        self._base("diamond", cx - w / 2, cy - h / 2, w, h, strokeColor=cor,
                   backgroundColor="#fff5f5")
        self.texto(t, cx - w / 2, cy - size * 1.25, w, size=size, cor=cor)

    def etapa(self, x, y, w, h, titulo, corpo, icone=None):
        self._base("rectangle", x, y, w, h, strokeColor=TEXTO,
                   backgroundColor="#ffffff")
        tx = x + 16
        if icone:
            self.imagem(icone, x + 12, y + 12, 44)
            tx = x + 66
        self.texto(titulo, tx, y + 12, w - (tx - x) - 10, size=19,
                   align="left")
        self.texto(corpo, tx, y + 42, w - (tx - x) - 10, size=15, cor=CINZA,
                   align="left")


def arquitetura():
    d = Diagrama()
    d.texto("Quantum Commerce · Módulo 03: o pipeline que entrega o "
            "Assistente de Políticas", 60, 30, 2200, size=30, cor=MAGENTA,
            align="left")
    d.texto("Empresa fictícia. O que reprova no validate não chega à AWS; "
            "o que reprova na avaliação pós-deploy deixa o pipeline vermelho "
            "e avisa o time antes do cliente perceber.",
            60, 76, 2200, size=19, cor=CINZA, align="left")

    # --- Esquerda: desenvolvimento ---
    d.caixa(60, 140, 380, 880, "Desenvolvimento", TEXTO, bg="#f8f9fa")
    d.no("user", "Você\nAI DevOps Engineer", 250, 240)
    d.seta([(250, 336), (250, 412)], cor=TEXTO, largura=3)
    d.rotulo("git push", 180, 362, 90)
    repo = d.no("gitlab", "Repositório GitLab\nprimeiro-projeto", 250, 460)
    d.texto("• código da Lambda (Python)\n• políticas em Markdown\n"
            "   (ex.: trocas.md)\n• golden set de perguntas\n"
            "• .gitlab-ci.yml (os 4 stages)\n• Terraform da infraestrutura",
            90, 570, 340, size=15, cor=CINZA, align="left")
    d.no("gitlab", "Aba Tests do GitLab\nmostra o que passou\ne o que "
         "falhou", 250, 820, w=300, size=16)

    # --- Centro: pipeline ---
    d.caixa(520, 140, 900, 880, "Pipeline do GitLab CI/CD", GITLAB,
            icone="gitlab")
    d.imagem("ec2", 560, 196, 48)
    d.texto("roda no GitLab Runner: EC2 quantum-ai-gitlab-runner\n"
            "(configurada pelo Ansible no módulo 02)", 618, 198, 470,
            size=15, align="left")

    d.etapa(560, 280, 520, 190, "1 · validate",
            "terraform fmt · tflint · checkov · terraform test\n"
            "pytest offline com o golden set:\n"
            "hit@3 · recusa · injeção de prompt · PII\n"
            "(nada é criado na AWS ainda)")
    d.seta([(820, 474), (820, 491)], cor=TEXTO, largura=3)
    d.losango(820, 535, 170, 84, "quality\ngate", cor=ALERTA)
    d.seta([(820, 579), (820, 601)], cor=TEXTO, largura=3)
    d.texto("passou", 832, 578, 80, size=15, cor=CINZA, align="left")
    d.seta([(907, 535), (962, 535)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("reprovou: o pipeline para\ne nada chega à AWS", 970, 515,
            260, size=15, cor=ALERTA, align="left")
    d.etapa(560, 605, 520, 90, "2 · plan",
            "terraform plan -out planfile", icone="terraform")
    d.seta([(820, 699), (820, 721)], cor=TEXTO, largura=3)
    d.etapa(560, 725, 520, 90, "3 · apply",
            "terraform apply planfile", icone="terraform")
    d.seta([(820, 819), (820, 841)], cor=TEXTO, largura=3)
    d.etapa(560, 845, 520, 100, "4 · avaliacao (avaliação pós-deploy)",
            "o golden set de novo, agora contra\no endpoint real")
    # o deploy ja aconteceu: reprovar aqui nao desfaz, avisa o time (L10)
    d.seta([(600, 949), (600, 990)], cor=ALERTA, tracejada=True, largura=3)
    d.texto("reprovou: pipeline vermelho, o time corrige e faz novo push",
            614, 968, 560, size=15, cor=ALERTA, align="left")

    # cada push dispara o pipeline (L19: ponta no stage, nao na borda)
    d.seta([(repo["x"] + ICON + 6, 455), (552, 375)], cor=TEXTO, largura=3)
    d.rotulo("cada push\ndispara", 375, 380, 70, size=15)
    # relatorios JUnit voltam ao GitLab (apoio)
    d.seta([(556, 450), (475, 450), (475, 815), (300, 815)],
           tracejada=True)
    d.rotulo("relatórios JUnit", 385, 776, 125, size=15)

    # --- Direita: AWS ---
    d.caixa(1480, 140, 800, 880, "AWS · us-east-1 (Learner Lab)", AWS,
            icone="aws-cloud")
    # LabRole dentro da AWS: e dela que o runner tira as credenciais
    d.imagem("iam", 1600, 360, ICON)
    d.texto("AWS IAM · LabRole\ncredenciais que o runner usa para criar\n"
            "os recursos (nenhuma chave AWS no GitLab)", 1692, 368, 420,
            size=16, align="left")
    d.imagem("s3", 1600, 610, ICON)
    d.texto("Amazon S3 · base-config-<seu RM>\nguarda o state do Terraform",
            1692, 628, 330, size=16, align="left")
    d.seta([(1088, 650), (1592, 650)], tracejada=True)
    d.rotulo("plan e apply gravam o state", 1340, 616, 210, size=15)
    # apply tambem grava o state: linha que se junta a seta do plan
    d._base("line", 1088, 740, 42, 90, strokeColor=CINZA,
            strokeStyle="dashed", points=[[0, 0], [42, 0], [42, -90]],
            lastCommittedPoint=None, startBinding=None, endBinding=None,
            startArrowhead=None, endArrowhead=None)

    api = d.no("apigateway", "", 1600, 860)
    d.texto("Amazon API Gateway\n(HTTP API)", 1480, 768, 240, size=16)
    lam = d.no("lambda", "", 1880, 860)
    d.texto("AWS Lambda · Assistente de\nPolíticas (RAG extrativo)", 1735,
            768, 290, size=16)
    d.texto("busca BM25 nas políticas\nempacotadas e cita a fonte", 1765,
            912, 230, size=15, cor=CINZA)
    dyn = d.no("dynamodb", "", 2160, 860)
    d.texto("Amazon DynamoDB\nquantum-ai-pedidos", 2050, 768, 220, size=16)
    d.texto("status do pedido\n(vem da API, não do índice)", 2045, 912,
            230, size=15, cor=CINZA)
    d.seta([(api["x"] + ICON + 6, 860), (lam["x"] - 8, 860)], cor=TEXTO,
           largura=3)
    d.rotulo("invoca", 1740, 826, 60, size=15)
    d.seta([(lam["x"] + ICON + 6, 860), (dyn["x"] - 8, 860)], cor=TEXTO,
           largura=3)
    d.rotulo("lê o pedido", 2020, 826, 90, size=15)

    # pipeline -> AWS
    d.seta([(1088, 770), (api["x"] - 8, 845)], cor=TEXTO, largura=3)
    d.rotulo("cria/atualiza API Gateway,\nLambda e tabela", 1272, 712, 200,
             size=15)
    d.seta([(1088, 905), (api["x"] - 8, 878)], cor=TEXTO, largura=3)
    d.rotulo("chama o endpoint real\ncom as perguntas", 1265, 918, 170,
             size=15)

    # --- Cliente ---
    # titulo a direita: as setas pergunta/resposta descem pela esquerda
    d.caixa(1480, 1060, 800, 190, "", TEXTO, bg="#f8f9fa")
    d.texto("Cliente da Quantum Commerce", 1665, 1074, 600, size=17,
            align="left")
    d.imagem("user", 1560, 1110, ICON)
    d.seta([(1585, 1104), (1585, 908)], cor=TEXTO, largura=3)
    d.seta([(1615, 908), (1615, 1104)], cor=TEXTO, largura=3)
    d.rotulo("pergunta", 1530, 950, 80, size=15)
    d.rotulo("resposta\n+ fonte", 1672, 940, 80, size=15)
    d.texto("pergunta: “Posso trocar um tênis depois de 20 dias?”",
            1665, 1124, 600, size=17, align="left")
    d.texto("resposta com a fonte: trocas.md › Prazo para troca", 1665,
            1176, 600, size=17, align="left")

    # --- Legenda ---
    d.caixa(60, 1060, 1360, 190, "Legenda", TEXTO, bg="#f8f9fa")
    d.texto("Sem LLM nesta aula: o assistente recupera e cita. A geração "
            "com LLM e a avaliação com juiz chegam na Aula 5.", 80, 1100,
            1320, size=19, align="left")
    d.seta([(80, 1172), (140, 1172)], cor=TEXTO, largura=3)
    d.texto("fluxo principal", 155, 1162, 200, size=16, align="left")
    d.seta([(400, 1172), (460, 1172)], tracejada=True)
    d.texto("apoio (state, relatórios)", 475, 1162, 260, size=16,
            cor=CINZA, align="left")
    d.seta([(780, 1172), (840, 1172)], cor=ALERTA, tracejada=True,
           largura=3)
    d.texto("reprovação: o pipeline para", 855, 1162, 300, size=16,
            cor=ALERTA, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-rag-pipeline.excalidraw"))


if __name__ == "__main__":
    arquitetura()
