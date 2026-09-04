/**
 * @author: liuqinhe
 */
import { Bubble, Sender, ThoughtChain } from "@ant-design/x";
import { App, Button, Descriptions, Select, Space, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { getTrace, wbReply, wbSuggestReply, wbTicketDetail, wbTickets, wbUpdateTicket } from "../../api/endpoints";
import DiagnosisCard from "../../shared/DiagnosisCard";
import Highlight from "../../shared/Highlight";
import Markdown from "../../shared/Markdown";
import { formatTime } from "../../shared/formatTime";
import { PRIORITY_COLOR, TICKET_STATUSES } from "../../shared/priority";
import type { TicketRow, TraceDetail } from "../../types/api";

export default function WorkbenchPage() {
  const { message } = App.useApp();
  const [tickets, setTickets] = useState<TicketRow[]>([]);
  const [filter, setFilter] = useState<{ status?: string; priority?: string }>({});
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof wbTicketDetail>> | null>(null);
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [reply, setReply] = useState("");
  const [suggesting, setSuggesting] = useState(false);

  const load = () => wbTickets(filter).then(setTickets);
  useEffect(() => {
    load();
  }, [filter.status, filter.priority]);

  const open = async (id: string) => {
    try {
      const d = await wbTicketDetail(id);
      setDetail(d);
      setTrace(null);
      const traceId = d.conversation_messages
        ?.map((m) => (m.meta as { trace_id?: string } | null)?.trace_id)
        .filter(Boolean)
        .pop();
      if (traceId) {
        try {
          setTrace(await getTrace(traceId));
        } catch {
          /* 链路可能不存在 */
        }
      }
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      message.error(status === 401 ? "登录已过期，请重新登录" : "打开工单失败");
    }
  };

  const setStatus = async (status: string) => {
    if (!detail) return;
    await wbUpdateTicket(detail.ticket.ticket_id, { status });
    message.success(`状态更新为 ${status}`);
    load();
    open(detail.ticket.ticket_id);
  };

  const sendReply = async (text: string) => {
    if (!detail?.ticket.conversation_id || !text.trim()) return;
    await wbReply(detail.ticket.conversation_id, text.trim());
    message.success("已回复客户");
    setReply("");
    open(detail.ticket.ticket_id);
  };

  if (!detail) {
    return (
      <div style={{ flex: 1, padding: 24, overflow: "auto" }}>
        <Space style={{ marginBottom: 16 }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            工单工作台
          </Typography.Title>
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 160 }}
            options={TICKET_STATUSES.map((s) => ({ value: s, label: s }))}
            onChange={(v) => setFilter((f) => ({ ...f, status: v }))}
          />
          <Select
            allowClear
            placeholder="优先级"
            style={{ width: 100 }}
            options={["P0", "P1", "P2", "P3"].map((s) => ({ value: s, label: s }))}
            onChange={(v) => setFilter((f) => ({ ...f, priority: v }))}
          />
          <Button onClick={load}>刷新</Button>
        </Space>
        <Table
          rowKey="ticket_id"
          dataSource={tickets}
          pagination={{ pageSize: 12 }}
          onRow={(r) => ({ onClick: () => open(r.ticket_id), style: { cursor: "pointer" } })}
          columns={[
            { title: "工单号", dataIndex: "ticket_id" },
            { title: "标题", dataIndex: "title", ellipsis: true },
            { title: "类型", dataIndex: "category", width: 100 },
            { title: "租户", dataIndex: "tenant_id", width: 110 },
            {
              title: "优先级",
              dataIndex: "priority",
              width: 90,
              render: (p: string) => <Tag color={PRIORITY_COLOR[p]}>{p}</Tag>,
            },
            { title: "状态", dataIndex: "status", width: 130, render: (s: string) => <Tag>{s}</Tag> },
            { title: "创建时间", dataIndex: "created_at", render: (t: string) => formatTime(t) },
          ]}
        />
      </div>
    );
  }

  const t = detail.ticket;
  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <section style={{ flex: 1, padding: 20, overflow: "auto", borderRight: "1px solid var(--ant-color-split, #f0f0f0)" }}>
        <Space style={{ marginBottom: 12 }} wrap>
          <Button onClick={() => { setDetail(null); load(); }}>返回列表</Button>
          <Typography.Text strong>{t.ticket_id}</Typography.Text>
          {TICKET_STATUSES.map((s) => (
            <Button key={s} size="small" type={t.status === s ? "primary" : "default"} onClick={() => setStatus(s)}>
              {s}
            </Button>
          ))}
        </Space>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="标题" span={2}>{t.title}</Descriptions.Item>
          <Descriptions.Item label="类型">{t.category}</Descriptions.Item>
          <Descriptions.Item label="优先级">
            <Tag color={PRIORITY_COLOR[t.priority]}>{t.priority}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="错误码">{t.error_code || "-"}</Descriptions.Item>
          <Descriptions.Item label="租户">{t.tenant_id}</Descriptions.Item>
          <Descriptions.Item label="AI 诊断" span={2}>
            {t.ai_diagnosis ? <Markdown text={t.ai_diagnosis} /> : "-"}
          </Descriptions.Item>
        </Descriptions>

        <Typography.Title level={5} style={{ marginTop: 24 }}>对话</Typography.Title>
        <Bubble.List
          role={{
            user: {
              placement: "end",
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
              contentRender: (c, info) => {
                const card = (info.extraInfo as { card?: Parameters<typeof DiagnosisCard>[0]["card"] } | undefined)?.card;
                if (card) return <DiagnosisCard card={card} />;
                return typeof c === "string" ? <Markdown text={c} /> : c;
              },
            },
          }}
          items={detail.conversation_messages.map((m, i) => ({
            key: i,
            role: m.role === "user" ? "user" : "ai",
            content: m.content,
            extraInfo: m.meta ?? undefined,
          }))}
        />

        {t.conversation_id && (
          <div style={{ marginTop: 16 }}>
            <Button
              size="small"
              loading={suggesting}
              style={{ marginBottom: 8 }}
              onClick={async () => {
                setSuggesting(true);
                try {
                  const r = await wbSuggestReply(t.conversation_id!);
                  setReply(r.suggestion);
                } catch {
                  message.error("生成失败");
                } finally {
                  setSuggesting(false);
                }
              }}
            >
              生成 AI 建议回复
            </Button>
            <Sender value={reply} onChange={setReply} onSubmit={sendReply} placeholder="编辑后发送给客户" />
          </div>
        )}
      </section>

      <aside style={{ width: 360, padding: 16, overflow: "auto" }}>
        <Typography.Title level={5}>Agent 执行链</Typography.Title>
        {trace ? (
          <>
            <Typography.Text type="secondary">
              {trace.trace_id} · {trace.total_duration_ms}ms · {trace.total_tokens} tokens
            </Typography.Text>
            <ThoughtChain
              style={{ marginTop: 12 }}
              items={[
                ...trace.steps.map((s) => ({
                  key: `s${s.step_order}`,
                  title: s.agent_name,
                  status: (s.status === "error" ? "error" : "success") as "error" | "success",
                  description: `${s.duration_ms}ms · ${s.token_usage} tok`,
                  content: s.output_summary || s.error_message || undefined,
                })),
                ...(trace.tool_calls.length
                  ? [
                      {
                        key: "tools",
                        title: "工具调用",
                        status: "success" as const,
                        content: (
                          <ThoughtChain
                            items={trace.tool_calls.map((c, i) => ({
                              key: `t${i}`,
                              title: c.tool_name,
                              status: (c.status === "ok" ? "success" : "error") as "success" | "error",
                              description: `${c.duration_ms}ms`,
                              content: c.error_message || c.result_summary || c.args_summary || undefined,
                            }))}
                          />
                        ),
                      },
                    ]
                  : []),
              ]}
            />
          </>
        ) : (
          <Typography.Text type="secondary">该工单暂无关联链路</Typography.Text>
        )}
      </aside>
    </div>
  );
}
