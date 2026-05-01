import { run, claudeCode } from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";

// Nakama sandcastle entry — pick an open issue, TDD-implement, commit; sandcastle merges back to host branch.
//
// Run from sandcastle-test/ (sibling to nakama/):
//   cd ~/Documents/sandcastle-test
//   npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
//
// Pre-req: docker build -t sandcastle:nakama -f .sandcastle/Dockerfile .sandcastle  (one-time, ~5 min)

await run({
  name: "nakama",

  // Target repo — sandcastle anchors `.sandcastle/` artifacts (worktrees, logs, env, patches)
  // and git operations here. Relative paths resolve against process.cwd().
  // Layout: sandcastle-test/ (where this runs) is sibling to nakama/ (target).
  cwd: "../nakama",

  sandbox: docker({
    // Custom-built image with node + python3 + gh + Claude Code CLI.
    imageName: "sandcastle:nakama",

    // Explicit env passing — sandcastle's default .env lookup is target-repo-relative,
    // which fails when .env lives in sibling sandcastle-test/ rather than nakama/.
    // (See reference_sandcastle.md round-1 trial pitfall #2.)
    env: {
      ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY!,
      GH_TOKEN: process.env.GH_TOKEN!,
    },
  }),

  // Round 2 (#288/#289) confirmed sonnet-4-6 sufficient for protocol-compliant code.
  // Switch to claude-opus-4-7 for harder problems (rare for sandcastle-eligible issues).
  agent: claudeCode("claude-sonnet-4-6"),

  promptFile: "./.sandcastle/prompt.md",

  // Round 2 raised 1 → 5. Each iteration tackles one issue; agent emits
  // <promise>COMPLETE</promise> when no actionable issue remains.
  maxIterations: 5,

  // Each iteration gets a fresh worktree on a temporary branch.
  // On run completion, sandcastle merges all commits back to host HEAD.
  branchStrategy: { type: "merge-to-head" },

  // Nakama is Python — skip the npm node_modules copy default.
  copyToWorktree: [],

  hooks: {
    sandbox: {
      // Install Python deps fresh per iteration.
      // requirements.txt drives runtime + test deps; pyproject.toml is canonical for CI but
      // requirements.txt is faster for sandcastle's per-iter setup.
      //
      // Sandcastle's default hook timeout is 60s — too short for 60+ pip deps from a
      // cold container. Bump to 10 min for resolve + download.
      // (Future opt: pre-install deps in Dockerfile build for cache hit; revisit if
      // per-iteration pip install dominates wall-clock cost.)
      onSandboxReady: [
        { command: "pip install -r requirements.txt -q", timeoutMs: 600000 },
      ],
    },
  },
});
