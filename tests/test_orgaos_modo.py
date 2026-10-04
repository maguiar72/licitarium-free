"""Acervo por ÓRGÃOS (edição JF): o recorte é um conjunto de CNPJs, não um
município.

O que estes testes seguram:
  · a fase 1 consulta o PNCP por `cnpj`, em janelas curtas, e grava como
    acervo próprio (`referencia=0`, `municipio_ibge=IBGE_ORGAOS`);
  · unidades alheias cadastradas no CNPJ guarda-chuva ficam de fora;
  · cada CNPJ tem a própria marca d'água — um que falha não segura os outros;
  · o cadastro manual de órgão deixa de exigir esfera municipal;
  · a checagem de atualização entende a tag `vX.Y.Z-jf.N` do fork.
Sem rede: o motor é um dublê, como em `test_pncp.py`.
"""
import inspect
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motor_pncp import Motor

import licitarium
import pncp

GUARDA_CHUVA = "00508903000188"
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


@pytest.fixture(autouse=True)
def sem_edicao_fixa(monkeypatch):
    """Estes testes seguram o motor GERAL do acervo por órgãos (predefinição
    da JF, CNPJs digitados). Neste ramo o programa sai fechado no CJF
    (`pncp.EDICAO_FIXA`); o que é próprio da edição está em
    `test_edicao_cjf.py`."""
    monkeypatch.setattr(pncp, "EDICAO_FIXA", None)
    monkeypatch.setattr(licitarium, "SUFIXO_EDICAO", "jf")


def contratacao(numero, cnpj=GUARDA_CHUVA, unidade="090026", **extra):
    base = {
        "numeroControlePNCP": numero, "anoCompra": 2026, "sequencialCompra": 1,
        "orgaoEntidade": {"cnpj": cnpj, "razaoSocial": "JUSTICA FEDERAL",
                          "esferaId": "F", "poderId": "J"},
        "unidadeOrgao": {"codigoUnidade": unidade, "nomeUnidade": "Secretaria"},
        "modalidadeId": 6, "modalidadeNome": "Pregão eletrônico",
        "situacaoCompraNome": "Homologada", "objetoCompra": "Objeto de teste",
        "valorTotalEstimado": 100.0, "valorTotalHomologado": 90.0,
        "dataPublicacaoPncp": "2026-03-01", "dataAtualizacao": "2026-03-02",
    }
    base.update(extra)
    return base


class MotorPorCnpj:
    """Dublê do `Motor` para a fase 1 por CNPJ: guarda as consultas que
    recebeu e devolve os registros que o teste combinou para cada CNPJ."""

    def __init__(self, por_cnpj=None, falha_em=()):
        self.por_cnpj = por_cnpj or {}
        self.falha_em = set(falha_em)
        self.chamadas = []

    def _baixar_com_disjuntor(self, caminho, consultas, *, rotulo_fase, tipo,
                              tamanho_pagina=500):
        self.chamadas.append({"caminho": caminho, "consultas": consultas,
                              "tamanho_pagina": tamanho_pagina})
        cnpj = consultas[0][1]["cnpj"]

        def gerar():
            for raw in self.por_cnpj.get(cnpj, []):
                yield tipo(raw)
            if cnpj in self.falha_em:
                raise pncp.PncpErro(f"{cnpj}: portal fora do ar")
        return gerar()

    def refazer(self, erro):
        return iter(())

    # fases que estes testes não exercitam: nada a devolver
    def contratos(self, cnpj, inicio, fim): return iter(())
    def atas(self, cnpj, inicio, fim): return iter(())
    def pca(self, cnpj, inicio, fim): return iter(())
    def ipca(self, inicio=None): return iter(())

    def itens_e_resultados(self, pendentes, **kw):
        return iter(())


# ── fase 1 por CNPJ ──────────────────────────────────────────────────────

