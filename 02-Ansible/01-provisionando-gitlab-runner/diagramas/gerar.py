#!/usr/bin/env python3
"""Gera arquitetura-ansible-runner.excalidraw (lab 02-Ansible).

Uso:  python3 gerar.py
Render: abra arquitetura-ansible-runner.excalidraw em https://excalidraw.com e
        exporte como PNG para ../img/arquitetura-ansible-runner.png

Icones OFICIAIS em assets/ (SVG fonte + PNG 160px embutido, porque SVG
embutido nao rasteriza no render headless).

Licoes de UX aplicadas (skill excalidraw-aulas): L1 acentuacao, L3 verbo com
sujeito na origem, L5 rotulo de aresta explicito, L6 empresa ficticia visivel,
L8 fluxo de apoio tracejado, L9/L18 rotulo de seta com fundo branco opaco,
L13 magenta so no titulo, L19 ponta de seta nunca sobre borda de container.
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
VPC_ROXO = "#8C4FFF"
SUBNET_VERDE = "#7AA116"
AZUL = "#1C7ED6"        # proximo modulo: informativo, nao alerta (L13)

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


def arquitetura():
    d = Diagrama()
    d.texto("Quantum Commerce · Lab 02: onde entra o Ansible", 60, 30, 1500,
            size=30, cor=MAGENTA, align="left")
    d.texto("Empresa fictícia. O Terraform cria o servidor do GitLab Runner; "
            "o Ansible o configura, sem SSH.",
            60, 76, 1700, size=19, cor=CINZA, align="left")

    # --- Codespaces: onde o aluno digita os dois comandos ---
    d.caixa(60, 140, 340, 960, "GitHub Codespaces", TEXTO, bg="#f8f9fa",
            icone="github")
    d.no("user", "Você\nAI DevOps Engineer", 230, 250)
    tf = d.no("terraform", "(1) terraform apply", 230, 430)
    d.texto("depois que a EC2 existe,\no Ansible a configura:", 80, 620,
            300, size=15, cor=CINZA)
    an = d.no("ansible", "(2) ansible-playbook", 230, 800)

    # --- AWS ---
    d.caixa(500, 140, 1040, 960, "AWS · us-east-1 (Learner Lab)", AWS,
            icone="aws-cloud")
    d.caixa(860, 200, 490, 505,
            "VPC fiap-lab (persistente, do lab 01.2)", VPC_ROXO,
            icone="vpc-group", size=16)
    d.caixa(882, 255, 446, 430, "Subnet pública", SUBNET_VERDE,
            icone="public-subnet", size=16)
    d.caixa(905, 305, 400, 360, "Security Group (sem porta 22)", CINZA,
            tracejada=True, size=16)
    ec2 = d.no("ec2", "EC2 quantum-ai-gitlab-runner\nt3.small · Ubuntu",
               1105, 405, w=330)
    d.texto("instalado pelo Ansible:\nPython · Terraform 1.10.5 · TFLint\n"
            "Checkov · gitlab-runner", 945, 515, 320, size=15, cor=CINZA)
    # L19: o instance profile e anexado a EC2 -> mostrar DENTRO do SG, junto
    # da maquina, em vez de seta saindo do canto dos containers.
    d.imagem("iam", 925, 590, 44)
    d.texto("perfil IAM da EC2:\nLabInstanceProfile\n(libera SSM e S3)", 977, 584, 190,
            size=15, cor=TEXTO, align="left")

    s3 = d.no("s3", "Amazon S3\nbase-config-<seu RM>", 720, 580, w=280)
    ssm = d.no("ssm", "AWS Systems Manager (SSM)\nSession Manager", 720, 800,
               w=280)
    ps = d.no("parameter-store",
              "SSM Parameter Store\n/fiap/gitlab-runner/token", 1010, 980,
              w=320)

    # --- GitLab (fora da AWS) ---
    gl = d.no("gitlab", "GitLab\nprimeiro-projeto", 1930, 410, w=190)

    # L19: toda ponta de seta termina no ICONE/conteudo, dentro do SG, nunca
    # em cima de borda de container.
    # (1) Terraform cria EC2 + SG (caminho principal, solido)
    d.seta([(tf["x"] + ICON + 6, 405), (ec2["x"] - 8, 405)], cor=TEXTO,
           largura=3)
    d.rotulo("cria a EC2 e o SG", 680, 371, 190)
    # Terraform grava o state no S3 (apoio, tracejado)
    d.seta([(tf["x"] + ICON + 2, 462), (s3["x"] - 8, s3["cy"] - 6)],
           tracejada=True)
    d.rotulo("grava o state do Terraform", 620, 490, 200, size=15)

    # (2) Ansible conecta via SSM (caminho principal)
    d.seta([(an["x"] + ICON + 6, 800), (ssm["x"] - 8, 800)], cor=TEXTO,
           largura=3)
    d.rotulo("conecta via SSM\n(sem SSH)", 588, 742, 140, size=15)
    # SSM executa as tarefas na EC2: ponta sob a lista do que foi instalado
    d.seta([(ssm["x"] + ICON + 6, 800), (1180, 800), (1180, 584)],
           cor=TEXTO, largura=3)
    d.rotulo("executa as tarefas na EC2\n(inclusive o registro com o token)", 1000, 812, 250, size=15)
    # SSM usa o mesmo bucket para transferir arquivos (apoio)
    d.seta([(720, ssm["y"] - 6), (720, 684)], tracejada=True)
    d.rotulo("transfere arquivos\npelo bucket S3\n(o mesmo do state)", 834, 716, 130,
             size=15)
    # Ansible le o token no Parameter Store (lookup roda no Codespaces)
    d.seta([(230, 884), (230, 980), (ps["x"] - 8, 980)], tracejada=True)
    d.rotulo("lê o token de registro (glrt-...)", 700, 945, 250, size=15)

    # (3) Runner se registra no GitLab (ultimo passo do playbook)
    d.seta([(ec2["x"] + ICON + 8, 388), (gl["x"] - 8, 388)], cor=TEXTO,
           largura=3)
    d.rotulo("(3) registra-se no GitLab com o token\nexecutor shell · "
             "tags shell,terraform", 1715, 334, 300, size=15)
    # Proximo modulo: GitLab dispara pipeline no runner (azul = informativo)
    d.seta([(gl["x"] - 8, 428), (ec2["x"] + ICON + 8, 428)], cor=AZUL,
           tracejada=True, largura=3)
    d.rotulo("módulo 03: cada git push\ndispara um pipeline\n"
             "que roda neste runner", 1715, 446, 230, size=15, cor=AZUL)

    # --- Legenda ---
    d.caixa(1580, 700, 410, 260, "Legenda", TEXTO, bg="#f8f9fa", size=17)
    d.texto("Terraform = o que existe\n(EC2, Security Group)", 1600, 740,
            370, size=16, align="left")
    d.texto("Ansible = como está configurado\n(pacotes, runner registrado)",
            1600, 792, 370, size=16, align="left")
    d.seta([(1600, 862), (1660, 862)], cor=TEXTO, largura=3)
    d.texto("fluxo deste lab", 1675, 852, 280, size=15, align="left")
    d.seta([(1600, 892), (1660, 892)], tracejada=True)
    d.texto("apoio (state, arquivos, token)", 1675, 882, 300,
            size=15, cor=CINZA, align="left")
    d.seta([(1600, 922), (1660, 922)], cor=AZUL, tracejada=True, largura=3)
    d.texto("próximo módulo (03 · CI/CD)", 1675, 912, 300, size=15,
            cor=AZUL, align="left")

    d.salvar(os.path.join(DIR, "arquitetura-ansible-runner.excalidraw"))


if __name__ == "__main__":
    arquitetura()
