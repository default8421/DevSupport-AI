/**
 * @author: liuqinhe
 */
import type { StageEvent } from "../types/api";

/** 同一 key 原地更新，避免 running / success 变成两行。 */
export function mergeStages(prev: StageEvent[], evt: StageEvent): StageEvent[] {
  const i = prev.findIndex((p) => p.key === evt.key);
  if (i === -1) return [...prev, evt].sort((a, b) => a.order - b.order);
  const next = prev.slice();
  next[i] = evt;
  return next;
}

export function toThoughtStatus(status: StageEvent["status"]): "loading" | "success" | "error" {
  return status === "running" ? "loading" : status;
}
