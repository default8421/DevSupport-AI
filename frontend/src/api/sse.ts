/**
 * @author: liuqinhe
 */

/** 一个完整的 SSE 事件。data 已按规范把多个 data: 行拼回换行。 */
export interface SseEvent {
  event: string;
  data: string;
}

/**
 * SSE 增量解析器。
 *
 * 按 SSE 规范实现，而不是逐行 if/else：
 * - 行终止符是 CRLF、CR、LF 三者之一（sse_starlette 用 CRLF）；
 * - 一个事件内的多个 data: 行在派发时用 "\n" 拼回（后端把含换行的答案拆成多行发出，
 *   旧实现按 "\n" 切行后逐行回调，换行全部退化成 "\r"，markdown 排版因此在流式过程中是乱的）；
 * - 空行才派发事件，冒号开头是注释（心跳）。
 */
export function createSseParser() {
  let buf = "";
  let eventType = "";
  let dataLines: string[] = [];
  let hasData = false;

  const dispatch = (out: SseEvent[]) => {
    // 没有 data 行的事件不派发（规范如此，注释心跳也走这条）
    if (!hasData) {
      eventType = "";
      return;
    }
    out.push({ event: eventType || "message", data: dataLines.join("\n") });
    eventType = "";
    dataLines = [];
    hasData = false;
  };

  const handleLine = (line: string, out: SseEvent[]) => {
    if (line === "") {
      dispatch(out);
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    // 只剥一个前导空格，"data:  x" 的第二个空格属于内容
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventType = value;
    else if (field === "data") {
      dataLines.push(value);
      hasData = true;
    }
  };

  return {
    /** 喂入一块解码后的文本，返回本次凑齐的事件。 */
    push(chunk: string): SseEvent[] {
      const out: SseEvent[] = [];
      buf += chunk;
      // 末尾孤立的 \r 可能是 \r\n 的前半截，留到下一块再判断，否则会多切出一行
      let searchable = buf;
      let tail = "";
      if (searchable.endsWith("\r")) {
        tail = "\r";
        searchable = searchable.slice(0, -1);
      }
      const lines = searchable.split(/\r\n|\r|\n/);
      buf = (lines.pop() ?? "") + tail;
      for (const line of lines) handleLine(line, out);
      return out;
    },

    /** 流结束时调用。宽容处理：末尾缺空行也把攒着的事件吐出来，避免丢掉 done。 */
    flush(): SseEvent[] {
      const out: SseEvent[] = [];
      if (buf) {
        for (const line of buf.split(/\r\n|\r|\n/)) handleLine(line, out);
        buf = "";
      }
      dispatch(out);
      return out;
    },
  };
}

/**
 * 把 fetch 的字节流读成 SSE 事件序列。
 *
 * 解码用 stream 模式，否则被 chunk 边界切断的多字节汉字会变成替换字符。
 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      yield* parser.push(decoder.decode(value, { stream: true }));
    }
    yield* parser.push(decoder.decode());
    yield* parser.flush();
  } finally {
    reader.releaseLock();
  }
}
