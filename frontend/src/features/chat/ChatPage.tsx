/**
 * @author: liuqinhe
 */
import { CheckCircleOutlined, CustomerServiceOutlined, PaperClipOutlined } from "@ant-design/icons";
import { Actions, Bubble, Conversations, Prompts, Sender, Sources, ThoughtChain, Welcome } from "@ant-design/x";
import { App, Button, Empty, Space, Tabs, Tag, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";

import { chatStream } from "../../api/chat";
import { getConversation, listConversations, submitFeedback, uploadDocument } from "../../api/endpoints";
import { useAuth } from "../../auth/AuthContext";
import DiagnosisCard from "../../shared/DiagnosisCard";
import Highlight from "../../shared/Highlight";
import Markdown from "../../shared/Markdown";
import { conversationGroup } from "../../shared/formatTime";
import { mergeStages, toThoughtStatus } from "../../shared/stages";
import { isInternal, type Citation, type ConversationSummary, type DiagnosisCardData, type StageEvent } from "../../types/api";
import styles from "./chat.module.css";

interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  card?: DiagnosisCardData | null;
  citations?: Citation[];
  ticket_id?: string | null;
  need_human?: boolean;
  from_cache?: boolean;
  message_id?: string;
  streaming?: boolean;
  byHuman?: boolean;
  agentName?: string;
}

const CUSTOMER_PROMPTS = [
  { key: "p1", label: "根据我上传的文档，项目架构是怎样的", description: "先检索知识库再给结论" },
  { key: "p2", label: "实名认证接口返回 401", description: "request_id 是 req_20260615_8842" },
  { key: "p3", label: "签名算法怎么生成", description: "对照官方文档核对签名串" },
];

const INTERNAL_PROMPTS = [
  { key: "i1", label: "帮我看这条调用日志", description: "带上 request_id 即可定位" },
  { key: "i2", label: "客户账单对不上", description: "核对套餐变更与超额" },
];

