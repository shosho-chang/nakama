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
  - type: table
    name: All Tasks
    order:
      - ✅
      - file.name
      - priority
      - scheduled
      - 預估🍅
      - formula.實際🍅
      - formula.accuracy
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

## 👄 One Sentence About This Video


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

// Restore cached results on load
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
    const match = content.match(/##\s+👄[^\n]*\n+([\s\S]*?)(?=\n##|\n```)/);
    const query = match ? match[1].trim() : "";
    if (!query) {
      new Notice("⚠️ 請先填寫 One Sentence About This Video");
      return;
    }
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

## 🗝️ Keyword Research & Title Ideas

```dataviewjs
const cfg = dv.page("Scripts/nakama-config");
const ROBIN_URL = cfg?.robin_url ?? "";
const ROBIN_KEY = cfg?.robin_key ?? "";
const CONTENT_TYPE = dv.current().content_type ?? "youtube";
const fmtNum = (n) => n >= 1e6 ? (n/1e6).toFixed(1)+"M" : n >= 1e3 ? (n/1e3).toFixed(0)+"K" : String(n);
const badge = (v) => ({ high:"🔴 high", medium:"🟡 medium", low:"🟢 low" }[v] || v || "");
const srcBadge = (s) => ({ en:"🌍 en", zh:"🇹🇼 zh", both:"🌐 both" }[s] || s || "");
const pipe = (s) => String(s??"").replace(/\|/g,"\\|").replace(/\n/g," ");

function toMarkdown(data) {
  let md = "";
  if (data.analysis_summary)
    md += `> [!info] 策略摘要\n> ${pipe(data.analysis_summary)}\n\n`;

  if (data.keywords?.length) {
    md += `### 🔑 核心關鍵字\n\n`;
    md += `| 關鍵字 | 英文 | 搜尋量 | 競爭度 | 機會 | 來源 | 分析 |\n|---|---|---|---|---|---|---|\n`;
    for (const kw of data.keywords)
      md += `| **${pipe(kw.keyword)}** | ${pipe(kw.keyword_en)} | ${badge(kw.search_volume)} | ${badge(kw.competition)} | ${badge(kw.opportunity)} | ${srcBadge(kw.source)} | ${pipe(kw.reason)} |\n`;
    md += `\n`;
  }

  if (data.trend_gaps?.length) {
    md += `### 🌍 趨勢缺口\n\n`;
    md += `| 趨勢 | 英文端信號 | 中文端狀態 | 機會 |\n|---|---|---|---|\n`;
    for (const g of data.trend_gaps)
      md += `| **${pipe(g.topic)}** | ${pipe(g.en_signal)} | ${pipe(g.zh_status)} | ${pipe(g.opportunity)} |\n`;
    md += `\n`;
  }

  if (data.trending_videos?.length) {
    const long = data.trending_videos.filter(v => !v.is_short).slice(0,10);
    const shorts = data.trending_videos.filter(v => v.is_short).slice(0,10);
    if (long.length) {
      md += `### 🔥 熱門長影片\n\n`;
      md += `| 標題 | 頻道 | 觀看數 | 日期 |\n|---|---|---|---|\n`;
      for (const v of long)
        md += `| [${pipe(v.title)}](${v.url}) | ${pipe(v.channel)} | **${fmtNum(v.views)}** | ${v.published} |\n`;
      md += `\n`;
    }
    if (shorts.length) {
      md += `### ⚡ 熱門 Shorts\n\n`;
      md += `| 標題 | 頻道 | 觀看數 | 日期 |\n|---|---|---|---|\n`;
      for (const v of shorts)
        md += `| [${pipe(v.title)}](${v.url}) | ${pipe(v.channel)} | **${fmtNum(v.views)}** | ${v.published} |\n`;
      md += `\n`;
    }
  }

  if (data.social_posts?.length) {
    md += `### 💬 社群討論\n\n`;
    md += `| 平台 | 內容 | 互動 | 語言 |\n|---|---|---|---|\n`;
    for (const p of data.social_posts.slice(0,10)) {
      if (p.platform === "reddit")
        md += `| 🟠 r/${pipe(p.subreddit)} | [${pipe(p.title)}](${p.url}) | ⬆${fmtNum(p.score)} 💬${p.num_comments} | ${p.lang==="en"?"🌍":"🇹🇼"} |\n`;
      else
        md += `| 🐦 @${pipe(p.username)} | [${pipe((p.text||"").slice(0,80))}](${p.url}) | ❤️${fmtNum(p.likes||0)} 🔄${p.retweets||0} | ${p.lang==="en"?"🌍":"🇹🇼"} |\n`;
    }
    md += `\n`;
  }

  if (data.youtube_titles?.length) {
    md += `### 🎬 YouTube 標題建議\n\n`;
    for (let i = 0; i < data.youtube_titles.length; i++)
      md += `${i+1}. ${data.youtube_titles[i]}\n`;
    md += `\n`;
  }

  if (data.blog_titles?.length) {
    md += `### 📝 Blog 標題建議\n\n`;
    for (let i = 0; i < data.blog_titles.length; i++)
      md += `${i+1}. ${data.blog_titles[i]}\n`;
    md += `\n`;
  }

  return md.trim();
}

const btn = this.container.createEl("button", { text: "🗝️ 關鍵字研究 + 標題建議", attr: { style: "padding:6px 14px;cursor:pointer;border-radius:4px;" } });
btn.onclick = async () => {
  if (!ROBIN_URL || ROBIN_URL.includes("YOUR_VPS")) { new Notice("⚠️ 請先設定 Scripts/nakama-config.md 的 robin_url"); return; }
  btn.disabled = true; btn.textContent = "⏳ Zoro 研究中...";
  try {
    const { requestUrl } = require("obsidian");
    const file = app.workspace.getActiveFile();
    const content = await app.vault.read(file);
    const topic = dv.current().search_topic || dv.current().file.name;
    const resp = await requestUrl({ url: ROBIN_URL + "/zoro/keyword-research", method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", "X-Robin-Key": ROBIN_KEY }, body: "topic=" + encodeURIComponent(topic) + "&content_type=" + encodeURIComponent(CONTENT_TYPE) });
    const data = typeof resp.json === "string" ? JSON.parse(resp.json) : resp.json;
    const md = toMarkdown(data);
    const START = "%" + "%KW-START%" + "%";
    const END = "%" + "%KW-END%" + "%";
    let updated = await app.vault.read(file);
    if (updated.includes(START) && updated.includes(END)) {
      const re = new RegExp(START + "[\\s\\S]*?" + END);
      updated = updated.replace(re, START + "\n" + md + "\n" + END);
    } else {
      const anchor = "## Script / Outline";
      const idx = updated.indexOf(anchor);
      if (idx !== -1) updated = updated.slice(0, idx) + START + "\n" + md + "\n" + END + "\n\n" + updated.slice(idx);
    }
    await app.vault.modify(file, updated);
    new Notice("✅ 關鍵字研究完成（" + (data.sources_used||[]).join(", ") + "）");
  } catch (e) { new Notice("❌ Zoro 連線失敗：" + e.message); }
  finally { btn.disabled = false; btn.textContent = "🗝️ 關鍵字研究 + 標題建議"; }
};
```

%%KW-START%%
%%KW-END%%

## Script / Outline



## 專案筆記

