# Error Recovery

Failures from `scripts/run_project_bootstrap.py` and how to handle them.

## Exit code 2 — ProjectExistsError

**Cause**: Either `Projects/<title>.md` or one of the target
`TaskNotes/Tasks/<title> - <task>.md` already exists. The script aborts before
writing anything.

**JSON output**:
```json
{"error": "ProjectExistsError", "detail": "Project already exists: Projects/X.md"}
```

**Response to user**: Do NOT auto-resolve. Ask:

```
已經有同名 project 或 task 了：<detail>

要改標題（例如加日期後綴 "超加工食品 2026"）還是先處理舊的？
```

Wait for the user's call. Never delete or overwrite.

## Exit code 1 — Unexpected error

Surfaced as a Python traceback. Usually config issues:

- `vault_path` in `config.yaml` missing or pointing to non-existent directory
  → Tell the user to verify `config.yaml`
- Permission denied writing to vault → filesystem / WSL bridge issue
- YAML encoding error → unlikely (tests cover Chinese + emoji); if it happens,
  file a bug

**Response to user**: Share the last ~20 lines of the traceback and wait. Do
NOT retry.

## Partial write (should never happen)

`create_project_with_tasks` does conflict checks up front, so partial writes
shouldn't occur. If the filesystem vanishes mid-write (e.g., WSL mount drop),
you may get a project file without all 3 tasks. Check manually:

```bash
ls "<vault>/Projects/<title>.md"
ls "<vault>/TaskNotes/Tasks/<title> - "*.md
```

If inconsistent, tell the user and let them clean up before retrying.

## Encoding issues on Windows

The script calls `sys.stdout.reconfigure(encoding="utf-8")` on startup. If the
user's terminal still mangles Chinese / emoji in the JSON output, they can
`--json-out` to write the payload to a file and parse that instead.

## Never do

- Auto-retry after any failure
- Delete or overwrite an existing project / task file
- Skip the confirm gate on re-run after adjusting the title