def test_consulta_por_cnpj_em_janelas_de_um_mes(db):
    motor = MotorPorCnpj({GUARDA_CHUVA: [contratacao("JF-1")]})
    n = pncp.sync_contratacoes_orgao(db, GUARDA_CHUVA, date(2026, 1, 1),
                                     date(2026, 3, 31), motor=motor)
    assert n == 1
    chamada = motor.chamadas[0]
    assert chamada["caminho"] == "/v1/contratacoes/atualizacao"
    assert chamada["tamanho_pagina"] == 50      # teto do endpoint
    params = [p for _, p in chamada["consultas"]]
    # todas por CNPJ, nenhuma por município
    assert all(p["cnpj"] == GUARDA_CHUVA for p in params)
    assert not any("codigoMunicipioIbge" in p for p in params)
    # 13 modalidades × 3 janelas (90 dias em fatias de 31)
    assert len(params) == 13 * 3
    assert {p["codigoModalidadeContratacao"] for p in params} == set(range(1, 14))
    assert {(p["dataInicial"], p["dataFinal"]) for p in params} == {
        ("20260101", "20260131"), ("20260201", "20260303"),
        ("20260304", "20260331")}


def test_grava_como_acervo_proprio_com_o_pseudo_codigo(db):
    motor = MotorPorCnpj({GUARDA_CHUVA: [contratacao("JF-1")]})
    pncp.sync_contratacoes_orgao(db, GUARDA_CHUVA, date(2026, 3, 1),
                                 date(2026, 3, 5), motor=motor)
    linha = db.execute("SELECT referencia, municipio_ibge, orgao_cnpj, raw"
                       " FROM contratacoes").fetchone()
    assert linha["referencia"] == 0
    assert linha["municipio_ibge"] == pncp.IBGE_ORGAOS
    assert linha["orgao_cnpj"] == GUARDA_CHUVA
    assert linha["raw"]          # acervo próprio guarda o JSON bruto


def test_janela_configuravel_e_com_limites(db):
    assert pncp.janela_orgaos_dias(db) == pncp.JANELA_ORGAO_DIAS
    pncp._config(db, "janela_orgaos_dias", "90")
    assert pncp.janela_orgaos_dias(db) == 90
    for invalido in ("0", "365", "abc"):
        pncp._config(db, "janela_orgaos_dias", invalido)
        assert pncp.janela_orgaos_dias(db) == pncp.JANELA_ORGAO_DIAS


def test_unidade_alheia_do_cnpj_guarda_chuva_fica_de_fora(db):
    pncp._config(db, "unidades_excluidas", "40101,925343,925368")
    motor = MotorPorCnpj({GUARDA_CHUVA: [
        contratacao("JF-CJF", unidade="090026"),
        contratacao("TJAL", unidade="925343"),
        contratacao("CGJ-ES", unidade="040101"),   # zero à esquerda no portal
        contratacao("PMDF", unidade="925368"),
    ]})
    n = pncp.sync_contratacoes_orgao(db, GUARDA_CHUVA, date(2026, 3, 1),
                                     date(2026, 3, 5), motor=motor)
    assert n == 1
    assert [r[0] for r in db.execute(
        "SELECT numero_controle FROM contratacoes")] == ["JF-CJF"]


def test_sem_lista_de_exclusao_nada_e_descartado(db):
    motor = MotorPorCnpj({GUARDA_CHUVA: [contratacao("A", unidade="925343")]})
    assert pncp.sync_contratacoes_orgao(
        db, GUARDA_CHUVA, date(2026, 3, 1), date(2026, 3, 5), motor=motor) == 1


def test_motor_real_ainda_tem_o_metodo_interno_com_a_mesma_assinatura():
    """`sync_contratacoes_orgao` chama um método interno do motor_pncp. Se
    uma versão nova do motor mudar a assinatura, isto quebra aqui — antes
    de quebrar a coleta em produção."""
    assinatura = inspect.signature(Motor._baixar_com_disjuntor)
    assert list(assinatura.parameters) == [
        "self", "caminho", "consultas", "rotulo_fase", "tipo", "tamanho_pagina"]


# ── orquestração ─────────────────────────────────────────────────────────

def _acervo_jf(db, cnpjs=(GUARDA_CHUVA, TRF3)):
    pncp.configurar_acervo_orgaos(
        db, "Justiça Federal", "BR", [(c, c) for c in cnpjs],
        excluidas=["040101"])


