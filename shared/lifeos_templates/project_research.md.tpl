# __TITLE__

## 🎯 對應 OKR
- **季度計畫**：`= this.quarter`
- **關鍵結果**：`= this.parent_kr`

## ✅ Tasks

```base
filters:
  and:
    - file.hasTag("task")
    - file.inFolder("TaskNotes/Tasks")
    - projects.contains(link("__TITLE__"))
formulas:
  實際🍅: if(timeEntries, (list(timeEntries).filter(value.endTime).map((number(date(value.endTime)) - number(date(value.startTime))) / 60000).reduce(acc + value, 0) / 25).floor(), 0)
  accuracy: if("預估🍅" && "預估🍅" > 0 && formula.實際🍅 > 0, (formula.實際🍅 / "預估🍅" * 100).round(), null)
views:
  - type: table
    name: Active
    order:
      - ✅
      - file.name
      - priority
      - scheduled
      - 預估🍅
      - formula.實際🍅
    sort:
      - property: scheduled
        direction: ASC
```

## 📊 番茄統計

```dataviewjs
const name = dv.current().file.name;
const tasks = dv.pages('"TaskNotes/Tasks"').where(p =>
  p.projects && String(p.projects).includes(name)
);

const totalEst = tasks.values.reduce((s, p) => s + (Number(p["預估🍅"]) || 0), 0);

const totalTrackedMin = tasks.values.reduce((s, p) => {
  if (!p.timeEntries) return s;
  const entries = Array.from(p.timeEntries);
  return s + entries
    .filter(e => e.endTime)
    .reduce((sum, e) => sum + (new Date(String(e.endTime)) - new Date(String(e.startTime))) / 60000, 0);
}, 0);
const totalActual = Math.floor(totalTrackedMin / 25);

const accuracy = totalEst > 0 && totalActual > 0
  ? Math.round(totalActual / totalEst * 100) + "%"
  : "—";

const done = tasks.values.filter(p => p.status === "done" || p.status === "achieved").length;
const total = tasks.values.length;

dv.table(
  ["Tasks 完成", "預估🍅", "實際🍅", "預估準確率"],
  [[(done + " / " + total), totalEst, totalActual, accuracy]]
);
```

---

## 專案描述


## 預期成果


## 📚 KB Research

```dataviewjs
const cfg = dv.page("Scripts/nakama-config");
const ROBIN_URL = cfg?.robin_url ?? "";
const ROBIN_KEY = cfg?.robin_key ?? "";
const CACHE_KEY = "kb-results-" + dv.current().file.name;

const container = this.container;

function renderResults(results) {
  const old = container.querySelector(".kb-results");
  if (old) old.remove();
  if (!results || results.length === 0) return;

  const wrap = container.createEl("div", { cls: "kb-results" });
  wrap.style.marginTop = "1rem";
  const tbl = wrap.createEl("table", { attr: { style: "width:100%;border-collapse:collapse;" } });
  const hdr = tbl.createEl("thead").createEl("tr");
  ["類型","標題","相關原因"].forEach(h =>
    hdr.createEl("th", { text: h, attr: { style: "text-align:left;padding:6px 8px;border-bottom:2px solid var(--background-modifier-border);" } })
  );
  const body = tbl.createEl("tbody");
  for (const r of results) {
    const row = body.createEl("tr");
    const cellStyle = "padding:6px 8px;border-bottom:1px solid var(--background-modifier-border);";
    row.createEl("td", { text: r.type, attr: { style: cellStyle } });
    const td = row.createEl("td", { attr: { style: cellStyle } });
    const link = td.createEl("a", { text: r.title, cls: "internal-link", attr: { href: r.path } });
    link.addEventListener("click", e => { e.preventDefault(); app.workspace.openLinkText(r.path, ""); });
    row.createEl("td", { text: r.relevance_reason, attr: { style: cellStyle + "font-size:0.9em;color:var(--text-muted);" } });
  }
}

try {
  const cached = localStorage.getItem(CACHE_KEY);
  if (cached) renderResults(JSON.parse(cached));
} catch(e) {}

const btn = container.createEl("button", {
  text: "🔍 從 KB 抓取相關素材",
  attr: { style: "padding:6px 14px;cursor:pointer;border-radius:4px;" }
});

btn.onclick = async () => {
  if (!ROBIN_URL || ROBIN_URL.includes("YOUR_VPS")) {
    new Notice("⚠️ 請先設定 Scripts/nakama-config.md 的 robin_url");
    return;
  }
  btn.disabled = true;
  btn.textContent = "⏳ Robin 搜尋中...";
  try {
    const { requestUrl } = require("obsidian");
    const file = app.workspace.getActiveFile();
    const content = await app.vault.read(file);
    const match = content.match(/##\s+專案描述[^\n]*\n+([\s\S]*?)(?=\n##)/);
    const query = match ? match[1].trim() : dv.current().file.name;
    const resp = await requestUrl({
      url: ROBIN_URL + "/kb/research",
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Robin-Key": ROBIN_KEY },
      body: "query=" + encodeURIComponent(query),
    });
    const results = resp.json.results ?? [];
    if (results.length === 0) { new Notice("找不到相關 KB 素材。"); return; }
    renderResults(results);
    localStorage.setItem(CACHE_KEY, JSON.stringify(results));
    new Notice("✅ 找到 " + results.length + " 筆相關 KB 素材");
  } catch (e) {
    new Notice("❌ Robin 連線失敗：" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔍 從 KB 抓取相關素材";
  }
};
```

