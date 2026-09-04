/**
 * @author: liuqinhe
 */

/** 把后端 isoformat 收成 `YYYY-MM-DD HH:MM:SS`，缺字段返回空串。 */
export function formatTime(iso?: string | null): string {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 19);
}

/** 会话列表分组：今天 / 昨天 / 更早。now 可注入，便于测试。 */
export function conversationGroup(iso?: string | null, now = new Date()): string {
  if (!iso) return "更早";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "更早";
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (d >= start) return "今天";
  const yesterday = new Date(start);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d >= yesterday) return "昨天";
  return "更早";
}