def test_sincronizar_tudo_passa_por_cada_cnpj_ativo(db):
    _acervo_jf(db)
    motor = MotorPorCnpj({GUARDA_CHUVA: [contratacao("JF-1")],
                          TRF3: [contratacao("TRF3-1", cnpj=TRF3)]})
    resumo = pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert resumo["contratacoes"] == 2
    consultados = [c["consultas"][0][1]["cnpj"] for c in motor.chamadas]
    assert consultados == sorted([GUARDA_CHUVA, TRF3])
    hoje = date.today().isoformat()
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA_CHUVA}") == hoje
    assert pncp._config(db, f"last_sync_contratacoes_{TRF3}") == hoje
    assert pncp._config(db, "last_sync_contratacoes") == hoje


def test_orgao_inativo_nao_e_consultado(db):
    _acervo_jf(db)
    db.execute("UPDATE orgaos SET ativo=0 WHERE cnpj=?", (TRF3,))
    db.commit()
    motor = MotorPorCnpj()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert [c["consultas"][0][1]["cnpj"] for c in motor.chamadas] == [GUARDA_CHUVA]


def test_falha_em_um_cnpj_nao_avanca_a_marca_dele_nem_segura_os_outros(db):
    _acervo_jf(db)
    motor = MotorPorCnpj({GUARDA_CHUVA: [contratacao("JF-1")],
                          TRF3: [contratacao("TRF3-1", cnpj=TRF3)]},
                         falha_em={GUARDA_CHUVA})
    resumo = pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    assert resumo["contratacoes"] is None          # a fase não fechou
    assert pncp._config(db, f"last_sync_contratacoes_{GUARDA_CHUVA}") is None
    assert pncp._config(db, f"last_sync_contratacoes_{TRF3}") \
        == date.today().isoformat()
    assert pncp._config(db, "last_sync_contratacoes") is None
    # o que chegou antes da falha fica gravado (falha ≠ ausência)
    assert db.execute("SELECT COUNT(*) FROM contratacoes").fetchone()[0] == 2


def test_primeira_coleta_respeita_o_ano_inicial_escolhido(db):
    _acervo_jf(db, cnpjs=(TRF3,))
    pncp._config(db, "inicio_coleta", "2025-01-01")
    motor = MotorPorCnpj()
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=motor)
    datas = [p["dataInicial"] for _, p in motor.chamadas[0]["consultas"]]
    assert min(datas) == "20250101"


def test_inicio_coleta_invalido_cai_no_inicio_do_pncp(db):
    assert pncp.inicio_coleta(db) == pncp.DATA_INICIO_PNCP
    pncp._config(db, "inicio_coleta", "não é data")
    assert pncp.inicio_coleta(db) == pncp.DATA_INICIO_PNCP
    pncp._config(db, "inicio_coleta", "2019-01-01")   # antes do portal
    assert pncp.inicio_coleta(db) == pncp.DATA_INICIO_PNCP


def test_modo_municipio_continua_usando_a_consulta_por_ibge(db):
    """O fluxo original não pode ter mudado: sem `modo_acervo=orgaos`, a
    fase 1 segue chamando `Motor.contratacoes(codigo_ibge, ...)`."""
    chamou = []

    class MotorMunicipal(MotorPorCnpj):
        def contratacoes(self, codigo_ibge, inicio, fim):
            chamou.append(codigo_ibge)
            return iter(())

    motor = MotorMunicipal()
    pncp.sincronizar_tudo(db, "3534203", motor=motor)
    assert chamou == ["3534203"]
    assert motor.chamadas == []


# ── ponte (Api) ──────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(licitarium, "DIR_DADOS", tmp_path)
    monkeypatch.setattr(licitarium, "ARQUIVO_DB", tmp_path / "t.db")
    licitarium.abrir_db().close()
    return licitarium.Api()


def _cfg(chave):
    db = licitarium.abrir_db()
    try:
        return pncp._config(db, chave)
    finally:
        db.close()


