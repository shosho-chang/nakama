"""畫面搬移：曲線（眼線不動、不露黑邊、同速退回）、攤到片段、寫進 Fusion 並讀回。不開 Resolve。"""

from __future__ import annotations

import json

import pytest

from agents.brook.youtube_video.visuals import reframe as rf

FPS = rf.FPS
ANCHOR = rf.Anchor(x=0.517, y=0.223)  # 2026-10-27 那集：臉中心 992px、眼線 241px


def _edges(z: float, tx: float, ty: float) -> tuple[float, float, float, float]:
    """原畫面四個邊在輸出上的位置（左、右、上、下）。"""
    return tx, z + tx, ty, z + ty


@pytest.mark.parametrize("kind", ["push", "left", "right"])
def test_eye_line_stays_and_no_black_edges(kind):
    move = rf.Move(kind, 1.5, 5.0)
    for n in rf.move_frames(move):
        z, tx, ty = rf.transform_at(move, ANCHOR, n / FPS)
        assert ANCHOR.y * z + ty == pytest.approx(ANCHOR.y)  # 眼線高度不變
        left, right, top, bottom = _edges(z, tx, ty)
        assert left <= 1e-9 and right >= 1 - 1e-9  # 左右不露黑邊
        assert top <= 1e-9 and bottom >= 1 - 1e-9  # 上下不露黑邊


@pytest.mark.parametrize(
    "kind, zoom, face_x",
    [("push", 1.3, ANCHOR.x), ("left", 1.4, 1 / 3), ("right", 1.4, 2 / 3)],
)
def test_holds_at_target(kind, zoom, face_x):
    move = rf.Move(kind, 1.5, 5.0)
    z, tx, _ = rf.transform_at(move, ANCHOR, 3.0)
    assert z == pytest.approx(zoom)
    assert ANCHOR.x * z + tx == pytest.approx(face_x)


def test_left_is_clamped_when_face_cannot_reach():
    # 臉偏右很多時，1.4 倍也到不了 1/3 線 → 停在不露黑邊的極限
    move = rf.Move("left", 0, 3)
    far = rf.Anchor(0.8, 0.25)
    z, tx, _ = rf.transform_at(move, far, 1.5)
    assert tx == pytest.approx(1 - 1.4)
    assert far.x * z + tx > 1 / 3


def test_timing_ramp_hold_and_return_at_same_speed():
    move = rf.Move("left", 1.5, 5.0)
    assert rf.progress(move, 1.5) == 0
    assert rf.progress(move, 1.75) == pytest.approx(0.5)
    assert rf.progress(move, 2.0) == 1
    assert move.return_at == pytest.approx(4.75)  # 書封開始退場
    assert rf.progress(move, 5.0) == pytest.approx(0.5)
    assert move.finish == pytest.approx(5.25)
    assert rf.progress(move, 5.25) == 0
    short = rf.Move("push", 0, 0.3)
    assert short.return_at == pytest.approx(0.5)  # 太短就推完馬上退


def test_fusion_values_identity_and_mapping():
    assert rf.fusion_values(1.0, 0.0, 0.0) == (1.0, 0.5, 0.5)
    size, cx, cy = rf.fusion_values(1.4, -0.2, -0.1)
    # Pivot 在中心：輸出 x＝(原 x−0.5)×Size＋Center x；y 往上
    assert (0.3 - 0.5) * size + cx == pytest.approx(0.3 * 1.4 - 0.2)
    y_down = 0.3
    assert 1 - ((1 - y_down - 0.5) * size + cy) == pytest.approx(y_down * 1.4 - 0.1)


def test_parse_move_and_overlap():
    assert rf.parse_move("left@12.0-15.5") == rf.Move("left", 12.0, 15.5)
    with pytest.raises(rf.ReframeError, match="看不懂"):
        rf.parse_move("left 12")
    with pytest.raises(rf.ReframeError, match="不認得"):
        rf.parse_move("zoom@1-2")
    with pytest.raises(rf.ReframeError, match="不能重疊"):
        rf.check_no_overlap([rf.Move("left", 1, 4), rf.Move("push", 4.1, 6)])


# ── 假的 Resolve／Fusion ──────────────────────────────────────────────


class FakeTool:
    def __init__(self, comp, reg_id, name):
        self.comp, self.reg_id, self.name = comp, reg_id, name
        self.inputs: dict[str, dict[int, object]] = {}
        self.links: dict[str, FakeTool] = {}

    def GetAttrs(self):
        return {"TOOLS_RegID": self.reg_id, "TOOLS_Name": self.name}

    def SetAttrs(self, attrs):
        self.name = attrs.get("TOOLS_Name", self.name)

    def ConnectInput(self, name, tool):
        self.links[name] = tool

    def AddModifier(self, name, kind):
        self.comp.tools.append(FakeTool(self.comp, kind, f"{self.name}{name}"))
        return True

    def SetInput(self, name, value, frame):
        self.inputs.setdefault(name, {})[frame] = value

    def GetInput(self, name, frame):
        value = self.inputs[name][frame]
        if self.comp.corrupt and name == "Size":
            return value + 0.1
        return value

    def Delete(self):
        self.comp.tools.remove(self)


