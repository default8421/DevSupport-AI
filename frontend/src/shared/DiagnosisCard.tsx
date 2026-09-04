/**
 * @author: liuqinhe
 */
import { Typography } from "antd";

import type { DiagnosisCardData } from "../types/api";
import Highlight from "./Highlight";

export default function DiagnosisCard({ card }: { card: DiagnosisCardData }) {
  return (
    <div>
      {card.conclusion && (
        <div
          style={{
            background: "var(--ant-color-success-bg, #f6ffed)",
            border: "1px solid var(--ant-color-success-border, #b7eb8f)",
            borderRadius: 10,
            padding: "10px 12px",
          }}
        >
          <Typography.Text strong>结论：</Typography.Text>
          <Highlight text={card.conclusion} />
        </div>
      )}
      {card.evidence?.length > 0 && (
        <>
          <Typography.Text type="secondary" style={{ display: "block", margin: "12px 0 6px" }}>
            证据
          </Typography.Text>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {card.evidence.map((e, i) => (
              <li key={i}>
                <Highlight text={e} />
              </li>
            ))}
          </ul>
        </>
      )}
      {card.steps?.length > 0 && (
        <>
          <Typography.Text type="secondary" style={{ display: "block", margin: "12px 0 6px" }}>
            建议步骤
          </Typography.Text>
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {card.steps.map((s, i) => (
              <li key={i} style={{ margin: "3px 0" }}>
                <Highlight text={s} />
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
