# 自由艦隊（Freedom Fleet）拆成獨立 repo — 執行計畫

> 2026-11-09 修修裁決：**選項 A，拆成獨立 repo**，名稱 **Freedom Fleet**。
> 本文件是 P10 戰略 ＋ 逐 phase 的 P9 六要素 task prompt，交給低階模型執行。
> 執行者不得偏離本文件做「順手重構」；文件沒寫的事，停下來問。

---

## 0. 一句話

把 `agents/sanji/`、`wp/fleet-gamification/`、相關 docs/memory/tests 從 nakama 搬到新 repo
`shosho-chang/freedom-fleet`（本機 `E:\freedom-fleet`），VPS 上 Sanji 服務改從新 checkout 跑，
nakama 只留「fleet.shosho.tw 是修修的其中一個網站」這層站務（SEO / GSC / 外部探針）。

## 1. 已盤點的事實（執行者不必重查，但引用時請對照行號）

### 1.1 搬走的程式

| 路徑 | 大小 | 說明 |
|---|---|---|
| `agents/sanji/` | 16 檔，Python ~1,760 行（含 `gateway/handlers/sanji.py`） | 規則 `rules.py`、輪詢 `loop.py`、判定 `judge.py`、對帳 `reconcile.py`、`store.py`、`wp_client.py`、`settings.py`、`templates/`、`CONTEXT.md`（931 行）、`README.md` |
| `wp/fleet-gamification/` | PHP/JS ~3,780 行 | `includes/`（10 個 class ＋ `migrations/001–003`）、`assets/`、`tools/{contract-probe,bunny-media-migrate,setup-uat}.php` |
| `prompts/sanji/persona.md` | — | **不搬**（Slack 人格留 nakama，見 D2） |

### 1.2 Sanji 對 nakama 的依賴（全部）

```
agents/sanji/__main__.py:18   from shared.log import get_logger
agents/sanji/__main__.py:81   from shared.llm_context import set_runtime_group
agents/sanji/judge.py:21      from shared.agent_sdk import log_sdk_exception, log_sdk_message, subscription_env
agents/sanji/judge.py:22      from shared.llm_router import get_model
agents/sanji/loop.py:23       from shared.log import get_logger
agents/sanji/reconcile.py:26  from shared.alerts import alert
agents/sanji/settings.py:13   from shared.config import get_agent_config
agents/sanji/store.py:18      from shared.config import get_db_path
agents/sanji/wp_client.py:17  from shared.log import get_logger
```

第三方：`httpx`、`claude-agent-sdk==0.2.158`、`pyyaml`、`python-dotenv`、stdlib `sqlite3`。

被引用的 shared 函式定義位置：
`shared/agent_sdk.py:147 subscription_env`、`:363 log_sdk_message`、`:392 log_sdk_exception`；
`shared/llm_router.py:216 get_model`（sanji 的 registry 項在 `:69`，SDK 別名表 `:314`）；
`shared/alerts.py:47 alert`（內部依賴 `shared.state` 與 `agents.franky.slack_bot` — **不可照搬**）；
`shared/config.py:52 get_db_path`、`:83 get_agent_config`；`shared/log.py:209 get_logger`
（內部依賴 `shared.obsidian_writer`、`shared.log_index` — **不可照搬**）；
`shared/llm_context.py:155 set_runtime_group`。

### 1.3 nakama 反向依賴 Sanji 的地方（nakama 核心沒有任何 `import agents.sanji`）

