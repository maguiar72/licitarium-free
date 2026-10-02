// Edição JF: o assistente inicial também monta o acervo por ÓRGÃOS (CNPJ),
// além do fluxo original por município.
const { test, expect } = require("@playwright/test");
const { abrirApp } = require("./harness");

test.beforeEach(async ({ page }) => {
  await abrirApp(page);
  await page.evaluate(() => iniciarWizard());
  await expect(page.locator("#wizard")).toBeVisible();
});

const chamadasDe = (page, metodo) => page.evaluate(
  m => window.__chamadas.filter(c => c.metodo === m), metodo);

test("abre no modo órgãos, com a Justiça Federal já escolhida", async ({ page }) => {
  await expect(page.locator('input[name="wiz-modo"][value="orgaos"]')).toBeChecked();
  await expect(page.locator("#wiz-campos-orgaos")).toBeVisible();
  await expect(page.locator("#wiz-campos-municipio")).toBeHidden();
  await expect(page.locator("#wiz-predef")).toHaveValue("jf");
  // a lista de CNPJs do grupo fica à vista antes de confirmar
  await expect(page.locator("#wiz-predef-lista")).toContainText("00508903000188");
  await expect(page.locator("#wiz-cnpjs-caixa")).toBeHidden();
  await expect(page.locator("#wiz-ok")).toBeEnabled();
  await expect(page.locator("#wiz-nota")).toContainText("leva horas");
});

test("confirmar a predefinição chama a ponte com a chave e o ano", async ({ page }) => {
  page.on("dialog", d => d.accept());   // já há acervo no mock: pede confirmação
  await page.locator("#wiz-desde").selectOption("2024");
  await page.locator("#wiz-ok").click();
  await expect(page.locator("#wizard")).toBeHidden();
  const [c] = await chamadasDe(page, "configurar_orgaos");
  expect(c.predefinicao).toBe("jf");
  expect(c.desde).toBe(2024);
});

test("recusar a confirmação não mexe no acervo", async ({ page }) => {
  page.on("dialog", d => d.dismiss());
  await page.locator("#wiz-ok").click();
  await expect(page.locator("#wizard")).toBeVisible();
  expect(await chamadasDe(page, "configurar_orgaos")).toHaveLength(0);
});

test("outros órgãos: pede nome e CNPJs e mostra o erro da ponte", async ({ page }) => {
  page.on("dialog", d => d.accept());
  await page.locator("#wiz-predef").selectOption("");
  await expect(page.locator("#wiz-cnpjs-caixa")).toBeVisible();
  await expect(page.locator("#wiz-predef-lista")).toBeEmpty();
  await page.locator("#wiz-nome-acervo").fill("Conselho da Justiça Federal");
  await page.locator("#wiz-ok").click();
  await expect(page.locator("#wiz-orgaos-erro")).toContainText("ao menos um CNPJ");
  await expect(page.locator("#wiz-ok")).toBeEnabled();

  await page.locator("#wiz-cnpjs").fill("00.508.903/0001-88");
  await page.locator("#wiz-ok").click();
  await expect(page.locator("#wizard")).toBeHidden();
  const chamadas = await chamadasDe(page, "configurar_orgaos");
  const ultima = chamadas[chamadas.length - 1];
  expect(ultima.predefinicao).toBeNull();
  expect(ultima.nome).toBe("Conselho da Justiça Federal");
  expect(ultima.cnpjs).toContain("00.508.903/0001-88");
});

test("modo município continua como no original", async ({ page }) => {
  await page.locator('input[name="wiz-modo"][value="municipio"]').check();
  await expect(page.locator("#wiz-campos-municipio")).toBeVisible();
  await expect(page.locator("#wiz-campos-orgaos")).toBeHidden();
  // sem município escolhido não dá para começar
  await expect(page.locator("#wiz-ok")).toBeDisabled();
  await expect(page.locator("#wiz-nota")).toContainText("alguns minutos");
});
