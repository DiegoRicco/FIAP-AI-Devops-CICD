"""Testes unitarios da recuperacao: cada peca do RAG isolada, sem AWS."""
import rag


def test_normalizar_tira_acento_maiuscula_e_stopword():
    assert rag.normalizar("Qual é o PRAZO da Devolução?") == ["praz", "devolu"]


def test_radical_junta_variacoes_da_mesma_palavra():
    assert rag.radical("troca") == rag.radical("trocas") == rag.radical("trocar")


def test_dividir_secoes_usa_titulo_como_citacao():
    md = "# Titulo do documento\n\n## Prazo\n\nTexto um.\n\n## Vazia\n\n## Outra\nTexto dois.\n"
    secoes = rag.dividir_secoes("x.md", md)
    assert [s["secao"] for s in secoes] == ["Prazo", "Outra"]
    assert secoes[0] == {"documento": "x.md", "secao": "Prazo", "texto": "Texto um."}


def test_bm25_prefere_o_trecho_que_fala_do_assunto():
    secoes = [
        {"documento": "a.md", "secao": "Frete", "texto": "O frete e gratis acima de R$ 299."},
        {"documento": "b.md", "secao": "Garantia", "texto": "A garantia legal e de 90 dias."},
    ]
    indice = rag.IndiceBM25(secoes)
    score, melhor = indice.buscar("quanto tempo de garantia?")[0]
    assert melhor["secao"] == "Garantia" and score > 0


def test_base_de_politicas_carregada_no_cold_start():
    documentos = {s["documento"] for s in rag.SECOES}
    assert len(documentos) == 6
    assert len(rag.VERSAO_POLITICAS) == 12


def test_resposta_cita_a_fonte():
    r = rag.responder("Qual a garantia de um notebook?")
    assert r["fora_do_escopo"] is False
    assert r["fontes"][0]["documento"] == "garantia-eletronicos.md"
    assert "Fonte: garantia-eletronicos.md" in r["resposta"]


def test_pergunta_fora_do_escopo_e_recusada():
    r = rag.responder("Qual a melhor receita de bolo de chocolate?")
    assert r["fora_do_escopo"] is True
    assert r["fontes"] == []
    assert r["resposta"].startswith("Não encontrei isso nas políticas da Quantum Commerce")


def test_pergunta_de_status_sem_pedido_pede_o_numero():
    r = rag.responder("Onde está meu pedido?")
    assert "número do pedido" in r["resposta"]
    assert r["fora_do_escopo"] is False and r["fontes"] == []


def test_status_vem_do_pedido_e_significado_da_politica():
    pedido = {"pedido_id": "QC-1001", "encontrado": True, "status": "em_transito"}
    r = rag.responder("Onde está meu pedido?", pedido)
    assert "QC-1001 está com status em_transito" in r["resposta"]
    assert r["fontes"] == [{"documento": "status-do-pedido.md", "secao": "em_transito", "score": None}]
