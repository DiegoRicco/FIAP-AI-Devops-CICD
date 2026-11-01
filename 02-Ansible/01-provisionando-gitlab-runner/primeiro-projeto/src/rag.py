"""Assistente de Politicas da Quantum Commerce: RAG extrativo, sem LLM.

Recupera (R) trechos das politicas versionadas em politicas/*.md e responde
copiando as frases do melhor trecho, sempre com a fonte citada. Nao ha modelo
gerando texto: o assistente nunca inventa regra, prazo ou cupom.

O status de um pedido NUNCA vem daqui: ele e lido no DynamoDB pelo handler.
Este modulo so explica o que cada status significa, segundo a politica.
"""
import hashlib
import math
import os
import re
import unicodedata

PASTA_POLITICAS = os.path.join(os.path.dirname(__file__), "politicas")

# Abaixo deste score BM25 o trecho e parecido demais com "nada": melhor recusar
# do que responder com uma politica que nao tem a ver com a pergunta.
LIMIAR_SCORE = 3.0

RECUSA = (
    "Não encontrei isso nas políticas da Quantum Commerce. "
    "Posso ajudar com trocas, devoluções, entrega e frete, garantia, Black Friday "
    "e status de pedidos."
)
RECUSA_INJECAO = (
    "Não posso mudar as regras do atendimento nem criar cupons ou códigos. "
    "Posso ajudar com dúvidas sobre as políticas da Quantum Commerce."
)
PEDE_NUMERO = (
    "Para consultar o status, preciso do número do pedido. "
    "Envie a pergunta de novo com o campo pedido_id (ex.: QC-1001)."
)

# Palavras que aparecem em quase toda pergunta e nao ajudam a achar o trecho.
STOPWORDS = set("""
a o as os um uma uns umas de da do das dos em no na nos nas ao aos para pra por
pelo pela pelos pelas com sem e ou que se me mim eu meu minha meus minhas voce
voces seu sua seus suas ele ela eles elas isso esse essa este esta isto qual quais
quanto quantos quanta quantas como quando onde porque ja nao sim mais muito tem
ter foi ser sao era vou vai posso pode podem quero queria gostaria saber preciso
quem tenho
""".split())

# Pergunta de status: a resposta depende do pedido, nao da politica.
PADROES_STATUS = re.compile(
    r"onde esta|cade|rastre|status do (meu )?pedido|situacao do (meu )?pedido|"
    r"meu pedido (ja )?(saiu|chegou|foi enviado)"
)

# Tentativas de mudar o comportamento do assistente (prompt injection). Sem LLM
# o ataque nao "funciona", mas recusar explicitamente deixa o gate testavel.
PADROES_INJECAO = re.compile(
    r"ignor\w* (as |todas as |suas )?(instruc|regras)|esquec\w* (as |suas )?(instruc|regras)|"
    r"desconsider\w*|voce agora e|finja|system prompt|modo desenvolvedor"
)

# Sufixos removidos no stemming leve, do mais longo para o mais curto, para que
# "troca", "trocas" e "trocar" virem o mesmo radical "troc".
SUFIXOS = (
    "amento", "imento", "mente", "coes", "cao", "idade", "ando", "endo", "indo",
    "adas", "idas", "ados", "idos", "ada", "ida", "ado", "ido", "ei", "ar", "er", "ir", "as", "es", "os", "is",
    "a", "e", "o", "i", "s",
)


def sem_acento(texto):
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def radical(palavra):
    for sufixo in SUFIXOS:
        # Radical com menos de 3 letras junta palavras sem relacao ("dias" -> "di").
        if palavra.endswith(sufixo) and len(palavra) - len(sufixo) >= 3:
            return palavra[: -len(sufixo)]
    return palavra


def normalizar(texto):
    """Texto -> lista de termos: minusculas, sem acento, sem stopword, com radical."""
    palavras = re.findall(r"[a-z0-9]+", sem_acento(texto))
    return [radical(p) for p in palavras if p not in STOPWORDS]


def dividir_secoes(nome_documento, markdown):
    """Cada secao '## Titulo' vira um trecho recuperavel, com o titulo como citacao."""
    secoes = []
    titulo, linhas = None, []
    for linha in markdown.splitlines() + ["## fim"]:
        if linha.startswith("## "):
            if titulo and " ".join(linhas).strip():
                secoes.append({
                    "documento": nome_documento,
                    "secao": titulo,
                    "texto": " ".join(l.strip() for l in linhas if l.strip()),
                })
            titulo, linhas = linha[3:].strip(), []
        elif titulo:
            linhas.append(linha)
    return secoes