def test_predefinicao_jf_configura_o_acervo_sem_consultar_o_portal(api, monkeypatch):
    def nao_pode(cnpj):
        raise AssertionError("predefinição não deveria consultar o PNCP")
    monkeypatch.setattr(pncp, "consultar_orgao", nao_pode)
    assert api.configurar_orgaos("jf", desde=2024) == {"ok": True}
    estado = api.get_estado()
    assert estado["modo"] == "orgaos"
    assert estado["municipio"] == "Justiça Federal"
    assert estado["ibge"] == pncp.IBGE_ORGAOS
    assert _cfg("inicio_coleta") == "2024-01-01"
    assert _cfg("unidades_excluidas") == "40101,925343,925368"
    orgaos = api.listar_orgaos()
    assert len(orgaos) == len(pncp.PREDEFINICOES["jf"]["orgaos"])
    assert all(o["ativo"] == 1 and o["origem"] == "manual" for o in orgaos)
    assert GUARDA_CHUVA in {o["cnpj"] for o in orgaos}


def test_predefinicoes_tem_cnpjs_validos_e_sem_repeticao():
    for p in pncp.PREDEFINICOES.values():
        cnpjs = [c for c, _ in p["orgaos"]]
        assert all(len(c) == 14 and c.isdigit() for c in cnpjs)
        assert len(set(cnpjs)) == len(cnpjs)


def test_cnpjs_digitados_sao_conferidos_no_pncp(api, monkeypatch):
    conhecidos = {TRF3: {"razaoSocial": "TRIBUNAL REGIONAL FEDERAL 3 REGIAO",
                         "esferaId": "F"}}
    monkeypatch.setattr(pncp, "consultar_orgao", conhecidos.get)
    r = api.configurar_orgaos(None, "TRF3", "59.949.362/0001-76")
    assert r == {"ok": True}
    assert api.listar_orgaos()[0]["razao_social"] \
        == "TRIBUNAL REGIONAL FEDERAL 3 REGIAO"
    assert _cfg("inicio_coleta") is None

    r = api.configurar_orgaos(None, "Outro", "99999999000199")
    assert r["ok"] is False and "não encontrado" in r["erro"]
    r = api.configurar_orgaos(None, "Outro", "123")
    assert r["ok"] is False and "14 dígitos" in r["erro"]
    r = api.configurar_orgaos(None, "", TRF3)
    assert r["ok"] is False and "nome" in r["erro"]
    r = api.configurar_orgaos("inexistente")
    assert r["ok"] is False


def test_trocar_de_acervo_reinicia_o_banco(api):
    api.configurar_municipio(3534203, "Orindiúva", "SP")
    db = licitarium.abrir_db()
    db.execute("INSERT INTO contratacoes (numero_controle) VALUES ('VELHA')")
    db.execute("INSERT INTO orgaos (cnpj, razao_social) VALUES ('1', 'Prefeitura')")
    pncp._config(db, "last_sync_contratacoes", "2026-01-01")
    db.close()
    assert api.configurar_orgaos("jf") == {"ok": True}
    db = licitarium.abrir_db()
    try:
        assert db.execute("SELECT COUNT(*) FROM contratacoes").fetchone()[0] == 0
        assert pncp._config(db, "last_sync_contratacoes") is None
        assert "1" not in {r[0] for r in db.execute("SELECT cnpj FROM orgaos")}
    finally:
        db.close()


def test_orgao_federal_entra_manualmente_no_acervo_por_orgaos(api, monkeypatch):
    api.configurar_orgaos("jf")
    monkeypatch.setattr(pncp, "consultar_orgao", lambda cnpj:
        {"razaoSocial": "SUPERIOR TRIBUNAL DE JUSTICA", "esferaId": "F"})
    assert api.add_orgao("00488478000102", "") == {"ok": True}


def test_no_acervo_municipal_orgao_federal_continua_recusado(api, monkeypatch):
    api.configurar_municipio(3534203, "Orindiúva", "SP")
    monkeypatch.setattr(pncp, "consultar_orgao", lambda cnpj:
        {"razaoSocial": "SUPERIOR TRIBUNAL DE JUSTICA", "esferaId": "F"})
    r = api.add_orgao("00488478000102", "")
    assert r["ok"] is False and "não é órgão municipal" in r["erro"]


