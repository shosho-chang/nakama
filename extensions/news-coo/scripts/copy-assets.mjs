import { copyFile, mkdir, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
await mkdir(dist, { recursive: true });

await copyFile(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));

const publicDir = resolve(root, "public");
for (const file of await readdir(publicDir)) {
  await copyFile(join(publicDir, file), join(dist, file));
}

console.log("[copy-assets] manifest + public/* copied to dist/");
