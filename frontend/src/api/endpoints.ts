/**
 * @author: liuqinhe
 */
import type {
  ConversationDetail,
  ConversationSummary,
  DocBody,
  DocMeta,
  DocumentView,
  MetricsData,
  TicketDetail,
  TicketRow,
  TraceDetail,
} from "../types/api";
import api from "./client";

export const listConversations = () =>
  api.get<{ conversations: ConversationSummary[] }>("/conversations").then((r) => r.data.conversations);

export const getConversation = (id: string) =>
  api.get<ConversationDetail>(`/conversations/${id}`).then((r) => r.data);

export const sendCustomerMessage = (id: string, content: string) =>
  api.post(`/conversations/${id}/messages`, { content }).then((r) => r.data);

export const myTickets = () =>
  api.get<{ tickets: TicketRow[] }>("/tickets").then((r) => r.data.tickets);

export const getTicket = (id: string) =>
  api.get<TicketDetail>(`/tickets/${id}`).then((r) => r.data);

export const submitFeedback = (body: {
  conversation_id: string;
  message_id?: string;
  type: "resolved" | "unresolved" | "need_human";
}) => api.post<{ ok: boolean; ticket_id: string | null }>("/feedback", body).then((r) => r.data);

export const listDocuments = () =>
  api.get<{ documents: DocumentView[] }>("/documents").then((r) => r.data.documents);

export const uploadDocument = (file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return api.post<{ doc_id: string; status: string; filename: string }>("/documents", fd).then((r) => r.data);
};

export const deleteDocument = (id: string) =>
  api.delete(`/documents/${id}`).then((r) => r.data);

export const listDocs = () =>
  api.get<{ documents: DocMeta[] }>("/docs").then((r) => r.data.documents);

export const getDoc = (id: string) =>
  api.get<DocBody>(`/docs/${id}`).then((r) => r.data);

export const wbTickets = (params: Record<string, string | undefined>) =>
  api.get<{ tickets: TicketRow[] }>("/workbench/tickets", { params }).then((r) => r.data.tickets);

export const wbTicketDetail = (id: string) =>
  api.get<{
    ticket: TicketDetail & { tenant_id: string };
    conversation_messages: { role: string; content: string; meta?: Record<string, unknown> | null }[];
  }>(`/workbench/tickets/${id}`).then((r) => r.data);

export const wbUpdateTicket = (id: string, body: { status?: string; assignee?: string; note?: string }) =>
  api.post(`/workbench/tickets/${id}`, body).then((r) => r.data);

export const wbSuggestReply = (convId: string) =>
  api.get<{ suggestion: string }>(`/workbench/conversations/${convId}/suggest_reply`).then((r) => r.data);

export const wbReply = (convId: string, content: string) =>
  api.post(`/workbench/conversations/${convId}/reply`, { content }).then((r) => r.data);

export const getTrace = (id: string) =>
  api.get<TraceDetail>(`/traces/${id}`).then((r) => r.data);

export const getMetrics = () =>
  api.get<MetricsData>("/metrics").then((r) => r.data);

export const runEval = () => api.post("/eval/run").then((r) => r.data);
