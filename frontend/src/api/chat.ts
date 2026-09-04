/**
 * @author: liuqinhe
 */
import type { ChatDone, ChatError, ChatMeta, StageEvent } from "../types/api";
import { readSse } from "./sse";

export async function chatStream(
  message: string,
  conversationId: string | null,
  handlers: {
    onStage?: (s: StageEvent) => void;
    onMeta?: (m: ChatMeta) => void;
    onToken?: (t: string) => void;
    onDone?: (d: ChatDone) => void;
    onError?: (e: ChatError) => void;
  },
) {
  const resp = await fetch(`${import.meta.env.BASE_URL}api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (resp.status === 401) {
    throw new Error("登录已过期");
  }
  if (!resp.ok) {
    throw new Error(`对话失败（${resp.status}）`);
  }
  if (!resp.body) throw new Error("服务端未返回流式响应");
  for await (const { event, data } of readSse(resp.body)) {
    if (event === "stage") handlers.onStage?.(JSON.parse(data) as StageEvent);
    else if (event === "meta") handlers.onMeta?.(JSON.parse(data) as ChatMeta);
    else if (event === "token") handlers.onToken?.(data);
    else if (event === "done") handlers.onDone?.(JSON.parse(data) as ChatDone);
    else if (event === "error") handlers.onError?.(JSON.parse(data) as ChatError);
  }
}
