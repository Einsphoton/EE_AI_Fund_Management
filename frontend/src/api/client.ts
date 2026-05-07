import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  timeout: 60_000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const msg =
      err?.response?.data?.detail ||
      err?.response?.data?.message ||
      err?.message ||
      "请求失败";
    return Promise.reject(new Error(msg));
  },
);

// ---------- types ----------
export type AssetType = "fund" | "stock";
export type Market = "A" | "HK" | "US" | "OTC";
export type TxnType = "buy" | "sell";

export interface Asset {
  id: number;
  name: string;
  code: string;
  asset_type: AssetType;
  market: Market;
  platform: string;
  note: string;
  watch_only: boolean;
  created_at: string;
  updated_at: string;
}

export interface Transaction {
  id: number;
  asset_id: number;
  txn_type: TxnType;
  shares: number;
  price: number;
  amount: number;
  fee: number;
  trade_date: string | null;
  note: string;
}

export interface QuotePoint {
  date: string;
  open?: number; high?: number; low?: number;
  close: number; volume?: number;
}

export interface Quote {
  code: string; name: string;
  asset_type: AssetType; market: Market;
  points: QuotePoint[];
  current_price: number | null;
  transactions: Transaction[];
  error?: string;
}

export interface Holding {
  asset: Asset;
  total_shares: number;
  total_cost: number;
  avg_cost: number;
  total_fee?: number;
  realized_pnl?: number;
  current_price: number | null;
  market_value: number | null;
  profit: number | null;
  profit_pct: number | null;
}

export interface Snapshot {
  symbol?: string;
  name?: string;
  last?: number | null;
  prev_close?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  change?: number | null;
  change_pct?: number | null;
  amount?: number | null;
  amount_unit?: string;
  turnover?: number | null;
  pe_ttm?: number | null;
  pb?: number | null;
  total_mktcap?: number | null;
  circ_mktcap?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  amplitude?: number | null;
  currency?: string;
  market?: string;
}

export interface Skill {
  id: number;
  skill_id: string;
  name: string;
  description: string;
  category: string;
  source: string;
  enabled: boolean;
  installed_at: string;
}

export interface MarketSkill {
  skill_id: string;
  name: string;
  description: string;
  category: string;
  source: string;
  default?: boolean;
}

export interface AdviceExtra {
  score?: {
    technical?: number;
    fundamental?: number;
    sentiment?: number;
    risk?: number;
  };
  fundamentals?: string;
  macro?: string;
  micro?: string;
  risks?: string[];
  pros?: string[];
  advice?: string;
  time_horizon?: "short" | "mid" | "long" | string;
  target_price?: number | null;
  stop_loss?: number | null;
}

export interface Advice {
  id: number;
  asset_id: number | null;
  batch_id: string;
  source: "batch" | "single";
  action: "buy" | "hold" | "sell";
  confidence: number;
  summary: string;
  detail: string;
  extra?: AdviceExtra | null;
  skill_used: string;
  created_at: string;
}

export interface DcaSuggestion {
  base_amount: number;
  suggest_amount: number;
  suggest_shares: number;
  estimated_fee: number;
  last_price: number | null;
  ma20: number | null;
  ma60: number | null;
  ma250: number | null;
  deviation: number | null;
  price_factor: number;
  trend_factor: number;
  decision: "buy_more" | "buy_normal" | "buy_less" | "skip";
  reason: string;
}

