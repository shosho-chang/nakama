"""Nami handler — LLM tool-use agent loop。

取代舊 state-machine 架構：讓 Claude 自己用 tool_use 決定下一步動作
（create_project / create_task / list_tasks / ask_user）。

關鍵設計：
- ``ask_user`` 是特殊 tool — LLM 呼叫時，我們**不執行**，而是把問題回 Slack
  thread 並存住 messages，等使用者下一條訊息進來時，把它當成 tool_result
  丟回去繼續 loop。
- 其他 tool 直接執行、把結果包成 tool_result、繼續 loop。
- 單次呼叫最多 ``_MAX_ITERS`` 輪，避免 LLM 卡在無限 tool 循環。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gateway.handlers.base import BaseHandler, Continuation, HandlerResponse
from shared import agent_memory, google_calendar, google_gmail
from shared.events import emit
from shared.google_calendar import CalendarEvent, GoogleCalendarAuthError
from shared.google_gmail import GoogleGmailAuthError
from shared.lifeos_writer import (
    CONTENT_TYPES,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
)
from shared.llm import ask_with_tools
from shared.llm_context import set_current_agent
from shared.log import get_logger, kb_log
from shared.memory_extractor import extract_in_background
from shared.obsidian_writer import delete_page, list_files, read_page, write_page
from shared.prompt_loader import load_prompt
from shared.vault_rules import VaultRuleViolation, assert_nami_can_read, assert_nami_can_write

logger = get_logger("nakama.gateway.nami")

NAMI_AGENT_FLOW = "nami_agent"
TASK_DIR = "TaskNotes/Tasks"
PROJECT_DIR = "Projects"

_MAX_ITERS = 15
_MODEL = "claude-sonnet-4-6"

# ── Tool definitions（stable, will be prompt-cached） ──────────────────

NAMI_TOOLS: list[dict] = [
    {
        "name": "create_project",
        "description": (
            "建立新的 LifeOS Project（含三個預設 task）。"
            "只有當你確定 topic 與 content_type 時才呼叫。"
            "若缺這兩項，先用 ask_user 問。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "專案主題（繁體中文，去掉「建立」「幫我」等指令詞）",
                },
                "content_type": {
                    "type": "string",
                    "enum": list(CONTENT_TYPES),
                    "description": "專案類型",
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "health", "family", "self-growth", "play", "visibility"],
                    "description": "領域，沒提就填 work",
                },
                "priority": {
                    "type": "string",
                    "enum": ["first", "high", "medium", "low"],
                    "description": "優先級，沒提就填 medium",
                },
                "search_topic": {
                    "type": "string",
                    "description": "SEO 關鍵字（只有 youtube/blog 才適用）",
                },
            },
            "required": ["topic", "content_type"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "建立 Task 檔案。可以獨立存在，也可以 linked 到某個 project。"
            "當使用者說「提醒我」「下週要」「加個 task」等時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任務標題"},
                "scheduled": {
                    "type": "string",
                    "description": (
                        "排程日期時間 ISO 8601。"
                        "有時間就填 datetime（例：2026-04-23T15:00:00）；"
                        "只有日期就填 date（例：2026-04-23）。沒講就不填。"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "normal", "low"],
                    "description": "沒講就填 normal",
                },
                "project": {
                    "type": "string",
                    "description": "掛在哪個 project 的名稱（若有）",
                },
                "category": {
                    "type": "string",
                    "enum": ["work", "health", "growth", "misc"],
                    "description": (
                        "依任務內容判斷分類（只有 work 計入 🍅 統計）："
                        " work = 專案、寫作、開發、商業、工作相關任務；"
                        " health = 運動、睡眠、飲食、醫療；"
                        " growth = 閱讀、學習、課程、技能培養、個人成長；"
                        " misc = 雜務、行政、家事、不屬於以上三類的事項。"
                        " 使用者沒說就自行判斷，不要問。"
                    ),
                },
                "est_pomodoros": {
                    "type": "integer",
                    "description": "預估幾顆番茄（25 分鐘一顆），沒講就 4",
                },
                "notes": {"type": "string", "description": "備註"},
            },
            "required": ["title", "category"],
        },
    },
    {
        "name": "list_tasks",
        "description": "列出所有待辦 task（status=to-do / in-progress）。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "update_task",
        "description": (
            "修改現有 Task 的欄位（排程日期、優先級、狀態、預估番茄數）。"
            "當使用者說「改」「設」「調整日期」「把...改成」「完成了」「番茄設成」等時使用。"
            "若找不到 task，回傳錯誤讓 LLM 告知使用者。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要修改的任務標題（用來搜尋，不需完全符合）",
                },
                "scheduled": {
                    "type": "string",
                    "description": (
                        "新的排程日期時間 ISO 8601。"
                        "有時間就填 datetime（例：2026-04-23T15:00:00）；"
                        "只有日期就填 date（例：2026-04-23）。"
                        "要清除排程就填空字串。"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "normal", "low"],
                    "description": "新的優先級",
                },
                "status": {
                    "type": "string",
                    "enum": ["to-do", "in-progress", "done"],
                    "description": "新的狀態",
                },
                "pomodoros": {
                    "type": "integer",
                    "description": "預估番茄數（pomodoro 數量，例如 4）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_task",
        "description": (
            "刪除現有 Task 檔案。呼叫前必須先用 ask_user 告知使用者將刪除哪個 task 並請確認。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要刪除的任務標題（用來搜尋，不需完全符合）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_project",
        "description": (
            "刪除 Project 檔案，可選擇一併刪除該 project 下的所有 tasks。"
            "呼叫前必須先用 ask_user 列出將刪除的所有檔案並請使用者確認。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要刪除的 project 標題（用來搜尋）",
                },
                "include_tasks": {
                    "type": "boolean",
                    "description": "是否一併刪除該 project 下的所有 tasks（預設 true）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "建立 Google Calendar 事件，預設同時建立對應的 Obsidian Task"
            "（方便在 Tasks view 看到）。預設會先檢查時段衝突，若有重疊事件"
            "會回傳衝突資訊（不建立）— 此時用 ask_user 問使用者要改時段還是"
            "覆蓋。使用者確認要覆蓋時用 force=true 再呼叫一次。"
            "純事件（婚禮、生日、紀念日）不需要 task 的話用 also_create_task=false。"
            "適用於「排會議」「排行程」「XX 點跟 XX 開會」等需求。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "事件標題"},
                "start": {
                    "type": "string",
                    "description": (
                        "開始時間 ISO 8601 本地時間（例：2026-04-25T15:00:00），"
                        "時區會自動套用 Asia/Taipei。"
                    ),
                },
                "end": {
                    "type": "string",
                    "description": "結束時間 ISO 8601（同 start 格式）",
                },
                "description": {"type": "string", "description": "事件描述（可選）"},
                "force": {
                    "type": "boolean",
                    "description": "跳過衝突偵測強制建立，預設 false",
                },
                "also_create_task": {
                    "type": "boolean",
                    "description": (
                        "是否同時建立對應 Task（預設 true）。純事件（婚禮、生日、紀念日）設 false。"
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": ["work", "health", "growth", "misc"],
                    "description": (
                        "依事件內容判斷分類，寫進一併建立的 Task（只有 work 計入 🍅 統計）："
                        " work = 專案、寫作、開發、商業、工作相關；"
                        " health = 運動、睡眠、飲食、醫療；"
                        " growth = 閱讀、學習、課程、技能培養、個人成長；"
                        " misc = 雜務、行政、家事、不屬於以上三類的事項。"
                        " 使用者沒說就自行判斷，不要問。"
                        "純事件（also_create_task=false）也照填即可。"
                    ),
                },
            },
            "required": ["title", "start", "end", "category"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": ("列出 Google Calendar 事件。用於「查今天行程」「這週有什麼」等需求。"),
        "input_schema": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "this_week", "next_week", "custom"],
                    "description": "時段範圍",
                },
                "time_min": {
                    "type": "string",
                    "description": "range=custom 時的起始日期（ISO 8601，含時間）",
                },
                "time_max": {
                    "type": "string",
                    "description": "range=custom 時的結束日期（ISO 8601）",
                },
            },
            "required": ["range"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": (
            "修改現有 Calendar 事件。by title 模糊搜尋最近 30 天的事件。"
            "若改動時段，會再次檢查衝突（同 create 行為）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要修改的事件現有標題（模糊搜尋）",
                },
                "new_title": {"type": "string", "description": "新標題（可選）"},
                "start": {"type": "string", "description": "新開始時間（可選）"},
                "end": {"type": "string", "description": "新結束時間（可選）"},
                "description": {"type": "string", "description": "新描述（可選）"},
                "force": {
                    "type": "boolean",
                    "description": "改時段時跳過衝突偵測，預設 false",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "schedule_task_entry",
        "description": (
            "把一個『已存在的 Task』排進某天的時段（或整天），寫進該 task 的 plan[] 並推到 "
            "Google Calendar（ADR-041 多事件模型）。**專用於 Bridge 偵測到時段衝突、在 Slack "
            "請使用者改時段的情境**：使用者選了新時間就用這個工具排入；使用者說『強制 / 就原時段』"
            "就 force=true 排回原時段。這是排『既有 task』，不是建新事件——別用 "
            "create_calendar_event（會撞名 / 產生孤兒）。task_slug 是 task 檔名（不含 .md），"
            "Bridge 的衝突訊息裡會附上。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_slug": {"type": "string", "description": "task 檔名（不含 .md）"},
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "time": {
                    "type": "string",
                    "description": "時間 HH:MM；留空＝整天事件（all-day）",
                },
                "pomodoros": {
                    "type": "integer",
                    "description": "番茄數（1🍅=30 分）；留空＝沿用 task 的預估🍅",
                },
                "reason": {
                    "type": "string",
                    "description": "週末排程原因（排到週六/日時必填）",
                },
                "force": {
                    "type": "boolean",
                    "description": "與行事曆衝突時仍強制排入，預設 false",
                },
            },
            "required": ["task_slug", "date"],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "刪除 Calendar 事件。**呼叫前必須先用 ask_user 列出要刪的事件請使用者確認。**"
            "by title 模糊搜尋最近 30 天的事件。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "要刪除的事件標題（模糊搜尋）"},
            },
            "required": ["title"],
        },
    },
    # ── Gmail tools ───────────────────────────────────────────────
    {
        "name": "list_gmail_unread",
        "description": (
            "列出 Gmail 信件（支援 Gmail search syntax）。"
            "掃信箱時固定呼叫兩次："
            "1) query='category:primary is:unread'（Primary 未讀，不含 Promotions/Social/Updates）"
            "2) query='label:Respond/Shosho older_than:1d'（超過 24 小時未處理的待回信）"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search syntax。"
                        "掃 Primary 未讀用 'category:primary is:unread'；"
                        "掃超時待回信用 'label:Respond/Shosho older_than:1d'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多回傳幾封，預設 10，最多 20",
                },
            },
        },
    },
    {
        "name": "get_gmail_message",
        "description": (
            "取得單封 Gmail 信件的完整內容（含 body）。"
            "在 list_gmail_unread 後，使用者要求看某封信的完整內容，或你需要讀內文才能回覆時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "信件 ID（從 list_gmail_unread 取得）",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "search_gmail_history",
        "description": (
            "搜尋 Gmail 全域歷史（含寄件備份）。"
            "報價時用來找自己過去寄過的類似報價信、合作信。"
            "建議 query 帶 'in:sent' 搜已寄出的信。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search syntax。例：'in:sent 報價 YouTube'、"
                        "'in:sent subject:合作邀約'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多回傳幾封，預設 5",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_gmail_draft",
        "description": (
            "把撰寫好的信件存成 Gmail 草稿。"
            "草稿建立後，**在 Slack 貼出完整預覽（收件人 / 主旨 / 信件內容），"
            "附 Gmail 連結，告訴使用者確認後說「發」才發出**。"
            "若是回覆某封信，請傳入 thread_id 與 in_reply_to_message_id。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "收件人 email 列表",
                },
                "subject": {"type": "string", "description": "信件主旨"},
                "body": {"type": "string", "description": "信件正文（plain text）"},
                "thread_id": {
                    "type": "string",
                    "description": "若為回覆，帶入原信件的 thread_id",
                },
                "in_reply_to_message_id": {
                    "type": "string",
                    "description": "若為回覆，帶入原信件的 message_id（設 In-Reply-To header）",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "update_gmail_draft",
        "description": (
            "修改既有 Gmail 草稿（未提供的欄位保留原值）。"
            "使用者說「改第二段」「收件人換成 X」等時使用。"
            "修改後同樣在 Slack 貼出新版完整預覽。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "要修改的草稿 ID（從 create_gmail_draft 取得）",
                },
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "新的收件人列表（可選）",
                },
                "subject": {"type": "string", "description": "新的主旨（可選）"},
                "body": {"type": "string", "description": "新的信件正文（可選）"},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "send_gmail_draft",
        "description": (
            "發送既有 Gmail 草稿。"
            "**只有在使用者明確說「發」「發出去」「確認」「OK 發」之後才呼叫。**"
            "發送後回報已寄出。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "要發送的草稿 ID",
                },
            },
            "required": ["draft_id"],
        },
    },
    # ── / Gmail tools ─────────────────────────────────────────────
    # ── Vault note tools ──────────────────────────────────────────
    {
        "name": "write_vault_note",
        "description": (
            "把自由格式的 markdown 筆記寫入 vault"
            "（Nami 專屬筆記區 AgentOutputs/nami/notes/，ADR-028）。"
            "用途：整理交付物（sales kit、會議摘要、研究整理），"
            "或你覺得值得留底給使用者的資料。"
            "寫入前若路徑可能已存在，先用 read_vault_note 確認，避免意外覆寫。"
            "**不要**用這個工具寫 Project/Task、KB/Wiki、Journals——那些有專屬工具或不該碰。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": (
                        "vault-relative 路徑，必須在 AgentOutputs/nami/notes/ 底下，"
                        "例：'AgentOutputs/nami/notes/sales-kit-2026-04.md'"
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "note 標題（放進 frontmatter）",
                },
                "body": {
                    "type": "string",
                    "description": "markdown 內文（繁體中文）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可選 tags，例：['sales-kit', 'quotes']",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "既有檔案是否覆寫，預設 false（防誤覆寫）",
                },
            },
            "required": ["relative_path", "title", "body"],
        },
    },
    {
        "name": "read_vault_note",
        "description": (
            "讀取 vault 內已存在的筆記。"
            "用途：寫入前確認是否已有同路徑檔案、或翻舊筆記查閱內容。"
            "可讀取 AgentOutputs/nami/notes/、Projects/、TaskNotes/Tasks/ 底下的檔案。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": (
                        "vault-relative 路徑，例：'AgentOutputs/nami/notes/sales-kit-2026-04.md'"
                    ),
                },
            },
            "required": ["relative_path"],
        },
    },
    {
        "name": "list_vault_notes",
        "description": (
            "列出 vault 內某資料夾下的筆記清單。"
            "用途：查看已有哪些 note、避免重複寫入。"
            "預設列 AgentOutputs/nami/notes/，也可指定 Projects/ 或 TaskNotes/Tasks/。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_dir": {
                    "type": "string",
                    "description": "vault-relative 資料夾路徑，預設 'AgentOutputs/nami/notes/'",
                },
            },
            "required": [],
        },
    },
    # ── / Vault note tools ────────────────────────────────────────
    # ── Web research tools ────────────────────────────────────────
    {
        "name": "web_search",
        "description": (
            "搜尋網路上的資訊，回傳 title + URL + 摘要的候選清單。"
            "做研究報告時先用這個廣撒網，再用 fetch_url 深讀最相關的幾個來源。"
            "一次搜尋只用一個角度；需要多角度時分多次呼叫。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字（可中英混用）",
                },
                "num_results": {
                    "type": "integer",
                    "description": "候選數量，預設 10，上限 20",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "抓取指定 URL 的主要內文（已去除導覽列、廣告、無關 boilerplate）。"
            "用在 web_search 之後，深讀 3–6 個最相關的來源。"
            "不要一次 fetch 超過 6 個 URL——先判斷相關性再決定讀哪幾個。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整 URL（https://...）",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "pubmed_lookup",
        "description": (
            "查 PubMed 醫學文獻資料庫，回傳前 N 篇文獻的標題 + 作者 + 期刊 + 年份 + DOI + "
            "PMID URL。比 web_search 快又準（直接資料庫查詢，無 SEO 噪音），適合快速 "
            "evidence lookup：「最近有沒有 X 的 RCT？」「Y 跟 Z 的關聯文獻有哪些？」。"
            "查英文（PubMed 索引語言）。需要深讀全文時，回傳結果裡的 PMID 可丟給 Robin 的 "
            "pubmed-to-reader pipeline 拿雙語版。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "PubMed 查詢字串（英文）。可用 boolean operators 與 MeSH 標籤，"
                        '例：``"intermittent fasting" AND insulin resistance``。'
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "回傳筆數，預設 5，上限 20。",
                    "default": 5,
                },
                "since_year": {
                    "type": "integer",
                    "description": "只看這年（含）之後發表的文獻，例：2024。沒講就不限。",
                },
            },
            "required": ["query"],
        },
    },
    # ── / Web research tools ──────────────────────────────────────
    # ── Academic / Media research tools ──────────────────────────
    {
        "name": "arxiv_lookup",
        "description": (
            "查 arXiv 學術論文資料庫，回傳前 N 篇文獻的標題 + 作者 + 摘要 + "
            "分類 + abs URL + PDF URL。比 web_search 快又準，"
            "適合長壽 / 營養 / 睡眠 / AI 等學術主題的 evidence lookup："
            "「最近 cs.AI / q-bio 有沒有 X 的 paper」「Y 跟 Z 的 arXiv 文獻有哪些」。"
            "查英文（arXiv 索引語言）。需要引用關係（誰引用了它、它引用了誰、"
            "influential citation count）時改用 arxiv_citations。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "arXiv 查詢字串（英文）。可用 ``ti:`` / ``au:`` / ``cat:`` prefix "
                        '與 boolean operators，例：``"intermittent fasting" AND cat:q-bio``。'
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "回傳筆數，預設 5，上限 20。",
                    "default": 5,
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "submittedDate", "lastUpdatedDate"],
                    "description": "排序方式。預設 relevance；要找最新發表用 submittedDate。",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "arxiv_citations",
        "description": (
            "查一篇 arXiv paper 的引用關係（Semantic Scholar API）。"
            "回傳 paper 本身的 citation count / influential citation count / "
            "open access 狀態 + 引用此 paper 的文獻清單（citing）+ 此 paper 引用的"
            "文獻清單（references）。適合評估某篇 paper 的影響力、或找延伸閱讀。"
            "**只在船長要 evaluate 某篇特定 arXiv paper 的影響力 / 找相關文獻時用**，"
            "不是每次 arxiv_lookup 都要跟著呼叫。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {
                    "type": "string",
                    "description": (
                        "arXiv ID，例：``2402.03300`` 或 ``2402.03300v1``。"
                        "從 arxiv_lookup 的結果取得，或船長直接給。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "citing / references 各回傳幾筆，預設 10，上限 20。",
                    "default": 10,
                },
            },
            "required": ["arxiv_id"],
        },
    },
    {
        "name": "youtube_transcript",
        "description": (
            "抓 YouTube 影片字幕，回傳 plain text 或帶時間戳的版本。"
            "適合：船長丟一個 YouTube URL 想要摘要 / 整理 chapters / 找 quote。"
            "**不要自動把整個 transcript 直接貼給船長**——拿到後做摘要、提取章節、"
            "或挑 quote 再回報。預設繁中字幕優先，無繁中時 fallback 英文，"
            "都沒有再退到任何可用語言。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url_or_id": {
                    "type": "string",
                    "description": (
                        "YouTube URL（watch / youtu.be / shorts / live / embed 任一種）"
                        "或 11 碼 video id。"
                    ),
                },
                "with_timestamps": {
                    "type": "boolean",
                    "description": (
                        "是否回傳帶時間戳格式（``[mm:ss] text``）。"
                        "要做 chapters / quote pickup 時設 true；"
                        "純摘要設 false。預設 false。"
                    ),
                    "default": False,
                },
            },
            "required": ["url_or_id"],
        },
    },
    # ── / Academic / Media research tools ─────────────────────────
    {
        "name": "ask_zoro",
        "description": (
            "把超出你能力的 social listening / trend / KOL / 關鍵字熱度類 query 委託給 "
            "Zoro（劍士，情報偵察）。Zoro 能做：\n"
            "- trend_check: Google Trends — 看一個關鍵字的 3 個月趨勢方向（rising/"
            "declining/stable）+ 相關熱搜 + 上升搜尋。快（<10s）。\n"
            "- social_listening: Reddit 健康類 subreddit (r/longevity, r/biohacking, "
            "r/nutrition, r/sleep 等) 24-48h 內 hot post 列表。快（<10s）。\n"
            "- keyword_research: 中英雙語完整關鍵字研究（Trends + Reddit + YouTube + "
            "Twitter + autocomplete + LLM 合成標題建議）。慢（30-60s）。\n"
            "**何時用**：船長問「最近社群熱議」「XX 在 Reddit 紅嗎」「XX 趨勢如何」"
            "「想知道 XX 的關鍵字機會」。\n"
            "**何時不用**：一般 web search / 新聞報導 / 學術研究——你自己用 web_search/"
            "pubmed_lookup 即可，不要繞道 Zoro。\n"
            "**收到結果後**：用你自己的 Nami 口吻 paraphrase 給船長，不要照貼 Zoro "
            "結構化原文（船長看到的是你不是 Zoro）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要問 Zoro 的內容（中英皆可，保留原始問題語意）",
                },
                "capability": {
                    "type": "string",
                    "enum": ["trend_check", "social_listening", "keyword_research"],
                    "description": (
                        "trend_check = Google Trends 趨勢方向（快）；"
                        "social_listening = Reddit 健康類熱門 post（快）；"
                        "keyword_research = 完整關鍵字研究全套（慢，30-60s）"
                    ),
                },
            },
            "required": ["query", "capability"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "當必要資訊缺失時向使用者問一個澄清問題。"
            "使用者回覆後你會繼續接力完成任務。一次只問一個最關鍵的缺項。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "給使用者的簡潔問題（繁中）",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("可選項（若適用），例如 ['youtube', 'blog', 'research']"),
                },
            },
            "required": ["question"],
        },
    },
]


# ── Helper: message / content block serialization ─────────────────────


def _content_blocks_to_dicts(blocks: list[Any]) -> list[dict]:
    """將 Claude response content blocks（SDK 物件）轉為可存入 state 的 dict。"""
    result: list[dict] = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            result.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif btype == "thinking":
            # Thinking blocks 在 tool-use 續輪時必須保留（否則 Claude 會困惑）
            result.append({"type": "thinking", "thinking": block.thinking})
    return result


def _extract_text(content_dicts: list[dict]) -> str:
    """從 content dicts 抽出可顯示給使用者的純文字。"""
    parts = [b["text"] for b in content_dicts if b.get("type") == "text"]
    return "\n".join(p.strip() for p in parts if p and p.strip())


# ── Handler ──────────────────────────────────────────────────────────


@dataclass
class _ToolOutcome:
    """Tool 執行的結果（給 LLM 看的字串 + 可選的事件 payload）。"""

    content: str
    is_error: bool = False
    event: dict | None = None


class NamiHandler(BaseHandler):
    """Nami：LLM agent loop handler。"""

    agent_name = "nami"
    supported_intents = ["create_task", "list_tasks", "create_project", "general"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("nami")
        date_context = _build_date_context()
        memory_context = agent_memory.format_as_context("nami", user_id)
        parts = [date_context]
        if memory_context:
            parts.append(memory_context)
        parts.append(text)
        messages: list[dict] = [{"role": "user", "content": "\n\n".join(parts)}]
        return self._run_loop(messages, user_id)

    def continue_flow(
        self,
        flow_name: str,
        state: dict,
        text: str,
        user_id: str,
    ) -> HandlerResponse:
        set_current_agent("nami")
        if flow_name != NAMI_AGENT_FLOW:
            return super().continue_flow(flow_name, state, text, user_id)

        messages = state.get("messages", [])
        pending_id = state.get("pending_tool_use_id")
        if not messages:
            return HandlerResponse(text="流程狀態異常，已重置。請重新開始。")

        messages = list(messages)
        if pending_id:
            # 有 pending ask_user：把使用者回覆當成 tool_result 塞回 loop
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": pending_id,
                            "content": text,
                        }
                    ],
                }
            )
        else:
            # 後續問題（task 建完後繼續問）：直接 append 新 user message
            messages.append({"role": "user", "content": text})
        return self._run_loop(messages, user_id)

    # ── Agent loop ────────────────────────────────────────────────

    def _run_loop(self, messages: list[dict], user_id: str) -> HandlerResponse:
        try:
            system_prompt = load_prompt("nami", "agent_system")
        except FileNotFoundError:
            logger.error("agent_system prompt missing — fallback to minimal system")
            system_prompt = "你是 Nami，修修的 LifeOS 任務助手。用繁體中文。"

        for _ in range(_MAX_ITERS):
            response = ask_with_tools(
                messages=messages,
                tools=NAMI_TOOLS,
                system=system_prompt,
                model=_MODEL,
                max_tokens=8192,
            )

            stop_reason = response.stop_reason
            content_dicts = _content_blocks_to_dicts(response.content)

            if stop_reason == "end_turn":
                text = _extract_text(content_dicts) or "完成。"
                # 把 assistant 回覆存進 messages，讓 thread 保持存活接受後續問題
                messages.append({"role": "assistant", "content": content_dicts})
                # 背景抽取記憶（Phase 2）。失敗不影響主流程。
                try:
                    extract_in_background(agent="nami", user_id=user_id, messages=messages)
                except Exception as e:
                    logger.warning(f"Failed to spawn memory extractor: {e}")
                return HandlerResponse(
                    text=text,
                    continuation=Continuation(
                        flow_name=NAMI_AGENT_FLOW,
                        state={"messages": messages, "pending_tool_use_id": None},
                    ),
                )

            if stop_reason != "tool_use":
                logger.warning(f"Unexpected stop_reason: {stop_reason}")
                text = _extract_text(content_dicts) or "流程異常，已中止。"
                return HandlerResponse(text=text)

            # 把 assistant 的完整回覆（含 tool_use blocks）append
            messages.append({"role": "assistant", "content": content_dicts})

            tool_uses = [b for b in content_dicts if b.get("type") == "tool_use"]
            if not tool_uses:
                # stop_reason=tool_use 但沒 tool_use block — 保底結束
                text = _extract_text(content_dicts) or "完成。"
                return HandlerResponse(text=text)

            tool_results: list[dict] = []
            for tu in tool_uses:
                name = tu["name"]
                tool_id = tu["id"]
                tool_input = tu["input"]

                if name == "ask_user":
                    # 特殊：pause loop，丟問題回使用者，等下一輪
                    question = str(tool_input.get("question", "")).strip()
                    options = tool_input.get("options") or []
                    text = question
                    if options:
                        text += "\n\n" + "\n".join(f"  • {o}" for o in options)

                    return HandlerResponse(
                        text=text,
                        continuation=Continuation(
                            flow_name=NAMI_AGENT_FLOW,
                            state={
                                "messages": messages,
                                "pending_tool_use_id": tool_id,
                            },
                        ),
                    )

                outcome = self._execute_tool(name, tool_input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                )
                if outcome.event and not outcome.is_error:
                    emit("nami", outcome.event["name"], outcome.event["payload"])
                    kb_log("nami", outcome.event["name"], outcome.event.get("log", ""))

            messages.append({"role": "user", "content": tool_results})

        logger.warning(f"Agent loop hit max iters ({_MAX_ITERS}) without end_turn")
        return HandlerResponse(text="已達最大迴圈次數，請重新下指令。")

    # ── Tool executors ───────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict) -> _ToolOutcome:
        try:
            if name == "create_project":
                return self._tool_create_project(tool_input)
            if name == "create_task":
                return self._tool_create_task(tool_input)
            if name == "update_task":
                return self._tool_update_task(tool_input)
            if name == "delete_task":
                return self._tool_delete_task(tool_input)
            if name == "delete_project":
                return self._tool_delete_project(tool_input)
            if name == "list_tasks":
                return self._tool_list_tasks()
            if name == "create_calendar_event":
                return self._tool_create_calendar_event(tool_input)
            if name == "list_calendar_events":
                return self._tool_list_calendar_events(tool_input)
            if name == "update_calendar_event":
                return self._tool_update_calendar_event(tool_input)
            if name == "schedule_task_entry":
                return self._tool_schedule_task_entry(tool_input)
            if name == "delete_calendar_event":
                return self._tool_delete_calendar_event(tool_input)
            if name == "list_gmail_unread":
                return self._tool_list_gmail_unread(tool_input)
            if name == "get_gmail_message":
                return self._tool_get_gmail_message(tool_input)
            if name == "search_gmail_history":
                return self._tool_search_gmail_history(tool_input)
            if name == "create_gmail_draft":
                return self._tool_create_gmail_draft(tool_input)
            if name == "update_gmail_draft":
                return self._tool_update_gmail_draft(tool_input)
            if name == "send_gmail_draft":
                return self._tool_send_gmail_draft(tool_input)
            if name == "write_vault_note":
                return self._tool_write_vault_note(tool_input)
            if name == "read_vault_note":
                return self._tool_read_vault_note(tool_input)
            if name == "list_vault_notes":
                return self._tool_list_vault_notes(tool_input)
            if name == "web_search":
                return self._tool_web_search(tool_input)
            if name == "fetch_url":
                return self._tool_fetch_url(tool_input)
            if name == "pubmed_lookup":
                return self._tool_pubmed_lookup(tool_input)
            if name == "arxiv_lookup":
                return self._tool_arxiv_lookup(tool_input)
            if name == "arxiv_citations":
                return self._tool_arxiv_citations(tool_input)
            if name == "youtube_transcript":
                return self._tool_youtube_transcript(tool_input)
            if name == "ask_zoro":
                return self._tool_ask_zoro(tool_input)
            return _ToolOutcome(content=f"Unknown tool: {name}", is_error=True)
        except VaultRuleViolation as e:
            return _ToolOutcome(content=f"Vault 規則違反：{e}", is_error=True)
        except GoogleCalendarAuthError as e:
            return _ToolOutcome(
                content=f"Google Calendar 授權失效：{e}",
                is_error=True,
            )
        except GoogleGmailAuthError as e:
            return _ToolOutcome(
                content=f"Gmail 授權失效：{e}",
                is_error=True,
            )
        except Exception as e:
            logger.exception(f"Tool {name} failed")
            return _ToolOutcome(content=f"Tool {name} error: {e}", is_error=True)

    def _tool_create_project(self, input_: dict) -> _ToolOutcome:
        topic = str(input_.get("topic", "")).strip()
        content_type = input_.get("content_type")
        if not topic or content_type not in CONTENT_TYPES:
            return _ToolOutcome(
                content="Missing required fields: topic and/or content_type",
                is_error=True,
            )

        area = input_.get("area") or "work"
        priority = input_.get("priority") or "medium"
        search_topic = input_.get("search_topic")
        tasks = default_task_names(content_type)

        try:
            result = create_project_with_tasks(
                title=topic,
                content_type=content_type,
                task_names=tasks,
                area=area,
                priority=priority,
                search_topic=search_topic,
            )
        except ProjectExistsError as e:
            return _ToolOutcome(
                content=f"Project 或 Task 已存在：{e}。請改用不同標題。",
                is_error=True,
            )

        project_rel = _to_vault_relative(result.project_path)
        task_rels = [_to_vault_relative(p) for p in result.task_paths]

        payload = {
            "title": topic,
            "content_type": content_type,
            "project_path": project_rel,
            "task_paths": task_rels,
        }
        summary = (
            f"✅ Project 建立成功\n"
            f"  📄 {project_rel}\n"
            f"  ✅ {len(task_rels)} 個 Task：{', '.join(tasks)}"
        )
        return _ToolOutcome(
            content=summary,
            event={"name": "project_created", "payload": payload, "log": topic},
        )

    def _tool_create_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        scheduled = input_.get("scheduled") or None
        priority = input_.get("priority") or "normal"
        project = (input_.get("project") or "").strip() or None
        category = input_.get("category") or "work"
        est = input_.get("est_pomodoros")
        notes = input_.get("notes", "")

        # Shared dual-write creator — the SAME path the web 新增任務 buttons use, so
        # chat + web produce identical files (filename prefix + projects: [[…]] when
        # linked; bare {title}.md when standalone). ADR-041 v3.
        from shared.config import get_vault_path
        from shared.project_writer import ProjectWriteError, create_task

        try:
            path_obj = create_task(
                vault_root=get_vault_path(),
                project_slug=project,
                task_name=title,
                estimated_pomodoros=int(est) if est else 4,
                priority=priority,
                category=category,
                scheduled=scheduled,
                notes=notes,
            )
        except ProjectWriteError as exc:
            return _ToolOutcome(content=f"建立 task 失敗：{exc}", is_error=True)

        path = f"{TASK_DIR}/{path_obj.name}"
        scheduled_info = f"（排程：{scheduled}）" if scheduled else ""
        project_info = f"（掛在 {project}）" if project else ""
        summary = f"✅ 已建立 task：{title}{scheduled_info}{project_info}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "task_created",
                "payload": {"title": title, "path": path, "scheduled": scheduled},
                "log": title,
            },
        )

    def _tool_list_tasks(self) -> _ToolOutcome:
        files = list_files(TASK_DIR)
        tasks = []
        for f in files:
            content = read_page(f"{TASK_DIR}/{f.name}")
            if not content:
                continue
            fm = _extract_frontmatter(content)
            if fm.get("status") in ("to-do", "todo", "in-progress"):
                tasks.append(
                    {
                        "title": fm.get("title", f.stem),
                        "scheduled": _plan_scheduled_display(fm),  # v3-D: from plan[]
                        "priority": fm.get("priority", "normal"),
                        "status": fm.get("status", "to-do"),
                    }
                )

        if not tasks:
            return _ToolOutcome(content="目前沒有待辦任務。")

        lines = []
        for t in tasks[:20]:
            icon = "🔴" if t["priority"] == "high" else "⚪"
            line = f"- {icon} {t['title']}"
            if t["scheduled"]:
                line += f" ({t['scheduled']})"
            if t["status"] == "in-progress":
                line += " [進行中]"
            lines.append(line)

        return _ToolOutcome(content=f"*待辦任務（{len(tasks)} 項）*\n" + "\n".join(lines))

    def _tool_delete_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        found = self._find_task_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 task。請用 list_tasks 確認標題。",
                is_error=True,
            )

        rel_path, fm, _ = found
        matched_title = str(fm.get("title", title))
        deleted = delete_page(rel_path)
        if not deleted:
            return _ToolOutcome(content=f"刪除失敗：檔案不存在（{rel_path}）", is_error=True)

        return _ToolOutcome(
            content=f"🗑️ 已刪除 task：{matched_title}",
            event={
                "name": "task_deleted",
                "payload": {"title": matched_title, "path": rel_path},
                "log": matched_title,
            },
        )

    def _tool_delete_project(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing project title", is_error=True)

        include_tasks = input_.get("include_tasks", True)

        found = self._find_project_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 project。",
                is_error=True,
            )

        proj_rel, proj_fm = found
        matched_title = str(proj_fm.get("title", title))

        deleted_tasks: list[str] = []
        if include_tasks:
            for task_rel, task_fm in self._find_tasks_by_project(matched_title):
                if delete_page(task_rel):
                    deleted_tasks.append(str(task_fm.get("title", task_rel)))

        delete_page(proj_rel)

        task_summary = f"，含 {len(deleted_tasks)} 個 task" if deleted_tasks else ""
        summary = f"🗑️ 已刪除 project：{matched_title}{task_summary}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "project_deleted",
                "payload": {
                    "title": matched_title,
                    "path": proj_rel,
                    "deleted_tasks": deleted_tasks,
                },
                "log": matched_title,
            },
        )

    def _find_project_by_title(self, title: str) -> tuple[str, dict] | None:
        """以 title 搜尋 project 檔案，回傳 (relative_path, frontmatter) 或 None。"""
        title_lower = title.lower()
        for f in list_files(PROJECT_DIR):
            rel = f"{PROJECT_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            fm_title = str(fm.get("title", f.stem)).lower()
            if fm_title == title_lower or title_lower in fm_title or fm_title in title_lower:
                return rel, fm
        return None

    def _find_tasks_by_project(self, project_title: str) -> list[tuple[str, dict]]:
        """找出所有 linked 到 project_title 的 task，回傳 [(rel_path, frontmatter)]。"""
        results: list[tuple[str, dict]] = []
        link = f"[[{project_title}]]"
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            projects_field = fm.get("projects") or []
            if isinstance(projects_field, list) and link in projects_field:
                results.append((rel, fm))
            elif isinstance(projects_field, str) and link in projects_field:
                results.append((rel, fm))
        return results

    def _find_task_by_title(self, title: str) -> tuple[str, dict, str] | None:
        """以 title 搜尋 task 檔案，回傳 (relative_path, frontmatter, body) 或 None。"""
        title_lower = title.lower()
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            fm_title = str(fm.get("title", "")).lower()
            if fm_title == title_lower or title_lower in fm_title or fm_title in title_lower:
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else ""
                return rel, fm, body
        return None

    def _find_task_by_calendar_id(self, event_id: str) -> tuple[str, dict, str] | None:
        """以 calendar_event_id 搜尋 task 檔案，回傳 (relative_path, frontmatter, body) 或 None。
        v3-D：先比對 per-entry ``plan[]`` 的 calendar_event_id，再退回 legacy 的
        task 層級 calendar_event_id（v3-D 之前 Nami 建的事件仍是後者）。"""
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            per_entry = any(e.get("calendar_event_id") == event_id for e in _plan_entries(fm))
            if per_entry or fm.get("calendar_event_id") == event_id:
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else ""
                return rel, fm, body
        return None

    def _tool_update_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        found = self._find_task_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 task。請用 list_tasks 確認標題。",
                is_error=True,
            )

        rel_path, fm, body = found
        matched_title = str(fm.get("title", title))

        updated_fields: list[str] = []
        if input_.get("scheduled") is not None:
            scheduled_val = input_["scheduled"]
            if scheduled_val == "":
                fm.pop("scheduled", None)
                updated_fields.append("scheduled 已清除")
            else:
                fm["scheduled"] = scheduled_val
                updated_fields.append(f"scheduled={scheduled_val}")
        if input_.get("priority") is not None:
            fm["priority"] = input_["priority"]
            updated_fields.append(f"priority={input_['priority']}")
        if input_.get("status") is not None:
            fm["status"] = input_["status"]
            updated_fields.append(f"status={input_['status']}")
        if input_.get("pomodoros") is not None:
            fm["pomodoros"] = int(input_["pomodoros"])
            updated_fields.append(f"pomodoros={input_['pomodoros']}")

        if not updated_fields:
            return _ToolOutcome(content="沒有指定要更新的欄位。", is_error=True)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm["dateModified"] = now_iso
        fm = _stringify_fm_dates(fm)

        write_page(rel_path, fm, body)

        summary = f"✅ 已更新 task：{matched_title}（{', '.join(updated_fields)}）"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "task_updated",
                "payload": {
                    "title": matched_title,
                    "path": rel_path,
                    "updated_fields": updated_fields,
                },
                "log": matched_title,
            },
        )

    # ── Calendar tool executors ──────────────────────────────────

    def _tool_schedule_task_entry(self, input_: dict) -> _ToolOutcome:
        """ADR-041 v3-F: schedule an EXISTING task's plan entry (timed block or all-day)
        via the shared calendar_scheduler — the tool Nami uses to act on 修修's reply
        when the Bridge escalated a clash to Slack. Distinct from create_calendar_event
        (which mints a NEW task and is orphan-guarded)."""
        from datetime import time as _time

        from shared import calendar_scheduler
        from shared.config import get_vault_path
        from shared.weekly_indexer import WeeklyIndexer
        from shared.weekly_writer import (
            TaskNotFoundError,
            WeekendReasonRequired,
            WeeklyWriteError,
        )

        slug = str(input_.get("task_slug", "")).strip()
        date_s = str(input_.get("date", "")).strip()
        time_s = str(input_.get("time", "")).strip()
        reason = str(input_.get("reason", "")).strip() or None
        force = bool(input_.get("force", False))
        if not slug or not date_s:
            return _ToolOutcome(content="Missing task_slug or date", is_error=True)
        try:
            d = datetime.strptime(date_s[:10], "%Y-%m-%d").date()
        except ValueError:
            return _ToolOutcome(content=f"日期格式無效（需 YYYY-MM-DD）：{date_s}", is_error=True)

        all_day = not time_s
        if all_day:
            start = datetime.combine(d, _time.min)
        else:
            m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", time_s)
            if not m:
                return _ToolOutcome(content=f"時間格式無效（需 HH:MM）：{time_s}", is_error=True)
            start = datetime.combine(d, _time(int(m.group(1)), int(m.group(2))))

        vault = get_vault_path()
        task = WeeklyIndexer(vault).find_task(slug)
        if task is None:
            return _ToolOutcome(content=f"找不到 task：{slug}（task_slug 是檔名）", is_error=True)
        pom_in = input_.get("pomodoros")
        pom = int(pom_in) if pom_in else (task.est_pomodoros or 2)

        try:
            outcome = calendar_scheduler.schedule_entry(
                vault,
                slug,
                start=start,
                pomodoros=pom,
                title=task.title,
                all_day=all_day,
                reason=reason,
                force=force,
            )
        except WeekendReasonRequired:
            return _ToolOutcome(
                content=f"{date_s} 是週末，請補一個排程原因（reason）後再排。", is_error=True
            )
        except TaskNotFoundError:
            return _ToolOutcome(content=f"找不到 task：{slug}", is_error=True)
        except WeeklyWriteError as exc:
            return _ToolOutcome(content=f"寫入失敗：{exc}", is_error=True)

        when = "整天" if all_day else start.strftime("%H:%M")
        st = outcome.calendar_status
        if st == calendar_scheduler.CREATED:
            return _ToolOutcome(
                content=(
                    f"✅ 已把「{task.title}」排到 {date_s} {when}（{pom}🍅）並推到 Google 行事曆。"
                ),
                event={
                    "name": "calendar_event_created",
                    "payload": {"task": slug, "date": date_s, "all_day": all_day},
                    "log": task.title,
                },
            )
        if st == calendar_scheduler.CONFLICT:
            slots = google_calendar.find_free_slots(d, pom * 30, near=start.isoformat())
            sug = "、".join(s[11:16] for s, _ in slots) or "（今天找不到空檔）"
            return _ToolOutcome(
                content=(
                    f"{date_s} {when} 仍與既有事件衝突。附近空檔：{sug}。"
                    "要改到哪個時間，或回『強制』就排原時段？"
                ),
                is_error=True,
            )
        if st == calendar_scheduler.UNAVAILABLE:
            return _ToolOutcome(
                content=(
                    f"已把「{task.title}」記到 {date_s} 的計畫，"
                    "但 Google 行事曆暫時無法連動，稍後可重試。"
                )
            )
        return _ToolOutcome(content="排入後寫回失敗，已回滾，請重試。", is_error=True)

    def _tool_create_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        start = str(input_.get("start", "")).strip()
        end = str(input_.get("end", "")).strip()
        if not title or not start or not end:
            return _ToolOutcome(content="Missing required fields: title, start, end", is_error=True)

        description = input_.get("description", "") or ""
        force = bool(input_.get("force", False))
        also_create_task = bool(input_.get("also_create_task", True))
        category = input_.get("category") or "work"

        # Pre-check task 檔案不存在，避免 calendar 建完後 task 撞名產生孤兒 event
        task_rel_path: str | None = None
        if also_create_task:
            slug = _slugify(title)
            task_rel_path = f"{TASK_DIR}/{slug}.md"
            existing = self._find_task_by_title(title)
            if existing is not None:
                return _ToolOutcome(
                    content=(
                        f"Task 標題撞名：vault 內已有「{existing[1].get('title', title)}」。"
                        "請改 event 標題，或用 also_create_task=false 只建 calendar 不建 task。"
                    ),
                    is_error=True,
                )

        result = google_calendar.create_event(
            title=title,
            start=start,
            end=end,
            description=description,
            check_conflict=not force,
        )

        # 衝突 → result 是 list[CalendarEvent]，沒有建立
        if isinstance(result, list):
            conflicts_desc = "、".join(
                f"{_fmt_event_time(e.start, e.end)}「{e.title}」" for e in result[:3]
            )
            return _ToolOutcome(
                content=(
                    f"時段衝突：{conflicts_desc}。"
                    " 要改時段還是強制建立（ask_user 問使用者，若同意覆蓋再用 force=true 重試）？"
                ),
                is_error=True,
            )

        event = result
        task_path_display = ""
        if also_create_task and task_rel_path is not None:
            try:
                self._write_calendar_linked_task(task_rel_path, event, category)
            except Exception as e:
                # Task 寫入失敗 → rollback calendar 避免孤兒事件
                logger.exception(
                    f"Task write failed after calendar create; rolling back event {event.id}"
                )
                try:
                    google_calendar.delete_event(event.id)
                except Exception:
                    logger.exception(
                        f"Rollback delete_event({event.id}) also failed — orphan event remains"
                    )
                    return _ToolOutcome(
                        content=(
                            f"Calendar 已建立但 task 寫入失敗（{e}），"
                            f"自動 rollback 也失敗。請手動刪除 Calendar 事件「{event.title}」。"
                        ),
                        is_error=True,
                    )
                return _ToolOutcome(
                    content=(
                        f"Task 寫入失敗（{e}），Calendar 事件已 rollback。"
                        " 請檢查 vault 狀態後再試。"
                    ),
                    is_error=True,
                )
            task_path_display = f"\n   📝 Task：{task_rel_path}"

        summary = (
            f"📅 Calendar 事件已建立：{event.title}\n"
            f"   時間：{_fmt_event_time(event.start, event.end)}\n"
            f"   連結：{event.html_link}"
            f"{task_path_display}"
        )
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_created",
                "payload": {
                    "id": event.id,
                    "title": event.title,
                    "start": event.start,
                    "end": event.end,
                    "html_link": event.html_link,
                    "task_path": task_rel_path if also_create_task else None,
                },
                "log": event.title,
            },
        )

    def _write_calendar_linked_task(
        self, rel_path: str, event: CalendarEvent, category: str = "work"
    ) -> None:
        """建立 calendar-linked task。v3-D：排程寫進 per-entry ``plan[]``（date /
        pomodoros / start / end / calendar_event_id），不再寫 task 層級的
        scheduled 鏡像——與 Bridge 的多事件模型同一個來源（ADR-041 v3）。``category``
        由 LLM 依事件內容判斷（只有 work 計入 🍅 統計），與 ``create_task`` 同源。"""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter = {
            "title": event.title,
            "status": "to-do",
            "priority": "normal",
            "category": category,
            "tags": ["task"],
            "dateCreated": now_iso,
            "dateModified": now_iso,
            "plan": [_event_to_plan_entry(event)],
        }
        write_page(rel_path, frontmatter, "")

    def _tool_list_calendar_events(self, input_: dict) -> _ToolOutcome:
        range_ = input_.get("range", "today")
        tz = ZoneInfo("Asia/Taipei")
        now = datetime.now(tz)

        if range_ == "today":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif range_ == "tomorrow":
            time_min = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif range_ == "this_week":
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_sunday = 6 - now.weekday()  # 週一=0, 週日=6
            time_min = start_of_today
            time_max = start_of_today + timedelta(days=days_to_sunday + 1)
        elif range_ == "next_week":
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_next_monday = 7 - now.weekday()
            time_min = start_of_today + timedelta(days=days_to_next_monday)
            time_max = time_min + timedelta(days=7)
        elif range_ == "custom":
            t_min_str = input_.get("time_min")
            t_max_str = input_.get("time_max")
            if not t_min_str or not t_max_str:
                return _ToolOutcome(
                    content="range=custom 時必須同時提供 time_min 和 time_max",
                    is_error=True,
                )
            time_min = _parse_iso_local(t_min_str, tz)
            time_max = _parse_iso_local(t_max_str, tz)
        else:
            return _ToolOutcome(content=f"Unknown range: {range_}", is_error=True)

        events = google_calendar.list_events(time_min=time_min, time_max=time_max, max_results=30)

        if not events:
            return _ToolOutcome(content=f"{range_} 時段沒有 Calendar 事件。")

        lines = [f"*Calendar 事件（{range_}，共 {len(events)} 項）*"]
        for e in events[:20]:
            lines.append(f"  • {_fmt_event_time(e.start, e.end)} — {e.title}")
        return _ToolOutcome(content="\n".join(lines))

    def _tool_update_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing title", is_error=True)

        found = self._find_calendar_event_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 Calendar 事件（最近 30 天）。",
                is_error=True,
            )

        new_title = input_.get("new_title")
        start = input_.get("start")
        end = input_.get("end")
        description = input_.get("description")
        force = bool(input_.get("force", False))

        if not any([new_title, start, end, description]):
            return _ToolOutcome(content="沒有指定要更新的欄位。", is_error=True)

        # 若改時段，先做衝突檢查（排除當前這筆事件本身）
        if (start or end) and not force:
            effective_start = start or found.start
            effective_end = end or found.end
            conflicts = [
                c
                for c in google_calendar.find_conflicts(effective_start, effective_end)
                if c.id != found.id
            ]
            if conflicts:
                conflicts_desc = "、".join(
                    f"{_fmt_event_time(c.start, c.end)}「{c.title}」" for c in conflicts[:3]
                )
                return _ToolOutcome(
                    content=(
                        f"更新時段衝突：{conflicts_desc}。"
                        " 要改到別的時段還是強制覆蓋（force=true）？"
                    ),
                    is_error=True,
                )

        updated = google_calendar.update_event(
            found.id,
            title=new_title,
            start=start,
            end=end,
            description=description,
        )

        changes = []
        if new_title:
            changes.append(f"標題→{new_title}")
        if start:
            changes.append(f"start={start}")
        if end:
            changes.append(f"end={end}")
        if description is not None:
            changes.append("描述已更新")

        task_sync_note = self._sync_task_from_calendar_update(
            updated, title_changed=bool(new_title)
        )

        summary = f"📝 Calendar 事件已更新：{updated.title}（{', '.join(changes)}）{task_sync_note}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_updated",
                "payload": {
                    "id": updated.id,
                    "title": updated.title,
                    "start": updated.start,
                    "end": updated.end,
                    "changes": changes,
                },
                "log": updated.title,
            },
        )

    def _sync_task_from_calendar_update(self, event: CalendarEvent, *, title_changed: bool) -> str:
        """更新 calendar 後同步對應 task。回傳要附在 summary 後的備註（可為空字串）。
        v3-D：更新 per-entry ``plan[]`` 那一筆的 start/end/date/pomodoros（保留該筆的
        done/reason），並退役 task 層級 scheduled 鏡像。對 legacy（只有 task 層級
        calendar_event_id）的 task，這次同步就地遷移成 plan entry。"""
        linked = self._find_task_by_calendar_id(event.id)
        if linked is None:
            return ""

        rel_path, fm, body = linked
        new_entry = _event_to_plan_entry(event)
        plan = _plan_entries(fm)
        matched = False
        for i, e in enumerate(plan):
            if e.get("calendar_event_id") == event.id:
                preserved = {k: e[k] for k in ("done", "reason") if k in e}
                plan[i] = {**new_entry, **preserved}
                matched = True
                break
        if not matched:  # legacy task-level link → migrate into plan[] now
            plan.append(new_entry)
        fm["plan"] = _stringify_plan(plan)
        # retire the legacy task-level mirror (v3-D)
        for k in ("scheduled", "scheduled_end", "calendar_event_id"):
            fm.pop(k, None)

        if title_changed:
            fm["title"] = event.title
            new_rel = f"{TASK_DIR}/{_slugify(event.title)}.md"
        else:
            new_rel = rel_path

        fm["dateModified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm = _stringify_fm_dates(fm)

        # Write-then-delete 順序：先寫新檔，成功後才刪舊檔。若 write 拋例外，舊檔還在，
        # task 不會遺失。
        write_page(new_rel, fm, body)
        if new_rel != rel_path:
            delete_page(rel_path)
        return f"\n   📝 Task 同步更新：{new_rel}"

    def _tool_delete_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing title", is_error=True)

        found = self._find_calendar_event_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 Calendar 事件（最近 30 天）。",
                is_error=True,
            )

        google_calendar.delete_event(found.id)

        # 靜默處理對應 task（找不到不視為錯誤，PRD 規格）。v3-D：只拔掉這一筆 plan
        # entry（保住同一個 task 的其他天，多事件不能被一次刪光）；若刪完已無排程且沒有
        # 筆記內容，視為純行事曆 task → 整檔刪除（沿用舊 UX）。
        task_note = ""
        linked = self._find_task_by_calendar_id(found.id)
        if linked is not None:
            task_rel, fm, body = linked
            remaining = [e for e in _plan_entries(fm) if e.get("calendar_event_id") != found.id]
            legacy_match = fm.get("calendar_event_id") == found.id
            if not remaining and not body.strip():
                if delete_page(task_rel):
                    task_note = f"\n   📝 Task 一併刪除：{task_rel}"
            else:
                fm["plan"] = _stringify_plan(remaining)
                if legacy_match:  # retire the legacy task-level mirror (v3-D)
                    for k in ("scheduled", "scheduled_end", "calendar_event_id"):
                        fm.pop(k, None)
                fm["dateModified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                fm = _stringify_fm_dates(fm)
                write_page(task_rel, fm, body)
                task_note = f"\n   📝 Task 已移除該時段：{task_rel}"

        summary = f"🗑️ 已刪除 Calendar 事件：{found.title}{task_note}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_deleted",
                "payload": {"id": found.id, "title": found.title},
                "log": found.title,
            },
        )

    def _find_calendar_event_by_title(self, title: str) -> CalendarEvent | None:
        """在最近 30 天（past 7 + future 23）內 by title 模糊搜尋，回第一個匹配。"""
        tz = ZoneInfo("Asia/Taipei")
        now = datetime.now(tz)
        time_min = now - timedelta(days=7)
        time_max = now + timedelta(days=23)
        title_lower = title.lower()
        events = google_calendar.find_events_by_title(title, time_min=time_min, time_max=time_max)
        # Google q 搜尋已做過濾；這裡再確認以防萬一
        for e in events:
            if title_lower in e.title.lower():
                return e
        return None

    # ── Gmail tool executors ─────────────────────────────────────

    def _tool_list_gmail_unread(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query") or "is:unread")
        max_results = min(int(input_.get("max_results") or 10), 20)

        messages = google_gmail.list_messages(query=query, max_results=max_results)
        if not messages:
            return _ToolOutcome(content=f"沒有符合條件的信件（query: {query}）。")

        lines = [f"*Gmail 信件（{query}，共 {len(messages)} 封）*"]
        for i, m in enumerate(messages, 1):
            lines.append(
                f"{i}. [{m['date'][:16]}] {m['from']}\n"
                f"   主旨：{m['subject']}\n"
                f"   id: {m['id']} | thread: {m['thread_id']}\n"
                f"   摘要：{m['snippet'][:100]}"
            )
        return _ToolOutcome(content="\n\n".join(lines))

    def _tool_get_gmail_message(self, input_: dict) -> _ToolOutcome:
        message_id = str(input_.get("message_id", "")).strip()
        if not message_id:
            return _ToolOutcome(content="Missing message_id", is_error=True)

        msg = google_gmail.get_message(message_id)
        content = (
            f"*信件詳情*\n"
            f"From: {msg['from']}\n"
            f"To: {msg['to']}\n"
            f"CC: {msg['cc']}\n"
            f"Subject: {msg['subject']}\n"
            f"Date: {msg['date']}\n"
            f"Thread ID: {msg['thread_id']}\n"
            f"Message ID: {msg['id']}\n\n"
            f"---\n{msg['body'] or '（無純文字內容）'}"
        )
        return _ToolOutcome(content=content)

    def _tool_search_gmail_history(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        if not query:
            return _ToolOutcome(content="Missing query", is_error=True)
        max_results = min(int(input_.get("max_results") or 5), 10)

        messages = google_gmail.list_messages(query=query, max_results=max_results)
        if not messages:
            return _ToolOutcome(content=f"沒有找到符合的歷史信件（query: {query}）。")

        lines = [f"*Gmail 歷史搜尋（{query}，共 {len(messages)} 封）*"]
        for i, m in enumerate(messages, 1):
            lines.append(
                f"{i}. [{m['date'][:16]}] to:{m['to']}\n"
                f"   主旨：{m['subject']}\n"
                f"   id: {m['id']}\n"
                f"   摘要：{m['snippet'][:120]}"
            )
        return _ToolOutcome(content="\n\n".join(lines))

    def _tool_create_gmail_draft(self, input_: dict) -> _ToolOutcome:
        to = input_.get("to")
        subject = str(input_.get("subject", "")).strip()
        body = str(input_.get("body", "")).strip()
        if not to or not subject or not body:
            return _ToolOutcome(content="Missing required fields: to, subject, body", is_error=True)
        if isinstance(to, str):
            to = [to]

        thread_id = input_.get("thread_id") or None
        in_reply_to = input_.get("in_reply_to_message_id") or None

        result = google_gmail.create_draft(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to_message_id=in_reply_to,
        )

        content = (
            f"✉️ 草稿已存 Gmail Drafts\n"
            f"draft_id: {result['draft_id']}\n"
            f"Gmail 連結：{result['gmail_web_link']}\n\n"
            f"---\n"
            f"To: {', '.join(to)}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n"
            f"---\n\n"
            f"確認 OK 後說「發」，Nami 會呼叫 send_gmail_draft 發出。\n"
            f"要修改就告訴我哪裡要改。"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_draft_created",
                "payload": {
                    "draft_id": result["draft_id"],
                    "to": to,
                    "subject": subject,
                    "gmail_web_link": result["gmail_web_link"],
                },
                "log": subject,
            },
        )

    def _tool_update_gmail_draft(self, input_: dict) -> _ToolOutcome:
        draft_id = str(input_.get("draft_id", "")).strip()
        if not draft_id:
            return _ToolOutcome(content="Missing draft_id", is_error=True)

        to = input_.get("to") or None
        if isinstance(to, str):
            to = [to]
        subject = input_.get("subject") or None
        body = input_.get("body") or None

        result = google_gmail.update_draft(draft_id, to=to, subject=subject, body=body)

        content = (
            f"✏️ 草稿已更新\n"
            f"draft_id: {result['draft_id']}\n"
            f"Gmail 連結：{result['gmail_web_link']}\n\n"
            f"---\n"
            f"To: {', '.join(result['to'])}\n"
            f"Subject: {result['subject']}\n\n"
            f"{result['body']}\n"
            f"---\n\n"
            f"確認 OK 後說「發」。"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_draft_updated",
                "payload": {
                    "draft_id": draft_id,
                    "subject": result["subject"],
                    "gmail_web_link": result["gmail_web_link"],
                },
                "log": result["subject"],
            },
        )

    def _tool_send_gmail_draft(self, input_: dict) -> _ToolOutcome:
        draft_id = str(input_.get("draft_id", "")).strip()
        if not draft_id:
            return _ToolOutcome(content="Missing draft_id", is_error=True)

        result = google_gmail.send_draft(draft_id)

        content = (
            f"📬 信件已發出\nmessage_id: {result['message_id']}\nthread_id: {result['thread_id']}"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_sent",
                "payload": {
                    "message_id": result["message_id"],
                    "thread_id": result["thread_id"],
                },
                "log": f"draft {draft_id}",
            },
        )

    def _tool_write_vault_note(self, input_: dict) -> _ToolOutcome:
        relative_path = str(input_.get("relative_path", "")).strip()
        title = str(input_.get("title", "")).strip()
        body = str(input_.get("body", "")).strip()
        if not relative_path or not title or not body:
            return _ToolOutcome(
                content="Missing required fields: relative_path, title, body",
                is_error=True,
            )

        tags = input_.get("tags") or []
        overwrite = bool(input_.get("overwrite", False))

        assert_nami_can_write(relative_path)

        if not overwrite:
            existing = read_page(relative_path)
            if existing is not None:
                return _ToolOutcome(
                    content=(
                        f"⚠️ 路徑已存在：{relative_path}\n"
                        f"若要覆寫，請在請求中加上 overwrite: true，"
                        f"或先用 read_vault_note 確認內容再決定。"
                    ),
                    is_error=True,
                )

        frontmatter: dict = {"title": title}
        if tags:
            frontmatter["tags"] = tags

        write_page(relative_path, frontmatter, body, overwrite=True)

        content = f"📝 筆記已寫入：{relative_path}\n標題：{title}"
        if tags:
            content += f"\nTags：{', '.join(tags)}"
        return _ToolOutcome(
            content=content,
            event={
                "name": "vault_note_written",
                "payload": {"relative_path": relative_path, "title": title},
                "log": title,
            },
        )

    def _tool_read_vault_note(self, input_: dict) -> _ToolOutcome:
        relative_path = str(input_.get("relative_path", "")).strip()
        if not relative_path:
            return _ToolOutcome(content="Missing relative_path", is_error=True)

        assert_nami_can_read(relative_path)

        content = read_page(relative_path)
        if content is None:
            return _ToolOutcome(
                content=f"找不到此路徑的筆記：{relative_path}",
                is_error=True,
            )
        return _ToolOutcome(content=content)

    def _tool_list_vault_notes(self, input_: dict) -> _ToolOutcome:
        _default_dir = "AgentOutputs/nami/notes/"
        relative_dir = str(input_.get("relative_dir", _default_dir)).strip() or _default_dir

        assert_nami_can_read(relative_dir if relative_dir.endswith("/") else relative_dir + "/")

        files = list_files(relative_dir)
        if not files:
            return _ToolOutcome(content=f"資料夾 {relative_dir} 目前沒有任何 .md 筆記。")

        lines = [f"📂 {relative_dir} 底下共 {len(files)} 個筆記："]
        for f in files:
            lines.append(f"  - {relative_dir.rstrip('/')}/{f.name}")
        return _ToolOutcome(content="\n".join(lines))

    def _tool_web_search(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        if not query:
            return _ToolOutcome(content="query 不能為空", is_error=True)
        num = min(int(input_.get("num_results", 10)), 20)

        from shared.firecrawl_search import FirecrawlSearchError, firecrawl_search

        try:
            results = firecrawl_search(query, num_results=num)
        except FirecrawlSearchError as e:
            return _ToolOutcome(content=f"搜尋失敗：{e}", is_error=True)

        if not results:
            return _ToolOutcome(content="搜尋無結果，換個關鍵字試試。")

        lines = []
        for r in results:
            title = r["title"] or r["url"]
            desc = r["description"]
            lines.append(f"- [{title}]({r['url']})")
            if desc:
                lines.append(f"  {desc}")
        return _ToolOutcome(
            content="\n".join(lines),
            event={
                "name": "web_search",
                "payload": {"query": query, "hits": len(results)},
                "log": f"search: {query!r} ({len(results)} hits)",
            },
        )

    def _tool_fetch_url(self, input_: dict) -> _ToolOutcome:
        url = str(input_.get("url", "")).strip()
        if not url:
            return _ToolOutcome(content="url 不能為空", is_error=True)

        from shared.web_scraper import scrape_url

        try:
            content = scrape_url(url, mode="auto")
        except RuntimeError as e:
            return _ToolOutcome(content=f"無法擷取頁面：{e}", is_error=True)

        _MAX_CHARS = 5000
        original_len = len(content)
        truncated = original_len > _MAX_CHARS
        if truncated:
            content = content[:_MAX_CHARS] + f"\n\n[...已截斷，原文共 {original_len} 字元]"

        return _ToolOutcome(
            content=content,
            event={
                "name": "fetch_url",
                "payload": {"url": url, "chars": original_len, "truncated": truncated},
                "log": f"fetch: {url}",
            },
        )

    def _tool_pubmed_lookup(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        if not query:
            return _ToolOutcome(content="query 不能為空", is_error=True)

        try:
            max_results = int(input_.get("max_results") or 5)
        except (TypeError, ValueError):
            return _ToolOutcome(content="max_results 必須是整數", is_error=True)
        max_results = max(1, min(max_results, 20))

        since_year = input_.get("since_year")
        if since_year is not None:
            try:
                since_year = int(since_year)
            except (TypeError, ValueError):
                return _ToolOutcome(content="since_year 必須是整數年份", is_error=True)

        from shared.pubmed_client import PubMedClientError, lookup

        try:
            results = lookup(query, max_results=max_results, since_year=since_year)
        except PubMedClientError as e:
            return _ToolOutcome(content=f"PubMed 查詢失敗：{e}", is_error=True)

        if not results:
            return _ToolOutcome(
                content=f"PubMed 沒找到 {query!r} 相關文獻"
                + (f"（{since_year} 之後）" if since_year else "")
                + "。換個關鍵字、放寬年份限制、或改用 web_search。"
            )

        lines: list[str] = []
        for r in results:
            authors = r["authors"]
            if len(authors) == 0:
                author_label = ""
            elif len(authors) == 1:
                author_label = authors[0]
            elif len(authors) <= 3:
                author_label = ", ".join(authors)
            else:
                author_label = f"{authors[0]} et al."

            head = f"- **{r['title']}**"
            meta_bits = [b for b in (author_label, r["journal"], r["year"]) if b]
            if meta_bits:
                head += f"  \n  {' · '.join(meta_bits)}"

            link_bits = [f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/)"]
            if r["pmcid"]:
                link_bits.append(f"[PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/{r['pmcid']}/)")
            if r["doi"]:
                link_bits.append(f"[doi:{r['doi']}](https://doi.org/{r['doi']})")
            head += f"  \n  {' · '.join(link_bits)}"
            lines.append(head)

        body = "\n\n".join(lines)
        header = f"### PubMed top {len(results)} for {query!r}"
        if since_year:
            header += f"（{since_year} 起）"

        return _ToolOutcome(
            content=f"{header}\n\n{body}",
            event={
                "name": "pubmed_lookup",
                "payload": {
                    "query": query,
                    "hits": len(results),
                    "since_year": since_year,
                },
                "log": f"pubmed: {query!r} ({len(results)} hits)",
            },
        )

    # ── arXiv + YouTube tool executors ──────────────────────────

    def _tool_arxiv_lookup(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        if not query:
            return _ToolOutcome(content="query 不能為空", is_error=True)

        try:
            max_results = int(input_.get("max_results") or 5)
        except (TypeError, ValueError):
            return _ToolOutcome(content="max_results 必須是整數", is_error=True)
        max_results = max(1, min(max_results, 20))

        sort_by = str(input_.get("sort_by") or "relevance").strip()
        if sort_by not in ("relevance", "submittedDate", "lastUpdatedDate"):
            return _ToolOutcome(
                content=(
                    f"sort_by 必須是 relevance / submittedDate / lastUpdatedDate，收到：{sort_by!r}"
                ),
                is_error=True,
            )

        from shared.arxiv_client import ArxivClientError, search

        try:
            results = search(query, max_results=max_results, sort_by=sort_by)
        except ArxivClientError as e:
            return _ToolOutcome(content=f"arXiv 查詢失敗：{e}", is_error=True)

        if not results:
            return _ToolOutcome(
                content=(
                    f"arXiv 沒找到 {query!r} 相關文獻。"
                    f"換個關鍵字（記得用英文）、改用 cat: prefix、或改 PubMed / web_search。"
                )
            )

        lines: list[str] = []
        for r in results:
            authors = r["authors"]
            if len(authors) <= 3:
                author_label = ", ".join(authors)
            else:
                author_label = f"{authors[0]} et al."

            head = f"- **{r['title']}**  \n  arXiv:{r['arxiv_id']}"
            meta_bits = [b for b in (author_label, r["published"], r["primary_category"]) if b]
            if meta_bits:
                head += f"  \n  {' · '.join(meta_bits)}"
            head += f"  \n  [abs]({r['abs_url']}) · [pdf]({r['pdf_url']})"
            lines.append(head)

        header = f"### arXiv top {len(results)} for {query!r}"
        if sort_by != "relevance":
            header += f"（sorted by {sort_by}）"

        return _ToolOutcome(
            content=f"{header}\n\n" + "\n\n".join(lines),
            event={
                "name": "arxiv_lookup",
                "payload": {"query": query, "hits": len(results), "sort_by": sort_by},
                "log": f"arxiv: {query!r} ({len(results)} hits)",
            },
        )

    def _tool_arxiv_citations(self, input_: dict) -> _ToolOutcome:
        arxiv_id = str(input_.get("arxiv_id", "")).strip()
        if not arxiv_id:
            return _ToolOutcome(content="arxiv_id 不能為空", is_error=True)

        try:
            limit = int(input_.get("limit") or 10)
        except (TypeError, ValueError):
            return _ToolOutcome(content="limit 必須是整數", is_error=True)
        limit = max(1, min(limit, 20))

        from shared.arxiv_client import ArxivClientError, get_citations

        try:
            data = get_citations(arxiv_id, limit=limit)
        except ArxivClientError as e:
            return _ToolOutcome(content=f"Semantic Scholar 查詢失敗：{e}", is_error=True)

        paper = data.get("paper")
        if not paper:
            return _ToolOutcome(
                content=(f"Semantic Scholar 沒索引 arXiv:{arxiv_id}（可能太新或不在 S2 範圍內）。"),
                is_error=False,
            )

        lines: list[str] = [
            f"### {paper.get('title', arxiv_id)}",
            "",
            f"- Authors: {', '.join(paper.get('authors', []))}",
            f"- Year: {paper.get('year') or '?'}",
            f"- Citation count: {paper.get('citation_count') or 0}"
            f"（influential: {paper.get('influential_citation_count') or 0}）",
            f"- References: {paper.get('reference_count') or 0}",
        ]
        if paper.get("is_open_access"):
            lines.append("- Open access ✓")

        citing = data.get("citing") or []
        if citing:
            lines.append("")
            lines.append(f"#### 引用此 paper 的文獻 (top {len(citing)})")
            for c in citing:
                if not c:
                    continue
                bits = [c.get("title") or "(no title)"]
                if c.get("year"):
                    bits.append(str(c["year"]))
                if c.get("citation_count") is not None:
                    bits.append(f"{c['citation_count']} cites")
                lines.append(f"- {' · '.join(bits)}")

        refs = data.get("references") or []
        if refs:
            lines.append("")
            lines.append(f"#### 此 paper 引用的文獻 (top {len(refs)})")
            for r in refs:
                if not r:
                    continue
                bits = [r.get("title") or "(no title)"]
                if r.get("year"):
                    bits.append(str(r["year"]))
                if r.get("citation_count") is not None:
                    bits.append(f"{r['citation_count']} cites")
                lines.append(f"- {' · '.join(bits)}")

        return _ToolOutcome(
            content="\n".join(lines),
            event={
                "name": "arxiv_citations",
                "payload": {
                    "arxiv_id": arxiv_id,
                    "citation_count": paper.get("citation_count"),
                    "citing_returned": len(citing),
                    "references_returned": len(refs),
                },
                "log": f"arxiv_citations: {arxiv_id}",
            },
        )

    def _tool_youtube_transcript(self, input_: dict) -> _ToolOutcome:
        url_or_id = str(input_.get("url_or_id", "")).strip()
        if not url_or_id:
            return _ToolOutcome(content="url_or_id 不能為空", is_error=True)

        with_timestamps = bool(input_.get("with_timestamps", False))

        from shared.youtube_transcript import (
            YouTubeTranscriptError,
            fetch_transcript,
            to_plain_text,
            to_timestamped_text,
            total_duration,
        )

        try:
            segments = fetch_transcript(url_or_id)
        except YouTubeTranscriptError as e:
            return _ToolOutcome(content=f"YouTube transcript 失敗：{e}", is_error=True)

        if not segments:
            return _ToolOutcome(
                content="抓到 transcript 但內容為空（可能影片只有音樂沒口白）。",
            )

        text = to_timestamped_text(segments) if with_timestamps else to_plain_text(segments)
        duration_sec = int(total_duration(segments))
        m, s = divmod(duration_sec, 60)
        h, m = divmod(m, 60)
        duration_label = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

        _MAX_CHARS = 12000  # 給 LLM 摘要保留充足上下文
        truncated = len(text) > _MAX_CHARS
        if truncated:
            text = text[:_MAX_CHARS] + f"\n\n[...已截斷，原文共 {len(text)} 字元]"

        header = (
            f"### YouTube transcript（{len(segments)} 段，總長 {duration_label}）"
            f"{'（含時間戳）' if with_timestamps else ''}"
        )
        return _ToolOutcome(
            content=f"{header}\n\n{text}",
            event={
                "name": "youtube_transcript",
                "payload": {
                    "url_or_id": url_or_id,
                    "segments": len(segments),
                    "duration_sec": duration_sec,
                    "with_timestamps": with_timestamps,
                    "truncated": truncated,
                },
                "log": f"yt: {url_or_id} ({len(segments)} segs, {duration_label})",
            },
        )

    def _tool_ask_zoro(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        capability = str(input_.get("capability", "")).strip()
        if not query:
            return _ToolOutcome(content="query 不能為空", is_error=True)
        if capability not in ("trend_check", "social_listening", "keyword_research"):
            return _ToolOutcome(
                content=(
                    "capability 必須是 trend_check / social_listening / keyword_research，"
                    f"收到：{capability!r}"
                ),
                is_error=True,
            )

        try:
            if capability == "trend_check":
                from agents.zoro.trends_api import get_trends

                data = get_trends(query)
                if not data:
                    return _ToolOutcome(
                        content=(
                            f"Zoro 報：Google Trends 查不到「{query}」資料（API 失敗或無數據）"
                        )
                    )
                top = data.get("related_top", [])
                rising = data.get("related_rising", [])
                lines = [
                    f"Zoro 報 Google Trends「{query}」（過去 3 個月）：",
                    f"- 趨勢方向：{data.get('trend_direction', 'unknown')}",
                ]
                if top:
                    top_str = ", ".join(f"{r['query']}({r['value']})" for r in top[:10])
                    lines.append(f"- 相關熱搜 top {len(top[:10])}：{top_str}")
                if rising:
                    rising_str = ", ".join(f"{r['query']}({r['value']})" for r in rising[:5])
                    lines.append(f"- 上升搜尋 top {len(rising[:5])}：{rising_str}")
                content = "\n".join(lines)

            elif capability == "social_listening":
                from agents.zoro.reddit_api import (
                    hot_in_health_subreddits,
                    search_reddit_posts,
                )

                # 先試健康 subreddit hot，title 模糊比對
                hot = hot_in_health_subreddits(limit=50, max_age_hours=48)
                q_lower = query.lower()
                matched = [p for p in hot if q_lower in p.get("title", "").lower()][:10]
                if matched:
                    posts = matched
                    source_label = "r/health-subreddits hot 24-48h"
                else:
                    # 退到全 Reddit search
                    fallback = search_reddit_posts(query, max_results=10)
                    posts = fallback.get("posts", [])
                    source_label = "Reddit search (year)"

                if not posts:
                    return _ToolOutcome(content=f"Zoro 報：Reddit 上找不到「{query}」相關熱門貼文")

                lines = [f"Zoro 報 Reddit「{query}」（{source_label}，{len(posts)} 篇）："]
                for p in posts[:10]:
                    title = p.get("title", "")
                    url = p.get("url", "")
                    score = p.get("score", 0)
                    comments = p.get("num_comments", 0)
                    sub = p.get("subreddit", "")
                    lines.append(
                        f"- [{title}]({url}) — {score} upvote / {comments} comment / r/{sub}"
                    )
                content = "\n".join(lines)

            else:  # keyword_research
                from agents.zoro.keyword_research import research_keywords

                data = research_keywords(query, content_type="blog")
                if not data:
                    return _ToolOutcome(content=f"Zoro keyword research 失敗：「{query}」")
                keywords = data.get("keywords", [])[:10]
                titles = data.get("blog_titles", [])[:5]
                used = data.get("sources_used", [])
                failed = data.get("sources_failed", [])
                summary = (data.get("analysis_summary", "") or "")[:500]
                lines = [
                    f"Zoro 報完整關鍵字研究「{query}」：",
                    f"- Keywords (top 10): {keywords}",
                    f"- Blog title 建議 (top 5): {titles}",
                    f"- Sources used: {used}",
                ]
                if failed:
                    lines.append(f"- Sources failed: {failed}")
                if summary:
                    lines.append(f"- Analysis: {summary}")
                content = "\n".join(lines)

        except Exception as e:
            logger.exception(f"ask_zoro {capability} failed for query={query!r}")
            return _ToolOutcome(content=f"Zoro 執行 {capability} 失敗：{e}", is_error=True)

        return _ToolOutcome(
            content=content,
            event={
                "name": "ask_zoro",
                "payload": {"query": query, "capability": capability},
                "log": f"ask_zoro: {capability} {query!r}",
            },
        )


# ── Utilities ────────────────────────────────────────────────────────


def _build_date_context() -> str:
    """今日日期資訊，注入到 user message（而非 system）以保持 system prompt 可快取。"""
    _weekday_zh = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    today_str = now.strftime("%Y-%m-%d")
    today_zh = _weekday_zh[now.weekday()]
    date_lines = []
    for i in range(14):
        d = now + timedelta(days=i)
        label = "今天" if i == 0 else ("明天" if i == 1 else "")
        suffix = f"（{label}）" if label else ""
        date_lines.append(f"  {d.strftime('%Y-%m-%d')} {_weekday_zh[d.weekday()]}{suffix}")
    date_table = "\n".join(date_lines)
    return (
        f"## 今日資訊\n"
        f"今天是 {today_str}（{today_zh}）。\n\n"
        f"未來 14 天日期對照表（直接查，不要自行推算）：\n{date_table}"
    )


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]", " ", title)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:60] or "untitled"


def _stringify_fm_dates(fm: dict) -> dict:
    """yaml.safe_load 會把 2026-04-23 解析成 date 物件，寫回前先轉字串。"""
    import datetime as _dt

    return {
        k: v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v for k, v in fm.items()
    }


def _extract_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml

        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def _to_vault_relative(path: Path) -> str:
    """把 absolute Path 轉成 vault-relative 字串（供訊息顯示）。"""
    parts = path.parts
    for marker in ("Projects", "TaskNotes"):
        if marker in parts:
            idx = parts.index(marker)
            return "/".join(parts[idx:])
    return path.name


def _fmt_event_time(start: str, end: str) -> str:
    """格式化事件時間給使用者看（Asia/Taipei，精簡）。"""
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if s.tzinfo is not None:
            tz = ZoneInfo("Asia/Taipei")
            s = s.astimezone(tz)
            e = e.astimezone(tz)
        if s.date() == e.date():
            return f"{s.strftime('%m/%d %H:%M')}-{e.strftime('%H:%M')}"
        return f"{s.strftime('%m/%d %H:%M')} 至 {e.strftime('%m/%d %H:%M')}"
    except Exception:
        # 全日事件是 YYYY-MM-DD 格式，無時間
        return f"{start} 至 {end}"


def _parse_iso_local(s: str, tz: ZoneInfo) -> datetime:
    """ISO 字串轉 datetime，無時區時假設為 ``tz``。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _strip_tz(iso_str: str) -> str:
    """剝掉 ISO 字串的時區，對齊 Obsidian task scheduled 格式。

    ``2026-04-25T15:00:00+08:00`` → ``2026-04-25T15:00:00``
    ``2026-04-25T15:00:00Z``      → ``2026-04-25T15:00:00``
    無 tz 原樣回。全日事件 (``2026-04-25``) 原樣回。
    """
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        return iso_str
    local = dt.astimezone(ZoneInfo("Asia/Taipei"))
    return local.strftime("%Y-%m-%dT%H:%M:%S")


