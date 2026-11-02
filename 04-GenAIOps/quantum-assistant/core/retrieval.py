"""Retriever BM25 em portugues, com filtros de vigencia, status, tenant e acesso.

Por que BM25 e nao embeddings: e legivel (da para explicar o score na lousa),
roda sem dependencia e sem GPU, e e deterministico: mesma pergunta, mesmo ranking.
"""
import math
import re
import unicodedata

# Palavras que aparecem em quase toda pergunta e nao ajudam a achar o trecho.
STOPWORDS = set("""
a o as os um uma uns umas de da do das dos em no na nos nas ao aos para pra por
pelo pela pelos pelas com sem e ou que se me mim eu meu minha meus minhas voce
voces seu sua seus suas ele ela eles elas isso esse essa este esta isto qual quais
quanto quantos quanta quantas como quando onde porque ja nao sim mais muito tem
ter foi ser sao era vou vai posso pode podem quero queria gostaria saber preciso
quem tenho ainda quantum commerce loja
""".split())
# "quantum", "commerce" e "loja" aparecem em todo documento: so somariam ruido.
# "ainda" puxava a secao "confirmado" (que repete "ainda") em perguntas de troca.

# Stem leve: corta o sufixo mais longo para "troca", "trocas" e "trocar" virarem
# o mesmo radical. Nao e um stemmer completo; e o suficiente e da para ler.
SUFFIXES = (
    "amento", "imento", "mente", "coes", "cao", "idade", "ando", "endo", "indo",
    "adas", "idas", "ados", "idos", "ada", "ida", "ado", "ido", "ei", "ar", "er", "ir",
    "as", "es", "os", "is", "a", "e", "o", "i", "s",
)


def strip_accents(text):
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def stem(word):
    for suffix in SUFFIXES:
        # Radical com menos de 3 letras junta palavras sem relacao ("dias" -> "di").
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokens(text):
    """Texto -> termos: minusculas, sem acento, sem stopword, com radical."""
    return [stem(w) for w in re.findall(r"[a-z0-9]+", strip_accents(text)) if w not in STOPWORDS]


def check_access(chunk, as_of_date, tenant, user_role, filters):
    """Devolve None se o chunk pode ser usado, ou o motivo do bloqueio.

    Cada filtro pode ser desligado em config/settings.json justamente para a
    aula mostrar o que acontece quando alguem remove uma protecao.
    """
    if filters.get("require_active", True) and chunk["status"] != "active":
        return "status"
    if filters.get("respect_effective_date", True):
        if chunk["effective_date"] > as_of_date:
            return "future"
        if chunk["expires_date"] and chunk["expires_date"] < as_of_date:
            return "expired"
    if filters.get("enforce_tenant", True) and chunk["tenant"] != tenant:
        return "tenant"
    if filters.get("enforce_access_level", True) and chunk["access_level"] != "public":
        # Conteudo interno so para quem atende (agent); cliente nunca ve.
        if user_role not in ("agent", "admin"):
            return "access_level"
    return None


class BM25Index:
    """BM25 classico (Okapi). k1 controla a saturacao da frequencia do termo; b,
    o peso do tamanho do trecho (trecho longo nao ganha so por ter mais palavras)."""

    def __init__(self, chunks, k1=1.5, b=0.75, title_boost=2):
        self.chunks, self.k1, self.b = chunks, k1, b
        # O titulo da secao entra repetido: ele resume a regra e deve pesar mais.
        self.docs = [tokens((c["section"] + " ") * title_boost + c["text"]) for c in chunks]
        self.avg_len = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        self.df = {}
        for doc in self.docs:
            for term in set(doc):
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term):
        n, df = len(self.docs), self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query_terms, position):
        doc, total = self.docs[position], 0.0
        for term in set(query_terms):
            tf = doc.count(term)
            if tf:
                norm = self.k1 * (1 - self.b + self.b * len(doc) / self.avg_len)
                total += self.idf(term) * tf * (self.k1 + 1) / (tf + norm)
        return total


def filter_chunks(index, *, as_of_date, tenant, user_role, filters):
    """Filtra ANTES de ranquear: um chunk bloqueado nunca ocupa vaga do top-k.

    Retorna (posicoes_permitidas, bloqueados_por_motivo).
    """
    allowed, blocked = [], {}
    for position, chunk in enumerate(index.chunks):
        reason = check_access(chunk, as_of_date, tenant, user_role, filters)
        if reason:
            blocked[reason] = blocked.get(reason, 0) + 1
        else:
            allowed.append(position)
    return allowed, blocked


def rank(index, question, allowed, *, k, corpus_version):
    """Ranqueia os chunks permitidos. Saida: doc_id, chunk_id, corpus_version, score."""
    terms = tokens(question)
    scored = []
    for position in allowed:
        value = index.score(terms, position)
        if value > 0:
            scored.append((value, index.chunks[position]))
    # Desempate pelo chunk_id: garante o mesmo ranking em qualquer maquina.
    scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
    return [
        {
            "doc_id": chunk["doc_id"],
            "chunk_id": chunk["chunk_id"],
            "corpus_version": corpus_version,
            "score": round(value, 4),
            "chunk": chunk,
        }
        for value, chunk in scored[:k]
    ]


def search(index, question, *, as_of_date, tenant, user_role, filters, k, corpus_version):
    allowed, blocked = filter_chunks(index, as_of_date=as_of_date, tenant=tenant,
                                     user_role=user_role, filters=filters)
    return rank(index, question, allowed, k=k, corpus_version=corpus_version), blocked