def test_voltar_para_municipio_desliga_o_modo_orgaos(api):
    api.configurar_orgaos("jf", desde=2024)
    assert api.trocar_municipio(3534203, "Orindiúva", "SP") == {"ok": True}
    assert api.get_estado()["modo"] == "municipio"
    assert _cfg("unidades_excluidas") is None
    assert _cfg("inicio_coleta") is None
    assert api.listar_orgaos() == []


# ── versão do fork ───────────────────────────────────────────────────────

@pytest.mark.parametrize("texto, esperado", [
    ("2.15.0", (2, 15, 0, 0)),
    ("v2.15.0-jf.1", (2, 15, 0, 1)),
    ("2.15.0-jf.12", (2, 15, 0, 12)),
    ("2.16.0", (2, 16, 0, 0)),
    ("", None), ("latest", None), (None, None),
])
def test_versao_tupla(texto, esperado):
    assert licitarium._versao_tupla(texto) == esperado


def test_ordem_das_versoes_do_fork():
    v = licitarium._versao_tupla
    assert v("2.15.0-jf.2") > v("2.15.0-jf.1") > v("2.15.0")
    assert v("2.16.0-jf.1") > v("2.15.0-jf.9")


def test_atualizacao_aponta_para_o_fork():
    assert licitarium.REPO_ATUALIZACAO == "maguiar72/licitarium-free"


# ── linha de status: de qual órgão é a volta (jf.2) ──────────────────────

@pytest.mark.parametrize("msg,orgao,esperado", [
    ("Contratações — Leilão eletrônico (8/117)…", "TRF3 (órgão 8 de 9)",
     "Contratações — TRF3 (órgão 8 de 9) — Leilão eletrônico (8/117)…"),
    ("Contratações…", "TRF3 (órgão 8 de 9)",
     "Contratações — TRF3 (órgão 8 de 9)…"),
    ("Itens — contratação 3 de 50", "", "Itens — contratação 3 de 50"),
])
def test_mensagem_de_progresso_ganha_o_orgao_em_curso(msg, orgao, esperado):
    assert pncp._com_orgao(msg, orgao) == esperado


def test_rotulo_do_orgao_usa_sigla_razao_social_ou_cnpj(db):
    _acervo_jf(db)
    assert pncp.rotulo_orgao(db, TRF3, 8, 9) == "TRF3 (órgão 8 de 9)"
    db.execute("INSERT INTO orgaos (cnpj, razao_social, ativo, origem) VALUES"
               " ('11111111000111', 'Conselho Nacional de Alguma Coisa Muito"
               " Comprida', 1, 'manual')")
    rotulo = pncp.rotulo_orgao(db, "11111111000111", 1, 2)
    assert rotulo.startswith("Conselho Nacional de Alguma") and "…" in rotulo
    assert rotulo.endswith("(órgão 1 de 2)")
    assert pncp.rotulo_orgao(db, "22222222000122", 2, 2) == \
        "22222222000122 (órgão 2 de 2)"


def test_sincronizar_avisa_o_orgao_de_cada_volta_e_limpa_depois(db):
    _acervo_jf(db)
    mensagens = []
    pncp.sincronizar_tudo(db, pncp.IBGE_ORGAOS, motor=MotorPorCnpj(),
                          progresso=mensagens.append)
    assert "Contratações — JF 1ª Inst./CJF (órgão 1 de 2)…" in mensagens
    assert "Contratações — TRF3 (órgão 2 de 2)…" in mensagens
    # o que vem depois das voltas por órgão não carrega órgão nenhum
    assert not any("órgão" in m for m in mensagens
                   if m.startswith(("Itens", "Compactando")))


def test_todas_as_siglas_sao_de_cnpjs_da_predefinicao():
    cnpjs = {c for c, _ in pncp.PREDEFINICOES["jf"]["orgaos"]}
    assert set(pncp.SIGLAS_ORGAOS) == cnpjs
