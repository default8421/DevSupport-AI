/**
 * @author: liuqinhe
 */
export interface UserInfo {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  tenant_id: string;
  tenant_name?: string;
}

export const isInternal = (role: string) => role === "support" || role === "admin";

export interface StageEvent {
  key: string;
  label: string;
  status: "running" | "success" | "error";
  order: number;
  duration_ms?: number;
}

export interface Citation {
  index?: number;
  doc_title: string;
  section?: string;
  score?: number;
  source?: "builtin" | "upload" | string;
}

export interface DiagnosisCardData {
  conclusion: string;
  evidence: string[];
  steps: string[];
}

export interface ChatMeta {
  conversation_id: string;
  message_id: string;
  intent?: string | null;
  trace_id?: string | null;
}

export interface ChatDone {
  answer: string;
  card: DiagnosisCardData | null;
  citations: Citation[];
  ticket_id?: string | null;
  need_human?: boolean;
  need_clarify?: boolean;
  from_cache?: boolean;
  trace_id?: string | null;
  human_mode?: boolean;
}

export interface ChatError {
  message: string;
  conversation_id?: string;
}

export interface ConversationSummary {
  id: string;
  tenant_id: string;
  status: string;
  latest_intent: string | null;
  transferred_to_human: boolean;
  satisfaction: string | null;
  updated_at: string;
}

export interface MessageMeta {
  intent?: string | null;
  citations?: Citation[];
  card?: DiagnosisCardData | null;
  trace_id?: string | null;
  ticket_id?: string | null;
  need_human?: boolean;
  from_cache?: boolean;
  by?: string;
  agent_name?: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  meta?: MessageMeta | null;
  created_at: string;
}

export interface ConversationDetail {
  conversation: {
    id: string;
    tenant_id: string;
    status: string;
    latest_intent: string | null;
    transferred_to_human: boolean;
    satisfaction: string | null;
  };
  messages: ConversationMessage[];
}

export interface DocumentView {
  id: string;
  title: string;
  category: string;
  status: string;
  source: string;
  chunk_count: number;
  size_bytes: number | null;
  original_filename: string | null;
  error_message: string | null;
  updated_at: string | null;
  deletable: boolean;
}

export interface TicketRow {
  ticket_id: string;
  title: string;
  category: string;
  priority: string;
  status: string;
  error_code?: string | null;
  created_at: string;
  tenant_id?: string;
}

export interface TicketDetail {
  ticket_id: string;
  title: string;
  category: string;
  priority: string;
  status: string;
  summary?: string | null;
  related_request_ids?: string[] | null;
  related_endpoint?: string | null;
  error_code?: string | null;
  ai_diagnosis?: string | null;
  evidence?: unknown;
  assignee?: string | null;
  conversation_id?: string | null;
  created_at: string;
}

export interface DocMeta {
  id: string;
  title: string;
  category: string;
  filename: string;
}

export interface DocBody extends DocMeta {
  content: string;
}

export interface TraceStep {
  step_order: number;
  agent_name: string;
  status: string;
  duration_ms: number;
  token_usage: number;
  input_summary?: string | null;
  output_summary?: string | null;
  hit_docs?: string[] | null;
  error_message?: string | null;
}

export interface ToolCall {
  tool_name: string;
  status: string;
  duration_ms: number;
  args_summary?: string | null;
  result_summary?: string | null;
  error_message?: string | null;
}

export interface TraceDetail {
  trace_id: string;
  conversation_id: string;
  tenant_id: string;
  total_duration_ms: number;
  total_tokens: number;
  steps: TraceStep[];
  tool_calls: ToolCall[];
}

export interface MetricsData {
  conversations: {
    total: number;
    resolved_by_ai: number;
    transferred_to_human: number;
    ai_resolution_rate: number;
  };
  intent_distribution: Record<string, number>;
  tickets: {
    by_status: Record<string, number>;
    by_priority: Record<string, number>;
  };
  token_cost_by_tenant: { tenant_id: string; turns: number; total_tokens: number }[];
}
