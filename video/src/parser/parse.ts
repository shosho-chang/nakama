/**
 * DSL parser — converts script.md into a Manifest JSON structure.
 *
 * Slice 1 supports only the [aroll-full] directive.
 * Other directives throw ParseError with a "Slice N" message so callers
 * get a clear error rather than silent data loss.
 *
 * DSL format:
 *   [aroll-full]
 *   Narration text for this scene.
 *
 *   [aroll-pip source="slide.png" position=top-right]
 *   Narration text with slide overlay.
 */

import type {
  ARollFullScene,
  CutPoint,
  Manifest,
  Scene,
} from "./types.js";
import { validate } from "./validate.js";

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class ParseError extends Error {
  constructor(message: string, public readonly line?: number) {
    super(message);
    this.name = "ParseError";
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DirectiveBlock = {
  directive: string;
  attrs: Record<string, string>;
  body: string;
  lineNumber: number;
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Parse a script.md string into a Manifest.
 *
 * @param scriptText - Full contents of script.md
 * @param episodeId  - Episode identifier used in the manifest
 * @param cuts       - CutPoints produced by mistake_removal (Python side)
 */
export function parseScript(
  scriptText: string,
  episodeId: string,
  cuts: CutPoint[] = [],
): Manifest {
  const blocks = extractBlocks(scriptText);

  if (blocks.length === 0) {
    throw new ParseError("No DSL directives found in script. Add at least one [aroll-full] block.");
  }

  const scenes: Scene[] = [];
  let frameCounter = 0;

  for (const block of blocks) {
    const scene = buildScene(block, frameCounter);
    scenes.push(scene);
    frameCounter += scene.duration_frames;
  }

  const manifest: Manifest = {
    episode_id: episodeId,
    fps: 30,
    total_frames: frameCounter,
    scenes,
    // aroll_audio and aroll_video are filled in by the Python pipeline
    aroll_audio: "",
    aroll_video: "",
    cuts,
  };

  validate(manifest);
  return manifest;
}

// ---------------------------------------------------------------------------
// Block extraction
// ---------------------------------------------------------------------------

const DIRECTIVE_RE = /^\[([a-z][a-z0-9-]*)((?:\s+[a-zA-Z_-]+=(?:"[^"]*"|[^\s\]]+))*)\]$/;
const ATTR_RE = /([a-zA-Z_-]+)=(?:"([^"]*)"|([^\s\]]+))/g;

export function extractBlocks(scriptText: string): DirectiveBlock[] {
  const lines = scriptText.split("\n");
  const blocks: DirectiveBlock[] = [];
  let currentBlock: DirectiveBlock | null = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const trimmed = line.trim();

    const match = DIRECTIVE_RE.exec(trimmed);
    if (match !== null) {
      // Save previous block
      if (currentBlock !== null) {
        currentBlock.body = currentBlock.body.trim();
        blocks.push(currentBlock);
      }
      const directive = match[1] ?? "";
      const attrStr = match[2] ?? "";
      const attrs: Record<string, string> = {};
      let m: RegExpExecArray | null;
      while ((m = ATTR_RE.exec(attrStr)) !== null) {
        attrs[m[1] ?? ""] = m[2] ?? m[3] ?? "";
      }
      currentBlock = { directive, attrs, body: "", lineNumber: i + 1 };
    } else if (currentBlock !== null) {
      currentBlock.body += line + "\n";
    }
  }

  if (currentBlock !== null) {
    currentBlock.body = currentBlock.body.trim();
    blocks.push(currentBlock);
  }

  return blocks;
}

// ---------------------------------------------------------------------------
// Scene builder (dispatch by directive type)
// ---------------------------------------------------------------------------

const PLACEHOLDER_FRAMES_PER_WORD = 5; // rough estimate; Stage 1 ASR refines this

function buildScene(block: DirectiveBlock, startFrame: number): Scene {
  switch (block.directive) {
    case "aroll-full":
      return buildARollFull(block, startFrame);

    case "aroll-pip":
      throw new ParseError(
        `[aroll-pip] is not yet implemented — will be added in Slice 2`,
        block.lineNumber,
      );
    case "transition":
      throw new ParseError(
        `[transition] is not yet implemented — will be added in Slice 2`,
        block.lineNumber,
      );
    case "quote":
      throw new ParseError(
        `[quote] is not yet implemented — will be added in Slice 3`,
        block.lineNumber,
      );
    case "big-stat":
      throw new ParseError(
        `[big-stat] is not yet implemented — will be added in Slice 2`,
        block.lineNumber,
      );
    default:
      throw new ParseError(
        `Unknown directive [${block.directive}] at line ${block.lineNumber}`,
        block.lineNumber,
      );
  }
}

function buildARollFull(block: DirectiveBlock, startFrame: number): ARollFullScene {
  const wordCount = block.body.split(/\s+/).filter((w) => w.length > 0).length;
  const durationFrames = Math.max(30, wordCount * PLACEHOLDER_FRAMES_PER_WORD);

  return {
    type: "aroll-full",
    id: `scene-${String(startFrame).padStart(6, "0")}`,
    start_frame: startFrame,
    duration_frames: durationFrames,
    aroll_start_sec: 0.0, // refined by WhisperX in Slice 2
  };
}

// ---------------------------------------------------------------------------
// CLI entry — invoked by the Python pipeline's Stage 2 subprocess
//
//   node parse.js --script <path-to-script.md> --out <path-to-manifest.json>
//
// Episode ID is derived from the parent directory of --out
// (data/script_video/<episode_id>/manifest.json), matching the convention
// in agents/brook/script_video/pipeline.py.
// ---------------------------------------------------------------------------

import { readFileSync, writeFileSync } from "fs";
import { basename, dirname } from "path";
import { fileURLToPath } from "url";

function runCli(argv: string[]): void {
  const scriptIdx = argv.indexOf("--script");
  const outIdx = argv.indexOf("--out");
  const scriptPath = argv[scriptIdx + 1];
  const outPath = argv[outIdx + 1];
  if (scriptIdx === -1 || outIdx === -1 || scriptPath === undefined || outPath === undefined) {
    process.stderr.write(
      "Usage: node parse.js --script <script.md> --out <manifest.json>\n",
    );
    process.exit(2);
  }

  const episodeId = basename(dirname(outPath));
  const scriptText = readFileSync(scriptPath, "utf-8");

  try {
    const manifest = parseScript(scriptText, episodeId);
    writeFileSync(outPath, JSON.stringify(manifest, null, 2));
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(1);
  }
}

// Run only when invoked directly (not when imported by tests / other modules).
const entry = process.argv[1];
if (entry !== undefined && entry === fileURLToPath(import.meta.url)) {
  runCli(process.argv.slice(2));
}
