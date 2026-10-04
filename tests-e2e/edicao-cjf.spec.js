// Edição CJF: o assistente inicial não oferece escolha — só o CJF.
const { test, expect } = require("@playwright/test");
const { abrirApp } = require("./harness");

const CJF = [{ chave: "cjf", nome: "Conselho da Justiça Federal", fixa: true,
  descricao: "Somente as unidades do CJF no CNPJ da Justiça Federal",
  orgaos: [{ cnpj: "00508903000188", nome: "Conselho da Justiça Federal" }],
  unidades: ["SECRETARIA DO CONSELHO DA JUSTICA FEDERAL-DF",
             "CONSELHO DA JUSTICA FEDERAL-DF"] }];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(p => { window.__predefs = p; }, CJF);
  await abrirApp(page);
  await page.evaluate(() => iniciarWizard());
  await expect(page.locator("#wizard")).toBeVisible();
});

test("assistente não deixa escolher município, grupo nem CNPJs", async ({ page }) => {
  await expect(page.locator("#wiz-modos")).toBeHidden();
  await expect(page.locator("#wiz-predef")).toBeHidden();
  await expect(page.locator("#wiz-campos-municipio")).toBeHidden();
  await expect(page.locator("#wiz-cnpjs-caixa")).toBeHidden();
  await expect(page.locator("#wiz-predef option")).toHaveCount(1);
  await expect(page.locator("#wiz-subtitulo")).toContainText("Conselho da Justiça Federal");
  // mostra as unidades — o recorte de verdade — e o ano inicial continua lá
  await expect(page.locator("#wiz-predef-lista"))
    .toContainText("SECRETARIA DO CONSELHO DA JUSTICA FEDERAL-DF");
  await expect(page.locator("#wiz-desde")).toBeVisible();
  await expect(page.locator("#wiz-nota")).toContainText("do CJF");
  await expect(page.locator("#wiz-ok")).toBeEnabled();
});

test("Começar configura o CJF com o ano escolhido", async ({ page }) => {
  page.on("dialog", d => d.accept());
  await page.locator("#wiz-desde").selectOption("2025");
  await page.locator("#wiz-ok").click();
  await expect(page.locator("#wizard")).toBeHidden();
  const [c] = await page.evaluate(
    () => window.__chamadas.filter(x => x.metodo === "configurar_orgaos"));
  expect(c.predefinicao).toBe("cjf");
  expect(c.desde).toBe(2025);
});