# ── ADR-041 v3-D: Nami writes the per-entry plan[] projection, not the retired
#    task-level scheduled mirror. These build/read plan entries in the SAME shape
#    the Bridge writes (shared.weekly_writer): date + pomodoros + start/end with the
#    Asia/Taipei offset + calendar_event_id. One 🍅 = 30 min. ─────────────────────

_BLOCK_MIN_PER_POM = 30  # matches shared.weekly_writer.CALENDAR_BLOCK_MINUTES_PER_POMODORO


def _event_dt_taipei(iso_str: str) -> datetime | None:
    """Parse a Google RFC3339 datetime into an Asia/Taipei-aware datetime; None for
    an all-day date string or anything unparseable."""
    if not iso_str or "T" not in iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return dt.astimezone(ZoneInfo("Asia/Taipei"))


def _event_to_plan_entry(event: CalendarEvent) -> dict:
    """A v3 plan[] entry from a calendar event (ADR-041 v3-D): date + derived
    pomodoros (duration ÷ 30, ≥1) + start/end (ISO seconds WITH the Taipei offset,
    byte-aligned with shared.weekly_writer._iso_with_offset) + calendar_event_id.
    An all-day / unparseable start degrades to a bare linked entry (no time)."""
    start_dt = _event_dt_taipei(event.start)
    if start_dt is None:
        return {"calendar_event_id": event.id}
    end_dt = _event_dt_taipei(event.end)
    if end_dt is not None and end_dt > start_dt:
        pom = max(1, round((end_dt - start_dt).total_seconds() / 60 / _BLOCK_MIN_PER_POM))
    else:
        pom = 1
        end_dt = start_dt + timedelta(minutes=_BLOCK_MIN_PER_POM)
    return {
        "date": start_dt.date().isoformat(),
        "pomodoros": pom,
        "start": start_dt.isoformat(timespec="seconds"),
        "end": end_dt.isoformat(timespec="seconds"),
        "calendar_event_id": event.id,
    }