class IndiceBM25:
    """BM25 classico (Okapi). k1 controla a saturacao da frequencia do termo; b, o
    peso do tamanho do trecho (trecho longo nao ganha so por ter mais palavras)."""

    def __init__(self, secoes, k1=1.5, b=0.75):
        self.secoes, self.k1, self.b = secoes, k1, b
        # O titulo entra duas vezes: ele resume a secao e deve pesar mais.
        self.docs = [normalizar(s["secao"] + " " + s["secao"] + " " + s["texto"]) for s in secoes]
        self.media = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        self.df = {}
        for doc in self.docs:
            for termo in set(doc):
                self.df[termo] = self.df.get(termo, 0) + 1

    def idf(self, termo):
        n, df = len(self.docs), self.df.get(termo, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def buscar(self, pergunta, k=3):
        termos = normalizar(pergunta)
        resultados = []
        for secao, doc in zip(self.secoes, self.docs):
            score = 0.0
            for termo in set(termos):
                tf = doc.count(termo)
                if tf:
                    peso = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(doc) / self.media))
                    score += self.idf(termo) * peso
            if score > 0:
                resultados.append((score, secao))
        resultados.sort(key=lambda r: -r[0])
        return resultados[:k]


def carregar_politicas(pasta=PASTA_POLITICAS):
    secoes, digest = [], hashlib.sha256()
    for nome in sorted(os.listdir(pasta)):
        if not nome.endswith(".md"):
            continue
        with open(os.path.join(pasta, nome), encoding="utf-8") as arquivo:
            conteudo = arquivo.read()
        digest.update(nome.encode() + conteudo.encode())
        secoes.extend(dividir_secoes(nome, conteudo))
    # Versao curta das politicas: muda sempre que um .md muda, entao cada resposta
    # diz exatamente qual versao da base foi usada (rastreabilidade).
    return secoes, digest.hexdigest()[:12]


# Montado uma vez por cold start da Lambda: as politicas viajam dentro do zip.
SECOES, VERSAO_POLITICAS = carregar_politicas()
INDICE = IndiceBM25(SECOES)


def extrair_resposta(pergunta, secao):
    """Resposta extrativa: a 1a frase da secao (onde a politica escreve a regra)
    mais a frase que mais se parece com a pergunta. Nenhuma palavra e inventada."""
    frases = re.split(r"(?<=[.!?])\s+", secao["texto"])
    termos = set(normalizar(pergunta))
    melhor = max(
        frases[1:],
        key=lambda f: sum(INDICE.idf(t) for t in termos & set(normalizar(f))),
        default=None,
    )
    escolhidas = [frases[0]] + ([melhor] if melhor else [])
    return " ".join(escolhidas) + f" Fonte: {secao['documento']} ({secao['secao']})."


def significado_status(status):
    for secao in SECOES:
        if secao["documento"] == "status-do-pedido.md" and secao["secao"] == status:
            return secao
    return None


def responder(pergunta, pedido=None):
    """pedido: None ou {"pedido_id", "encontrado", "status"} lido no DynamoDB."""
    base = {"versao_politicas": VERSAO_POLITICAS, "pedido": pedido}
    texto = sem_acento(pergunta)

    if PADROES_INJECAO.search(texto):
        return {**base, "resposta": RECUSA_INJECAO, "fontes": [], "fora_do_escopo": True}

    if pedido and pedido["encontrado"]:
        secao = significado_status(pedido["status"])
        if secao:
            resposta = (
                f"O pedido {pedido['pedido_id']} está com status {pedido['status']}. "
                + extrair_resposta(pergunta, secao)
            )
            fonte = {"documento": secao["documento"], "secao": secao["secao"], "score": None}
            return {**base, "resposta": resposta, "fontes": [fonte], "fora_do_escopo": False}

    if PADROES_STATUS.search(texto):
        if pedido:
            resposta = f"Não encontrei o pedido {pedido['pedido_id']}. Confira o número e tente de novo."
        else:
            resposta = PEDE_NUMERO
        return {**base, "resposta": resposta, "fontes": [], "fora_do_escopo": False}

    resultados = INDICE.buscar(pergunta)
    if not resultados or resultados[0][0] < LIMIAR_SCORE:
        return {**base, "resposta": RECUSA, "fontes": [], "fora_do_escopo": True}

    fontes = [
        {"documento": s["documento"], "secao": s["secao"], "score": round(score, 2)}
        for score, s in resultados
    ]
    return {
        **base,
        "resposta": extrair_resposta(pergunta, resultados[0][1]),
        "fontes": fontes,
        "fora_do_escopo": False,
    }
