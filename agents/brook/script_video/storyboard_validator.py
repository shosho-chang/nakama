"""Storyboard guardrails validator — hard limits 的 code 強制（ADR-051 panel v2 §11）.

guardrails.yaml 的 hard limits 此前只是 prompt 約定：planner / Director 產出
違規 storyboard 不會被任何程式擋下。本模組把它變成可執行的驗證 —
`validate-storyboard` CLI 在送 Bridge 審核前跑，錯誤即 exit 1。

規則來源：guardrails.yaml（機器可讀）＋ ADR-051 D6 KOL 護欄。
narrative 版見 STYLE.md 與 .claude/skills/brook-director/SKILL.md。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from agents.brook.script_video.schemas.storyboard import Beat

DEFAULT_GUARDRAILS_PATH = Path(__file__).parent / "guardrails.yaml"


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str  # "error" | "warning"
    beat_id: int | None
    message: str


def load_guardrails(path: Path | None = None) -> dict:
    p = path or DEFAULT_GUARDRAILS_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _beat_duration(beat: Beat) -> float | None:
    return beat.timing.duration if beat.timing is not None else None


def _component_key(beat: Beat) -> str | None:
    """視覺重複判定用的 component 識別：hyperframes 用 component 名，asset 用素材來源.

    stock/kol 以 source_url 為識別 — 相鄰兩個「不同 footage」是合法的快切
    （修修 2026-07-17：hook 段常見連續 stock），同一支 footage 相鄰才算視覺重複。
    無 source_url 時退回素材種類。
    """
    if beat.broll is None:
        return None
    if beat.broll.render_target == "asset" and beat.broll.asset is not None:
        spec = beat.broll.asset
        if spec.kind in ("stock", "kol") and spec.source_url:
            return f"asset:{spec.kind}:{spec.source_url}"
        return f"asset:{spec.kind}"
    return f"{beat.broll.render_target}:{beat.broll.component}"


def validate_storyboard(
    raw_beats: list[dict],
    guardrails: dict,
    *,
    duration_sec: float | None = None,
) -> list[Violation]:
    """回傳全部違規（error + warning），呼叫端決定 exit code.

    duration_sec: 全集長度（算 cutaway 密度用）。None 時退回 beats timing 的
    最大 end；連 timing 都沒有就跳過密度檢查並記 warning（不靜默）。
    """
    violations: list[Violation] = []
    limits = guardrails.get("hard_limits", {})
    allowed_layouts = set(guardrails.get("allowed_layouts", []))
    allowed_targets = set(guardrails.get("allowed_render_targets", []))
    allowed_components = set(guardrails.get("allowed_components", {}).get("hyperframes", []))
    allowed_asset_kinds = set(guardrails.get("allowed_asset_kinds", []))

    # 1. schema：逐 beat 過 pydantic（asset ⟺ render_target 等 model 級約束）
    beats: list[Beat] = []
    for raw in raw_beats:
        try:
            beats.append(Beat(**raw))
        except ValidationError as exc:
            violations.append(
                Violation(
                    rule="schema",
                    severity="error",
                    beat_id=raw.get("beat_id"),
                    message=f"pydantic 驗證失敗：{exc.errors()[0].get('msg', exc)}",
                )
            )
    if not beats:
        return violations

    # 2. 詞彙：layout / render_target / component / asset kind 都要在 allow list
    for b in beats:
        if allowed_layouts and b.layout not in allowed_layouts:
            violations.append(
                Violation(
                    rule="layout",
                    severity="error",
                    beat_id=b.beat_id,
                    message=f"layout '{b.layout}' 不在 allowed_layouts {sorted(allowed_layouts)}",
                )
            )
        if b.broll is None:
            continue
        if allowed_targets and b.broll.render_target not in allowed_targets:
            violations.append(
                Violation(
                    rule="render_target",
                    severity="error",
                    beat_id=b.beat_id,
                    message=f"render_target '{b.broll.render_target}' 不在 allowed_render_targets",
                )
            )
        if b.broll.render_target == "hyperframes":
            if allowed_components and b.broll.component not in allowed_components:
                violations.append(
                    Violation(
                        rule="component",
                        severity="error",
                        beat_id=b.beat_id,
                        message=(
                            f"component '{b.broll.component}' 不在 "
                            f"allowed_components.hyperframes {sorted(allowed_components)}"
                        ),
                    )
                )
        elif b.broll.render_target == "asset" and b.broll.asset is not None:
            if allowed_asset_kinds and b.broll.asset.kind not in allowed_asset_kinds:
                violations.append(
                    Violation(
                        rule="asset_kind",
                        severity="error",
                        beat_id=b.beat_id,
                        message=f"asset.kind '{b.broll.asset.kind}' 不在 allowed_asset_kinds",
                    )
                )

    # 3. cutaway 密度（對齊 planner prompt 預算，panel v2 §8）
    cutaways = [b for b in beats if b.broll_decision == "cutaway"]
    max_per_min = limits.get("max_cutaways_per_minute")
    if max_per_min is not None:
        effective_duration = duration_sec
        if effective_duration is None:
            ends = [b.timing.start + b.timing.duration for b in beats if b.timing is not None]
            effective_duration = max(ends) if ends else None
        if effective_duration is None or effective_duration <= 0:
            violations.append(
                Violation(
                    rule="cutaway_rate",
                    severity="warning",
                    beat_id=None,
                    message="無法取得集長（無 duration_sec、beats 無 timing）— 密度檢查跳過",
                )
            )
        else:
            rate = len(cutaways) / (effective_duration / 60.0)
            if rate > max_per_min:
                violations.append(
                    Violation(
                        rule="cutaway_rate",
                        severity="error",
                        beat_id=None,
                        message=(
                            f"cutaway 密度 {rate:.2f}/min 超過上限 {max_per_min}/min"
                            f"（{len(cutaways)} cutaways / {effective_duration:.0f}s）"
                        ),
                    )
                )

    # 4. 連續兩 beat 同 component（STYLE.md hard rule：視覺重複）
    if limits.get("no_consecutive_same_component"):
        for prev, cur in zip(beats, beats[1:], strict=False):
            if prev.broll_decision != "cutaway" or cur.broll_decision != "cutaway":
                continue
            key_prev, key_cur = _component_key(prev), _component_key(cur)
            if key_prev is not None and key_prev == key_cur:
                violations.append(
                    Violation(
                        rule="consecutive_component",
                        severity="error",
                        beat_id=cur.beat_id,
                        message=f"與前一 beat {prev.beat_id} 連續使用同 component '{key_cur}'",
                    )
                )

    # 5. 連續 none 太長（guardrails 註解語意：警告太靜，不是 error）
    max_none = limits.get("max_consecutive_no_decision")
    if max_none:
        streak = 0
        for b in beats:
            streak = streak + 1 if b.broll_decision == "none" else 0
            if streak == max_none + 1:
                violations.append(
                    Violation(
                        rule="consecutive_none",
                        severity="warning",
                        beat_id=b.beat_id,
                        message=f"連續 {streak} 個 beat 無 B-roll（> {max_none}）— 重看是否太靜",
                    )
                )

    # 6. 出處欄位（ADR-051 D6：asset beat 強制留痕）
    for b in beats:
        if b.broll is None or b.broll.asset is None:
            continue
        spec = b.broll.asset
        if spec.kind == "stock" and not spec.source_url:
            violations.append(
                Violation(
                    rule="asset_provenance",
                    severity="error",
                    beat_id=b.beat_id,
                    message="stock beat 缺 asset.source_url",
                )
            )
        if spec.kind == "kol":
            missing = [
                f for f in ("source_url", "source_span", "attribution") if not getattr(spec, f)
            ]
            if missing:
                violations.append(
                    Violation(
                        rule="asset_provenance",
                        severity="error",
                        beat_id=b.beat_id,
                        message=f"kol beat 缺 asset.{'/'.join(missing)}（D6 出處護欄）",
                    )
                )

    # 7. KOL 單一來源取用總長 ≤ 上限（D6）
    kol_cap = limits.get("kol_max_total_sec_per_source")
    if kol_cap is not None:
        totals: dict[str, float] = {}
        unknown: dict[str, list[int]] = {}
        for b in beats:
            if b.broll is None or b.broll.asset is None or b.broll.asset.kind != "kol":
                continue
            src = b.broll.asset.source_url or f"<missing:{b.beat_id}>"
            dur = _beat_duration(b)
            if dur is None:
                unknown.setdefault(src, []).append(b.beat_id)
            else:
                totals[src] = totals.get(src, 0.0) + dur
        for src, total in totals.items():
            if total > kol_cap:
                violations.append(
                    Violation(
                        rule="kol_source_cap",
                        severity="error",
                        beat_id=None,
                        message=f"KOL 來源 {src} 取用總長 {total:.1f}s 超過 {kol_cap}s 上限",
                    )
                )
        for src, ids in unknown.items():
            violations.append(
                Violation(
                    rule="kol_source_cap",
                    severity="warning",
                    beat_id=ids[0],
                    message=f"KOL beats {ids}（來源 {src}）無 timing — 總長檢查不完整",
                )
            )

    return violations


def format_report(violations: list[Violation]) -> str:
    """人讀報告：無違規也要明說（窮盡一切 — 不靜默）."""
    if not violations:
        return "validate-storyboard: 0 errors, 0 warnings — 全部規則已檢查，無違規"
    lines = []
    errors = [v for v in violations if v.severity == "error"]
    warnings = [v for v in violations if v.severity == "warning"]
    for v in violations:
        where = f"beat {v.beat_id}" if v.beat_id is not None else "storyboard"
        lines.append(f"[{v.severity.upper():7s}] {v.rule:22s} {where}: {v.message}")
    lines.append(f"validate-storyboard: {len(errors)} errors, {len(warnings)} warnings")
    return "\n".join(lines)
