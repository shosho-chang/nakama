"""ADR-028 §8 — one missed Journal ref rewrite from the Files/ migration.

The resume script's MISSED dict targeted the wrong figure (#13 instead of
#14). #14 `Pasted image 20240310161726.png` was moved but its embed in
Journals/Daily/2024-03-09.md stayed bare. This finishes that single
path-only rewrite (same §8 Journals exception as the migration).
"""

from pathlib import Path

MD = Path(r"E:\Shosho LifeOS\Journals\Daily\2024-03-09.md")
OLD = "![[Pasted image 20240310161726.png]]"
NEW = "![[Attachments/journal-pasted/2024-03/Pasted image 20240310161726.png]]"

t = MD.read_text(encoding="utf-8")
n = t.count(OLD)
if n:
    MD.write_text(t.replace(OLD, NEW), encoding="utf-8")
print(f"rewrote {n} ref(s) for Pasted image 20240310161726.png in {MD.name}")
