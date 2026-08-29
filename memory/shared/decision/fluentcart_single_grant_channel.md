---
type: decision
visibility: shared
agent: shared
confidence: high
created: 2026-08-16
expires: permanent
tags: [fleet, fluent-community, fluentcart, fluentcrm, refund, entitlement, sanji]
name_zh: FluentCart feed 是社群權限的唯一授予通道
name_en: The FluentCart product feed is the single grant channel for community access
description_zh: 自由艦隊的空間/課程權限只能由 FluentCart product feed 授予；FluentCRM funnel #42 的 add_to_fluent_community 是刻意刪除的，不要加回去。
description_en: Fleet space/course access may only be granted by the FluentCart product feed; the add_to_fluent_community action in FluentCRM funnel #42 was deliberately deleted and must not be restored.
---

# FluentCart Feed Is the Single Grant Channel

## Decision

`fleet.shosho.tw` 的社群空間與課程權限，**只能由 FluentCart product 的 integration feed 授予**。

- FluentCRM funnel #42（`Order Paid (Payment/Subscription)`）原本有一個
  `add_to_fluent_community` sequence（id 22），**已於 2026-08-16 刪除，這是刻意的，不要加回去。**
- 新增付費空間或課程時，加進 **product feed 的 `space_ids` / `course_ids`**，
  不要用 FluentCRM funnel 的 add-to-space / add-to-course action。
- 付費內容的空間 `privacy` 必須是 `private`。public 空間任何登入者都能自行加入，且會被
  `cacheAccessSpaces()` 無條件算進可存取清單，等於繞過整套權限模型。

## Why This Matters

FluentCart 的權限模型是 **order-scoped entitlement**：授予時在 `fcom_space_user.meta` 蓋上
`fct_ids: [order_id]` 憑證，退款時把該 order id 拔掉，空了才真正移除會籍。這個設計本身是正確的，
而且能正確處理「同時持有多個產品、權限重疊」的情況。

但它成立的前提是**所有授予都留下憑證**。FluentCRM 的 `AddToSpaceAction` 直接呼叫
`Helper::addToSpace()`，**不寫任何 meta**；而且 FluentCart 的 revoke 只會掃**自己 feed 列出的**
space/course，funnel 多加的空間它根本看不到。

實際後果（2026-08-16 發現）：18 個全額退款帳號全部仍留在 funnel 多加的兩個空間裡，
其中 8 個還留在課程裡，最久的已經超過半年。FluentCart 本身**沒有壞**——它正確移除了自己授予的
9 個空間，log 有完整紀錄。壞的是同一件事被設定了兩次，其中一次用了不支援回收的工具。

## Known Gaps (accepted, not bugs)

- **部分退款不會自動回收**。FluentCart 的 revoke 只認 `order_fully_refunded`、
  `subscription_expired_validity`、`order_status_changed_to_canceled` 三個 hook；
  `order_partially_refunded` 刻意不在其中（部分退款 ≠ 取消服務）。目前靠人工處理，
  要自動化需寫 mu-plugin 掛 hook。
- **`blocked` 狀態不會因重新購買而解除**，需人工改回 `active`。

## Restore Payload

若日後判定需要還原 funnel #42 的 sequence 22（**不建議**）：

```
funnel_id    : 42
action_name  : add_to_fluent_community
sequence     : 4
status       : published
c_delay/delay: 0/0
conditions   : a:0:{}
settings     : a:2:{s:12:"community_id";a:12:{i:0;s:2:"12";i:1;s:1:"2";i:2;s:2:"15";i:3;s:1:"9";
               i:4;s:2:"18";i:5;s:2:"10";i:6;s:2:"14";i:7;s:1:"3";i:8;s:2:"17";i:9;s:1:"8";
               i:10;s:2:"11";i:11;s:2:"19";}s:21:"send_wp_welcome_email";s:3:"yes";}
```

註：上面的 `community_id` 含 space `18`，該空間已不存在。

## See Also

- `agents/sanji/CONTEXT.md` — 完整機制、資料模型與地雷清單
- [[fleet_community_stack]] — 存取方式
