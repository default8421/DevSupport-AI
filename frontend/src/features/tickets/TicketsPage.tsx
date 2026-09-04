/**
 * @author: liuqinhe
 */
import { Drawer, Empty, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

import { getTicket, myTickets } from "../../api/endpoints";
import Markdown from "../../shared/Markdown";
import { formatTime } from "../../shared/formatTime";
import { PRIORITY_COLOR } from "../../shared/priority";
import type { TicketDetail, TicketRow } from "../../types/api";

export default function TicketsPage() {
  const [data, setData] = useState<TicketRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<TicketDetail | null>(null);

  useEffect(() => {
    myTickets()
      .then(setData)
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ flex: 1, padding: 28, overflow: "auto" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        我的工单
      </Typography.Title>
      <Table
        rowKey="ticket_id"
        loading={loading}
        dataSource={data}
        onRow={(r) => ({
          onClick: () => {
            getTicket(r.ticket_id).then(setDetail);
          },
          style: { cursor: "pointer" },
        })}
        columns={[
          { title: "工单号", dataIndex: "ticket_id" },
          { title: "标题", dataIndex: "title", ellipsis: true },
          { title: "类型", dataIndex: "category", width: 110 },
          {
            title: "优先级",
            dataIndex: "priority",
            width: 90,
            render: (p: string) => <Tag color={PRIORITY_COLOR[p]}>{p}</Tag>,
          },
          { title: "状态", dataIndex: "status", width: 130, render: (s: string) => <Tag>{s}</Tag> },
          { title: "错误码", dataIndex: "error_code" },
          { title: "创建时间", dataIndex: "created_at", render: (t: string) => formatTime(t) },
        ]}
      />
      <Drawer
        title={detail ? `工单 ${detail.ticket_id}` : "工单详情"}
        width={480}
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
      >
        {detail ? (
          <>
            <Typography.Title level={5}>{detail.title}</Typography.Title>
            <Tag color={PRIORITY_COLOR[detail.priority]}>{detail.priority}</Tag>
            <Tag>{detail.status}</Tag>
            {detail.error_code && <Tag>{detail.error_code}</Tag>}
            <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
              {detail.summary || "无摘要"}
            </Typography.Paragraph>
            <Typography.Text strong>AI 诊断</Typography.Text>
            <div style={{ marginTop: 8 }}>
              {detail.ai_diagnosis ? <Markdown text={detail.ai_diagnosis} /> : <Empty />}
            </div>
          </>
        ) : null}
      </Drawer>
    </div>
  );
}