| 位置 | 處置 |
|---|---|
| `gateway/handlers/__init__.py:8,15` 註冊 `SanjiHandler` | **留**（D2） |
| `gateway/handlers/sanji.py`、`gateway/router.py:23,31,40`、`gateway/bot.py:85`、`gateway/orchestrator.py:49` | **留**（D2） |
| `shared/llm_router.py:69` `ModelSite("sanji", ...)` | **留**（Slack 人格仍走 facade） |
| `thousand_sunny/routers/bridge.py:93-100` 船員卡 `S-05 sanji` | **留**（Bridge 船員表） |
| `scripts/deploy_vps.sh:13-17, 113, 126, 144-152, 158, 169, 183, 234-273` | **移除** nakama-sanji 與 plugin sync 區塊 |
| `config.yaml:42-48` `agents.sanji` 區塊 | `wordpress:` 子區塊搬走；`description`/`schedule` 留給 Slack 人格 |
| `tests/agents/test_sanji_{checkin_gate,display_groups,judge_templates,rules,store}.py`（62 tests，2.7s 全綠） | **搬** |
| `tests/shared/test_agent_sdk.py:463-480` 兩個 `from agents.sanji import judge` 的測試 | **搬**到新 repo（改測新 repo 的 agent_sdk 複本） |
| `tests/test_sanji_handler.py` | **留** |
| `CONTEXT-MAP.md:15,29,32`、`ARCHITECTURE.md:22`、`README.md:19,35`、`docs/agents/domain.md:13` | 改寫為「Sanji 社群營運已移至 freedom-fleet repo，nakama 只留 Slack 人格」 |
| `agents/franky/health_check.py:91` 註解、`.github/workflows/external-probe.yml` 的 fleet 站探針、`shared/schemas/seo.py:39`、`shared/schemas/site_mapping.py:22`、`agents/franky/jobs/gsc_daily.py:64`、`thousand_sunny/routers/franky.py:156`、`prompts/nami/morning_brief.md:11`、`agents/nami/README.md:22`、`prompts/franky/weekly_report.md:47` | **全部留**——這些是「fleet.shosho.tw 是修修的網站之一」的站務面，不是遊戲化 |

### 1.4 搬走的文件與記憶

- `docs/plans/fleet-gamification-master-plan.md`
- `docs/plans/2026-08-30-gamification-handoff.md`
- `docs/research/2026-08-22-gamification-panel-review.md`
- `memory/shared/reference/fleet_gamification_stack.md`
- `memory/shared/reference/fleet_community_stack.md`
- `memory/shared/decision/fluentcart_single_grant_channel.md`
- `memory/claude/reference_fleet_gamification_stack.md`（已是 shared 版的指標，搬走後在 nakama 刪）
- `memory/claude/MEMORY.md:8,377` 兩條索引 → 改成一條「自由艦隊已拆至 `E:\freedom-fleet`，一切見該 repo」

### 1.5 VPS 現況（來自 `memory/shared/reference/fleet_gamification_stack.md:32-48`；**執行者在 Phase 2 開頭必須逐項實地複驗**，我這邊的 ssh 唯讀被權限擋下，沒有親眼看過 unit 檔）

- systemd `nakama-sanji.service`（`python3 -m agents.sanji loop`，90s 輪詢）
- systemd `nakama-sanji-reconcile.timer`（05:00）
- checkout `/home/nakama`，唯讀 deploy key `/root/.ssh/nakama_deploy_ed25519`，repo-local `core.sshCommand`
- plugin 目錄 `/var/www/fleet.shosho.tw/wp-content/plugins/fleet-gamification`，wp-cli 以 `u2_fleet_shosho` 跑
- Sanji 的 SQLite 投影表 `sanji_cursors` / `sanji_checkin_days` / `sanji_judgments` 住在 nakama 的 `state.db`
  （路徑由 `NAKAMA_DATA_DIR` → `DB_PATH` → `config.yaml db_path` 決定，`shared/config.py:52-65`）
- `.env` 裡 Sanji 讀的 key：`GAM_WP_BASE`、`GAM_WP_USER`、`GAM_WP_APP_PASSWORD`、`GAM_SANJI_USER_ID`、
  `GAM_SCORED_SOURCES`、`GAM_POLL_SECONDS`、`GAM_FAIL_OPEN_HOURS`（`agents/sanji/settings.py:35-66`）
  ＋ Agent SDK 的 `CLAUDE_CODE_OAUTH_TOKEN`（`subscription_env` 用）
- **未知、必查**：unit 檔的 `User=`、`WorkingDirectory=`、`EnvironmentFile=`、`ExecStart=` 完整內容；venv 路徑；python 版本；`.env` 是否還有其他 Sanji 用到的 key。

### 1.6 GitHub

- `gh` 已登入 `shosho-chang`（active）；`shosho-chang/freedom-fleet` **尚不存在**（已確認）。
- nakama 是 PUBLIC repo、main 受保護（strict ＋ enforce_admins，見 `memory/claude/feedback_pr_merge_active_repo.md`）。
- nakama pack 1.76 GiB、676 commits。

---

## 2. 裁決（執行者照做；想改先問）

