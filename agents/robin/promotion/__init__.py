"""Robin Source Promotion bounded package（ADR-052）.

Source Promotion 的 domain logic 與組裝工廠（composition root）。原先散在
``shared/`` 平面命名空間的 19 個 promotion 領域模組收攏於此 — ``shared/``
只保留「2+ agent 共用」的基礎設施（ADR-052 邊界規則）。

對外入口：

- ``agents.robin.promotion.factory`` — composition root：
  ``load_promotion_config()``（env → config）＋
  ``build_promotion_review_service(config)``（config → 組裝好的 service）。
  Thousand Sunny lifespan、CLI、未來 agent 都走同一個工廠，不重複組裝知識。
- 其餘 module 為 pipeline 協作者（preflight / source map builder / concept +
  entity engines / commit / acceptance gate / renderer / targets / dry-run
  fixtures / KB indexes）。

刻意不在此 re-export：promotion 模組群 import 成本高（sqlite registry、
schema 解析），re-export 會把成本掛在任何 ``agents.robin.promotion.*`` 的
import 上（``DISABLE_ROBIN=1`` 的冷啟也吃到）。呼叫端直接 import 子模組。
"""
