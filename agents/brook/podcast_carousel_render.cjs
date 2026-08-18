"use strict";

const { chromium } = require("playwright");

async function main() {
  const [chrome, url, output] = process.argv.slice(2);
  if (!chrome || !url || !output) {
    throw new Error("usage: render.cjs <chrome> <url> <output.png>");
  }
  const browser = await chromium.launch({
    headless: true,
    executablePath: chrome,
    args: ["--allow-file-access-from-files"],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1080, height: 1350 },
      deviceScaleFactor: 1,
    });
    await page.goto(url, { waitUntil: "load", timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForFunction(
      () => document.body.dataset.fitDiagnostics !== undefined,
      undefined,
      { timeout: 10000 },
    );
    await page.evaluate(async () => {
      await Promise.all(
        [...document.images].map((img) => {
          if (img.complete) return undefined;
          return new Promise((resolve) => {
            img.addEventListener("load", resolve, { once: true });
            img.addEventListener("error", resolve, { once: true });
          });
        }),
      );
      const broken = [...document.images].filter((img) => img.naturalWidth === 0);
      if (broken.length) throw new Error(`broken render images: ${broken.map((img) => img.alt).join(", ")}`);
    });
    const diagnostic = await page.evaluate(() => {
      return JSON.parse(document.body.dataset.fitDiagnostics);
    });
    await page.screenshot({ path: output, type: "png", animations: "disabled" });
    process.stdout.write(JSON.stringify(diagnostic));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
