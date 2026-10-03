// Edição JF (jf.3): filtro de unidade administrativa no acervo e
// liga/desliga de unidades na tela de Sincronização.
const { test, expect } = require("@playwright/test");
const { abrirApp, abrirLista } = require("./harness");

const GUARDA = "45148970000177";   // um dos órgãos do mock de filtros
const OUTRO = "51351716000174";
const UNIDADES = [
  { id: `${GUARDA}|90026`, cnpj: GUARDA, codigo: "90026", uf: "DF", n: 15,
    nome: "SECRETARIA DO CJF", municipio: "Brasília", ativo: true, excluida: false },
  { id: `${GUARDA}|90012`, cnpj: GUARDA, codigo: "90012", uf: "BA", n: 7,
    nome: "JF DE 1A. INSTANCIA - BA", municipio: "Salvador", ativo: true, excluida: false },
  { id: `${OUTRO}|90029`, cnpj: OUTRO, codigo: "90029", uf: "SP", n: 3,
    nome: "TRF DA 3A. REGIAO", municipio: "São Paulo", ativo: true, excluida: false },
];

const chamadasDe = (page, metodo) => page.evaluate(
  m => window.__chamadas.filter(c => c.metodo === m), metodo);
const opcoes = page => page.locator("#f-unidade option").allTextContents();

test("sem unidades (modo município) o filtro nem aparece", async ({ page }) => {
  await abrirApp(page);
  await abrirLista(page);
  await expect(page.locator("#f-unidade")).toBeHidden();
});

test.describe("acervo por órgãos", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(u => { window.__unidadesAdm = u; }, UNIDADES);
    await abrirApp(page);
    await abrirLista(page);
  });

  test("filtro lista todas as unidades e manda a escolhida ao backend", async ({ page }) => {
    await expect(page.locator("#f-unidade")).toBeVisible();
    expect(await opcoes(page)).toEqual(["Todas as unidades",
      "SECRETARIA DO CJF — DF (15)", "JF DE 1A. INSTANCIA - BA — BA (7)",
      "TRF DA 3A. REGIAO — SP (3)"]);
    await page.locator("#f-unidade").selectOption(`${GUARDA}|90026`);
    await expect.poll(async () => {
      const l = await chamadasDe(page, "listar");
      return l[l.length - 1].filtros.unidade_adm;
    }).toBe(`${GUARDA}|90026`);
  });

  test("escolher o órgão restringe as unidades e solta a que não é dele", async ({ page }) => {
    await page.locator("#f-unidade").selectOption(`${OUTRO}|90029`);
    await page.locator("#f-orgao").selectOption(GUARDA);
    expect(await opcoes(page)).toEqual(["Todas as unidades",
      "SECRETARIA DO CJF — DF (15)", "JF DE 1A. INSTANCIA - BA — BA (7)"]);
    await expect(page.locator("#f-unidade")).toHaveValue("");
    await expect.poll(async () => {
      const l = await chamadasDe(page, "listar");
      return l[l.length - 1].filtros;
    }).toMatchObject({ orgao: GUARDA, unidade_adm: null });
  });

  test("limpar filtros devolve todas as unidades", async ({ page }) => {
    await page.locator("#f-orgao").selectOption(GUARDA);
    await page.locator("#btn-limpar").click();
    expect(await opcoes(page)).toHaveLength(4);
  });

  test("Sincronização: desmarcar unidade e desmarcar todas chamam a ponte", async ({ page }) => {
    await page.evaluate(() => carregarConfigSync());
    await expect(page.locator("#cfg-unidades-caixa")).not.toHaveClass(/oculto/);
    await expect(page.locator("#cfg-unidades details")).toHaveCount(2);
    await expect(page.locator("#cfg-unidades summary").first())
      .toContainText("2 de 2 unidades marcadas");
    await page.evaluate(() => {
      document.querySelector("#cfg-unidades details").open = true;
      document.querySelector('#cfg-unidades input[data-ucodigo="90012"]').click();
    });
    await expect.poll(() => chamadasDe(page, "set_unidade_ativa")).toEqual([
      { metodo: "set_unidade_ativa", cnpj: GUARDA, codigo: "90012", ativo: false }]);
    // a lista se refaz mantendo o órgão aberto
    await expect(page.locator("#cfg-unidades summary").first())
      .toContainText("1 de 2 unidades marcadas");
    await expect(page.locator("#cfg-unidades details").first()).toHaveAttribute("open", "");
    await page.evaluate(() =>
      document.querySelector('#cfg-unidades button[data-todas="0"]').click());
    await expect.poll(() => chamadasDe(page, "set_unidades_ativas")).toEqual([
      { metodo: "set_unidades_ativas", cnpj: GUARDA, ativo: false }]);
    await page.evaluate(() => document.querySelector("#cfg-por-unidade").click());
    await expect.poll(() => chamadasDe(page, "set_coleta_por_unidade")).toEqual([
      { metodo: "set_coleta_por_unidade", ligada: true }]);
  });
});
