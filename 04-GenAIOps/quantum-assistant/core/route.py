"""Roteamento de intencao por regras legiveis (sem LLM, sempre reproduzivel).

Ordem das regras, da mais segura para a mais generica:
1. instrucao do usuario tentando mudar o assistente -> unsupported (injection_blocked)
2. pergunta sobre um pedido especifico              -> order_status (vai na API)
3. pergunta sem nenhum termo do dominio da loja      -> unsupported (out_of_scope)
4. resto                                             -> conhecimento documental;
   policy ou faq e decidido pela categoria do melhor trecho recuperado.
"""
import re

from .retrieval import strip_accents

# Pedidos ficticios do laboratorio usam o formato Q-1001.
ORDER_ID = re.compile(r"\bq-?(\d{4,})\b")

ORDER_PATTERNS = re.compile(
    r"onde esta|cade|rastre|status do (meu )?pedido|situacao do (meu )?pedido|"
    r"meu pedido|pedido q-?\d|o pedido (ja )?(saiu|chegou|foi)"
)

# Tentativa de mudar o comportamento do assistente. Sem LLM o ataque nao
# "funciona", mas recusar explicitamente deixa o comportamento testavel.
INJECTION = re.compile(
    r"ignor\w* (as |todas as |suas |os |o |a )?(instruc|regras|politicas|orientac)|"
    r"esquec\w* (as |suas )?(instruc|regras)|desconsider\w*|voce agora e|finja|"
    r"system prompt|modo desenvolvedor|^\s*assistente\s*,|\bassistente\s*,\s*(ignore|informe|diga|responda)"
)

# Vocabulario do dominio da loja. Se nenhum aparece, a pergunta esta fora do escopo.
DOMAIN_TERMS = (
    "troc", "devol", "reembols", "estorn", "entreg", "fret", "garanti", "defeit",
    "black friday", "cupom", "cupons", "desconto", "pedido", "compra", "comprei", "produto",
    "pagamento", "pagar", "pix", "boleto", "cartao", "parcel", "prazo", "arrepend",
    "cadastro", "conta", "senha", "nota fiscal", "atendimento", "loja", "transportadora",
    "etiqueta", "tamanho", "roupa", "eletronic", "celular", "notebook", "presente",
    "politica", "regra", "encomend", "receb", "cashback", "estorno", "atendente",
)


def normalize(text):
    return strip_accents(text or "")


def extract_order_id(question):
    match = ORDER_ID.search(normalize(question))
    return f"Q-{match.group(1)}" if match else None


def is_injection(text):
    return bool(INJECTION.search(normalize(text)))


def route_intent(question, order_id=None):
    """Retorna (intencao, order_id). intencao: order_status | unsupported | knowledge."""
    text = normalize(question)
    if is_injection(text):
        return "unsupported", None
    found = order_id or extract_order_id(question)
    if found or ORDER_PATTERNS.search(text):
        return "order_status", found
    if not any(term in text for term in DOMAIN_TERMS):
        return "unsupported", None
    return "knowledge", None