export default function ChatPage() {
  const { message } = App.useApp();
  const { user } = useAuth();
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [stages, setStages] = useState<StageEvent[]>([]);
  const [stagesOpen, setStagesOpen] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [contextOpen, setContextOpen] = useState(true);
  const convId = useRef<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | undefined>();
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshList = useCallback(() => {
    listConversations().then(setConvs).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  const newChat = () => {
    convId.current = null;
    setActiveKey(undefined);
    setMsgs([]);
    setStages([]);
    setStagesOpen(true);
  };

  const openConv = async (id: string) => {
    const d = await getConversation(id);
    convId.current = id;
    setActiveKey(id);
    setStages([]);
    setMsgs(
      d.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        card: m.meta?.card,
        citations: m.meta?.citations,
        ticket_id: m.meta?.ticket_id,
        need_human: m.meta?.need_human,
        from_cache: m.meta?.from_cache,
        message_id: m.id,
        byHuman: m.meta?.by === "human",
        agentName: m.meta?.agent_name,
      })),
    );
  };

  const send = async (textRaw?: string) => {
    const text = (textRaw ?? input).trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);
    setStages([]);
    setStagesOpen(true);
    const userId = `u_${Date.now()}`;
    const asstId = `a_${Date.now()}`;
    setMsgs((m) => [
      ...m,
      { id: userId, role: "user", content: text },
      { id: asstId, role: "assistant", content: "", streaming: true },
    ]);
    try {
      await chatStream(text, convId.current, {
        onStage: (s) => setStages((prev) => mergeStages(prev, s)),
        onMeta: (m) => {
          convId.current = m.conversation_id;
          setActiveKey(m.conversation_id);
          setMsgs((prev) => {
            const c = prev.slice();
            c[c.length - 1] = { ...c[c.length - 1], message_id: m.message_id };
            return c;
          });
        },
        onToken: (t) => {
          setStagesOpen(false);
          setMsgs((prev) => {
            const c = prev.slice();
            const last = c[c.length - 1];
            c[c.length - 1] = { ...last, content: last.content + t };
            return c;
          });
        },
        onDone: (d) => {
          setMsgs((prev) => {
            const c = prev.slice();
            c[c.length - 1] = {
              ...c[c.length - 1],
              streaming: false,
              content: d.answer ?? c[c.length - 1].content,
              card: d.card,
              citations: d.citations,
              ticket_id: d.ticket_id,
              need_human: d.need_human,
              from_cache: d.from_cache,
            };
            return c;
          });
          refreshList();
        },
        onError: (e) => {
          message.error(e.message);
          setMsgs((prev) => {
            const c = prev.slice();
            c[c.length - 1] = { ...c[c.length - 1], streaming: false, content: e.message };
            return c;
          });
        },
      });
    } catch {
      message.error("对话失败");
      setMsgs((prev) => prev.filter((m) => m.id !== asstId));
    } finally {
      setSending(false);
    }
  };

  const feedback = async (type: "resolved" | "unresolved" | "need_human") => {
    if (!convId.current) {
      message.info("请先开始对话");
      return;
    }
    const r = await submitFeedback({ conversation_id: convId.current, type });
    if (type === "need_human" && r.ticket_id) {
      message.success(`已转人工，工单 ${r.ticket_id}`);
      setMsgs((m) => [
        ...m,
        {
          id: `h_${Date.now()}`,
          role: "assistant",
          content: `已为你转接人工技术支持，工单号 ${r.ticket_id}。`,
          ticket_id: r.ticket_id,
        },
      ]);
    } else {
      message.success(type === "resolved" ? "已标记为已解决" : "已记录");
    }
  };

  const onUpload = async (file: File) => {
    try {
      await uploadDocument(file);
      message.success(`已上传 ${file.name}，解析完成后可在对话中引用`);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || "上传失败");
    }
  };

  const last = msgs[msgs.length - 1];
  const citations = last?.citations ?? [];
  const ticketId = last?.ticket_id;
  const prompts = user && isInternal(user.role) ? INTERNAL_PROMPTS : CUSTOMER_PROMPTS;

  return (
    <div className={styles.page}>
      <aside className={styles.list}>
        <div className={styles.listBody}>
          <Conversations
            activeKey={activeKey}
            items={convs.map((c) => ({
              key: c.id,
              label: c.latest_intent || "新会话",
              group: conversationGroup(c.updated_at),
            }))}
            groupable
            creation={{ label: "新建对话", onClick: newChat }}
            onActiveChange={(key) => {
              if (typeof key === "string") openConv(key);
            }}
          />
        </div>
      </aside>

      <section className={styles.center}>
        <div className={styles.bubbles}>
          {msgs.length === 0 ? (
            <div className={styles.welcome}>
              <Welcome
                title="DevSupport 智能技术支持"
                description="可问本租户上传的知识库，也可问接口报错、账单或平台文档。先给结论，再给证据。"
              />
              <div style={{ marginTop: 20 }}>
                <Prompts
                  items={prompts}
                  wrap
                  onItemClick={({ data }) => send(`${data.label}${data.description ? `，${data.description}` : ""}`)}
                />
              </div>
            </div>
          ) : (
            <>
              {stages.length > 0 && (
                <div className={styles.stages}>
                  <ThoughtChain
                    items={stages.map((s) => ({
                      key: s.key,
                      title: s.label,
                      status: toThoughtStatus(s.status),
                      description: s.duration_ms != null ? `${s.duration_ms}ms` : undefined,
                    }))}
                    expandedKeys={stagesOpen ? stages.map((s) => s.key) : []}
                    onExpand={(keys) => setStagesOpen(keys.length > 0)}
                  />
                </div>
              )}
              <Bubble.List
                autoScroll
                style={{ height: "auto" }}
                role={{
                  user: {
                    placement: "end",
                    variant: "filled",
                    shape: "corner",
                    contentRender: (content) =>
                      typeof content === "string" ? (
                        <span style={{ whiteSpace: "pre-wrap" }}>
                          <Highlight text={content} />
                        </span>
                      ) : (
                        content
                      ),
                  },
                  ai: {
                    placement: "start",
                    variant: "outlined",
                    shape: "corner",
                    contentRender: (content, info) => {
                      const m = info.extraInfo as ChatMsg | undefined;
                      if (m?.card) return <DiagnosisCard card={m.card} />;
                      if (typeof content === "string" && content) return <Markdown text={content} />;
                      return m?.streaming ? "正在生成…" : null;
                    },
                    footer: (_content, info) => {
                      const m = info.extraInfo as ChatMsg | undefined;
                      if (!m || m.streaming || m.role !== "assistant") return null;
                      return (
                        <Space wrap size={6} style={{ marginTop: 6 }}>
                          <Tag color="green">{m.byHuman ? `人工 · ${m.agentName ?? ""}` : "AI 助手"}</Tag>
                          {m.from_cache && <Tag color="gold">缓存命中</Tag>}
                          {m.need_human && <Tag color="volcano">已转人工</Tag>}
                          {m.ticket_id && <Tag color="blue">工单 {m.ticket_id}</Tag>}
                          <Actions
                            items={[
                              { key: "ok", label: "已解决", icon: <CheckCircleOutlined /> },
                              { key: "no", label: "未解决" },
                              { key: "human", label: "转人工", icon: <CustomerServiceOutlined /> },
                            ]}
                            onClick={({ key }) => {
                              if (key === "ok") feedback("resolved");
                              if (key === "no") feedback("unresolved");
                              if (key === "human") feedback("need_human");
                            }}
                          />
                        </Space>
                      );
                    },
                  },
                }}
                items={msgs.map((m) => ({
                  key: m.id,
                  role: m.role === "user" ? "user" : "ai",
                  content: m.content,
                  extraInfo: m,
                }))}
              />
            </>
          )}
        </div>
        <div className={styles.sender}>
          <input
            ref={fileRef}
            type="file"
            hidden
            accept=".md,.txt,.pdf"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = "";
            }}
          />
          <Sender
            value={input}
            onChange={setInput}
            onSubmit={(v) => send(v)}
            loading={sending}
            placeholder="描述你的问题，可粘贴 request_id / 错误码…"
            onPasteFile={(files) => {
              const f = files[0];
              if (f) onUpload(f);
            }}
            prefix={
              <Button
                type="text"
                icon={<PaperClipOutlined />}
                onClick={() => fileRef.current?.click()}
                aria-label="上传文档到知识库"
              />
            }
          />
        </div>
      </section>

      {contextOpen ? (
        <aside className={styles.context}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <Typography.Text strong>上下文</Typography.Text>
            <Button type="text" size="small" onClick={() => setContextOpen(false)}>
              收起
            </Button>
          </div>
          <Tabs
            size="small"
            items={[
              {
                key: "src",
                label: "引用来源",
                children:
                  citations.length > 0 ? (
                    <Sources
                      title="参考资料"
                      items={citations.map((c, i) => ({
                        key: i,
                        title: `《${c.doc_title}》${c.section ? ` · ${c.section}` : ""}`,
                        description: c.source === "upload" ? "来自你上传的文档" : undefined,
                      }))}
                    />
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮尚无引用" />
                  ),
              },
              {
                key: "chain",
                label: "执行链",
                children:
                  stages.length > 0 ? (
                    <ThoughtChain
                      items={stages.map((s) => ({
                        key: s.key,
                        title: s.label,
                        status: toThoughtStatus(s.status),
                        description: s.duration_ms != null ? `${s.duration_ms}ms` : undefined,
                      }))}
                    />
                  ) : (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送后显示编排过程" />
                  ),
              },
              {
                key: "ticket",
                label: "关联工单",
                children: ticketId ? (
                  <Tag color="blue">{ticketId}</Tag>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本轮未建单" />
                ),
              },
            ]}
          />
        </aside>
      ) : (
        <Button type="text" style={{ alignSelf: "flex-start", margin: 8 }} onClick={() => setContextOpen(true)}>
          上下文
        </Button>
      )}
    </div>
  );
}
