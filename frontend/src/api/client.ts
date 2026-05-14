import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  timeout: 60_000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // FastAPI 在不同失败下 detail 形态不同：
    //   - HTTPException → string
    //   - Pydantic 422 → list[{loc, msg, type, ...}]
    //   - 自定义异常处理器 → object
    // axios 拦截器以前直接 `new Error(detail)`，遇到 list/object 会拼成 "[object Object]"，
    // 用户看到这种没法排查；这里做归一化，尽量挤出能读懂的字符串。
    const data = err?.response?.data;
    const fmt = (d: any): string => {
      if (d == null) return "";
      if (typeof d === "string") return d;
      if (Array.isArray(d)) {
        // pydantic ValidationError 形态：[{loc:['body','items',0,'asset_type'], msg:'...', type:'...'}]
        return d.map((it) => {
          if (it && typeof it === "object") {
            const loc = Array.isArray(it.loc) ? it.loc.join(".") : it.loc;
            const msg = it.msg || it.message || JSON.stringify(it);
            return loc ? `${loc}: ${msg}` : String(msg);
          }
          return String(it);
        }).join("; ");
      }
      if (typeof d === "object") {
        if (typeof d.detail !== "undefined") return fmt(d.detail);
        if (typeof d.message === "string") return d.message;
        if (typeof d.error === "string") return d.error;
        try {
          return JSON.stringify(d);
        } catch {
          return String(d);
        }
      }
      return String(d);
    };
    const msg =
      fmt(data?.detail) ||
      fmt(data?.message) ||
      fmt(data) ||
      err?.message ||
      "请求失败";
    return Promise.reject(new Error(msg));
  },
);

// ---------- types ----------
export type AssetType = "fund" | "stock" | "etf" | "money_fund" | "wealth" | "cash" | "bond";
export type Market = "A" | "HK" | "US" | "OTC" | "CNY" | "USD" | "HKD";
export type TxnType = "buy" | "sell";

export interface Asset {
  id: number;
  user_id?: number | null;
  name: string;

  code: string;
  asset_type: AssetType;
  market: Market;
  platform: string;
  note: string;
  watch_only: boolean;
  target_source?: "manual" | "ai" | string;
  // 理财/货基/现金/债券扩展字段
  yield_7d?: number | null;
  expected_apr?: number | null;
  start_date?: string | null;
  maturity_date?: string | null;
  principal_amount?: number | null;
  is_principal_guaranteed?: boolean;
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

export interface RealizedPnlItem {
  transaction_id: number;
  asset_id: number;
  asset_name: string;
  asset_code: string;
  asset_type: AssetType;
  market: Market;
  platform: string;
  operation: string;
  trade_date: string | null;
  shares: number;
  sell_price: number;
  avg_cost: number;
  sell_amount: number;
  fee: number;
  realized_pnl: number;
  note: string;
}

export interface RealizedPnlResponse {
  total: number;
  count: number;
  items: RealizedPnlItem[];
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

export interface DividendItem {
  date?: string | null;
  cash_dividend?: number | null;
  nav?: number | null;
  record_date?: string | null;
  ex_dividend_date?: string | null;
  raw?: Record<string, any>;
}

export interface AssetFundamentals {
  asset_type: AssetType | string;
  market: Market | string;
  code: string;
  name?: string;
  platform?: string;
  stats?: {
    latest_price?: number | null;
    latest_date?: string | null;
    high_52w?: number | null;
    low_52w?: number | null;
    history_count?: number;
    source?: string;
  };
  dividends?: {
    source?: string;
    symbol?: string;
    items?: DividendItem[];
    total_count?: number;
    total_cash_dividend?: number | null;
    trailing_12m_cash_dividend?: number | null;
    dividend_yield_pct?: number | null;
    last_date?: string | null;
    error?: string;
  };
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
  /** 本条建议如何按当前投资者性格做了仓位/节奏/止盈止损优化。 */
  profile_note?: string;
  /** 生成该建议时使用的投资者性格。 */
  investor_profile?: { id?: string; name?: string; tagline?: string } | string;
  fundamentals?: string;
  macro?: string;
  micro?: string;
  risks?: string[];
  pros?: string[];
  advice?: string;
  /** AI 深度点评：自由发挥的 Markdown 长文，是分析报告的主观核心。 */
  commentary?: string;
  /** 是否复用了近期 AI 分析结果以节省 token。 */
  reused?: boolean;
  reused_from_advice_id?: number;
  reuse_reason?: string;
  cost_mode?: string;
  /** 本次资产分析实际使用的 AI Provider 名称。 */

