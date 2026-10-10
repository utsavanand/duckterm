import { authHeaders } from "./api";
import { sessionFetch, splitSessionRef } from "./hostTransport";

export interface BugContext {
  items: { id: string; label: string; text: string }[];
  recipient: string;
  limits: { attachments: number; file_bytes: number; total_bytes: number; body_bytes: number };
}
export interface BugAttachment { name: string; content_base64: string; size: number; type: string; }
export interface BugDraft { status: "draft_prepared"; sent: false; mailto_url: string | null; recipient: string; download_url: string; notice: string; }

async function json<T>(context: string, path: string, data?: unknown): Promise<T> {
  const response = await sessionFetch(context, path, { method: data === undefined ? "GET" : "POST", cache: "no-store", headers: authHeaders({ "Content-Type": "application/json" }), ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value as T;
}
export const bugReport = {
  context: (session: string | null) => json<BugContext>(session ?? "", `/bugreport/context${session ? `?session_key=${encodeURIComponent(splitSessionRef(session).key)}` : ""}`),
  prepare: (session: string | null, summary: string, body: string, attachments: BugAttachment[]) => json<BugDraft>(session ?? "", "/bugreport/submit", { summary, body, attachments: attachments.map(({ name, content_base64 }) => ({ name, content_base64 })) }),
  bundle: async (session: string | null, path: string) => {
    if (!/^\/bugreport\/bundles\/[a-f0-9]{32}$/.test(path)) throw new Error("Invalid report download link");
    const response = await sessionFetch(session ?? "", path, { cache: "no-store", headers: authHeaders() });
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || `Download failed (${response.status})`); }
    return response.blob();
  },
};
export function reportBody(summary: string, description: string, context: BugContext | null, removed: Set<string>): string {
  const items = context?.items.filter(item => !removed.has(item.id)) ?? [];
  return `# ${summary}\n\n${description}${items.length ? `\n\n## Included context\n\n${items.map(item => `### ${item.label}\n${item.text}`).join("\n\n")}` : ""}\n`;
}
export async function captureFiles(files: File[], existing: BugAttachment[], limits: BugContext["limits"]): Promise<BugAttachment[]> {
  if (files.length + existing.length > limits.attachments) throw new Error(`Choose at most ${limits.attachments} files.`);
  if (files.some(file => file.size > limits.file_bytes)) throw new Error("Each file must be 5 MiB or less.");
  if ([...files, ...existing].reduce((sum, file) => sum + file.size, 0) > limits.total_bytes) throw new Error("Attachments must total 15 MiB or less.");
  return Promise.all(files.map(async file => {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return { name: file.name, size: bytes.length, type: file.type, content_base64: btoa(binary) };
  }));
}
