// Content extraction wrapper — adapts the Defuddle-invocation pattern from
// Obsidian Web Clipper (https://github.com/obsidianmd/obsidian-clipper, MIT).

import Defuddle from "defuddle/full";
import type { ExtractedPage, ImageRef } from "../shared/types.js";

function collectImageRefs(
  doc: Document,
  htmlContent: string,
  baseUrl: string,
): ImageRef[] {
  const tmp = doc.createElement("div");
  tmp.innerHTML = htmlContent;
  return Array.from(tmp.querySelectorAll<HTMLImageElement>("img")).flatMap(
    (img) => {
      const rawSrc = img.getAttribute("src") ?? "";
      if (!rawSrc) return [];
      let src = rawSrc;
      try {
        src = new URL(rawSrc, baseUrl).href;
      } catch {
        // keep rawSrc when URL construction fails (e.g. data: URIs)
      }
      const ref: ImageRef = { src, alt: img.alt };
      if (img.title) ref.title = img.title;
      return [ref];
    },
  );
}

export function extractPage(doc: Document, url: string): ExtractedPage {
  const result = new Defuddle(doc, { url, separateMarkdown: true }).parse();
  const imageRefs = collectImageRefs(doc, result.content, url);
  return {
    url,
    title: result.title ?? "",
    markdown: result.contentMarkdown ?? "",
    description: result.description ?? "",
    author: result.author ?? "",
    published: result.published ?? "",
    imageRefs,
  };
}