  provider_used?: string;
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

export interface TodoItem {
  id: number;
  todo_type: "dca_due" | string;
  status: "pending" | "accepted" | "rejected" | string;
  asset_id: number | null;
  title: string;
  description: string;
  action: "buy" | "sell" | "hold" | "skip" | string;
  payload: Record<string, any>;
  result: Record<string, any>;
  due_date: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  asset?: Asset | null;
}

export interface TodoResolvePayload {
  decision: "accept" | "reject";
  shares?: number;
  price?: number;
  fee?: number;
  trade_date?: string;
  note?: string;
}

export interface BudgetStatusItem {
  platform: string;
  currency: string;
  monthly_amount: number;
  used_this_month: number;
  remaining_budget: number;
  asset_types?: AssetType[];
}

export interface InvestmentManagerRunResult {
  summary: string;
  created: number;
  budget_status: BudgetStatusItem[];
  todos: TodoItem[];
}

export interface BudgetStatusResponse {
  items: BudgetStatusItem[];
}

export interface ChatMsg {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface InvestmentBudgetItem {
  platform: string;
  currency: "CNY" | "HKD" | "USD" | string;
  monthly_amount: number;
  /** 该预算允许购买的资产类型：fund=场外基金，stock/etf=股票/场内基金。 */
  asset_types?: AssetType[];
}

export interface AIProviderConfig {
  id: string;
  name: string;
  enabled?: boolean;
  base_url?: string;
  api_key?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  timeout?: number;
  rpm_limit?: number;
  min_interval_sec?: number;
  nim_optimization_enabled?: boolean;
  thinking_mode?: "auto" | "on" | "off";
  thinking_budget?: number;
  reasoning_effort?: "minimal" | "low" | "medium" | "high" | string;
  weight?: number;
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
    /**
     * 每分钟最大请求数（滑动窗口）。0 = 不限。
     * NVIDIA NIM 免费 Kimi K2 ≈ 40，DeepSeek/通义 ≈ 60。设官方上限的 85%。
     */
    rpm_limit?: number;
    /** 相邻两次请求最小硬间隔（秒）。已被 rpm_limit 覆盖大多数场景；兜底用。 */
    min_interval_sec?: number;
    /** NIM 友好优化：通过全局排队和 token 预算片平滑限流，不裁剪模型能力。 */
    nim_optimization_enabled?: boolean;
    /** AI 成本控制：quality=质量优先 / balanced=均衡省钱 / economy=极省钱 */
    cost_mode?: "quality" | "balanced" | "economy" | string;
    /** 强制 JSON Mode，减少废话和 JSON 解析失败重试。 */
    json_mode?: boolean;
    /** 记录模型返回的 token usage/cache usage 日志。 */
    token_usage_logging?: boolean;
    /** 投资者性格 id：balanced / conservative / aggressive / income / growth / value / trader */


