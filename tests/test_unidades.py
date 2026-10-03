"""Unidades administrativas no acervo por órgãos (edição JF, jf.3).

Um CNPJ federal reúne dezenas de unidades (o da Justiça Federal: CJF, TRFs,
seções judiciárias). O que estes testes seguram:
  · cada registro grava o código da unidade, e a unidade entra no catálogo;
  · o catálogo do PNCP completa nome/município sem mexer no liga/desliga;
  · unidade desligada não entra no acervo, em nenhuma fase;
  · com todas ligadas o CNPJ sai numa volta só; com alguma desligada (ou com
    a opção ligada), a consulta é unidade por unidade, só as ligadas, com
    marca d'água própria;
  · a lista do acervo filtra por unidade; banco antigo é reprojetado do raw.
Sem rede: o motor é um dublê.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import licitarium
import pncp

GUARDA = "00508903000188"
TRF3 = "59949362000176"


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


def contratacao(numero, unidade="090026", nome="SECRETARIA DO CJF", cnpj=GUARDA):
    return {
        "numeroControlePNCP": numero, "anoCompra": 2026, "sequencialCompra": 1,
        "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": "JUSTICA FEDERAL"},
        "unidadeOrgao": {"codigoUnidade": unidade, "nomeUnidade": nome,
                         "municipioNome": "Brasília", "ufSigla": "DF"},
        "modalidadeId": 6, "modalidadeNome": "Pregão eletrônico",
        "objetoCompra": "Objeto", "dataPublicacaoPncp": "2026-03-01",
        "dataAtualizacao": "2026-03-02",
    }


class ClienteCatalogo:
    def __init__(self, resposta, erro=None):
        self.resposta, self.erro, self.pedidos = resposta, erro, []

    def get(self, base, caminho, params, **kw):
        self.pedidos.append(caminho)
        if self.erro:
            raise self.erro
        return self.resposta


class MotorUnidades:
    """Dublê do motor: devolve as contratações combinadas por CNPJ,
    respeitando o filtro `codigoUnidadeAdministrativa` como o portal faz."""

    def __init__(self, por_cnpj=None, catalogo=None, erro_catalogo=None,
                 falha_na_unidade=()):
        self.por_cnpj = por_cnpj or {}
        self.chamadas = []
        self.falha_na_unidade = set(falha_na_unidade)
        self._base_pncp = "https://pncp.invalido/api/pncp"
        self._cliente = ClienteCatalogo(catalogo or [], erro_catalogo)

    def _baixar_com_disjuntor(self, caminho, consultas, *, rotulo_fase, tipo,
                              tamanho_pagina=500):
        params = consultas[0][1]
        cnpj, unidade = params["cnpj"], params.get("codigoUnidadeAdministrativa")
        self.chamadas.append({"cnpj": cnpj, "unidade": unidade,
                              "inicio": params["dataInicial"]})

        def gerar():
            if unidade in self.falha_na_unidade:
                raise pncp.PncpErro(f"unidade {unidade}: portal fora do ar")
            for raw in self.por_cnpj.get(cnpj, []):
                if unidade is None or raw["unidadeOrgao"]["codigoUnidade"] == unidade:
                    yield tipo(raw)
        return gerar()

    def refazer(self, erro): return iter(())
    def contratos(self, cnpj, inicio, fim): return iter(())
    def atas(self, cnpj, inicio, fim): return iter(())
    def pca(self, cnpj, inicio, fim): return iter(())
    def ipca(self, inicio=None): return iter(())
    def itens_e_resultados(self, pendentes, **kw): return iter(())


CATALOGO = [
    {"codigoUnidade": "090026", "nomeUnidade": "SECRETARIA DO CJF",
     "municipio": {"nome": "Brasília", "uf": {"siglaUF": "DF"}}},
    {"codigoUnidade": "090012", "nomeUnidade": "JF DE 1A. INSTANCIA - BA",
     "municipio": {"nome": "Salvador", "uf": {"siglaUF": "BA"}}},
    {"codigoUnidade": "925368", "nomeUnidade": "POLICIA MILITAR DO DF",
     "municipio": {"nome": "Brasília", "uf": {"siglaUF": "DF"}}},
]
REGISTROS = {GUARDA: [contratacao("CJF-1"),
                      contratacao("BA-1", "090012", "JF DE 1A. INSTANCIA - BA"),
                      contratacao("PM-1", "925368", "POLICIA MILITAR DO DF")]}


def _acervo(db, cnpjs=(GUARDA,)):
    pncp.configurar_acervo_orgaos(db, "Justiça Federal", "BR",
                                  [(c, c) for c in cnpjs], excluidas=["925368"])


def _unidades(db):
    return {r["codigo"]: dict(r) for r in db.execute("SELECT * FROM unidades")}


# ── catálogo e gravação ──────────────────────────────────────────────────

def test_catalogo_do_pncp_entra_sem_zeros_e_com_a_grafia_do_portal(db):
    _acervo(db)
    assert pncp.sync_catalogo_unidades(db, GUARDA, MotorUnidades(catalogo=CATALOGO)) == 3
    u = _unidades(db)
    assert set(u) == {"90026", "90012", "925368"}
    assert u["90026"]["codigo_pncp"] == "090026"
    assert (u["90012"]["municipio"], u["90012"]["uf"]) == ("Salvador", "BA")


def test_catalogo_nao_religa_unidade_que_o_usuario_desligou(db):
    _acervo(db)
    pncp.sync_catalogo_unidades(db, GUARDA, MotorUnidades(catalogo=CATALOGO))
    db.execute("UPDATE unidades SET ativo=0 WHERE codigo='90012'")
    pncp.sync_catalogo_unidades(db, GUARDA, MotorUnidades(catalogo=CATALOGO))
    assert _unidades(db)["90012"]["ativo"] == 0


def test_registro_grava_o_codigo_e_descobre_a_unidade_sem_catalogo(db):
    _acervo(db)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=MotorUnidades(REGISTROS))
    linhas = {r["numero_controle"]: r["unidade_codigo"] for r in db.execute(
        "SELECT numero_controle, unidade_codigo FROM contratacoes")}
    # a unidade da predefinição (PM do DF) fica de fora
    assert linhas == {"CJF-1": "90026", "BA-1": "90012"}
    u = _unidades(db)
    assert u["90026"]["nome"] == "SECRETARIA DO CJF"
    assert u["90026"]["origem"] == "descoberta"


def test_falha_no_catalogo_nao_impede_a_coleta_e_tenta_de_novo_depois(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, erro_catalogo=pncp.PncpErro("tempo esgotado"))
    resumo = pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert resumo["contratacoes"] == 2
    assert pncp._config(db, f"unidades_catalogo_{GUARDA}") is None
    assert db.execute("SELECT status FROM sync_log WHERE tipo='unidades'"
                      ).fetchone()[0] == "erro"


def test_catalogo_e_consultado_uma_vez_por_cnpj(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor, forcado=True)
    assert motor._cliente.pedidos == [f"/v1/orgaos/{GUARDA}/unidades"]


# ── como a fase 1 consulta ───────────────────────────────────────────────

def test_todas_ligadas_o_cnpj_sai_numa_volta_so(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert [(c["cnpj"], c["unidade"]) for c in motor.chamadas] == [(GUARDA, None)]
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA}") == date.today().isoformat()


def test_unidade_desligada_faz_a_coleta_ir_unidade_por_unidade(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp.sync_catalogo_unidades(db, GUARDA, motor)
    db.execute("UPDATE unidades SET ativo=0 WHERE codigo='90012'")
    db.commit()
    mensagens = []
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor,
                          progresso=mensagens.append)
    # só a ligada, com a grafia do portal; a excluída pela predefinição não
    assert [(c["cnpj"], c["unidade"]) for c in motor.chamadas] == [(GUARDA, "090026")]
    assert [r[0] for r in db.execute("SELECT numero_controle FROM contratacoes")] == ["CJF-1"]
    hoje = date.today().isoformat()
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA}_90026") == hoje
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA}") is None
    assert any("SECRETARIA DO CJF (unidade 1 de 1)" in m for m in mensagens)


def test_opcao_coletar_sempre_por_unidade(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp._config(db, "coleta_por_unidade", "1")
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert sorted(c["unidade"] for c in motor.chamadas) == ["090012", "090026"]


def test_marca_do_cnpj_inteiro_vale_de_piso_para_a_unidade(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)   # volta só
    db.execute("UPDATE unidades SET ativo=0 WHERE codigo='90012'")
    db.commit()
    motor.chamadas.clear()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor, forcado=True)
    # a unidade nunca teve marca própria, mas não recomeça do início do PNCP
    assert motor.chamadas[0]["unidade"] == "090026"
    assert motor.chamadas[0]["inicio"] != pncp.amd(pncp.DATA_INICIO_PNCP)


def test_falha_numa_unidade_nao_avanca_a_marca_dela_nem_segura_as_outras(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO,
                          falha_na_unidade=["090012"])
    pncp._config(db, "coleta_por_unidade", "1")
    resumo = pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert resumo["contratacoes"] is None
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA}_90012") is None
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA}_90026")


def test_orgao_sem_nenhuma_unidade_ligada_nao_e_consultado(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=CATALOGO)
    pncp.sync_catalogo_unidades(db, GUARDA, motor)
    db.execute("UPDATE unidades SET ativo=0")
    db.commit()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert motor.chamadas == []


def test_modo_municipio_nao_cria_catalogo(db):
    pncp._config(db, "municipio_ibge", "3534203")

    class MotorMunicipio(MotorUnidades):
        def contratacoes(self, ibge, inicio, fim):
            return iter(())
    pncp.sincronizar_tudo(db, "3534203", motor=MotorMunicipio())
    assert _unidades(db) == {}


# ── ponte (Api) ──────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(licitarium, "DIR_DADOS", tmp_path)
    monkeypatch.setattr(licitarium, "ARQUIVO_DB", tmp_path / "t.db")
    licitarium.abrir_db().close()
    return licitarium.Api()


def _povoar(api):
    db = licitarium.abrir_db()
    try:
        _acervo(db)
        pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS,
                              motor=MotorUnidades(REGISTROS, catalogo=CATALOGO))
    finally:
        db.close()


def test_filtro_oferece_so_unidade_com_registro_e_a_lista_filtra(api):
    _povoar(api)
    unidades = api.filtros_disponiveis()["unidades_adm"]
    assert [(u["codigo"], u["n"]) for u in unidades] == [("90012", 1), ("90026", 1)]
    cjf = next(u for u in unidades if u["codigo"] == "90026")
    assert cjf["id"] == f"{GUARDA}|90026"
    lista = api.listar("contratacoes", {"unidade_adm": cjf["id"]})
    assert [i["numero_controle"] for i in lista["itens"]] == ["CJF-1"]
    assert api.listar("contratacoes", {})["total"] == 2


def test_tela_de_sincronizacao_ve_todas_as_unidades_e_marca_a_excluida(api):
    _povoar(api)
    r = api.listar_unidades()
    por_codigo = {u["codigo"]: u for u in r["unidades"]}
    assert set(por_codigo) == {"90026", "90012", "925368"}
    assert por_codigo["925368"]["excluida"] is True
    assert r["por_unidade"] is False


def test_ligar_e_desligar_unidade_pela_ponte(api):
    _povoar(api)
    api.set_unidade_ativa(GUARDA, "90012", False)
    assert {u["codigo"]: u["ativo"] for u in api.listar_unidades()["unidades"]
            }["90012"] is False
    # ligar a que a predefinição excluía tira-a da lista de exclusão
    api.set_unidade_ativa(GUARDA, "925368", True)
    db = licitarium.abrir_db()
    try:
        assert pncp.unidades_excluidas(db) == set()
    finally:
        db.close()
    api.set_unidades_ativas(GUARDA, False)
    assert not any(u["ativo"] for u in api.listar_unidades()["unidades"])
    api.set_coleta_por_unidade(True)
    assert api.listar_unidades()["por_unidade"] is True


def test_modo_municipio_nao_oferece_unidades(api):
    assert api.filtros_disponiveis()["unidades_adm"] == []
    assert api.listar_unidades()["unidades"] == []


def test_banco_antigo_e_reprojetado_do_raw(tmp_path, monkeypatch):
    """Banco da jf.2 (sem `unidade_codigo` nem `unidades`): ao abrir, a
    coluna nasce preenchida e o catálogo sai do que já estava no acervo."""
    monkeypatch.setattr(licitarium, "DIR_DADOS", tmp_path)
    monkeypatch.setattr(licitarium, "ARQUIVO_DB", tmp_path / "t.db")
    db = licitarium.abrir_db()
    _acervo(db)
    pncp._upsert_contratacao(db, contratacao("CJF-1"), pncp.IBGE_ORGAOS, 0)
    db.commit()
    for tabela in ("contratacoes", "contratos", "atas", "pca_itens"):
        db.execute(f"ALTER TABLE {tabela} DROP COLUMN unidade_codigo")
    db.execute("DROP TABLE unidades")
    db.commit()
    db.close()

    db = licitarium.abrir_db()
    try:
        assert db.execute("SELECT unidade_codigo FROM contratacoes"
                          ).fetchone()[0] == "90026"
        u = _unidades(db)["90026"]
        assert (u["codigo_pncp"], u["nome"], u["uf"]) == (
            "090026", "SECRETARIA DO CJF", "DF")
    finally:
        db.close()


def test_catalogo_vazio_ou_irreconhecivel_e_tentado_de_novo(db):
    _acervo(db)
    motor = MotorUnidades(REGISTROS, catalogo=[{"campoInesperado": 1}])
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert pncp._config(db, f"unidades_catalogo_{GUARDA}") is None
    # as unidades entram mesmo assim, pelos registros
    assert set(_unidades(db)) == {"90026", "90012"}
