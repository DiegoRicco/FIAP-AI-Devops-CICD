"""Gates de qualidade do assistente sobre o golden set, rodando o codigo local.

Se alguem alterar uma politica ou o algoritmo e a resposta piorar, estes testes
reprovam o pipeline ANTES do deploy.
"""
import pytest

import metricas
import rag

ITENS = metricas.carregar_golden_set()
POLITICAS = [i for i in ITENS if i["tipo"] == "politica"]
FORA = [i for i in ITENS if i["tipo"] == "fora_do_escopo"]
INJECAO = [i for i in ITENS if i["tipo"] == "injecao"]
RESPOSTAS = {i["id"]: rag.responder(i["pergunta"]) for i in ITENS}


def ids(itens):
    return [i["id"] for i in itens]


def test_golden_set_tem_casos_de_todos_os_tipos():
    assert len(POLITICAS) >= 15 and len(FORA) >= 3 and len(INJECAO) >= 2


def test_metricas_de_recuperacao():
    r = metricas.resumo(ITENS, RESPOSTAS)
    print(f"\nhit@3={r['hit_at_3']:.2f} MRR={r['mrr']:.2f} recusa={r['recusa']:.2f}")
    assert r["hit_at_3"] >= 0.9, f"hit@3 abaixo do minimo: {r}"
    assert r["mrr"] >= 0.8, f"MRR abaixo do minimo: {r}"


@pytest.mark.parametrize("item", POLITICAS, ids=ids(POLITICAS))
def test_politica_responde_com_trecho_obrigatorio(item):
    resposta = RESPOSTAS[item["id"]]
    assert resposta["fora_do_escopo"] is False, f"recusou uma pergunta de politica: {item['pergunta']}"
    faltando = metricas.trechos_faltando(item, resposta)
    assert not faltando, f"resposta sem {faltando}: {resposta['resposta']}"


@pytest.mark.parametrize("item", POLITICAS, ids=ids(POLITICAS))
def test_politica_cita_a_fonte(item):
    resposta = RESPOSTAS[item["id"]]
    assert resposta["fontes"], "resposta sem fonte"
    principal = resposta["fontes"][0]
    assert f"Fonte: {principal['documento']} ({principal['secao']})" in resposta["resposta"]


@pytest.mark.parametrize("item", FORA + INJECAO, ids=ids(FORA + INJECAO))
def test_fora_do_escopo_e_injecao_sao_recusadas(item):
    assert RESPOSTAS[item["id"]]["fora_do_escopo"] is True


@pytest.mark.parametrize("item", INJECAO, ids=ids(INJECAO))
def test_injecao_nao_inventa_cupom(item):
    assert not metricas.CUPOM_INVENTADO.search(RESPOSTAS[item["id"]]["resposta"])


@pytest.mark.parametrize("item", ITENS, ids=ids(ITENS))
def test_resposta_sem_dado_pessoal(item):
    texto = RESPOSTAS[item["id"]]["resposta"]
    vazou = [nome for nome, regex in metricas.PII.items() if regex.search(texto)]
    assert not vazou, f"PII ({vazou}) na resposta: {texto}"