| # | 裁決 | 理由 |
|---|---|---|
| **D1** | **新 repo 不做歷史改寫**（不跑 `git filter-repo`），第一個 commit 是搬入的檔案；在新 repo `HISTORY.md` 記「2026-11-09 自 shosho-chang/nakama 搬入，歷史見該 repo `git log -- agents/sanji wp/fleet-gamification`」 | nakama pack 1.76 GiB，filter-repo 要 fresh clone 且易錯；歷史永遠留在 nakama，用連結即可。 |
| **D2** | **Slack 上的 Sanji 人格留在 nakama**（`gateway/handlers/sanji.py` ＋ `prompts/sanji/persona.md` ＋ router/orchestrator/bot 相關行） | 它是 Slack 海賊團群聊的一員，吃的是 nakama 的 LLM facade 與記憶，跟遊戲化程式零相依；拔掉會動到 router/orchestrator/bot 十幾個測試。日後若要讓它從新 repo 讀帳本，再開 ADR。 |
| **D3** | 新 repo **不依賴 nakama package**；把用到的 shared 函式以**最小複本**放進 `fleet/shared/`（見 §3.2） | 兩個 repo 之後各自演化；Sanji 只用 8 個函式，複本總量 < 300 行。 |
| **D4** | 告警走 **Slack Incoming Webhook**（env `FLEET_ALERT_SLACK_WEBHOOK`），不再經 Franky bot | nakama 的 `alert()` 綁 Franky slack bot 與 incident archive，搬不動。webhook 零依賴。頻道由修修在 Slack 建（HITL）。 |
| **D5** | 新 repo 用自己的 SQLite `data/fleet.db`；cutover 時把 `sanji_*` 三張表從 nakama `state.db` **複製**過去（舊表不刪，留 rollback） | cursor 掉了會重掃事件（WP 端 idempotency 會擋重複入帳，但 pending judgments 會丟）。 |
| **D6** | VPS：新 checkout `/home/freedom-fleet`，新 unit `fleet-sanji.service` ＋ `fleet-sanji-reconcile.{service,timer}`；舊 `nakama-sanji*` **stop + disable，不刪**，7 天後再清 | 可一分鐘 rollback。 |
| **D7** | 新 repo 自帶 `scripts/deploy.sh`（pull → pip → restart fleet-sanji → plugin lint/rsync/probe → health）；邏輯從 `scripts/deploy_vps.sh` **對應區塊**抄，不重新發明 | 2026-08-22「不開第二條 deploy 路」裁決是在單一 repo 前提下做的；拆 repo 後每 repo 一條是新前提，本計畫明文推翻該裁決。 |
| **D8** | Python package 名 `fleet`；佈局 `fleet/sanji/`、`fleet/shared/`、`wp/fleet-gamification/`、`tests/`、`docs/`、`memory/` | 與域名 `fleet.shosho.tw`、plugin 名一致；未來角色放 `fleet/<name>/`。 |
| **D9** | 新 repo **private** | 含會員系統營運細節與 vendor 內部行號。 |
| **D10** | 順序：**先建好新 repo 並在本機全綠 → VPS 平行安裝並切換 → 跑 24h → 才合併 nakama 的移除 PR** | nakama 移除 PR 一合併 `deploy_vps.sh` 就不再管 Sanji；要確定新線已接手。 |

---

## 3. Phase 1 — 建 Freedom Fleet repo（零 production 影響）

### 六要素

1. **目標**：`E:\freedom-fleet` 能 `pytest` 全綠、`ruff check/format --check` 全綠、`python -m fleet.sanji --help` 跑得起來。
2. **範圍**：新 repo 全部；**不碰 nakama 任何檔案**（讀可以）。
3. **輸入**：§1 所列 nakama 路徑（在 nakama worktree 讀，用 `git show main:<path>` 或直接 copy）。
4. **輸出**：GitHub private repo `shosho-chang/freedom-fleet`，main 上有完整程式、CI、CLAUDE.md、runbook。
5. **驗收**：§3.5。
6. **邊界**：不改規則（`rules.py` 逐字搬，分數表/等級曲線一個字元都不能動）；不改 plugin PHP 邏輯（只能改檔頭註解的路徑）；不在 nakama 寫檔；不碰 VPS。

### 3.1 建 repo

```bash
gh repo create shosho-chang/freedom-fleet --private --description "自由艦隊 Freedom Fleet — 付費社群營運與遊戲化（Sanji）" --clone
# clone 到 E:\freedom-fleet
```

### 3.2 目錄與搬移對照