    investor_profile?: string;
    /** 报告风格 id：pro / beginner */
    report_style?: string;
    cf_access_client_id?: string;
    cf_access_client_secret?: string;
    cf_access_hosts?: string;
    /** 思考模式：auto / on / off （兼容 DeepSeek V4 / Qwen3.5 / GLM-5 / Claude / GPT-5 等） */
    thinking_mode?: "auto" | "on" | "off";
    /** 思考 token 预算（0=不限制） */
    thinking_budget?: number;
    /** OpenAI o-series / GPT-5 / Kimi 等的强度参数：minimal / low / medium / high */
    reasoning_effort?: "minimal" | "low" | "medium" | "high" | string;
    /** 批量资产分析是否把上方主配置也纳入 Provider 池 */
    pool_include_primary?: boolean;
    pool_primary_name?: string;
    /** 额外 AI Provider/API Key 池；批量资产分析会轮询并在失败时切换 */
    providers?: AIProviderConfig[];
  };

  /** 多模态视觉模型（用于 OCR 截图导入） */
  vision?: {
    /** 复用 AI 大模型配置（开启后下面字段都忽略，直接走 ai 配置） */
    use_ai?: boolean;
    base_url: string;
    api_key: string;
    model: string;
    temperature?: number;
    max_tokens?: number;
    timeout?: number;
    concurrency?: number;
    /** 两张图之间最小间隔（秒），用于规避 RPM 限流；Kimi 免费档建议 25-30 */
    /** 两张图之间最小间隔（秒）。已被 rpm_limit 覆盖大多数场景；保留作为兜底（次要）。 */
    min_interval_sec?: number;
    /**
     * 每分钟最大请求数（滑动窗口算法）。0 = 不限。
     * NVIDIA NIM 免费档 Kimi K2 ≈ 40，Kimi 官方免费 3，阿里 Qwen-VL ≈ 60。
     * 推荐设成官方上限的 85%（留余量给重试），如 40 → 35。
     */
    rpm_limit?: number;
    /** 强制 JSON Mode（response_format=json_object），Kimi/GLM/Qwen-VL 都支持；不支持时自动降级 */
    json_mode?: boolean;
    /** OCR 后自动用多源行情库补全基金/股票/ETF 代码和交易所 */
    auto_fill_code?: boolean;
    /** 单图墙钟硬超时（秒） */
    wall_timeout?: number;
    /** 单图输出字符硬上限 */
    content_hardcap?: number;
    /** 是否请求流式输出 */
    stream?: boolean;
    /** 双开关：只有 stream 和 force_stream 都开才真正走流式 */
    force_stream?: boolean;
  };

