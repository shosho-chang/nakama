# ADR-028 PubMed Attachments Flatten Residual — 2026-05-24

Closes #674.

The post-PR-B1 residual cleanup for ADR-028 Phase B sub-op #9
(`kb-attachments-flatten`). PR #656 landed the code half on 2026-05-21
(`agents/robin/pubmed_digest.py` writes to flat `KB/Attachments/{pmid}/`
instead of the `pubmed/` bucket), but VPS daemon stayed on pre-#656 code
through 2026-05-22 / 2026-05-23 / 2026-05-24 morning cron runs. Those
three days of PubMed digest writes landed in the deprecated bucket and
this commit captures the cleanup that drained it.

## Execution

2026-05-24 07:16 UTC+8 — ran `data/migrations/2026-05-20-vault-cleanup/scripts/flatten_attachments.py`
against the live vault.

```
moves       = 22  (all PMIDs from KB/Attachments/pubmed/* → KB/Attachments/*)
rewrites    = 23  (markdown files with attachments/pubmed/ → attachments/ refs)
collisions  = 0
sha256      = all moves verified
pubmed/ dir = deleted (recycle-bin)
```

## Deploy timing

修修 confirmed (2026-05-24 ~07:15 Asia/Taipei) that the VPS deploy was
applied **after** today's 05:32 cron run. The two PDFs written this
morning (`42174008.pdf` + `42174272.pdf`, mtime 05:32:52 + 05:33:09) are
the final batch of pre-deploy old-code writes. Tomorrow's
2026-05-25 05:32 cron should write directly to flat
`KB/Attachments/{pmid}/`.

If `KB/Attachments/pubmed/` reappears on 2026-05-25 morning, deploy is
not actually live and needs another pull + restart pass.

## Files

- `manifest.json` — per-op record with sha256 verification (from `flatten_attachments.py`)