export interface ChatMsg {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface AppSettings {
  ai: {
    base_url: string;
    api_key: string;
    model: string;
    temperature: number;
    /** 批量分析最大并发度（1=串行） */
    batch_concurrency?: number;
    /** 单次 LLM 响应的最大 token 数（0=不限制） */
    max_tokens?: number;
    /** HTTP 超时（秒） */
    timeout?: number;
    /** 投资者性格 id：balanced / conservative / aggressive / income / growth / value / trader */
    investor_profile?: string;
    /** 报告风格 id：pro / beginner */
    report_style?: string;
    cf_access_client_id?: string;
    cf_access_client_secret?: string;
    cf_access_hosts?: string;
  };
  schedule: { enabled: boolean; cron: string; preset: string };
  ui: { currency: string; theme: string };
}

export interface ProfileOption {
  id: string;
  name: string;
  tagline: string;
}

export interface ProfilesResponse {
  investor_profiles: ProfileOption[];
  report_styles: ProfileOption[];
}

// ---------- endpoints ----------
export const Assets = {
  list: () => api.get<Asset[]>("/assets").then((r) => r.data),
  create: (p: any) => api.post<Asset>("/assets", p).then((r) => r.data),
  update: (id: number, p: any) => api.patch<Asset>(`/assets/${id}`, p).then((r) => r.data),
  remove: (id: number) => api.delete(`/assets/${id}`).then((r) => r.data),
  txns: (id: number) => api.get<Transaction[]>(`/assets/${id}/transactions`).then((r) => r.data),
  addTxn: (id: number, p: any) => api.post<Transaction>(`/assets/${id}/transactions`, p).then((r) => r.data),
  updateTxn: (id: number, txnId: number, p: any) =>
    api.patch<Transaction>(`/assets/${id}/transactions/${txnId}`, p).then((r) => r.data),
  removeTxn: (id: number, txnId: number) => api.delete(`/assets/${id}/transactions/${txnId}`).then((r) => r.data),
  holdings: () => api.get<Holding[]>("/assets/summary/all").then((r) => r.data),
};

export const Quotes = {
  byAsset: (id: number, days = 365) =>
    api.get<Quote>(`/quotes/asset/${id}`, { params: { days } }).then((r) => r.data),
  snapshot: (id: number) =>
    api.get<Snapshot>(`/quotes/asset/${id}/snapshot`).then((r) => r.data),
  raw: (params: { code: string; asset_type: AssetType; market: Market; days?: number }) =>
    api.get("/quotes/raw", { params }).then((r) => r.data),
};

export const Settings = {
  getAll: () => api.get<AppSettings>("/settings").then((r) => r.data),
  getProfiles: () => api.get<ProfilesResponse>("/settings/profiles").then((r) => r.data),
  put: (key: string, value: any) => api.put(`/settings/${key}`, { value }).then((r) => r.data),
  testAi: (
    base_url: string,
    api_key: string,
    model: string,
    cf?: { cf_access_client_id?: string; cf_access_client_secret?: string; cf_access_hosts?: string },
  ) =>
    api.post<{
      ok: boolean;
      endpoint?: string;
      models?: string[];
      model_exists?: boolean;
      hint?: string;
      error?: string;
    }>("/settings/test-ai", {
      base_url,
      api_key,
      model,
      cf_access_client_id: cf?.cf_access_client_id || "",
      cf_access_client_secret: cf?.cf_access_client_secret || "",
      cf_access_hosts: cf?.cf_access_hosts || "",
    }).then((r) => r.data),
};

export const Skills = {
  installed: () => api.get<Skill[]>("/skills/installed").then((r) => r.data),
  marketplace: (q = "") =>
    api.get<{ items: MarketSkill[] }>("/skills/marketplace", { params: { q } }).then((r) => r.data.items),
  install: (p: any) => api.post<Skill>("/skills/install", p).then((r) => r.data),
  uninstall: (skillId: string) => api.delete(`/skills/${skillId}`).then((r) => r.data),
  toggle: (skillId: string, enabled: boolean) =>
    api.post(`/skills/${skillId}/toggle`, null, { params: { enabled } }).then((r) => r.data),
};

export const AdviceApi = {
  /** 列出最近建议。source 可选：batch（仅批量）/ single（仅单独）/ 不传（全部）。 */
  recent: (limit = 200, source?: "batch" | "single") =>
    api.get<Advice[]>("/advice", { params: { limit, ...(source ? { source } : {}) } }).then((r) => r.data),
  byAsset: (id: number) => api.get<Advice[]>(`/advice/asset/${id}`).then((r) => r.data),
  runOne: (id: number) => api.post<Advice>(`/advice/run/${id}`).then((r) => r.data),
  runAll: () => api.post<{ analyzed: number }>("/advice/run-all").then((r) => r.data),
};

// ---- AI 批量分析 SSE 事件类型 ----
export type RunAllEvent =
  | {
      type: "start";
      batch_id: string;
      total: number;
      /** 服务端使用的并发度（后端 settings.ai.batch_concurrency） */
      concurrency?: number;
      assets: { id: number; name: string; code: string; market: string }[];
    }
  | { type: "asset_start"; asset_id: number; name: string; code: string; index: number; total: number }
  | {
      type: "log";
      text: string;
      /** 并发模式下用于把日志归类到具体标的（可能为空表示全局日志） */
      asset_id?: number;
      name?: string;
    }
  | {
      type: "asset_done";
      asset_id: number; name: string; code: string; index: number; total: number;
      advice_id: number; action: "buy" | "hold" | "sell"; confidence: number;
      summary: string; skill_used: string;
    }
  | { type: "asset_error"; asset_id: number; name: string; code: string; index: number; total: number; error: string }
  | { type: "done"; batch_id: string; analyzed: number; failed: number }
  | { type: "fatal"; error: string };

/** 实时获取 /advice/run-all/stream 的进度事件流。 */
export async function runAllStream(
  onEvent: (evt: RunAllEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/advice/run-all/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`run-all stream failed: ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const evt = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!evt.startsWith("data:")) continue;
      const payload = evt.slice(5).trim();
      try {
        const obj = JSON.parse(payload) as RunAllEvent;
        onEvent(obj);
      } catch {
        // 容错：忽略无效数据块
      }
    }
  }
}

export const DcaApi = {
  suggest: (id: number, base = 1000, fee_rate = 0.001) =>
    api.get<DcaSuggestion>(`/dca/suggest/${id}`, { params: { base, fee_rate } }).then((r) => r.data),
};

/** Streaming chat via fetch + SSE-like reader. */
export async function chatStream(
  messages: ChatMsg[],
  onToken: (t: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`chat failed: ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const evt = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (!evt.startsWith("data:")) continue;
      const payload = evt.slice(5).trim();
      if (payload === "[DONE]") return;
      try {
        const text = JSON.parse(payload);
        if (typeof text === "string") onToken(text);
      } catch {
        // 容错：原样输出
        onToken(payload);
      }
    }
  }
}