  schedule: { enabled: boolean; cron: string; preset: string; include_investment_plan?: boolean; include_ai_targets?: boolean };
  investment_budget?: { items: InvestmentBudgetItem[] };
  quote_sources?: {
    fund_current?: "eastmoney_realtime" | "eastmoney_nav" | string;
    stock_current?: "tencent_realtime" | "kline_close" | string;
    a_stock_kline?: "sina" | "tencent" | string;
    hk_stock_kline?: "tencent" | string;
    us_stock_kline?: "tencent" | "yahoo" | string;
    fallback_enabled?: boolean;
  };
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

export interface AuthUser {
  id: number;
  username: string;
  email?: string | null;
}

export interface AuthResponse {
  token: string;
  user: AuthUser;
}

// ---------- endpoints ----------
export const AuthApi = {
  register: (username: string, password: string, email?: string) =>
    api.post<AuthResponse>("/auth/register", { username, password, email: email || undefined }).then((r) => r.data),
  login: (username: string, password: string) =>
    api.post<AuthResponse>("/auth/login", { username, password }).then((r) => r.data),
  me: () => api.get<AuthUser>("/auth/me").then((r) => r.data),
};

export const Assets = {

  list: () => api.get<Asset[]>("/assets").then((r) => r.data),
  get: (id: number) => api.get<Asset>(`/assets/${id}`).then((r) => r.data),
  holding: (id: number) => api.get<Holding>(`/assets/${id}/summary`).then((r) => r.data),
  create: (p: any) => api.post<Asset>("/assets", p).then((r) => r.data),
  aiTargets: (limit = 5) => api.post<Asset[]>("/assets/ai-targets", null, { params: { limit } }).then((r) => r.data),
  update: (id: number, p: any) => api.patch<Asset>(`/assets/${id}`, p).then((r) => r.data),
  remove: (id: number) => api.delete(`/assets/${id}`).then((r) => r.data),
  txns: (id: number) => api.get<Transaction[]>(`/assets/${id}/transactions`).then((r) => r.data),
  addTxn: (id: number, p: any) => api.post<Transaction>(`/assets/${id}/transactions`, p).then((r) => r.data),
  updateTxn: (id: number, txnId: number, p: any) =>
    api.patch<Transaction>(`/assets/${id}/transactions/${txnId}`, p).then((r) => r.data),
  removeTxn: (id: number, txnId: number) => api.delete(`/assets/${id}/transactions/${txnId}`).then((r) => r.data),
  holdings: () => api.get<Holding[]>("/assets/summary/all").then((r) => r.data),
  realizedPnl: () => api.get<RealizedPnlResponse>("/assets/realized-pnl").then((r) => r.data),
  /**
   * 智能补全资产缺失字段（首要用途：补 fund/etf 类代码）。

   * 先打天天基金 API，没结果再让 LLM 兜底。
   * apply=false 仅返回建议、不改库；用户确认后再调一次 apply=true。
   */
  enrich: (
    id: number,
    opts?: { fields?: string[]; apply?: boolean; useLLM?: boolean },
  ) =>
    api.post<EnrichResult>(`/assets/${id}/enrich`, null, {
      params: {
        ...(opts?.fields ? { fields: opts.fields.join(",") } : {}),
        apply: opts?.apply !== false,
        use_llm_fallback: opts?.useLLM !== false,
      },
      timeout: 60_000, // LLM 兜底可能需要几十秒
    }).then((r) => r.data),
  /**
   * 无状态代码查询（不依赖已存在的 asset）。
   * 用于 OCR 对账表里实时补全：用户编辑名字 → 一键查代码 → 填回输入框。
   *
   * 重要：**走独立 prefix `/api/enrich/fund-code`**，而不是 `/api/assets/lookup-code`。
   * 历史上用 `/api/assets/lookup-code` 在生产部署里会遇到 405 Method Not Allowed —
   * 因为 `/api/assets/{id}` 动态路由 / SPA fallback catch-all 会在某些 reload 情况下
   * 把 POST 吞掉。换成独立的 `/api/enrich` 前缀根治此问题。
   *
   * useLLM 默认 false：天天基金 API 没结果时，普通 LLM（不带联网）瞎猜
   * 反而误导用户，且 reasoning 模型可能陷入复读循环。仅在用户明确要求时启用。
   */
  lookupCode: (name: string, asset_type: AssetType = "fund", useLLM = false) =>
    api.post<{ ok: boolean; suggestion?: EnrichSuggestion; reason?: string }>(
      `/enrich/fund-code`,
      null,
      { params: { name, asset_type, use_llm_fallback: useLLM }, timeout: 30_000 },
    ).then((r) => r.data),
};

export interface EnrichSuggestion {
  value?: string;
  code?: string;
  matched_name?: string;
  score: number;
  source: "eastmoney" | "llm-fallback" | string;
  asset_type?: AssetType;
  market?: Market;
  exchange?: string;
  alternates?: { code: string; name: string; score: number; asset_type?: AssetType; market?: Market; exchange?: string }[];
}


export interface EnrichResult {
  ok: boolean;
  asset_id: number;
  updated: string[];
  suggestions: Record<string, EnrichSuggestion>;
  before?: Record<string, any>;
  after?: Record<string, any>;
  skipped_reason?: string;
  error?: string;
}

export const Quotes = {
  byAsset: (id: number, days = 365) =>
    api.get<Quote>(`/quotes/asset/${id}`, { params: { days } }).then((r) => r.data),
  snapshot: (id: number) =>
    api.get<Snapshot>(`/quotes/asset/${id}/snapshot`).then((r) => r.data),
  fundamentals: (id: number) =>
    api.get<AssetFundamentals>(`/quotes/asset/${id}/fundamentals`).then((r) => r.data),
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

export interface UpdateStatus {
  current_version: string;
  current_revision: string;
  build_date: string;
  image: string;
  dockerhub_repo: string;
  latest_version?: string;
  latest_updated_at?: string;
  latest_digest?: string;
  source?: string;
  checked_repo?: string;
  update_available: boolean | null;
  check_error: string;
  message: string;
  watchtower_url: string;
  watchtower_configured: boolean;
  web_update_enabled: boolean;
  confirm_text: string;
}

export interface TriggerUpdateResult {
  ok: boolean;
  status_code: number;
  message: string;
  watchtower_response?: string;
}

export const UpdateApi = {
  status: () => api.get<UpdateStatus>("/update/status", { timeout: 15_000 }).then((r) => r.data),
  trigger: (confirm: string) =>
    api.post<TriggerUpdateResult>("/update/trigger", { confirm }, { timeout: 180_000 }).then((r) => r.data),
};

export interface LogFileInfo {
  name: string;
  size: number;
  modified_at: number;
}

export interface LogsListResponse {
  log_dir: string;
  files: LogFileInfo[];
  active_user_id: number;
}

export const LogsApi = {
  list: () => api.get<LogsListResponse>("/logs", { timeout: 15_000 }).then((r) => r.data),
  tail: (name = "ai.log", lines = 300) =>
    api.get<string>("/logs/tail", { params: { name, lines }, responseType: "text", timeout: 15_000 }).then((r) => r.data),
  downloadUrl: (name: string) => `/api/logs/download?name=${encodeURIComponent(name)}`,
  bundleUrl: () => "/api/logs/bundle",
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
  /** 列出最近若干个完整批次，避免按行截断导致新批次影响旧批次显示。 */
  recentBatches: (batchLimit = 20) =>
    api.get<Advice[]>("/advice", { params: { limit: batchLimit, source: "batch", complete_batches: true } }).then((r) => r.data),
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
  const token = localStorage.getItem("ee_auth_token") || "";
  const resp = await fetch("/api/advice/run-all/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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
  createTodo: (id: number, base = 1000, fee_rate = 0.001) =>
    api.post<TodoItem>(`/dca/todo/${id}`, null, { params: { base, fee_rate } }).then((r) => r.data),
};

export const TodoApi = {
  list: (status: "pending" | "accepted" | "rejected" | "all" = "pending") =>
    api.get<TodoItem[]>("/todos", { params: { status } }).then((r) => r.data),
  budgetStatus: () =>
    api.get<BudgetStatusResponse>("/todos/budget-status").then((r) => r.data),
  runAiInvestmentPlan: () =>
    api.post<InvestmentManagerRunResult>("/todos/ai-investment-plan").then((r) => r.data),
  resolve: (id: number, payload: TodoResolvePayload) =>
    api.post<TodoItem>(`/todos/${id}/resolve`, payload).then((r) => r.data),
};

// =================== 危险操作（清库）===================

export interface WipeResult {
  ok: boolean;
  include_settings: boolean;
  total_rows_deleted: number;
  deleted: Record<string, number>;
  message: string;
}

export const Admin = {
  /**
   * 清空数据库。**高危操作**。
   *
   * @param includeSettings false=只清业务数据（资产/交易/快照/AI 建议）
   *                        true=连同 AI 配置 / Skills 元数据一起清
   *
   * 后端要求显式传 `confirm` 字符串，避免误调；前端在用户输入"DELETE"
   * 通过 modal 确认后才会真正发出。
   */
  wipeAll: (includeSettings = false) =>
    api.post<WipeResult>("/admin/wipe-all", null, {
      params: {
        confirm: "I_UNDERSTAND_DELETE_EVERYTHING",
        include_settings: includeSettings,
      },
      timeout: 60_000,
    }).then((r) => r.data),

  /**
   * 导出资产数据为文件。直接触发浏览器下载，不返回数据。
   *
   * @param format  "json" = 完整备份（含交易 + 快照，可用于恢复）；
   *                "csv"  = 资产扁平表（Excel 友好，不含交易快照）
   * @param includeSnapshots 仅对 json 有意义；默认包含
   */
  exportDownload: (format: "json" | "csv" = "json", includeSnapshots = true, includeSettings = false): void => {
    const qs = new URLSearchParams({
      format,
      include_snapshots: String(includeSnapshots),
      include_settings: String(includeSettings),
    });

    // 直接用 <a download> 下载，而不是 axios blob 再 createObjectURL——
    // 前者让浏览器原生决定保存位置，还能看到进度；后者会被内存占用
    const a = document.createElement("a");
    a.href = `/api/admin/export?${qs.toString()}`;
    // 服务端 Content-Disposition 已经带了合理 filename，这里不再覆盖
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  /** 单独导出交易流水 CSV（扁平，便于筛选）。 */
  exportTransactionsDownload: (): void => {
    const a = document.createElement("a");
    a.href = `/api/admin/export/transactions.csv`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  /**
   * 从 JSON 文件导入数据。
   *
   * @param file   前端选择的 File 对象（必须是 .json 备份）
   * @param opts.mode  merge(默认) / replace(清空重建，需二次确认) / skip(只补新)
   * @param opts.includeTransactions / includeSnapshots 是否导入子表
   */
  importData: (
    file: File,
    opts: {
      mode?: "merge" | "replace" | "skip";
      includeTransactions?: boolean;
      includeSnapshots?: boolean;
      includeSettings?: boolean;
    } = {},
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", opts.mode || "merge");
    fd.append("include_transactions", String(opts.includeTransactions !== false));
    fd.append("include_snapshots", String(opts.includeSnapshots !== false));
    fd.append("include_settings", String(!!opts.includeSettings));
    if (opts.mode === "replace") {
      fd.append("confirm", "I_UNDERSTAND_REPLACE_ALL");
    }
    return api.post<ImportResult>("/admin/import", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120_000,
    }).then((r) => r.data);
  },

};

export interface ImportResult {
  ok: boolean;
  assets_created: number;
  assets_updated: number;
  assets_skipped: number;
  transactions_added: number;
  snapshots_added: number;
  settings_imported?: number;
  skills_imported?: number;
  errors: string[];

  replaced_counts?: Record<string, number>;
}

/** Streaming chat via fetch + SSE-like reader. */
export async function chatStream(
  messages: ChatMsg[],
  onToken: (t: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem("ee_auth_token") || "";
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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

// =================== OCR 批量导入 ===================

export interface OcrCandidate {
  asset_id: number;
  name: string;
  code: string;
  asset_type: AssetType;
  platform: string;
  match_score: number;
}

export interface OcrSuggestion {
  action: "create" | "append_buy" | "append_sell" | "skip" | "update_field";
  delta_shares?: number;
  delta_amount?: number;
  reason: string;
}

export interface OcrItem {
  name: string | null;
  code: string | null;
  asset_type: AssetType;
  market?: Market | null;
  exchange?: string | null;
  shares: number | null;

  amount: number | null;
  avg_cost: number | null;
  current_price: number | null;
  market_value: number | null;
  profit: number | null;
  profit_pct: number | null;
  yield_7d: number | null;
  expected_apr: number | null;
  maturity_date: string | null;
  raw_text?: string;
  _candidates?: OcrCandidate[];
  _suggestion?: OcrSuggestion;
}

export interface OcrParseResult {
  file: string;
  platform: string;
  screenshot_date: string | null;
  items: OcrItem[];
  error?: string;
}

export interface OcrCommitItem {
  action: "create" | "append_buy" | "append_sell" | "update_field" | "skip";
  asset_id?: number | null;
  name?: string;
  code?: string;
  asset_type?: AssetType;
  market?: Market;
  exchange?: string | null;
  platform?: string;

  note?: string;
  yield_7d?: number | null;
  expected_apr?: number | null;
  start_date?: string | null;
  maturity_date?: string | null;
  principal_amount?: number | null;
  is_principal_guaranteed?: boolean;
  shares?: number | null;
  delta_shares?: number | null;
  delta_amount?: number | null;
  avg_cost?: number | null;
  current_price?: number | null;
  market_value?: number | null;
  profit?: number | null;
  profit_pct?: number | null;
  snapshot_date?: string | null;
  raw?: unknown;
}

export const ImportApi = {
  parse: async (files: File[], platformHint = ""): Promise<{ results: OcrParseResult[]; total: number }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    if (platformHint) fd.append("platform_hint", platformHint);
    const r = await api.post("/import/ocr/parse", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 600_000, // 视觉模型可能慢，10 分钟
    });
    return r.data;
  },
  /** 异步任务版：立即返回 job_id，后台跑视觉模型 */
  start: async (files: File[], platformHint = ""): Promise<{ job_id: string; snapshot: OcrJobSnapshot }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    if (platformHint) fd.append("platform_hint", platformHint);
    const r = await api.post("/import/ocr/start", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60_000,
    });
    return r.data;
  },
  getJob: async (jobId: string): Promise<{
    snapshot: OcrJobSnapshot;
    events: (OcrJobEvent & { ts: number })[];
    result: { results: OcrParseResult[]; total: number } | null;
  }> => {
    const r = await api.get(`/import/ocr/jobs/${jobId}`, { timeout: 30_000 });
    return r.data;
  },
  listJobs: async (limit = 10): Promise<{ items: OcrJobSnapshot[] }> => {
    const r = await api.get(`/import/ocr/jobs`, { params: { limit }, timeout: 10_000 });
    return r.data;
  },
  cancelJob: async (jobId: string): Promise<{ ok: boolean; status: string; already_finished?: boolean }> => {
    const r = await api.post(`/import/ocr/jobs/${jobId}/cancel`, null, { timeout: 10_000 });
    return r.data;
  },
  /**
   * 导入 portfolio-ocr Skill 产物的 JSON 文件，跳过视觉模型；
   * 返回结构与 parse 完全一致，前端可直接复用对账表。
   */
  importJson: async (
    files: File[],
    platformHint = "",
  ): Promise<{ results: OcrParseResult[]; total: number }> => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    if (platformHint) fd.append("platform_hint", platformHint);
    const r = await api.post("/import/ocr/import-json", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 30_000,
    });
    return r.data;
  },
  commit: async (items: OcrCommitItem[]): Promise<{ created: number; appended: number; skipped: number; errors: string[] }> => {
    const r = await api.post("/import/ocr/commit", { items }, { timeout: 60_000 });
    return r.data;
  },
};

// ---- OCR 异步任务事件 ----

export interface OcrJobSnapshot {
  job_id: string;
  status: "pending" | "parsing" | "done" | "error" | "cancelled";
  total: number;
  finished: number;
  platform_hint: string;
  file_names: string[];
  error: string | null;
  created_at: number;
  finished_at: number | null;
  has_result: boolean;
  cancelled?: boolean;
}

export type OcrJobEvent =
  | { type: "start"; total: number; platform_hint: string; files: string[] }
  | { type: "thought"; text: string; file?: string }
  | { type: "image_start"; index: number; total: number; file: string }
  | { type: "image_done"; index: number; file: string; platform: string; items_count: number; matched_count: number; elapsed: number }
  | { type: "image_error"; index: number; file: string; error: string; elapsed?: number }
  | { type: "image_cancelled"; index: number; file: string; elapsed?: number }
  | { type: "progress"; finished: number; total: number }
  | { type: "done"; total_items: number; files: number; errors: number }
  | { type: "cancelled"; total_items: number; files: number; cancelled_files: number; errors: number }
  | { type: "fatal"; error: string };
