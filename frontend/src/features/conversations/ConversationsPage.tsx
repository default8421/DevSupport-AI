/**
 * @author: liuqinhe
 */
import { Bubble, Conversations, Sender, Sources } from "@ant-design/x";
import { App, Empty, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { getConversation, listConversations, sendCustomerMessage } from "../../api/endpoints";
import DiagnosisCard from "../../shared/DiagnosisCard";
import Highlight from "../../shared/Highlight";
import Markdown from "../../shared/Markdown";
import { conversationGroup } from "../../shared/formatTime";
import type { ConversationDetail, ConversationSummary } from "../../types/api";

export default function ConversationsPage() {
  const { message } = App.useApp();
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  const refresh = () => listConversations().then(setConvs);
  useEffect(() => {
    refresh();
  }, []);

  const open = (id: string) => getConversation(id).then(setDetail);
  const transferred = Boolean(detail?.conversation.transferred_to_human);

  const send = async (text: string) => {
    if (!text.trim() || !detail) return;
    setSending(true);
    try {
      await sendCustomerMessage(detail.conversation.id, text.trim());
      setReply("");
      await open(detail.conversation.id);
      message.success("已发送，技术支持会尽快回复");
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <aside style={{ width: 280, borderRight: "1px solid var(--ant-color-split, #f0f0f0)", padding: 8, overflow: "auto" }}>
        <Conversations
          items={convs.map((c) => ({
            key: c.id,
            label: (
              <span>
                {c.latest_intent || "会话"}
                {c.transferred_to_human && (
                  <Tag color="volcano" style={{ marginLeft: 6 }}>
                    人工
                  </Tag>
                )}
              </span>
            ),
            group: conversationGroup(c.updated_at),
          }))}
          groupable
          onActiveChange={(key) => {
            if (typeof key === "string") open(key);
          }}
        />
      </aside>
      <section style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {detail ? (
          <>
            <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--ant-color-split, #f0f0f0)" }}>
              <Typography.Text strong>{detail.conversation.id}</Typography.Text>
              {transferred && (
                <Tag color="volcano" style={{ marginLeft: 8 }}>
                  人工模式 · 消息直达技术支持
                </Tag>
              )}
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
              <Bubble.List
                autoScroll
                role={{
                  user: {
                    placement: "end",
                    variant: "filled",
                    contentRender: (c) =>
                      typeof c === "string" ? (
                        <span style={{ whiteSpace: "pre-wrap" }}>
                          <Highlight text={c} />
                        </span>
                      ) : (
                        c
                      ),
                  },
                  ai: {
                    placement: "start",
                    variant: "outlined",
                    contentRender: (c, info) => {
                      const meta = info.extraInfo as ConversationDetail["messages"][0]["meta"];
                      if (meta?.card) return <DiagnosisCard card={meta.card} />;
                      return typeof c === "string" ? <Markdown text={c} /> : c;
                    },
                    footer: (_c, info) => {
                      const meta = info.extraInfo as ConversationDetail["messages"][0]["meta"];
                      const cites = meta?.citations;
                      if (!cites?.length) return null;
                      return (
                        <Sources
                          title="引用来源"
                          items={cites.map((x, i) => ({
                            key: i,
                            title: `《${x.doc_title}》`,
                            description: x.source === "upload" ? "来自你上传的文档" : x.section,
                          }))}
                        />
                      );
                    },
                  },
                }}
                items={detail.messages.map((m) => ({
                  key: m.id,
                  role: m.role === "user" ? "user" : "ai",
                  content: m.content,
                  extraInfo: m.meta ?? undefined,
                }))}
              />
            </div>
            {transferred && (
              <div style={{ padding: 16 }}>
                <Sender
                  value={reply}
                  onChange={setReply}
                  loading={sending}
                  onSubmit={send}
                  placeholder="向技术支持补充信息…"
                />
              </div>
            )}
          </>
        ) : (
          <Empty style={{ margin: "auto" }} description="点击左侧会话回放完整对话" />
        )}
      </section>
    </div>
  );
}
