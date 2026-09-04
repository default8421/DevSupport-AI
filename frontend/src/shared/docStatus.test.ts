/**
 * @author: liuqinhe
 */
import { describe, expect, it } from "vitest";

import { isTerminalStatus, quotaOf, statusLabel, UPLOAD_MAX_CHUNKS, UPLOAD_MAX_DOCS } from "./docStatus";
import { mergeStages, toThoughtStatus } from "./stages";

describe("docStatus", () => {
  it("只有 published 和 failed 是终态", () => {
    expect(isTerminalStatus("pending")).toBe(false);
    expect(isTerminalStatus("processing")).toBe(false);
    expect(isTerminalStatus("published")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
  });

  it("进行中文案面向用户", () => {
    expect(statusLabel("pending")).toBe("正在解析并建立索引");
    expect(statusLabel("processing")).toBe("正在解析并建立索引");
  });

  it("配额只计本租户上传，不含内置", () => {
    const q = quotaOf([
      { source: "builtin", chunk_count: 999 },
      { source: "upload", chunk_count: 10 },
      { source: "upload", chunk_count: 3 },
    ]);
    expect(q.docs).toBe(2);
    expect(q.chunks).toBe(13);
    expect(q.docsMax).toBe(UPLOAD_MAX_DOCS);
    expect(q.chunksMax).toBe(UPLOAD_MAX_CHUNKS);
  });
});

describe("mergeStages", () => {
  it("同一 key 原地更新", () => {
    const a = mergeStages([], { key: "intent", label: "识别中", status: "running", order: 1 });
    const b = mergeStages(a, { key: "intent", label: "已识别", status: "success", order: 1, duration_ms: 12 });
    expect(b).toHaveLength(1);
    expect(b[0].label).toBe("已识别");
    expect(b[0].status).toBe("success");
  });

  it("不同 key 按 order 插入", () => {
    const a = mergeStages([], { key: "b", label: "b", status: "running", order: 2 });
    const b = mergeStages(a, { key: "a", label: "a", status: "running", order: 1 });
    expect(b.map((e) => e.key)).toEqual(["a", "b"]);
  });

  it("running 映射为 ThoughtChain 的 loading", () => {
    expect(toThoughtStatus("running")).toBe("loading");
    expect(toThoughtStatus("success")).toBe("success");
    expect(toThoughtStatus("error")).toBe("error");
  });
});