def _plan_entries(fm: dict) -> list[dict]:
    """The task's plan[] as a list of dicts (defensive against None / scalars)."""
    return [e for e in (fm.get("plan") or []) if isinstance(e, dict)]


def _stringify_plan(plan: list[dict]) -> list[dict]:
    """yaml.safe_load turns ``date: 2026-06-05`` into a date object; normalise each
    entry's date/start/end back to ISO strings before write-back so the vault file
    stays string-uniform (matching what the Bridge writer produces)."""
    import datetime as _dt

    out = []
    for e in plan:
        out.append(
            {
                k: (v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v)
                for k, v in e.items()
            }
        )
    return out


def _plan_scheduled_display(fm: dict) -> str:
    """The earliest scheduled moment to show in list_tasks — derived from plan[]
    (v3) with a fallback to the legacy task-level ``scheduled`` mirror."""
    moments = []
    for e in _plan_entries(fm):
        when = e.get("start") or e.get("date")
        if when:
            moments.append(str(when))
    if moments:
        earliest = sorted(moments)[0]
        return _strip_tz(earliest) if "T" in earliest else earliest
    return str(fm.get("scheduled", ""))


# ── Deprecated alias（for backward-compat with old tests/imports） ─────

PROJECT_BOOTSTRAP_FLOW = NAMI_AGENT_FLOW  # 舊 flow name；保留以免 break import
