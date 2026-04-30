# LifeOS Project File Format & Fuzzy Match

The `--project-file` arg primes the pipeline with guest names, domain terms,
and topic context, improving correction accuracy. This reference explains
where to find Project files and how to match them against audio filenames.

## Directory Location

Read from env `LIFEOS_PROJECTS_DIR`. If unset:
- Windows default: `E:/Shosho LifeOS/Projects/`
- Open-source deployments should set this env var explicitly.

If the directory doesn't exist, skip Step 3 and ask the user to provide
`--project-file <path>` manually or proceed without one.

## File Structure (what the pipeline reads)

Frontmatter fields (YAML):
```yaml
---
type: project
content_type: youtube | podcast | article
search_topic: "主題關鍵字"         # ← pipeline reads this as topic
guest: "來賓姓名"                   # ← pipeline reads this as guest_name
created: "YYYY-MM-DD"
tags: [project, ...]
---
```

Body sections the pipeline extracts (skip code blocks):
- `## Research Dropbox`
- `## Script`
- `## Keywords Research`
- `## Description` / `## Description / Show Notes`
- `## 專案筆記`

Terms in 書名號 `《》` or 引號 `「」` become hotwords.

## Fuzzy Match Algorithm

Goal: given an audio path like `F:/Audio/Angie-E42.wav`, suggest the
best-matching Project file.

### Step A: Tokenize audio filename

```python
stem = Path(audio_path).stem           # "Angie-E42"
tokens = re.findall(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+", stem.lower())
# → ["angie", "e42"] — split on punctuation, keep Chinese segments whole
```

### Step B: For each Project file, compute score

```python
for project_file in Glob("{projects_dir}/*.md"):
    # Parse frontmatter
    fm = parse_frontmatter(project_file)
    guest = fm.get("guest", "").lower()
    topic = fm.get("search_topic", "").lower()
    stem = project_file.stem.lower()

    filename_overlap = sum(1 for t in tokens if t in stem) / len(tokens)
    guest_match = 1.0 if guest and any(t in guest for t in tokens) else 0
    topic_match = 0.5 if topic and any(t in topic for t in tokens) else 0

    score = 0.6 * filename_overlap + 0.3 * guest_match + 0.1 * topic_match

    # Recency bonus: files modified in last 7 days get +0.1
    if (now - mtime).days < 7:
        score += 0.1
```

### Step C: Rank and present

- Score > 0.9: high-confidence match. In fast mode, use directly.
  In normal mode, show for confirmation.
- Score 0.5-0.9: ambiguous. Always show selection UI.
- Score < 0.5: no usable match. Offer "(不帶 Project)" option and
  suggest manual path.

Always include `(不帶 Project)` as last option in the list.

## Selection UI (multi-candidate)

```
匹配到的 LifeOS Project：
  [x] Angie-E42.md       (score 0.95, modified 2h ago)  ← default highlight
  [ ] Angie.md           (score 0.68, generic)
  [ ] （不帶 Project，ASR only）
哪個要用？
```

Accept user picks by:
- Number: "1" / "2" / "3"
- Filename fragment: "E42" / "generic" / "不帶"
- "全部不用" / "none" / "skip" → pass None

## Why This Matters

Without a Project file, domain terms like `Omega-3`, guest nicknames, and
specialized jargon get mis-transcribed. With a well-populated Project file,
correction accuracy jumps substantially because:
- Hotwords bias the FunASR decoder
- Context text anchors Opus to the correct domain
- Topic helps Gemini disambiguate audio vs text

Encourage the user to maintain `guest:` and `search_topic:` frontmatter
in their Projects. The more specific, the better.

## Open-Source Note

For users without a LifeOS setup, Step 3 degrades gracefully:
- If `LIFEOS_PROJECTS_DIR` is unset or missing → skip auto-match, offer
  "`--project-file <path>` 手指定" prompt or "不帶 Project" option.
- The pipeline works fine without a Project file, just with reduced
  correction accuracy on domain terms.
