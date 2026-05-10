import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.ts", "tests/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      include: ["src/**/*.ts"],
      exclude: [
        "src/**/*.test.ts",
        "src/shared/types.ts",
        "src/background/serviceWorker.ts",
        "src/content/contentScript.ts",
        "src/popup/popup.ts",
        "src/options/options.ts"
      ]
    }
  }
});