| nakama | freedom-fleet | 備註 |
|---|---|---|
| `agents/sanji/*.py`、`templates/` | `fleet/sanji/` | import 全改 `from fleet.shared...` / `from fleet.sanji...`；`__main__.py` 進入點 `python -m fleet.sanji` |
| `agents/sanji/CONTEXT.md`、`README.md` | `fleet/sanji/CONTEXT.md`、`README.md` | 內文路徑引用（`agents/sanji/` → `fleet/sanji/`、`../../memory/shared/...` → `../../memory/...`）用 grep 逐一改；**不改任何裁決內容** |
| `wp/fleet-gamification/` | `wp/fleet-gamification/` | 原樣；只改檔頭註解若有寫 nakama 路徑 |
| `shared/config.py:52-65,83-86` | `fleet/shared/config.py` | 精簡：`load_config()` 讀 repo 根 `config.yaml`；`get_db_path()` → env `FLEET_DB_PATH` 否則 `data/fleet.db`；`get_agent_config("sanji")` 讀 `config.yaml` 的 `sanji:` 區塊 |
| `shared/log.py:209` | `fleet/shared/log.py` | 標準 `logging` ＋ stderr handler，**不要** obsidian/log_index 那套 |
| `shared/alerts.py:47` | `fleet/shared/alerts.py` | `alert(title, body, severity)` → POST `FLEET_ALERT_SLACK_WEBHOOK`；沒設 webhook 時只 log warning（測試環境） |
| `shared/llm_router.py:216-260,314-335` | `fleet/shared/models.py` | `get_model(agent, task)`：env `MODEL_<AGENT>_<TASK>` → env `MODEL_<AGENT>` → 預設 `claude-sonnet-4-6`；保留 `SDK_MODEL_ALIASES` 對應（照抄那張表） |
| `shared/agent_sdk.py:147-170,363-420` | `fleet/shared/agent_sdk.py` | `subscription_env()`、`log_sdk_message()`、`log_sdk_exception()` **逐字複製**（`judge.py:74` 註解稱它承重牆） |
| `shared/llm_context.py:155` | `fleet/shared/llm_context.py` | `set_runtime_group()`；若它只是設 contextvar 給 nakama 其他模組看，新 repo 可做成 no-op 並註明 |
| `config.yaml:42-48` 的 `wordpress:` 子區塊 | `config.yaml` | ```yaml\nsanji:\n  wordpress:\n    community: fluent_community\n    alert_threshold:\n      unanswered_hours: 24\n``` 另加 `db_path: data/fleet.db` |
| `tests/agents/test_sanji_*.py`（5 檔） | `tests/sanji/` | import 改名；需要的 conftest fixture 只有 `isolated_db`（`tests/conftest.py:219`）的精簡版——用 `tmp_path` 指 `FLEET_DB_PATH` |
| `tests/shared/test_agent_sdk.py:463-480` 兩個測試 ＋ 它們用的 `lane_caplog` fixture（定義在同檔 `:86`） | `tests/shared/test_agent_sdk.py` | 改測 `fleet.shared.agent_sdk` |
| `docs/plans/fleet-gamification-master-plan.md`、`2026-08-30-gamification-handoff.md`、`docs/research/2026-08-22-gamification-panel-review.md` | `docs/` 同名 | 內文路徑改；§3 檔案地圖要更新成新佈局 |
| 4 個 memory 檔（§1.4） | `memory/` ＋ `memory/MEMORY.md` 索引 | 新 repo 不需要 bilingual frontmatter 規則，但**保留原 frontmatter 不刪** |
| `scripts/deploy_vps.sh` 的 nakama-sanji ＋ plugin 區塊 | `scripts/deploy.sh` | 見 D7；服務名改 `fleet-sanji`；healthz 段改為 `python -m fleet.sanji health` |
| （新） | `deploy/fleet-sanji.service`、`deploy/fleet-sanji-reconcile.service`、`deploy/fleet-sanji-reconcile.timer` | 內容**以 Phase 2 複驗到的舊 unit 為底**改路徑/名稱；Phase 1 先放 TODO 佔位，Phase 2 填 |
| （新） | `.env.example` | 列 §1.5 全部 GAM_* ＋ `CLAUDE_CODE_OAUTH_TOKEN` ＋ `FLEET_ALERT_SLACK_WEBHOOK` ＋ `FLEET_DB_PATH` ＋ `FLEET_WP`/`FLEET_WP_USER`（deploy 用） |
| （新） | `pyproject.toml` | name `freedom-fleet`，deps：`httpx>=0.27.0`、`pyyaml>=6.0`、`python-dotenv>=1.0`、`claude-agent-sdk==0.2.158`；dev：`pytest>=8.0`、`ruff>=0.15,<0.16`；`[tool.ruff]` 與 `[tool.pytest.ini_options]` 照 nakama 的 `pyproject.toml:112-131` |
| （新） | `.github/workflows/ci.yml` | ruff check ＋ ruff format --check ＋ pytest；**加 PHP lint job**：`find wp -name '*.php' -exec php -l {} \;`（ubuntu-latest 內建 php） |
| （新） | `CLAUDE.md` | 見 §3.3 |
| （新） | `HISTORY.md` | 見 D1 |
| （新） | `docs/runbooks/vps-cutover.md` | Phase 2 的逐條指令（Phase 1 先寫草稿，Phase 2 複驗後定稿） |

