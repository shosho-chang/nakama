import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve, join } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
await mkdir(dist, { recursive: true });

await copyFile(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));

async function copyTree(srcDir, destDir) {
  await mkdir(destDir, { recursive: true });
  for (const name of await readdir(srcDir)) {
    const src = join(srcDir, name);
    const dest = join(destDir, name);
    const s = await stat(src);
    if (s.isDirectory()) await copyTree(src, dest);
    else await copyFile(src, dest);
  }
}

await copyTree(resolve(root, "public"), dist);

console.log("[copy-assets] manifest + public/** copied to dist/");