## 🪄 Brook: Scaffold

```dataviewjs
// ADR-027 PR-6: Brook scaffold trigger.
// Reads the Project page's nested frontmatter (`line`, `zoro_inputs`) per
// docs/schemas/project-frontmatter-nested.md and POSTs to
// /api/projects/{slug}/synthesize/run. Only Line 3 (topic-driven) calls
// Brook synthesize from this button. Line 2 = Robin RCP (different surface);
// Line 1b = Stage 5 extractor (different flow).
const cfg = dv.page("Scripts/nakama-config");
const ROBIN_URL = cfg?.robin_url ?? "";
const ROBIN_KEY = cfg?.robin_key ?? "";

const fm = dv.current();
const slug = fm.file.name;
const line = String(fm.line ?? "3");
const topic = fm.topic ?? fm.file.name;
const zoro = fm.zoro_inputs ?? {};
const keywords = Array.from(zoro.keywords ?? []);
const trending = Array.from(zoro.trending_angles ?? []);

const container = this.container;
const status = container.createEl("div", { attr: { style: "font-size:0.9em;color:var(--text-muted);margin-bottom:6px;" } });
status.setText(`line=${line}・keywords=${keywords.length}・trending_angles=${trending.length}`);

const btn = container.createEl("button", {
  text: "🪄 Brook: Scaffold",
  attr: { style: "padding:6px 14px;cursor:pointer;border-radius:4px;" }
});

btn.onclick = async () => {
  if (line === "2") {
    new Notice("Line 2 scaffold = Robin RCP. 開啟 /writing-assist/{source_id} 而非 Brook synthesize。");
    return;
  }
  if (line === "1b") {
    new Notice("Line 1b 是 Stage 5 (post-atomic)，不走 Brook scaffold。請先完成 transcript ingest。");
    return;
  }
  if (!ROBIN_URL || ROBIN_URL.includes("YOUR_VPS")) {
    new Notice("⚠️ 請先設定 Scripts/nakama-config.md 的 robin_url");
    return;
  }
  if (keywords.length === 0) {
    new Notice("⚠️ zoro_inputs.keywords 為空 — 先按 🗝️ 跑 Zoro 關鍵字研究");
    return;
  }
  btn.disabled = true; btn.textContent = "⏳ Brook synthesize 中...";
  try {
    const { requestUrl } = require("obsidian");
    const resp = await requestUrl({
      url: ROBIN_URL + "/api/projects/" + encodeURIComponent(slug) + "/synthesize/run",
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Robin-Key": ROBIN_KEY },
      body: JSON.stringify({ topic, keywords, trending_angles: trending }),
    });
    const data = typeof resp.json === "string" ? JSON.parse(resp.json) : resp.json;
    const nSections = (data.outline_draft ?? []).length;
    const nPool = (data.evidence_pool ?? []).length;
    const reviewUrl = ROBIN_URL + "/projects/" + encodeURIComponent(slug);
    const old = container.querySelector(".brook-scaffold-result");
    if (old) old.remove();
    const wrap = container.createEl("div", { cls: "brook-scaffold-result", attr: { style: "margin-top:8px;" } });
    wrap.createEl("div", { text: `✅ Outline ${nSections} 段，evidence pool ${nPool} 個 source` });
    const a = wrap.createEl("a", { text: "→ 開啟 /projects/" + slug + " review 頁面", attr: { href: reviewUrl, target: "_blank" } });
    a.style.display = "inline-block";
    a.style.marginTop = "4px";
    new Notice("✅ Brook scaffold 完成");
  } catch (e) {
    new Notice("❌ Brook 連線失敗：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "🪄 Brook: Scaffold";
  }
};
```

## Literature Notes



## Synthesis



## 專案筆記