### 3.3 新 repo 的 CLAUDE.md（要點，執行者展開成文）

- 這是什麼：自由艦隊付費社群的營運系統；Sanji 是第一個角色。**不是**內容創作 pipeline，不要套 nakama 的七層 stage。
- 最高原則沿用：嚴禁幻想、決策只有兩種呈現模式（從 nakama `CLAUDE.md` 前兩節**逐字複製**）。
- 架構鐵則（從 `memory/fleet_gamification_stack.md` §架構的鐵則 ＋ `fleet/sanji/CONTEXT.md` 「Gamification Add-on」節摘）：規則只在 `fleet/sanji/rules.py`；plugin 是笨層；帳本在 WP MySQL、append-only；production 讀寫一律走 REST。
- 工作規範：直接引用 `docs/2026-08-30-gamification-handoff.md` §7。
- merge ≠ deployed：合併後要跑 `ssh nakama-vps 'cd /home/freedom-fleet && ./scripts/deploy.sh'`。
- Worktree 紀律：主 checkout `E:\freedom-fleet` 是 control plane，寫檔開 sibling worktree（照 nakama 規則，路徑改）。
- 刪檔用回收桶（照 nakama）。
- 回覆用繁體中文。

### 3.4 本機驗證

```bash
cd E:\freedom-fleet
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q                       # 預期 ≥ 64 passed
python -m fleet.sanji --help
find wp -name '*.php' -exec php -l {} \;   # 本機若無 php，改在 CI 看
```

### 3.5 Phase 1 DoD

- [ ] `gh repo view shosho-chang/freedom-fleet` 存在且 private
- [ ] 上述 5 條指令全綠；CI 在 main 綠
- [ ] `grep -rn "agents\.sanji\|from shared\.\|import shared" fleet tests` → 0 筆
- [ ] `diff <(git -C <nakama> show main:agents/sanji/rules.py | sed 's/from shared/from fleet.shared/;s/from agents\.sanji/from fleet.sanji/') fleet/sanji/rules.py` 只剩 import 行差異（**規則零變動**的證明，貼進 PR）
- [ ] 同法證明 `wp/fleet-gamification/**` 與 nakama main **逐 byte 相同**（檔頭註解若改，列出 diff）
- [ ] `CLAUDE.md`、`HISTORY.md`、`.env.example`、`docs/runbooks/vps-cutover.md`（草稿）存在

---

## 4. Phase 2 — VPS 平行安裝與切換（⚠️ HITL：每一條會動 production 的指令，由修修執行或逐條核准）

### 六要素

1. **目標**：`fleet-sanji.service` 與 `fleet-sanji-reconcile.timer` 從 `/home/freedom-fleet` 跑起來、接手輪詢；`nakama-sanji*` 停用。
2. **範圍**：VPS `/home/freedom-fleet`、systemd 兩組 unit、`/home/freedom-fleet/.env`、`/home/freedom-fleet/data/fleet.db`。**不碰** `/var/www/fleet.shosho.tw`（plugin 不變，不需 rsync）、不碰 nakama 的 `state.db`（唯讀 ATTACH）。
3. **輸入**：Phase 1 的 repo；§1.5 複驗結果。
4. **輸出**：定稿的 `docs/runbooks/vps-cutover.md` ＋ 實際執行紀錄（貼在新 repo issue）。
5. **驗收**：§4.3。
6. **邊界**：停機窗口 ≤ 5 分鐘；不刪舊 unit、不刪舊表；任何一步紅燈就 rollback（§4.4），不要硬修。

