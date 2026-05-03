/**
 * Parser unit tests — vitest
 *
 * Tests parseScript() and extractBlocks() with [aroll-full] directive.
 * Other directives should throw ParseError in Slice 1.
 */

import { describe, expect, it } from "vitest";
import { ParseError, extractBlocks, parseScript } from "../src/parser/parse.js";
import { ValidationError } from "../src/parser/validate.js";

// ---------------------------------------------------------------------------
// extractBlocks
// ---------------------------------------------------------------------------

describe("extractBlocks", () => {
  it("returns empty array for empty input", () => {
    expect(extractBlocks("")).toEqual([]);
  });

  it("extracts a single aroll-full block", () => {
    const script = `[aroll-full]\nHello world\n`;
    const blocks = extractBlocks(script);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.directive).toBe("aroll-full");
    expect(blocks[0]?.body).toBe("Hello world");
  });

  it("trims whitespace from body", () => {
    const script = `[aroll-full]\n\n  Some text  \n\n`;
    const blocks = extractBlocks(script);
    expect(blocks[0]?.body).toBe("Some text");
  });

  it("extracts multiple blocks in order", () => {
    const script = [
      "[aroll-full]",
      "First narration.",
      "",
      "[aroll-full]",
      "Second narration.",
    ].join("\n");
    const blocks = extractBlocks(script);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]?.directive).toBe("aroll-full");
    expect(blocks[1]?.directive).toBe("aroll-full");
  });

  it("parses directive attributes", () => {
    const script = `[aroll-pip source="slide.png" position=top-right]\nSome text\n`;
    const blocks = extractBlocks(script);
    expect(blocks[0]?.directive).toBe("aroll-pip");
    expect(blocks[0]?.attrs?.["source"]).toBe("slide.png");
    expect(blocks[0]?.attrs?.["position"]).toBe("top-right");
  });

  it("records line number of directive", () => {
    const script = `# Intro\n\n[aroll-full]\nHello\n`;
    const blocks = extractBlocks(script);
    expect(blocks[0]?.lineNumber).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// parseScript — happy path
// ---------------------------------------------------------------------------

describe("parseScript — aroll-full", () => {
  const simpleScript = `[aroll-full]\n今天我們來聊睡眠對健康的重要性。研究顯示充足睡眠能提升記憶力。\n`;

  it("returns a valid Manifest", () => {
    const manifest = parseScript(simpleScript, "ep001");
    expect(manifest.episode_id).toBe("ep001");
    expect(manifest.fps).toBe(30);
    expect(manifest.scenes).toHaveLength(1);
  });

  it("scene type is aroll-full", () => {
    const manifest = parseScript(simpleScript, "ep001");
    expect(manifest.scenes[0]?.type).toBe("aroll-full");
  });

  it("scene starts at frame 0", () => {
    const manifest = parseScript(simpleScript, "ep001");
    expect(manifest.scenes[0]?.start_frame).toBe(0);
  });

  it("total_frames equals sum of scene durations", () => {
    const manifest = parseScript(simpleScript, "ep001");
    const sumDurations = manifest.scenes.reduce(
      (acc, s) => acc + s.duration_frames,
      0,
    );
    expect(manifest.total_frames).toBe(sumDurations);
  });

  it("propagates cuts into manifest", () => {
    const cuts = [
      { type: "ripple-delete" as const, start_sec: 5.0, end_sec: 10.0,
        reason: "marker" as const, confidence: 0.9 },
    ];
    const manifest = parseScript(simpleScript, "ep001", cuts);
    expect(manifest.cuts).toHaveLength(1);
    expect(manifest.cuts[0]?.start_sec).toBe(5.0);
  });

  it("multiple aroll-full blocks are contiguous", () => {
    const script = [
      "[aroll-full]",
      "First part of narration.",
      "",
      "[aroll-full]",
      "Second part of narration.",
    ].join("\n");
    const manifest = parseScript(script, "ep-multi");
    expect(manifest.scenes).toHaveLength(2);
    const s0 = manifest.scenes[0]!;
    const s1 = manifest.scenes[1]!;
    expect(s1.start_frame).toBe(s0.start_frame + s0.duration_frames);
  });
});

// ---------------------------------------------------------------------------
// parseScript — error cases
// ---------------------------------------------------------------------------

describe("parseScript — errors", () => {
  it("throws ParseError for empty script", () => {
    expect(() => parseScript("", "ep001")).toThrow(ParseError);
  });

  it("throws ParseError for [aroll-pip] (Slice 2)", () => {
    const script = `[aroll-pip source="slide.png" position=top-right]\nSome text\n`;
    expect(() => parseScript(script, "ep001")).toThrow(ParseError);
  });

  it("throws ParseError for [transition] (Slice 2)", () => {
    const script = `[transition]\nChapter title\n`;
    expect(() => parseScript(script, "ep001")).toThrow(ParseError);
  });

  it("throws ParseError for [quote] (Slice 3)", () => {
    const script = `[quote source="book" page=12]\nSome quote\n`;
    expect(() => parseScript(script, "ep001")).toThrow(ParseError);
  });

  it("throws ParseError for unknown directive", () => {
    const script = `[unknown-directive]\nContent\n`;
    expect(() => parseScript(script, "ep001")).toThrow(ParseError);
  });

  it("ParseError includes directive name", () => {
    const script = `[big-stat]\n42 | users\n`;
    try {
      parseScript(script, "ep001");
      expect.fail("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ParseError);
      expect((e as ParseError).message).toContain("big-stat");
    }
  });
});

// ---------------------------------------------------------------------------
// validate — ValidationError propagation
// ---------------------------------------------------------------------------

describe("validate integration", () => {
  it("throws ValidationError for empty episode_id", () => {
    const script = `[aroll-full]\nContent here\n`;
    expect(() => parseScript(script, "")).toThrow(ValidationError);
  });
});
