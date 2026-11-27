import type { Artifact, ArtifactKind } from "./api";
import { ARTIFACT_KINDS } from "./artifactKinds";
export type ArtifactFormat = "all" | "markdown" | "html" | "react" | "pdf" | "other";
export type ArtifactSort = "recent" | "oldest" | "name";
// A runnable React artifact is a self-contained HTML bundle, never raw JSX.
export function artifactFormat(a: Pick<Artifact, "source_path" | "media_type">): string {
  if (a.media_type === "application/pdf") return "PDF";
  if (a.media_type === "text/html" && /\.react\.html?$/i.test(a.source_path)) return "React app";
  if (a.media_type === "text/html") return "HTML";
  if (a.media_type === "text/markdown") return "Markdown";
  if (a.media_type.startsWith("image/")) return "Image";
  if (a.media_type.startsWith("text/")) return "Text";
  return "File";
}
export function filterArtifacts<T extends Artifact>(rows: T[], options: { query: string; format: ArtifactFormat; label: ArtifactKind | "all"; sort: ArtifactSort }): T[] {
  const query = options.query.trim().toLocaleLowerCase();
  return rows.filter(a => !a.removed_at && `${a.title} ${a.source_path} ${a.kind ?? "other"} ${ARTIFACT_KINDS[a.kind ?? "other"] ?? "Other"}`.toLocaleLowerCase().includes(query)
    && (options.label === "all" || (a.kind ?? "other") === options.label)
    && (options.format === "all" || (options.format === "pdf" ? a.media_type === "application/pdf" : options.format === "react" ? artifactFormat(a) === "React app" : options.format === "html" ? artifactFormat(a) === "HTML" : options.format === "markdown" ? a.media_type === "text/markdown" : !["HTML", "React app", "Markdown", "PDF"].includes(artifactFormat(a)))))
    .sort((a, b) => (options.sort === "name" ? a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) : options.sort === "oldest" ? a.updated_at - b.updated_at : b.updated_at - a.updated_at) || a.id.localeCompare(b.id));
}