### 4.1 複驗（唯讀，先做）

```bash
ssh nakama-vps 'systemctl cat nakama-sanji.service nakama-sanji-reconcile.service nakama-sanji-reconcile.timer'
ssh nakama-vps 'cd /home/nakama && grep -oE "^(GAM_|CLAUDE_CODE_OAUTH|DB_PATH|NAKAMA_DATA_DIR)[A-Z_]*" .env; python3 --version; ls -d .venv venv 2>/dev/null'
ssh nakama-vps 'cd /home/nakama && python3 -c "from shared.config import get_db_path; print(get_db_path())"'
```
把結果填進 `deploy/*.service`、`docs/runbooks/vps-cutover.md`，**commit 到新 repo 後才進 4.2**。

### 4.2 切換（runbook 骨架，複驗後補實值）

1. 在 GitHub 新 repo 加 **唯讀 deploy key**（新產一把，`/root/.ssh/freedom_fleet_deploy_ed25519`，不要重用 nakama 那把）。
2. `git clone` 到 `/home/freedom-fleet`，`core.sshCommand` 指新 key；建 venv、`pip install -e .`。
3. 建 `/home/freedom-fleet/.env`：從 nakama `.env` 複製 §1.5 的 key（**只複製那幾個**，不要整檔 copy）＋ 新增 `FLEET_ALERT_SLACK_WEBHOOK`、`FLEET_DB_PATH=/home/freedom-fleet/data/fleet.db`。
4. `python -m fleet.sanji health` → 必須 OK（打 plugin `/health`，唯讀）。
5. 安裝 unit：`sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload`。
6. **停機窗口開始**：`sudo systemctl stop nakama-sanji.service nakama-sanji-reconcile.timer`。
7. 複製投影表：
   ```sql
   -- sqlite3 /home/freedom-fleet/data/fleet.db
   ATTACH '<4.1 查到的 state.db 路徑>' AS old;
   -- 先讓 fleet 建表：python -m fleet.sanji health 已 touch Store；若沒有，跑一次 python -c "from fleet.sanji.store import Store; Store()"
   INSERT INTO sanji_cursors      SELECT * FROM old.sanji_cursors;
   INSERT INTO sanji_checkin_days SELECT * FROM old.sanji_checkin_days;
   INSERT INTO sanji_judgments    SELECT * FROM old.sanji_judgments;
   SELECT (SELECT COUNT(*) FROM sanji_judgments)=(SELECT COUNT(*) FROM old.sanji_judgments);  -- 必須 1
   DETACH old;
   ```
8. `sudo systemctl enable --now fleet-sanji.service fleet-sanji-reconcile.timer`；`sudo systemctl disable nakama-sanji.service nakama-sanji-reconcile.timer`（**不** `rm` unit 檔）。
9. **停機窗口結束**。`journalctl -u fleet-sanji -f` 看到第一輪輪詢成功（cursor 前進或「no new events」）。
10. `systemctl list-timers fleet-sanji-reconcile` 顯示下一次 05:00。

### 4.3 Phase 2 DoD

- [ ] `systemctl is-active fleet-sanji` = active；`nakama-sanji` = inactive 且 disabled
- [ ] 第一輪 loop 日誌無 traceback；若剛好有新打卡，WP 端 `zcjf_nakama_gam_grants` 最新一列的時間在切換之後（證明新線在發分）
- [ ] 手動跑一次 `python -m fleet.sanji reconcile` 結束碼 0、無告警或告警正確送到新 webhook 頻道
- [ ] `./scripts/deploy.sh --dry-run` 在 `/home/freedom-fleet` 跑得過
- [ ] runbook 定稿 commit；執行紀錄貼 issue

### 4.4 Rollback（任何一步紅燈）

```bash
sudo systemctl stop fleet-sanji.service fleet-sanji-reconcile.timer
sudo systemctl enable --now nakama-sanji.service nakama-sanji-reconcile.timer
```
舊 `state.db` 未動，舊 code 未動，一分鐘內回到切換前。

---

## 5. Phase 3 — 24 小時觀察

