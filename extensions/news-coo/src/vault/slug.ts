// Slug generator per PRD §5.1.

const MAX_SLUG_LENGTH = 80;

// Matches characters that are NOT word chars (\w = [a-z0-9_]), hyphens, or CJK / Hangul / kana ranges.
const NON_SLUG_RE =
  /[^\w㐀-鿿가-힯぀-ヿ-]/gu;

function toSlugBase(title: string): string {
  let s = title.toLowerCase();
  s = s.replace(NON_SLUG_RE, "-");
  s = s.replace(/-+/g, "-");
  s = s.replace(/^-+|-+$/g, "");
  if (s.length > MAX_SLUG_LENGTH) {
    s = s.slice(0, MAX_SLUG_LENGTH).replace(/-+$/g, "");
  }
  return s || "untitled";
}

export function slugify(title: string): string {
  return toSlugBase(title);
}

export async function slugifyUnique(
  title: string,
  exists: (slug: string) => Promise<boolean>,
): Promise<string> {
  const base = toSlugBase(title);
  if (!(await exists(base))) return base;
  let n = 2;
  while (await exists(`${base}-${n}`)) {
    n++;
  }
  return `${base}-${n}`;
}
