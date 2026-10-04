"""Edição CJF (ramo `cjf-apenas`): o programa só busca o Conselho da Justiça
Federal.

O CJF não tem CNPJ próprio no PNCP — é unidade administrativa do CNPJ
guarda-chuva da Justiça Federal. O que estes testes seguram:
  · o assistente só oferece a predefinição do CJF, e qualquer pedido de
    configuração por órgãos vira ela;
  · a fase 1 consulta SÓ as unidades do CJF, uma a uma, e nunca o CNPJ
    inteiro; o catálogo de unidades do PNCP nem é buscado;
  · contratos, atas e PCA (que o portal só filtra por CNPJ) descartam o que
    não é do CJF;
  · banco e releases são separados dos da edição JF.
Sem rede: o motor é um dublê.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motor_pncp import Ata, Contrato, PlanoPca

import licitarium
import pncp

GUARDA = "00508903000188"
CJF = {"90026", "90001"}


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(licitarium.SCHEMA)
    yield con
    con.close()


@pytest.fixture(autouse=True)
def sem_espera_de_repescagem(monkeypatch):
    monkeypatch.setattr(pncp, "REPESCAGEM_PAUSA", 0)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(licitarium, "DIR_DADOS", tmp_path)
    monkeypatch.setattr(licitarium, "ARQUIVO_DB", tmp_path / "t.db")
    licitarium.abrir_db().close()
    return licitarium.Api()


def contratacao(numero, unidade, nome="UNIDADE"):
    return {
        "numeroControlePNCP": numero, "anoCompra": 2026, "sequencialCompra": 1,
        "orgaoEntidade": {"cnpj": GUARDA, "razaoSocial": "JUSTICA FEDERAL"},
        "unidadeOrgao": {"codigoUnidade": unidade, "nomeUnidade": nome},
        "modalidadeId": 6, "objetoCompra": "Objeto",
        "dataPublicacaoPncp": "2026-03-01", "dataAtualizacao": "2026-03-02",
    }


class MotorCjf:
    """Dublê: responde como o portal — por CNPJ, respeitando o filtro de
    unidade na fase 1; contratos/atas/PCA vêm do CNPJ inteiro."""

    def __init__(self):
        self.chamadas, self.catalogo_pedido = [], False
        self._base_pncp = "x"
        motor = self

        class Cliente:
            def get(self, *a, **kw):
                motor.catalogo_pedido = True
                return []
        self._cliente = Cliente()
        self.acervo = [contratacao("CJF-1", "090026"),
                       contratacao("CJF-2", "090001"),
                       contratacao("BA-1", "090012"),
                       contratacao("TRF1-1", "090027")]

    def _baixar_com_disjuntor(self, caminho, consultas, *, rotulo_fase, tipo,
                              tamanho_pagina=500):
        unidade = consultas[0][1].get("codigoUnidadeAdministrativa")
        self.chamadas.append(unidade)
        return (tipo(r) for r in self.acervo
                if unidade is None or r["unidadeOrgao"]["codigoUnidade"] == unidade)

    def refazer(self, erro): return iter(())

    def contratos(self, cnpj, inicio, fim):
        for numero, unidade in (("C-CJF", "090026"), ("C-BA", "090012")):
            yield Contrato({"numeroControlePNCP": numero,
                            "orgaoEntidade": {"cnpj": GUARDA},
                            "unidadeOrgao": {"codigoUnidade": unidade}})

    def atas(self, cnpj, inicio, fim):
        for numero, unidade in (("A-CJF", "90026"), ("A-TRF1", "90027")):
            yield Ata({"numeroControlePNCPAta": numero, "cnpjOrgao": GUARDA,
                       "codigoUnidadeOrgao": unidade})

    def pca(self, cnpj, inicio, fim):
        for id_pca, unidade in (("P-CJF", "090026"), ("P-BA", "090012")):
            yield PlanoPca({"idPcaPncp": id_pca, "anoPca": 2026,
                            "orgaoEntidadeCnpj": GUARDA, "codigoUnidade": unidade,
                            "itens": [{"numeroItem": 1, "descricaoItem": "x"}]})

    def ipca(self, inicio=None): return iter(())
    def itens_e_resultados(self, pendentes, **kw): return iter(())


def _configurar(db):
    p = pncp.PREDEFINICOES["cjf"]
    pncp.configurar_acervo_orgaos(db, p["nome"], p["uf"], p["orgaos"],
                                  p["unidades_excluidas"], p["unidades"])


# ── a edição ─────────────────────────────────────────────────────────────

def test_a_edicao_e_fixa_no_cjf():
    assert pncp.EDICAO_FIXA == "cjf"
    p = pncp.PREDEFINICOES["cjf"]
    assert [c for c, _ in p["orgaos"]] == [GUARDA]
    assert {pncp._codigo_unidade(c) for c, *_ in p["unidades"][GUARDA]} == CJF


def test_assistente_so_oferece_o_cjf(api):
    grupos = api.predefinicoes()
    assert [g["chave"] for g in grupos] == ["cjf"]
    assert grupos[0]["fixa"] is True
    assert "SECRETARIA DO CONSELHO DA JUSTICA FEDERAL-DF" in grupos[0]["unidades"]


def test_qualquer_configuracao_por_orgaos_vira_o_cjf(api, monkeypatch):
    def nao_pode(cnpj):
        raise AssertionError("a edição CJF não valida CNPJ digitado")
    monkeypatch.setattr(pncp, "consultar_orgao", nao_pode)
    for pedido in ({"predefinicao": "jf"},
                   {"nome": "Outro", "cnpjs": "59949362000176"}):
        assert api.configurar_orgaos(**pedido, desde=2026) == {"ok": True}
        db = licitarium.abrir_db()
        try:
            assert pncp.unidades_somente(db) == CJF
            assert [r[0] for r in db.execute("SELECT cnpj FROM orgaos")] == [GUARDA]
            assert pncp._config(db, "municipio_nome") == "Conselho da Justiça Federal"
            assert pncp._config(db, "inicio_coleta") == "2026-01-01"
        finally:
            db.close()
    assert api.get_estado()["edicao"].startswith("CJF.")


# ── a coleta ─────────────────────────────────────────────────────────────

def test_fase_1_consulta_so_as_unidades_do_cjf_e_nunca_o_cnpj_inteiro(db):
    _configurar(db)
    motor = MotorCjf()
    mensagens = []
    resumo = pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor,
                                   progresso=mensagens.append)
    assert sorted(motor.chamadas) == ["090001", "090026"]
    assert motor.catalogo_pedido is False
    assert resumo["contratacoes"] == 2
    assert {r[0] for r in db.execute("SELECT numero_controle FROM contratacoes")
            } == {"CJF-1", "CJF-2"}
    assert any("(unidade 1 de 2)" in m for m in mensagens)


def test_contratos_atas_e_pca_descartam_o_que_nao_e_do_cjf(db):
    _configurar(db)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=MotorCjf())
    assert [r[0] for r in db.execute("SELECT numero_controle FROM contratos")] == ["C-CJF"]
    assert [r[0] for r in db.execute("SELECT numero_controle FROM atas")] == ["A-CJF"]
    assert [r[0] for r in db.execute("SELECT id_pca FROM pca_itens")] == ["P-CJF"]
    # e nenhuma unidade de fora entra no catálogo
    assert {r[0] for r in db.execute("SELECT codigo FROM unidades")} == CJF


def test_unidade_do_cjf_desligada_deixa_de_ser_consultada(db):
    _configurar(db)
    db.execute("UPDATE unidades SET ativo=0 WHERE codigo='90001'")
    db.commit()
    motor = MotorCjf()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert motor.chamadas == ["090026"]


def test_sem_nenhuma_unidade_ligada_nao_cai_no_cnpj_inteiro(db):
    _configurar(db)
    db.execute("UPDATE unidades SET ativo=0")
    db.commit()
    motor = MotorCjf()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert motor.chamadas == []
    assert db.execute("SELECT COUNT(*) FROM contratacoes").fetchone()[0] == 0


def test_tela_de_sincronizacao_avisa_que_a_lista_e_fechada(api):
    api.configurar_orgaos(predefinicao="cjf")
    r = api.listar_unidades()
    assert r["fixas"] is True
    assert {u["codigo"] for u in r["unidades"]} == CJF


# ── separação da edição JF ───────────────────────────────────────────────

def test_banco_em_pasta_propria():
    assert licitarium.DIR_DADOS.name == "LicitariumCJF"


@pytest.mark.parametrize("texto, esperado", [
    ("v2.15.0-cjf.1", (2, 15, 0, 1)),
    ("2.15.0-cjf.12", (2, 15, 0, 12)),
    ("2.15.0", (2, 15, 0, 0)),
    # release da edição JF, no mesmo repositório: não é versão desta edição
    ("v2.15.0-jf.4", None),
])
def test_so_reconhece_releases_da_propria_edicao(texto, esperado):
    assert licitarium._versao_tupla(texto) == esperado