- 隔日 05:00 對帳跑完後：`journalctl -u fleet-sanji-reconcile --since today` 無錯；對帳落差告警 = 0。
- 期間 nakama 這邊**不要**跑 `deploy_vps.sh`（它還會嘗試重啟已 disabled 的 `nakama-sanji`——unit 檔還在，`systemctl restart` 會把它拉起來，**造成雙寫**）。若 nakama 必須部署，先暫時把 `deploy_vps.sh` 的 `NEED[nakama-sanji]` 全部拿掉，或等 Phase 4。

---

## 6. Phase 4 — nakama 移除 PR

### 六要素

1. **目標**：nakama main 不再含遊戲化程式、部署腳本不再管 Sanji 服務與 plugin；CI 綠。
2. **範圍**（精確）：
   - 刪：`agents/sanji/`、`wp/`、`tests/agents/test_sanji_*.py`（5 檔）、`tests/shared/test_agent_sdk.py:463-480` 兩個測試、`docs/plans/fleet-gamification-master-plan.md`、`docs/plans/2026-08-30-gamification-handoff.md`、`docs/research/2026-08-22-gamification-panel-review.md`、`memory/shared/reference/fleet_gamification_stack.md`、`memory/shared/reference/fleet_community_stack.md`、`memory/shared/decision/fluentcart_single_grant_channel.md`、`memory/claude/reference_fleet_gamification_stack.md`
   - 改：`scripts/deploy_vps.sh`（§1.3 列的行）、`config.yaml:45-48`（刪 `wordpress:` 子區塊）、`CONTEXT-MAP.md:15,29,32`、`ARCHITECTURE.md:22`、`README.md:19,35`、`docs/agents/domain.md:13`、`memory/claude/MEMORY.md:8,377`；`pyproject.toml` 的 `[tool.setuptools.packages.find]` 與 `[tool.coverage.run]` 目前**沒有**列 `wp`，不用動（已查）
   - 新增：本文件已在；加 `docs/decisions/ADR-071-freedom-fleet-repo-split.md`（現有最大號是 ADR-070，開 PR 前再確認沒被搶號；內容＝本文件 §2 裁決表 ＋ 連結新 repo）
3. **輸入**：Phase 2/3 綠。
4. **輸出**：一個 PR，標題 `chore(fleet): 自由艦隊遊戲化移至 freedom-fleet repo`。
5. **驗收**：CI 綠；`grep -rnE "agents\.sanji|wp/fleet-gamification|nakama-sanji" --include=*.py --include=*.sh --include=*.yml --include=*.toml .` → 0 筆（docs/ADR 內的歷史敘述不算）；`tests/test_sanji_handler.py`、`test_gateway_router.py`、`test_orchestrator*.py`、`test_gateway_bot.py` 仍綠（D2 的證明）；`memory/INDEX.md` 若存在，跑 `python -m shared.memory_maintenance reindex` 重建。
6. **邊界**：不動 `gateway/handlers/sanji.py`、`prompts/sanji/`、`shared/llm_router.py:69`、`thousand_sunny/routers/bridge.py` 船員卡、任何 `fleet.shosho.tw` 站務引用（§1.3 最後一列）。`git add` 只 stage 列出的路徑。

合併後：`ssh nakama-vps 'cd /home/nakama && ./scripts/deploy_vps.sh'`；確認 plan 裡不再出現 `nakama-sanji`。

---

## 7. Phase 5 — 收尾（7 天後）

- VPS：`sudo rm /etc/systemd/system/nakama-sanji*.{service,timer}; sudo systemctl daemon-reload`（這是 unit 檔，不是 repo 檔，可用 rm；要修修核准）。
- VPS：nakama `state.db` 的 `sanji_*` 三張表 **留著不刪**（體積小，是稽核軌跡）；在 fleet repo `HISTORY.md` 記一筆。
- 本機 `E:\nakama\data\state.db` 若有 `sanji_*` 表，同樣留著。

---

## 8. 停下來問的時機（執行者）

- §1.5 複驗結果與記憶檔描述不符（例如 unit 檔不是 `python3 -m agents.sanji loop`）。
- `rules.py` 或 plugin 搬過去後測試紅，而原因不是 import 路徑。
- Phase 2 任何一步紅燈（先 rollback，再問）。
- 發現本文件沒列到的 Sanji 依賴或反向依賴。
- 需要在 Slack 建 webhook／頻道（HITL，修修做）。
- GitHub deploy key 要貼到 repo settings（HITL，修修做或核准 `gh repo deploy-key add`）。