class FakeComp:
    def __init__(self, extra=()):
        self.corrupt = False
        self.tools = [
            FakeTool(self, "MediaIn", "MediaIn1"),
            FakeTool(self, "MediaOut", "MediaOut1"),
        ]
        self.tools += [FakeTool(self, reg, name) for reg, name in extra]

    def GetToolList(self, _selected):
        return {i + 1: t for i, t in enumerate(self.tools)}

    def AddTool(self, reg_id, _x, _y):
        tool = FakeTool(self, reg_id, f"{reg_id}1")
        self.tools.append(tool)
        return tool

    def find(self, name):
        return next(t for t in self.tools if t.name == name)


class FakeItem:
    def __init__(self, name, start, end, comp=None):
        self.name, self.start, self.end = name, start, end
        self.comp = comp

    def GetName(self):
        return self.name

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetFusionCompCount(self):
        return 1 if self.comp else 0

    def GetFusionCompByIndex(self, _i):
        return self.comp

    def AddFusionComp(self):
        self.comp = FakeComp()
        return self.comp


class FakeTimeline:
    def __init__(self, items, start=108000):
        self.items, self.start = items, start

    def GetStartFrame(self):
        return self.start

    def GetItemListInTrack(self, _kind, _track):
        return self.items


def test_move_across_a_cut_is_split_and_continuous():
    # 剪點在 timeline 第 3 秒：搬移橫跨兩個片段
    cut = 108000 + 90
    a, b = FakeItem("A", 108000, cut), FakeItem("B", cut, 108000 + 600)
    tl = FakeTimeline([a, b])
    move = rf.Move("left", 2.0, 5.0)
    entries = rf.plan_keys(tl, [(move, ANCHOR)])
    keys_a, keys_b = entries[id(a)].keys, entries[id(b)].keys
    assert max(keys_a) == 89 and min(keys_b) == 0  # 片段內的格數各自從 0 起算
    total = len(keys_a) + len(keys_b)
    assert total == len(rf.move_frames(move))
    # 剪點兩側那兩格的值是同一條曲線上相鄰的兩格
    expected = rf.fusion_values(*rf.transform_at(move, ANCHOR, 90 / FPS))
    assert keys_b[0] == pytest.approx(expected)


def test_apply_writes_reframe_tool_and_reads_back():
    item = FakeItem("A", 108000, 108000 + 600)
    tl = FakeTimeline([item])
    moves = [(rf.Move("left", 1.5, 5.0), ANCHOR), (rf.Move("push", 10.0, 13.0), ANCHOR)]
    assert rf.apply_moves(tl, moves) == 1
    xf = item.comp.find("Reframe")
    assert xf.links["Input"].name == "MediaIn1"
    assert item.comp.find("MediaOut1").links["Input"] is xf
    assert {t.reg_id for t in item.comp.tools} >= {"BezierSpline", "XYPath"}
    hold = round(3.0 * FPS)
    assert xf.inputs["Size"][hold] == pytest.approx(1.4)
    assert xf.inputs["Size"][round(11.5 * FPS)] == pytest.approx(1.3)
    # 推近之前與退回之後都是原畫面
    first = min(xf.inputs["Size"])
    assert xf.inputs["Size"][first] == pytest.approx(1.0)
    assert xf.inputs["Center"][first] == {1: pytest.approx(0.5), 2: pytest.approx(0.5)}

    # 再跑一次：舊的 Reframe 先刪掉，不會疊兩個
    rf.apply_moves(tl, moves)
    assert [t.name for t in item.comp.tools].count("Reframe") == 1


def test_readback_mismatch_stops():
    item = FakeItem("A", 108000, 108000 + 300)
    item.AddFusionComp().corrupt = True
    with pytest.raises(rf.ReframeError, match="讀回不對"):
        rf.apply_moves(FakeTimeline([item]), [(rf.Move("push", 1, 3), ANCHOR)])


def test_refuses_clip_with_other_fusion_nodes():
    item = FakeItem("A", 108000, 108000 + 300, comp=FakeComp(extra=[("Transform", "PunchZoom")]))
    with pytest.raises(rf.ReframeError, match="別的 Fusion 節點"):
        rf.apply_moves(FakeTimeline([item]), [(rf.Move("push", 1, 3), ANCHOR)])


def test_moves_from_book_cover_receipts(tmp_path):
    visuals = tmp_path / "visuals"
    visuals.mkdir()
    for name, category, start, dur in [
        ("bk", "book_cover", 30.0, 3.8),
        ("ct", "concept_title", 50.0, 2.0),
    ]:
        receipt = {"category": category, "start_sec": start, "duration_sec": dur}
        (visuals / f"{name}.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert rf.moves_for_receipts(tmp_path) == [rf.Move("left", 30.0, 33.8)]
