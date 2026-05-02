/**
 * Manifest schema — shared contract between the TypeScript DSL parser and
 * the Python FCPXML/SRT emitters.  Changes here must be mirrored in
 * agents/brook/script_video/manifest.py.
 */

// ---------------------------------------------------------------------------
// CutPoint
// ---------------------------------------------------------------------------

export type CutPoint = {
  type: "razor" | "ripple-delete";
  /** Start of the region to delete, in seconds relative to source recording. */
  start_sec: number;
  /** End of the region to delete, in seconds relative to source recording. */
  end_sec: number;
  reason: "marker" | "alignment-detected";
  confidence: number; // 0..1
};

// ---------------------------------------------------------------------------
// Scene types
// ---------------------------------------------------------------------------

export type SceneBase = {
  id: string;
  /** Frame index in the final (post-cut) timeline. */
  start_frame: number;
  duration_frames: number;
};

export type ARollFullScene = SceneBase & {
  type: "aroll-full";
  /** In-point into aroll-audio.mp3/mp4 (seconds). */
  aroll_start_sec: number;
};

export type ARollPipScene = SceneBase & {
  type: "aroll-pip";
  aroll_start_sec: number;
  /**
   * Slide structure stays an open dict in Slice 1 (no spec yet).
   * Slice 2 #314 will introduce a Slide type when Remotion ARollPip lands.
   */
  slide?: Record<string, unknown> | null;
  pip_position: "top-left" | "top-right" | "bottom-left" | "bottom-right";
};

export type TransitionTitleScene = SceneBase & {
  type: "transition";
  title: string;
  subtitle?: string;
};

export type BBox = {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
};

export type Citation = {
  title: string;
  page: number;
  author?: string;
};

export type DocumentQuoteScene = SceneBase & {
  type: "document-quote";
  page_image_path: string;
  image_width: number;
  image_height: number;
  highlights: BBox[];
  variant: "highlighter-sweep" | "ken-burns" | "spotlight";
  citation: Citation;
  /**
   * Robin KB join key — populated by Slice 4's robin_metadata adapter
   * (ADR-015 §Q4-2). Slice 3 may emit a synthetic source_id when KB lookup misses.
   */
  source_id: string;
  /**
   * Markdown override for fuzzy matches (ADR-015 §Q4-3); null/undefined means top-1.
   * Slice 4 fuzzy match honours this; Slice 3 exact-match path leaves it null.
   */
  match_index?: number | null;
};

export type QuoteCardScene = SceneBase & {
  type: "quote-card";
  quote_text: string;
  attribution?: string;
};

export type BigStatScene = SceneBase & {
  type: "big-stat";
  number: string;
  unit: string;
  description?: string;
};

export type Scene =
  | ARollFullScene
  | ARollPipScene
  | TransitionTitleScene
  | DocumentQuoteScene
  | QuoteCardScene
  | BigStatScene;

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

export type Manifest = {
  episode_id: string;
  fps: 30;
  total_frames: number;
  scenes: Scene[];
  /** Absolute path to aroll-audio.mp3 */
  aroll_audio: string;
  /** Absolute path to aroll-video.mp4 */
  aroll_video: string;
  cuts: CutPoint[];
};
