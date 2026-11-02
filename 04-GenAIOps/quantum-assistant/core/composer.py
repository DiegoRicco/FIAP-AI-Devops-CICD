"""Composer deterministico e extrativo: copia frases do corpus, sempre com citacao.

Nao ha geracao de linguagem. Os unicos textos que nao vem do corpus sao os
templates versionados em prompts/templates.json (recusas e moldura da resposta).
Texto do corpus e DADO, nunca instrucao: frase com cara de comando ao
assistente e descartada e nunca chega a resposta.
"""
import re

from .retrieval import tokens
from .route import is_injection


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def safe_sentences(chunk):
    """Separa frases legitimas das que parecem instrucao embutida no documento."""
    legit, dropped = [], 0
    for sentence in split_sentences(chunk["text"]):
        if is_injection(sentence):
            dropped += 1
        else:
            legit.append(sentence)
    return legit, dropped


def extract(question, sentences, idf):
    """A 1a frase (onde a politica escreve a regra) + a frase mais parecida com a pergunta."""
    terms = set(tokens(question))
    best = max(
        sentences[1:],
        key=lambda s: (sum(idf(t) for t in terms & set(tokens(s))), -sentences.index(s)),
        default=None,
    )
    picked = [sentences[0]]
    if best and sum(idf(t) for t in terms & set(tokens(best))) > 0:
        picked.append(best)
    return " ".join(picked)


def citation(chunk):
    return {
        "doc_id": chunk["doc_id"],
        "chunk_id": chunk["chunk_id"],
        "section": chunk["section"],
        "version": chunk["version"],
        "effective_date": chunk["effective_date"],
    }


def source_line(templates, chunk):
    return templates["source"].format(
        title=chunk["title"], section=chunk["section"],
        version=chunk["version"], effective_date=chunk["effective_date"],
    )


def compose(question, results, idf, templates, settings):
    """Retorna dict com answer, citations, abstained, abstain_reason, injection_dropped."""
    min_score = settings["retrieval"]["min_score"]
    min_terms = settings["retrieval"]["min_matched_terms"]
    question_terms = set(tokens(question))
    evidence = [
        r for r in results
        if r["score"] >= min_score
        and len(question_terms & set(tokens(r["chunk"]["section"] + " " + r["chunk"]["text"]))) >= min_terms
    ]
    if not evidence:
        return {"answer": templates["insufficient_evidence"], "citations": [], "abstained": True,
                "abstain_reason": "insufficient_evidence", "injection_dropped": 0, "evidence_category": None}
    # A rota (policy ou faq) segue o melhor trecho que E evidencia, nao um trecho
    # fraco que so apareceu no ranking.
    category = evidence[0]["chunk"]["category"]

    parts, cites, dropped_total, covered = [], [], 0, set()
    max_cites = settings["composer"]["max_citations"]
    min_new_idf = settings["composer"]["second_source_min_new_idf"]
    for r in evidence:
        chunk = r["chunk"]
        legit, dropped = safe_sentences(chunk)
        dropped_total += dropped
        if not legit:
            continue
        chunk_terms = question_terms & set(tokens(chunk["section"] + " " + chunk["text"]))
        if parts:
            # Segunda fonte so entra se responde a uma PARTE da pergunta que a
            # primeira nao cobre (ex.: "frete da devolucao E reembolso no Pix").
            new_terms = chunk_terms - covered
            if not new_terms or max(idf(t) for t in new_terms) < min_new_idf:
                continue
        parts.append(extract(question, legit, idf) + " " + source_line(templates, chunk))
        cites.append(citation(chunk))
        covered |= chunk_terms
        if len(cites) >= max_cites:
            break
    if not parts:
        # Todo trecho relevante era instrucao embutida: nao sobrou evidencia legitima.
        return {"answer": templates["injection_blocked"], "citations": [], "abstained": True,
                "abstain_reason": "injection_blocked", "injection_dropped": dropped_total,
                "evidence_category": category}
    return {"answer": " ".join(parts), "citations": cites, "abstained": False,
            "abstain_reason": None, "injection_dropped": dropped_total, "evidence_category": category}


def compose_order(order, status_chunk, templates):
    """Status vem da API (fonte transacional). O corpus so explica o significado."""
    answer = templates["order_status"].format(
        order_id=order["order_id"], status=order["status"], updated_at=order["updated_at"])
    cites = []
    if status_chunk:
        legit, _ = safe_sentences(status_chunk)
        if legit:
            answer += " " + legit[0] + " " + source_line(templates, status_chunk)
            cites.append(citation(status_chunk))
    return {"answer": answer, "citations": cites, "abstained": False, "abstain_reason": None}
