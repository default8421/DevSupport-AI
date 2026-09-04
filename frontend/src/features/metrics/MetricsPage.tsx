/**
 * @author: liuqinhe
 */
import { App, Button, Card, Col, Descriptions, Progress, Row, Spin, Statistic, Table, Typography } from "antd";
import { useEffect, useState } from "react";

import { getMetrics, runEval } from "../../api/endpoints";
import type { MetricsData } from "../../types/api";

function share(part: number, total: number) {
  if (!total) return 0;
  return Math.round((part / total) * 100);
}

export default function MetricsPage() {
  const { message } = App.useApp();
  const [m, setM] = useState<MetricsData | null>(null);
  const [evalResult, setEvalResult] = useState<Record<string, unknown> | null>(null);
  const [evaluating, setEvaluating] = useState(false);

  useEffect(() => {
    getMetrics().then(setM);
  }, []);

  if (!m) return <Spin style={{ margin: "auto" }} />;
  const conv = m.conversations;
  const intentTotal = Object.values(m.intent_distribution).reduce((s, n) => s + n, 0);
  const statusTotal = Object.values(m.tickets.by_status).reduce((s, n) => s + n, 0);

  return (
    <div style={{ flex: 1, padding: 24, overflow: "auto" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        运营指标
      </Typography.Title>
      <Row gutter={16}>
        <Col span={6}><Card><Statistic title="会话总数" value={conv.total} /></Card></Col>
        <Col span={6}><Card><Statistic title="AI 解决率" value={(conv.ai_resolution_rate * 100).toFixed(1)} suffix="%" /></Card></Col>
        <Col span={6}><Card><Statistic title="转人工" value={conv.transferred_to_human} /></Card></Col>
        <Col span={6}><Card><Statistic title="AI 已解决" value={conv.resolved_by_ai} /></Card></Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card title="意图分布" size="small">
            {Object.entries(m.intent_distribution).map(([k, v]) => (
              <div key={k} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{k}</span>
                  <span>{v}</span>
                </div>
                <Progress percent={share(v, intentTotal)} size="small" showInfo={false} />
              </div>
            ))}
          </Card>
        </Col>
        <Col span={12}>
          <Card title="工单状态" size="small">
            {Object.entries(m.tickets.by_status).map(([k, v]) => (
              <div key={k} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>{k}</span>
                  <span>{v}</span>
                </div>
                <Progress percent={share(v, statusTotal)} size="small" showInfo={false} />
              </div>
            ))}
          </Card>
        </Col>
      </Row>

      <Card title="Token 成本（按租户）" size="small" style={{ marginTop: 16 }}>
        <Table
          rowKey="tenant_id"
          size="small"
          pagination={false}
          dataSource={m.token_cost_by_tenant}
          columns={[
            { title: "租户", dataIndex: "tenant_id" },
            { title: "对话轮次", dataIndex: "turns" },
            { title: "总 Token", dataIndex: "total_tokens" },
          ]}
        />
      </Card>

      <Card
        title="质量评估"
        size="small"
        style={{ marginTop: 16 }}
        extra={
          <Button
            type="primary"
            loading={evaluating}
            onClick={async () => {
              setEvaluating(true);
              try {
                setEvalResult(await runEval());
                message.success("评估完成");
              } catch {
                message.error("评估失败");
              } finally {
                setEvaluating(false);
              }
            }}
          >
            运行评估集
          </Button>
        }
      >
        {evalResult ? (
          <Descriptions column={3} size="small" bordered>
            <Descriptions.Item label="样本数">{String(evalResult.total_cases ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="意图准确率">{String(evalResult.intent_accuracy ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="实体准确率">{String(evalResult.entity_accuracy ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="引用率">{String(evalResult.citation_rate ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="转人工准确率">{String(evalResult.human_transfer_accuracy ?? "-")}</Descriptions.Item>
            <Descriptions.Item label="澄清准确率">{String(evalResult.clarify_accuracy ?? "-")}</Descriptions.Item>
          </Descriptions>
        ) : (
          <Typography.Text type="secondary">点击「运行评估集」对标准问题集真实跑分</Typography.Text>
        )}
      </Card>
    </div>
  );
}
