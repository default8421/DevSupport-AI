/**
 * @author: liuqinhe
 */

/** 与后端状态机对齐：pending → processing → published / failed。 */
export function isTerminalStatus(status: string): boolean {
  return status === "published" || status === "failed";
}

export function statusLabel(status: string): string {
  if (status === "pending" || status === "processing") return "正在解析并建立索引";
  if (status === "published") return "已就绪";
  if (status === "failed") return "处理失败";
  return status;
}

export const UPLOAD_MAX_DOCS = 50;
export const UPLOAD_MAX_CHUNKS = 5000;

export function quotaOf(docs: { source: string; chunk_count: number }[]) {
  const uploads = docs.filter((d) => d.source === "upload");
  return {
    docs: uploads.length,
    docsMax: UPLOAD_MAX_DOCS,
    chunks: uploads.reduce((s, d) => s + (d.chunk_count || 0), 0),
    chunksMax: UPLOAD_MAX_CHUNKS,
  };
}
