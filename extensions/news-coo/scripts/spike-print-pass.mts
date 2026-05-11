// ADR-025 Stage 0 — measure print-stylesheet pass impact on real publisher pages.
// Run: npx tsx scripts/spike-print-pass.mts <html-file-path> <site-name>

import { readFileSync, writeFileSync } from "node:fs";
import { Window } from "happy-dom";
import Defuddle from "defuddle/full";
import { applyPrintStylesheets, removeHiddenByPrint } from "../src/content/applyPrintStylesheets.js";

const [, , htmlPath, siteName] = process.argv;
if (!htmlPath || !siteName) {
  console.error("Usage: spike-print-pass.mts <html-file> <site-name>");
  process.exit(1);
}

const html = readFileSync(htmlPath, "utf-8");
const url = `https://example.test/${siteName}`;

function metrics(label: string, markdown: string): { label: string; lines: number; refStubs: number; bytes: number } {
  const lines = markdown.split("\n").length;
  const refStubs = (markdown.match(/^\d+\.$/gm) ?? []).length;
  return { label, lines, refStubs, bytes: markdown.length };
}

// Baseline — pristine document into Defuddle
function makeDoc(): Document {
  const win = new Window({ url, settings: { disableCSSFileLoading: true, disableJavaScriptEvaluation: true } });
  const doc = win.document as unknown as Document;
  doc.documentElement.innerHTML = html;
  return doc;
}

const baselineDoc = makeDoc();
const baseline = new Defuddle(baselineDoc, { url, separateMarkdown: true }).parse();
const baselineMd = baseline.contentMarkdown ?? "";
const baselineMetrics = metrics("baseline", baselineMd);

// Print-pass — apply switch, force layout pass, run Defuddle
const printDoc = makeDoc();
const switchReport = applyPrintStylesheets(printDoc);
const removed = removeHiddenByPrint(printDoc);
const printRun = new Defuddle(printDoc, { url, separateMarkdown: true }).parse();
const printMd = printRun.contentMarkdown ?? "";
const printMetrics = metrics("print-pass", printMd);

// Save outputs for eyeball comparison
const outBase = `tmp/spike-${siteName}`;
try {
  writeFileSync(`${outBase}-baseline.md`, baselineMd, "utf-8");
  writeFileSync(`${outBase}-print.md`, printMd, "utf-8");
} catch (err) {
  console.error("write failed (mkdir tmp/ first?):", err);
}

console.log("=".repeat(60));
console.log(`SITE: ${siteName}`);
console.log(`HTML bytes: ${html.length}`);
console.log(`Print switch report:`, switchReport, `removedHidden: ${removed}`);
console.log("");
console.table([baselineMetrics, printMetrics]);
console.log("");
console.log(`Δ lines:    ${printMetrics.lines - baselineMetrics.lines}`);
console.log(`Δ refStubs: ${printMetrics.refStubs - baselineMetrics.refStubs}`);
console.log(`Δ bytes:    ${printMetrics.bytes - baselineMetrics.bytes}`);
console.log("");
console.log(`Outputs: ${outBase}-{baseline,print}.md`);
