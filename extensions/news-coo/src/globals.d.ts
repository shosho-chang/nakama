// File System Access API augmentations missing from TypeScript 5.5 lib.dom.d.ts.

interface Window {
  showDirectoryPicker(options?: { mode?: "read" | "readwrite" }): Promise<FileSystemDirectoryHandle>;
}
