/**
 * @author: liuqinhe
 */
import { Attachments, FileCard } from "@ant-design/x";
import { App, Progress, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import { deleteDocument, listDocuments, uploadDocument } from "../../api/endpoints";
import { isTerminalStatus, quotaOf, statusLabel } from "../../shared/docStatus";
import { formatTime } from "../../shared/formatTime";
import type { DocumentView } from "../../types/api";

export default function KnowledgePage() {
  const { message, modal } = App.useApp();
  const [docs, setDocs] = useState<DocumentView[]>([]);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(() => {
    listDocuments().then(setDocs).catch(() => message.error("加载知识库失败"));
  }, [message]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!docs.some((d) => !isTerminalStatus(d.status))) return;
    const t = window.setInterval(load, 2500);
    return () => window.clearInterval(t);
  }, [docs, load]);

  const onFile = async (file: File) => {
    setUploading(true);
    try {
      await uploadDocument(file);
      message.success(`已接收 ${file.name}，正在建立索引`);
      load();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(typeof detail === "string" ? detail : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const remove = (d: DocumentView) => {
    modal.confirm({
      title: `删除「${d.title}」？`,
      content: "切片会一并清除，对话将不再引用这篇文档。",
      okButtonProps: { danger: true },
      onOk: async () => {
        await deleteDocument(d.id);
        message.success("已删除");
        load();
      },
    });
  };

  const q = quotaOf(docs);

  return (
    <div style={{ flex: 1, padding: 28, overflow: "auto" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        我的知识库
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        上传的文档只对本租户可见。内置平台文档不可删除。
      </Typography.Paragraph>

      <div style={{ display: "flex", gap: 24, marginBottom: 20, maxWidth: 520 }}>
        <div style={{ flex: 1 }}>
          <Typography.Text type="secondary">文档数 {q.docs} / {q.docsMax}</Typography.Text>
          <Progress percent={Math.round((q.docs / q.docsMax) * 100)} size="small" />
        </div>
        <div style={{ flex: 1 }}>
          <Typography.Text type="secondary">切片 {q.chunks} / {q.chunksMax}</Typography.Text>
          <Progress percent={Math.round((q.chunks / q.chunksMax) * 100)} size="small" />
        </div>
      </div>

      <Attachments
        disabled={uploading}
        accept=".md,.txt,.pdf"
        beforeUpload={(file) => {
          void onFile(file as File);
          return false;
        }}
        placeholder={{
          title: uploading ? "正在上传…" : "拖拽或点击上传 Markdown / 文本 / PDF",
          description: "单文件不超过 10 MB",
        }}
      />

      <div style={{ marginTop: 24 }}>
        <FileCard.List
          overflow="wrap"
          items={docs.map((d) => ({
            name: d.original_filename || d.title,
            byte: d.size_bytes ?? undefined,
            loading: !isTerminalStatus(d.status),
            description:
              d.status === "failed"
                ? d.error_message || "处理失败"
                : d.status === "published"
                  ? `${d.chunk_count} 个知识片段 · ${d.source === "builtin" ? "平台内置" : formatTime(d.updated_at)}`
                  : statusLabel(d.status),
            icon: d.original_filename?.endsWith(".pdf") ? "pdf" : "markdown",
          }))}
          removable={(item) => {
            const d = docs.find((x) => (x.original_filename || x.title) === item.name);
            return Boolean(d?.deletable);
          }}
          onRemove={(item) => {
            const d = docs.find((x) => (x.original_filename || x.title) === item.name);
            if (d) remove(d);
          }}
        />
      </div>
    </div>
  );
}
