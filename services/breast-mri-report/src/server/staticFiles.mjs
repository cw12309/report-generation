import { isAbsolute, relative, resolve } from "node:path";

const ROOT_FILES = new Set(["index.html", "styles.css"]);
const SOURCE_FILE = /^src\/(?:app\.js|domain\/[A-Za-z0-9_-]+\.js)$/;

export function resolveStaticFile(root, pathname) {
  const requested = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  if (!ROOT_FILES.has(requested) && !SOURCE_FILE.test(requested)) return null;

  const absoluteRoot = resolve(root);
  const filePath = resolve(absoluteRoot, requested);
  const relativePath = relative(absoluteRoot, filePath);
  if (!relativePath || relativePath.startsWith("..") || isAbsolute(relativePath)) return null;
  return filePath;
}
