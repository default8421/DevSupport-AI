/**
 * @author: liuqinhe
 */
import { describe, expect, it } from "vitest";

import { createSseParser, readSse, type SseEvent } from "./sse";

/**
 * 下面的字节串是从后端实测抓出来的，不是照着规范臆造的：
 *   ServerSentEvent(data='**结论**：时间戳超窗\n\n1. 校准时钟', event='token').encode()
 * sse_starlette 用 CRLF 分隔，并把含换行的 data 拆成多个 data: 行（空行那条是 "data: "）。
 */
const REAL_TOKEN =
  "event: token\r\ndata: **结论**：时间戳超窗\r\ndata: \r\ndata: 1. 校准时钟\r\n\r\n";
const REAL_META = 'event: meta\r\ndata: {"message_id": "m1"}\r\n\r\n';

/** 把若干文本块喂进解析器，收集全部事件。 */
function parseAll(chunks: string[]): SseEvent[] {
  const parser = createSseParser();
  const out: SseEvent[] = [];
  for (const c of chunks) out.push(...parser.push(c));
  out.push(...parser.flush());
  return out;
}

/** 用字节块伪造 fetch 的 ReadableStream，用来连解码一起测。 */
function streamOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(chunks[i++]);
      else controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseEvent[]> {
  const out: SseEvent[] = [];
  for await (const evt of readSse(stream)) out.push(evt);
  return out;
}

describe("createSseParser", () => {
  it("解析单个事件", () => {
    expect(parseAll([REAL_META])).toEqual([{ event: "meta", data: '{"message_id": "m1"}' }]);
  });

  it("多个 data 行拼回换行，而不是丢失", () => {
    const [evt] = parseAll([REAL_TOKEN]);
    expect(evt.event).toBe("token");
    expect(evt.data).toBe("**结论**：时间戳超窗\n\n1. 校准时钟");
  });

  it("CRLF 分隔符不残留 \\r", () => {
    const events = parseAll([REAL_TOKEN, REAL_META]);
    for (const evt of events) {
      expect(evt.data).not.toContain("\r");
      expect(evt.event).not.toContain("\r");
    }
  });

  it("事件被 chunk 边界切断也能拼回", () => {
    // 逐字符喂，等价于最坏情况的分块
    const chunks = [...REAL_TOKEN];
    expect(parseAll(chunks)).toEqual([
      { event: "token", data: "**结论**：时间戳超窗\n\n1. 校准时钟" },
    ]);
  });

  it("恰好切在 CRLF 中间不会多切出一行", () => {
    const cut = REAL_TOKEN.indexOf("\r\n\r\n") + 1; // 停在第一个 \r 之后
    const events = parseAll([REAL_TOKEN.slice(0, cut), REAL_TOKEN.slice(cut)]);
    expect(events).toEqual([{ event: "token", data: "**结论**：时间戳超窗\n\n1. 校准时钟" }]);
  });

  it("未知事件类型静默忽略且不影响后续", () => {
    const unknown = "event: stage\r\ndata: {}\r\n\r\n";
    const events = parseAll([unknown, REAL_META]);
    // 解析器如实产出，是否处理交给调用方；关键是后面的 meta 没被带坏
    expect(events.map((e) => e.event)).toEqual(["stage", "meta"]);
    expect(events[1].data).toBe('{"message_id": "m1"}');
  });

  it("data: 后有无空格都容忍，且只剥一个", () => {
    expect(parseAll(["event: t\r\ndata:abc\r\n\r\n"])[0].data).toBe("abc");
    expect(parseAll(["event: t\r\ndata: abc\r\n\r\n"])[0].data).toBe("abc");
    expect(parseAll(["event: t\r\ndata:  abc\r\n\r\n"])[0].data).toBe(" abc");
  });

  it("空 data 行产生换行而不是被丢弃", () => {
    // 三行 data，中间为空 —— 正是后端切到段落分隔时发出的形状
    expect(parseAll(["event: token\r\ndata: a\r\ndata: \r\ndata: b\r\n\r\n"])[0].data).toBe("a\n\nb");
  });

  it("注释心跳不产生事件", () => {
    const events = parseAll([": ping\r\n\r\n", REAL_META]);
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("meta");
  });

  it("无 data 行的事件不派发", () => {
    expect(parseAll(["event: token\r\n\r\n"])).toEqual([]);
  });

  it("LF 与 CR 单独作为分隔符也支持", () => {
    expect(parseAll(["event: t\ndata: x\n\n"])[0].data).toBe("x");
    expect(parseAll(["event: t\rdata: x\r\r"])[0].data).toBe("x");
  });

  it("末尾缺空行时 flush 仍吐出事件", () => {
    expect(parseAll(['event: done\r\ndata: {"ok": true}'])).toEqual([
      { event: "done", data: '{"ok": true}' },
    ]);
  });

  it("data 内容里的冒号不被当作字段分隔", () => {
    expect(parseAll(["event: token\r\ndata: 耗时: 890ms\r\n\r\n"])[0].data).toBe("耗时: 890ms");
  });
});

describe("readSse", () => {
  it("多字节汉字被 chunk 边界切断仍正确解码", async () => {
    const bytes = new TextEncoder().encode(REAL_TOKEN);
    // 逐字节喂：每个汉字的 3 个字节必然跨块
    const events = await collect(streamOf([...bytes].map((b) => new Uint8Array([b]))));
    expect(events).toEqual([{ event: "token", data: "**结论**：时间戳超窗\n\n1. 校准时钟" }]);
  });

  it("按顺序产出整条流的事件", async () => {
    const wire = "event: stage\r\ndata: {}\r\n\r\n" + REAL_META + REAL_TOKEN;
    const events = await collect(streamOf([new TextEncoder().encode(wire)]));
    expect(events.map((e) => e.event)).toEqual(["stage", "meta", "token"]);
  });

  it("空流不产出事件", async () => {
    expect(await collect(streamOf([]))).toEqual([]);
  });
});
