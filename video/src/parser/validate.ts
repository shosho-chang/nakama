/**
 * Manifest validation — schema and timeline invariants.
 *
 * Validates:
 * - episode_id is non-empty
 * - fps is 30
 * - total_frames >= 0
 * - scenes are non-overlapping and ordered
 * - scene start_frame + duration_frames <= total_frames
 * - cuts have start_sec < end_sec
 */

import type { CutPoint, Manifest, Scene } from "./types.js";

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

export function validate(manifest: Manifest): void {
  validateManifestFields(manifest);
  validateSceneTimeline(manifest.scenes, manifest.total_frames);
  validateCuts(manifest.cuts);
}

// ---------------------------------------------------------------------------
// Field-level checks
// ---------------------------------------------------------------------------

function validateManifestFields(m: Manifest): void {
  if (!m.episode_id || m.episode_id.trim().length === 0) {
    throw new ValidationError("episode_id must be non-empty");
  }
  if (m.fps !== 30) {
    throw new ValidationError(`fps must be 30, got ${String(m.fps)}`);
  }
  if (m.total_frames < 0) {
    throw new ValidationError(`total_frames must be >= 0, got ${String(m.total_frames)}`);
  }
}

// ---------------------------------------------------------------------------
// Scene timeline checks
// ---------------------------------------------------------------------------

function validateSceneTimeline(scenes: Scene[], totalFrames: number): void {
  let expectedStart = 0;

  for (const scene of scenes) {
    if (scene.start_frame !== expectedStart) {
      throw new ValidationError(
        `Scene '${scene.id}': start_frame=${String(scene.start_frame)} but expected ${String(expectedStart)} ` +
          `(scenes must be contiguous and non-overlapping)`,
      );
    }
    if (scene.duration_frames <= 0) {
      throw new ValidationError(
        `Scene '${scene.id}': duration_frames must be > 0, got ${String(scene.duration_frames)}`,
      );
    }
    expectedStart += scene.duration_frames;
  }

  if (scenes.length > 0 && expectedStart !== totalFrames) {
    throw new ValidationError(
      `total_frames=${String(totalFrames)} but scenes sum to ${String(expectedStart)}`,
    );
  }
}

// ---------------------------------------------------------------------------
// CutPoint checks
// ---------------------------------------------------------------------------

function validateCuts(cuts: CutPoint[]): void {
  for (let i = 0; i < cuts.length; i++) {
    const cut = cuts[i];
    if (cut === undefined) continue;
    if (cut.start_sec >= cut.end_sec) {
      throw new ValidationError(
        `Cut[${String(i)}]: start_sec (${String(cut.start_sec)}) must be < end_sec (${String(cut.end_sec)})`,
      );
    }
    if (cut.confidence < 0 || cut.confidence > 1) {
      throw new ValidationError(
        `Cut[${String(i)}]: confidence must be 0..1, got ${String(cut.confidence)}`,
      );
    }
  }
}
