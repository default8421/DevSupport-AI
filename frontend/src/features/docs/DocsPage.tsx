/**
 * @author: liuqinhe
 */
import { Empty, Input, Menu, Spin, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getDoc, listDocs } from "../../api/endpoints";
import Markdown from "../../shared/Markdown";
import type { DocBody, DocMeta } from "../../types/api";

export default function DocsPage() {
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [current, setCurrent] = useState<DocBody | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");

  useEffect(() => {
    listDocs().then((list) => {
      setDocs(list);
      if (list[0]) open(list[0].id);
    });
  }, []);

  const open = async (id: string) => {
    setLoading(true);
    try {
      setCurrent(await getDoc(id));
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return docs;
    return docs.filter((d) => `${d.title}${d.category}`.toLowerCase().includes(s));
  }, [docs, q]);

  const groups = useMemo(() => {
    const m = new Map<string, DocMeta[]>();
    for (const d of filtered) {
      const arr = m.get(d.category) ?? [];
      arr.push(d);
      m.set(d.category, arr);
    }
    return [...m.entries()];
  }, [filtered]);

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <aside style={{ width: 280, borderRight: "1px solid var(--ant-color-split, #f0f0f0)", padding: 16, overflow: "auto" }}>
        <Typography.Title level={5} style={{ marginTop: 0 }}>
          平台文档
        </Typography.Title>
        <Input.Search
          allowClear
          placeholder="搜索标题或分类"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        <Menu
          mode="inline"
          selectedKeys={current ? [current.id] : []}
          onClick={(e) => open(e.key)}
          items={groups.map(([cat, items]) => ({
            type: "group" as const,
            key: cat,
            label: cat,
            children: items.map((d) => ({ key: d.id, label: d.title })),
          }))}
        />
      </aside>
      <section style={{ flex: 1, padding: 28, overflow: "auto" }}>
        {loading ? (
          <Spin />
        ) : current ? (
          <>
            <Typography.Title level={3} style={{ marginTop: 0 }}>
              {current.title}
            </Typography.Title>
            <Tag color="blue">{current.category}</Tag>
            <div style={{ marginTop: 16 }}>
              <Markdown text={current.content} />
            </div>
          </>
        ) : (
          <Empty description="选择左侧文档查看" />
        )}
      </section>
    </div>
  );
}
