"""Franky state-layer modules（ADR-023 §3 + §6）。

兩塊互補職責：

- **Pre-RAG context substrate**（§3 Phase 1）— `franky_context_snapshot.md` 重生成
  路徑，inject 進 score + synthesis prompt。不依賴 ADR-020 RAG infra（後者尚未
  merge 進 main）。
- **Proposal lifecycle persistence**（§6）— S3 weekly synthesis 與 S4 monthly
  retrospective 用的 proposal_metrics 表 helpers。
"""
