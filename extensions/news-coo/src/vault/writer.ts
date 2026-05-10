// FSA vault writer — writes Inbox/kb/{slug}.md (PRD §5.1).

export interface WriteResult {
  slug: string;
  path: string;
}

async function resolveDir(
  root: FileSystemDirectoryHandle,
  parts: string[],
): Promise<FileSystemDirectoryHandle> {
  let dir = root;
  for (const part of parts) {
    dir = await dir.getDirectoryHandle(part, { create: true });
  }
  return dir;
}

async function fileExists(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<boolean> {
  try {
    await dir.getFileHandle(name);
    return true;
  } catch {
    return false;
  }
}

export async function writeToVault(
  root: FileSystemDirectoryHandle,
  slug: string,
  content: string,
): Promise<WriteResult> {
  const inboxDir = await resolveDir(root, ["Inbox", "kb"]);

  // Collision detection: auto-suffix until free slot.
  let finalSlug = slug;
  let suffix = 2;
  while (await fileExists(inboxDir, `${finalSlug}.md`)) {
    finalSlug = `${slug}-${suffix}`;
    suffix++;
  }

  const fileHandle = await inboxDir.getFileHandle(`${finalSlug}.md`, {
    create: true,
  });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();

  return { slug: finalSlug, path: `Inbox/kb/${finalSlug}.md` };
}
