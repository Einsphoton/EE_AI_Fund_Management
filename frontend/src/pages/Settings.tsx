import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Save, Camera, Check, User, BookOpen, Zap,
  AlertTriangle, Trash2, Loader2, X,
  Download, Upload, Database, FileJson, FileSpreadsheet, Plus,
  RefreshCw, Rocket, ShieldCheck,
} from "lucide-react";

import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import LLMConfigCard, { LLMPreset, LLMConfigState } from "../components/LLMConfigCard";
import { Settings as SettingsApi, AppSettings, Admin, ImportResult, InvestmentBudgetItem, AssetType, UpdateApi } from "../api/client";


const PRESETS: Record<string, string> = {
  hourly: "每小时",
  every6h: "每 6 小时",
  daily: "每天 09:00",
  weekly: "每周一 09:00",
  custom: "自定义",
};

type CustomScheduleMode = "daily" | "weekly" | "hourly";

interface CustomScheduleUi {
  mode: CustomScheduleMode;
  minute: number;
  hour: number;
  weekday: number;
  intervalHours: number;
}

const WEEKDAYS = [
  { value: 1, label: "周一" },
  { value: 2, label: "周二" },
  { value: 3, label: "周三" },
  { value: 4, label: "周四" },
  { value: 5, label: "周五" },
  { value: 6, label: "周六" },
  { value: 0, label: "周日" },
];

const clampInt = (value: unknown, min: number, max: number, fallback: number) => {
  const n = Number.parseInt(String(value), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
};

function parseCustomCron(cron: string | undefined): CustomScheduleUi {
  const [minuteRaw, hourRaw, dayRaw, monthRaw, weekRaw] = String(cron || "0 9 * * *").trim().split(/\s+/);
  const minute = clampInt(minuteRaw, 0, 59, 0);
  if (hourRaw?.startsWith("*/")) {
    return {
      mode: "hourly",
      minute,
      hour: 9,
      weekday: 1,
      intervalHours: clampInt(hourRaw.slice(2), 1, 23, 6),
    };
  }
  if (dayRaw === "*" && monthRaw === "*" && weekRaw && weekRaw !== "*") {
    return {
      mode: "weekly",
      minute,
      hour: clampInt(hourRaw, 0, 23, 9),
      weekday: clampInt(weekRaw, 0, 6, 1),
      intervalHours: 6,
    };
  }
  return {
    mode: "daily",
    minute,
    hour: clampInt(hourRaw, 0, 23, 9),
    weekday: 1,
    intervalHours: 6,
  };
}

function buildCustomCron(v: CustomScheduleUi): string {
  if (v.mode === "hourly") return `${v.minute} */${v.intervalHours} * * *`;
  if (v.mode === "weekly") return `${v.minute} ${v.hour} * * ${v.weekday}`;
  return `${v.minute} ${v.hour} * * *`;
}

function describeCustomSchedule(v: CustomScheduleUi): string {
  const mm = String(v.minute).padStart(2, "0");
  const hh = String(v.hour).padStart(2, "0");
  if (v.mode === "hourly") return `每 ${v.intervalHours} 小时，在第 ${mm} 分钟运行一次`;
  if (v.mode === "weekly") return `每${WEEKDAYS.find((x) => x.value === v.weekday)?.label || "周一"} ${hh}:${mm} 运行`;
  return `每天 ${hh}:${mm} 运行`;
}

const AI_PRESETS: LLMPreset[] = [
  { name: "DeepSeek", base_url: "https://api.deepseek.com/v1" },
  { name: "OpenAI", base_url: "https://api.openai.com/v1" },
  { name: "智谱 BigModel", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { name: "月之暗面 Kimi", base_url: "https://api.moonshot.cn/v1" },
  { name: "Ollama (本地)", base_url: "http://192.168.1.100:11434/v1" },
  { name: "LM Studio (本地)", base_url: "http://192.168.1.100:1234/v1" },
  { name: "oMLX (Apple Silicon)", base_url: "http://127.0.0.1:8080/v1" },
];

const VISION_PRESETS: LLMPreset[] = [
  { name: "通义千问 (视觉)", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { name: "智谱 BigModel", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { name: "OpenAI", base_url: "https://api.openai.com/v1" },
  { name: "Ollama (本地)", base_url: "http://192.168.1.100:11434/v1" },
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: SettingsApi.getAll });
  const { data: profilesData } = useQuery({ queryKey: ["profiles"], queryFn: SettingsApi.getProfiles });

  const [ai, setAi] = useState<AppSettings["ai"]>({
    base_url: "", api_key: "", model: "",
    temperature: 0.4, batch_concurrency: 2, max_tokens: 4096, timeout: 180,
    rpm_limit: 0, min_interval_sec: 0,
    investor_profile: "balanced", report_style: "pro",
    cf_access_client_id: "", cf_access_client_secret: "", cf_access_hosts: "",
    thinking_mode: "auto", thinking_budget: 0, reasoning_effort: "medium",
  });
  const [vision, setVision] = useState<NonNullable<AppSettings["vision"]>>({
    use_ai: true,  // 默认复用 AI 大模型，体验最简
    base_url: "", api_key: "", model: "",
    temperature: 0.1, max_tokens: 8192, timeout: 300, concurrency: 2,
    rpm_limit: 20, min_interval_sec: 0,
    json_mode: true, auto_fill_code: true,
    wall_timeout: 300, content_hardcap: 20000, stream: false, force_stream: false,


  });
  const [schedule, setSchedule] = useState<AppSettings["schedule"]>({ enabled: false, cron: "0 9 * * *", preset: "daily", include_investment_plan: false, include_ai_targets: false });
  const [budgetItems, setBudgetItems] = useState<InvestmentBudgetItem[]>([]);

  useEffect(() => {
    if (!data) return;
    setAi({
      base_url: data.ai.base_url ?? "",
      api_key: data.ai.api_key ?? "",
      model: data.ai.model ?? "",
      temperature: data.ai.temperature ?? 0.4,
      batch_concurrency: data.ai.batch_concurrency ?? 2,
      max_tokens: data.ai.max_tokens ?? 4096,
      timeout: data.ai.timeout ?? 180,
      rpm_limit: data.ai.rpm_limit ?? 0,
      min_interval_sec: data.ai.min_interval_sec ?? 0,
      investor_profile: data.ai.investor_profile ?? "balanced",
      report_style: data.ai.report_style ?? "pro",
      cf_access_client_id: data.ai.cf_access_client_id ?? "",
      cf_access_client_secret: data.ai.cf_access_client_secret ?? "",
      cf_access_hosts: data.ai.cf_access_hosts ?? "",
      thinking_mode: (data.ai.thinking_mode as any) ?? "auto",
      thinking_budget: data.ai.thinking_budget ?? 0,
      reasoning_effort: (data.ai.reasoning_effort as any) ?? "medium",
    });
    if (data.vision) {
      setVision({
        use_ai: data.vision.use_ai ?? true,
        base_url: data.vision.base_url ?? "",
        api_key: data.vision.api_key ?? "",
        model: data.vision.model ?? "",
        temperature: data.vision.temperature ?? 0.1,
        max_tokens: data.vision.max_tokens ?? 8192,
        timeout: data.vision.timeout ?? 300,
        concurrency: data.vision.concurrency ?? 2,
        rpm_limit: data.vision.rpm_limit ?? 0,
        min_interval_sec: data.vision.min_interval_sec ?? 0,
        json_mode: data.vision.json_mode ?? true,
        auto_fill_code: data.vision.auto_fill_code ?? true,
        wall_timeout: data.vision.wall_timeout ?? 90,
        content_hardcap: data.vision.content_hardcap ?? 20000,
        stream: data.vision.stream ?? false,
        force_stream: data.vision.force_stream ?? false,
      });

    }
    setSchedule(data.schedule);
    setBudgetItems(data.investment_budget?.items ?? []);
  }, [data]);

  const save = useMutation({
    mutationFn: async () => {
      await SettingsApi.put("ai", ai);
      await SettingsApi.put("schedule", schedule);
      await SettingsApi.put("vision", vision);
      await SettingsApi.put("investment_budget", { items: budgetItems });
    },
    onSuccess: () => {
      toast.success("配置已保存并生效");
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  // ---- LLMConfigCard 双向绑定的 helper ----
  // AI 卡片维护完整状态（含 CF / profile / report_style），但 LLMConfigCard 只接它认识的字段子集
  const aiAsLLM: LLMConfigState = useMemo(() => ({
    base_url: ai.base_url, api_key: ai.api_key, model: ai.model,
    temperature: ai.temperature, batch_concurrency: ai.batch_concurrency,
    max_tokens: ai.max_tokens, timeout: ai.timeout,
    rpm_limit: ai.rpm_limit, min_interval_sec: ai.min_interval_sec,
    cf_access_client_id: ai.cf_access_client_id,
    cf_access_client_secret: ai.cf_access_client_secret,
    cf_access_hosts: ai.cf_access_hosts,
    thinking_mode: ai.thinking_mode as any,
    thinking_budget: ai.thinking_budget,
    reasoning_effort: ai.reasoning_effort as any,
  }), [ai]);

  const onAiChange = (v: LLMConfigState) => {
    setAi((prev) => ({ ...prev, ...v }));
  };

  const visionAsLLM: LLMConfigState = useMemo(() => ({
    base_url: vision.base_url, api_key: vision.api_key, model: vision.model,
    temperature: vision.temperature, concurrency: vision.concurrency,
    max_tokens: vision.max_tokens, timeout: vision.timeout,
    // 视觉测试时也复用 ai 配置的 CF Access（如果用户在视觉卡片单独看不到 CF 区，仍要把 ai 的 token 带过去）
    cf_access_client_id: ai.cf_access_client_id,
    cf_access_client_secret: ai.cf_access_client_secret,
    cf_access_hosts: ai.cf_access_hosts,
  }), [vision, ai.cf_access_client_id, ai.cf_access_client_secret, ai.cf_access_hosts]);

  const onVisionChange = (v: LLMConfigState) => {
    // 注意：CF 字段不写回 vision，写回 ai（vision 视图只读地复用 ai 的 CF）
    setVision((prev) => ({
      ...prev,
      base_url: v.base_url ?? "",
      api_key: v.api_key ?? "",
      model: v.model ?? "",
      temperature: v.temperature,
      max_tokens: v.max_tokens,
      timeout: v.timeout,
      concurrency: v.concurrency,
    }));
  };

  return (
    <>
      <PageHeader
        title="设置"
        subtitle="AI 大模型 / 视觉模型 / 定时分析 / 投资性格 / 平台预算"
        actions={
          <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
            <Save className="w-4 h-4" /> {save.isPending ? "保存中…" : "保存全部"}
          </button>
        }
      />

      <div className="grid lg:grid-cols-2 gap-6">
        {/* ============ AI 大模型 ============ */}
        <LLMConfigCard
          title="AI 大模型"
          subtitle="支持任意 OpenAI 兼容协议（DeepSeek / OpenAI / 本地 Ollama / LM Studio 等）。用于生成 AI 分析报告。"
          icon={<Zap className="w-4 h-4 text-accent" />}
          presets={AI_PRESETS}
          value={aiAsLLM}
          onChange={onAiChange}
          mode="ai"
          showCfAccess
        />

        {/* ============ 视觉模型 ============ */}
        {vision.use_ai ? (
          <div className="space-y-3">
            <div className="card p-5">
              <h3 className="font-semibold mb-1 flex items-center gap-2">
                <Camera className="w-4 h-4 text-accent" />
                视觉模型（OCR 截图导入）
              </h3>
              <p className="text-xs text-muted mb-4">
                用于「OCR 导入」识别持仓截图。
              </p>

              <label className="flex items-center gap-2 cursor-pointer mb-3 select-none">
                <input
                  type="checkbox" className="accent-accent w-4 h-4"
                  checked={vision.use_ai ?? true}
                  onChange={(e) => setVision({ ...vision, use_ai: e.target.checked })}
                />
                <span className="text-sm">复用 AI 大模型配置</span>
              </label>

              <div className="rounded-lg border border-emerald2/30 bg-emerald2/5 p-4 text-xs leading-relaxed">
                <div className="flex items-center gap-2 text-emerald2 font-medium mb-2">
                  <Check className="w-4 h-4" />
                  已启用：直接使用上方 AI 大模型的 base_url / api_key / model
                </div>
                <p className="text-muted">
                  仅复用 <strong className="text-white/90">端点和模型</strong>；下面的 OCR 性能参数
                  （max_tokens / timeout / 并发 / JSON Mode）独立维护，不会被 AI 配置覆盖。
                </p>
                <p className="text-muted mt-2">
                  <strong className="text-white/90">⚠️ 但请注意：</strong>
                  必须确保 AI 大模型卡片里填的是 <strong className="text-white/90">多模态模型</strong>
                  （如 qwen-vl、glm-4v、gpt-4o、kimi-latest），普通文本模型（deepseek-chat、qwen3）不支持图片输入。
                </p>
              </div>
            </div>

            {/* OCR 性能参数（复用模式下也能编辑） */}
            <VisionAdvanced vision={vision} setVision={setVision} />
          </div>
        ) : (
          <div>
            <label className="flex items-center gap-2 cursor-pointer mb-3 select-none px-1">
              <input
                type="checkbox" className="accent-accent w-4 h-4"
                checked={vision.use_ai ?? false}
                onChange={(e) => setVision({ ...vision, use_ai: e.target.checked })}
              />
              <span className="text-sm">复用 AI 大模型配置</span>
              <span className="text-[11px] text-muted">（关闭时单独配置下方模型）</span>
            </label>
            <LLMConfigCard
              title="视觉模型（OCR 截图导入）"
              subtitle="用于「OCR 导入」识别持仓截图。需选支持图片输入的多模态模型。"
              icon={<Camera className="w-4 h-4 text-accent" />}
              presets={VISION_PRESETS}
              value={visionAsLLM}
              onChange={onVisionChange}
              mode="vision"
              showCfAccess={false}
              testHint={
                ai.cf_access_client_id
                  ? "已检测到 AI 配置里的 Cloudflare Access Token，视觉模型会自动复用（如果命中 hosts 列表）。"
                  : "若视觉模型也走 Cloudflare Tunnel，请先在 AI 大模型卡片填好 CF Access。"
              }
            />
            {/* OCR 高级选项：JSON Mode + 性能提示 */}
            <div className="mt-3">
              <VisionAdvanced vision={vision} setVision={setVision} compact />
            </div>
          </div>
        )}

        {/* ============ 定时分析 ============ */}
        <div className="card p-5">
          <h3 className="font-semibold mb-1">定时 AI 分析</h3>
          <p className="text-xs text-muted mb-4">
            启用后，AI Agent 会按设定频次自动分析所有资产以及标的，并生成「AI 分析我的资产」报告。
          </p>

          <label className="flex items-center gap-2 cursor-pointer mb-3 select-none">
            <input
              type="checkbox" className="accent-accent w-4 h-4"
              checked={schedule.enabled}
              onChange={(e) => setSchedule({ ...schedule, enabled: e.target.checked })}
            />
            启用定时分析
          </label>
          <label className="flex items-center gap-2 cursor-pointer mb-3 select-none">
            <input
              type="checkbox" className="accent-accent w-4 h-4"
              checked={!!schedule.include_investment_plan}
              onChange={(e) => setSchedule({ ...schedule, include_investment_plan: e.target.checked })}
            />
            定时分析后顺带生成 AI 投资建议
          </label>
          <label className="flex items-center gap-2 cursor-pointer mb-4 select-none">
            <input
              type="checkbox" className="accent-accent w-4 h-4"
              checked={!!schedule.include_ai_targets}
              onChange={(e) => setSchedule({ ...schedule, include_ai_targets: e.target.checked })}
            />
            定时分析后顺带更新 AI 推荐标的
          </label>

          <label className="label">频次预设</label>
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(PRESETS).map(([k, v]) => (
              <button
                key={k}
                className={`btn ${schedule.preset === k ? "border-accent/60 bg-accent/15 text-white" : ""}`}
                onClick={() => setSchedule({ ...schedule, preset: k })}
              >
                {v}
              </button>
            ))}
          </div>

          {schedule.preset === "custom" && (
            <CustomScheduleEditor
              cron={schedule.cron}
              onChange={(cron) => setSchedule({ ...schedule, cron })}
            />
          )}
        </div>

        {/* ============ 平台月投资额度 ============ */}
        <BudgetSettingsCard items={budgetItems} onChange={setBudgetItems} />

        {/* ============ 投资者性格 ============ */}
        <div className="card p-5 lg:col-span-2">
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <User className="w-4 h-4 text-accent" /> 投资者性格
          </h3>
          <p className="text-xs text-muted mb-4">
            AI 会把这里作为硬约束：影响每个资产的结论、仓位节奏、止盈止损和持有周期，并在分析卡里显示「性格适配」说明。
          </p>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {(profilesData?.investor_profiles ?? []).map((p) => {
              const active = ai.investor_profile === p.id;
              return (
                <button
                  key={p.id}
                  className={`text-left rounded-xl border p-3 transition ${
                    active
                      ? "border-accent/60 bg-accent/15 text-white"
                      : "border-line bg-bg-soft/40 hover:border-accent/40"
                  }`}
                  onClick={() => setAi({ ...ai, investor_profile: p.id })}
                >
                  <div className="flex items-center gap-2">
                    <div className="font-medium text-sm">{p.name}</div>
                    {active && <Check className="w-3.5 h-3.5 text-accent ml-auto" />}
                  </div>
                  <div className="text-[11px] text-muted mt-1 leading-relaxed">{p.tagline}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ============ 报告风格 ============ */}
        <div className="card p-5 lg:col-span-2">
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-accent" /> 分析报告风格
          </h3>
          <p className="text-xs text-muted mb-4">
            控制 AI 报告的用词：「专业模式」保留 MA/PE/MACD 等术语；「新手模式」全部转成大白话 + 操作建议，更易读。
          </p>

          <div className="grid sm:grid-cols-2 gap-2">
            {(profilesData?.report_styles ?? []).map((s) => {
              const active = ai.report_style === s.id;
              return (
                <button
                  key={s.id}
                  className={`text-left rounded-xl border p-3 transition ${
                    active
                      ? "border-accent/60 bg-accent/15 text-white"
                      : "border-line bg-bg-soft/40 hover:border-accent/40"
                  }`}
                  onClick={() => setAi({ ...ai, report_style: s.id })}
                >
                  <div className="flex items-center gap-2">
                    <div className="font-medium text-sm">{s.name}</div>
                    {active && <Check className="w-3.5 h-3.5 text-accent ml-auto" />}
                  </div>
                  <div className="text-[11px] text-muted mt-1 leading-relaxed">{s.tagline}</div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ============ 在线更新 ============ */}
        <UpdateCard />

        {/* ============ 数据备份 ============ */}
        <BackupCard />

        {/* ============ 危险区：清除所有数据 ============ */}

        <DangerZoneCard />
      </div>
    </>
  );
}

function CustomScheduleEditor({ cron, onChange }: {
  cron: string;
  onChange: (cron: string) => void;
}) {
  const value = parseCustomCron(cron);
  const update = (patch: Partial<CustomScheduleUi>) => {
    const next = { ...value, ...patch };
    onChange(buildCustomCron(next));
  };

  return (
    <div className="mt-4 rounded-xl border border-line bg-bg-soft/30 p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-sm font-medium">自定义运行时间</div>
          <div className="text-[11px] text-muted mt-0.5">无需手写 Cron，通过下面选项生成定时规则。</div>
        </div>
        <div className="text-[11px] text-accent-soft font-mono shrink-0">{cron || buildCustomCron(value)}</div>
      </div>

      <div className="grid md:grid-cols-4 gap-3 items-end">
        <div>
          <label className="label">运行方式</label>
          <select
            className="input"
            value={value.mode}
            onChange={(e) => update({ mode: e.target.value as CustomScheduleMode })}
          >
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
            <option value="hourly">每隔几小时</option>
          </select>
        </div>

        {value.mode === "weekly" && (
          <div>
            <label className="label">星期</label>
            <select
              className="input"
              value={value.weekday}
              onChange={(e) => update({ weekday: clampInt(e.target.value, 0, 6, 1) })}
            >
              {WEEKDAYS.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          </div>
        )}

        {value.mode === "hourly" ? (
          <div>
            <label className="label">间隔小时</label>
            <input
              className="input font-mono"
              type="number"
              min={1}
              max={23}
              step={1}
              value={value.intervalHours}
              onChange={(e) => update({ intervalHours: clampInt(e.currentTarget.value, 1, 23, 6) })}
            />
          </div>
        ) : (
          <div>
            <label className="label">小时</label>
            <select
              className="input font-mono"
              value={value.hour}
              onChange={(e) => update({ hour: clampInt(e.target.value, 0, 23, 9) })}
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>{String(h).padStart(2, "0")}</option>
              ))}
            </select>
          </div>
        )}

        <div>
          <label className="label">分钟</label>
          <select
            className="input font-mono"
            value={value.minute}
            onChange={(e) => update({ minute: clampInt(e.target.value, 0, 59, 0) })}
          >
            {Array.from({ length: 60 }, (_, m) => m).map((m) => (
              <option key={m} value={m}>{String(m).padStart(2, "0")}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-3 rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 text-xs text-white/80">
        当前规则：<span className="text-accent-soft">{describeCustomSchedule(value)}</span>
      </div>
    </div>
  );
}

function BudgetSettingsCard({ items, onChange }: {
  items: InvestmentBudgetItem[];
  onChange: (items: InvestmentBudgetItem[]) => void;
}) {
  const add = () => onChange([...items, { platform: "", currency: "CNY", monthly_amount: 1000, asset_types: ["fund"] }]);
  const update = (idx: number, patch: Partial<InvestmentBudgetItem>) => {
    onChange(items.map((it, i) => i === idx ? { ...it, ...patch } : it));
  };
  const remove = (idx: number) => onChange(items.filter((_, i) => i !== idx));
  const toggleAssetType = (idx: number, type: AssetType) => {
    const cur = items[idx]?.asset_types ?? [];
    const exists = cur.includes(type);
    const next = exists ? cur.filter((x) => x !== type) : [...cur, type];
    update(idx, { asset_types: next });
  };

  return (
    <div className="card p-5 lg:col-span-2">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <Database className="w-4 h-4 text-accent" /> 平台月投资额度
          </h3>
          <p className="text-xs text-muted leading-relaxed">
            按购买平台、币种和可购买资产类型设置每月可投入预算。同一平台可添加多个币种，例如富途 HKD 2000 + USD 2000。
          </p>
        </div>
        <button className="btn !px-3 !py-1.5 text-xs" onClick={add} type="button">
          <Plus className="w-3.5 h-3.5" /> 添加预算
        </button>
      </div>

      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line p-5 text-center text-sm text-muted">
          暂未设置预算。AI 投资经理需要预算后才会生成可执行投资建议。
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((it, idx) => (
            <div key={idx} className="grid md:grid-cols-[1fr_120px_180px_auto] gap-2 items-end rounded-xl border border-line/60 bg-bg-soft/30 p-3">
              <div>
                <label className="label">购买平台</label>
                <input
                  className="input"
                  placeholder="例如：支付宝 / 微信理财通 / 富途"
                  value={it.platform}
                  onChange={(e) => update(idx, { platform: e.target.value })}
                />
              </div>
              <div>
                <label className="label">币种</label>
                <select
                  className="input"
                  value={it.currency}
                  onChange={(e) => update(idx, { currency: e.target.value })}
                >
                  <option value="CNY">CNY</option>
                  <option value="HKD">HKD</option>
                  <option value="USD">USD</option>
                </select>
              </div>
              <div>
                <label className="label">月额度</label>
                <input
                  className="input font-mono"
                  type="number"
                  min={0}
                  step={100}
                  value={it.monthly_amount ?? 0}
                  onChange={(e) => update(idx, { monthly_amount: e.currentTarget.valueAsNumber || 0 })}
                />
              </div>
              <button className="btn !text-rose2 hover:!border-rose2/60" onClick={() => remove(idx)} type="button">
                <X className="w-4 h-4" /> 删除
              </button>
              <div className="md:col-span-4 border-t border-line/40 pt-3">
                <div className="label mb-2">可购买资产类型</div>
                <div className="flex flex-wrap gap-2">
                  {([
                    { type: "fund" as AssetType, label: "场外基金" },
                    { type: "stock" as AssetType, label: "股票" },
                    { type: "etf" as AssetType, label: "ETF / 场内基金" },
                  ]).map((opt) => {
                    const active = (it.asset_types ?? []).includes(opt.type);
                    return (
                      <button
                        key={opt.type}
                        type="button"
                        className={`px-3 py-1.5 rounded-lg border text-xs transition ${
                          active
                            ? "border-accent/60 bg-accent/15 text-white"
                            : "border-line text-muted hover:text-white hover:border-accent/40"
                        }`}
                        onClick={() => toggleAssetType(idx, opt.type)}
                      >
                        {active && <Check className="w-3 h-3 inline mr-1" />}{opt.label}
                      </button>
                    );
                  })}
                </div>
                {(it.asset_types ?? []).length === 0 && (
                  <div className="text-[10px] text-amber2 mt-2">至少选择一种类型，否则 AI 不会使用这条预算。</div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// VisionAdvanced：OCR 性能参数（max_tokens / timeout / 并发 / JSON Mode）
//   - 复用 AI 端点时也能编辑（这些是 OCR 任务专属）
//   - compact=true 时去掉标题和外层卡片样式（嵌入到 LLMConfigCard 下面用）
// ============================================================
type VisionState = NonNullable<AppSettings["vision"]>;
function VisionAdvanced({
  vision, setVision, compact = false,
}: {
  vision: VisionState;
  setVision: (v: VisionState) => void;
  compact?: boolean;
}) {
  const set = (patch: Partial<VisionState>) => setVision({ ...vision, ...patch });

  return (
    <div className={compact ? "card p-4 text-xs space-y-3" : "card p-5 text-xs space-y-3"}>
      {!compact && (
        <h4 className="font-semibold text-sm mb-1 flex items-center gap-2">
          <Camera className="w-4 h-4 text-accent" />
          OCR 性能参数
          <span className="text-[11px] text-muted font-normal">（独立维护，与 AI 大模型互不影响）</span>
        </h4>
      )}

      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="label">并发度</label>
          <input
            className="input"
            type="number" step="1" min={1} max={5}
            value={vision.concurrency ?? 1}
            onChange={(e) => set({ concurrency: Math.max(1, Math.min(5, e.target.valueAsNumber || 1)) })}
          />
          <p className="text-[10px] text-muted mt-1 leading-relaxed">
            同时识别的图片数。视觉模型贵且慢，建议 1-2。
          </p>
        </div>
        <div>
          <label className="label">Max Tokens</label>
          <input
            className="input"
            type="number" step="512" min={1024} max={32768}
            value={vision.max_tokens ?? 8192}
            onChange={(e) => set({ max_tokens: Math.max(1024, e.target.valueAsNumber || 8192) })}
          />
          <p className="text-[10px] text-muted mt-1 leading-relaxed">
            单图响应上限。8K 覆盖 5-10 项；持仓多请调到 12000+。
          </p>
        </div>
        <div>
          <label className="label">Timeout (秒)</label>
          <input
            className="input"
            type="number" step="30" min={30} max={1800}
            value={vision.timeout ?? 300}
            onChange={(e) => set({ timeout: Math.max(30, e.target.valueAsNumber || 300) })}
          />
          <p className="text-[10px] text-muted mt-1 leading-relaxed">
            流式调用首字 1-3s，整体 30-60s。给 300 足够。
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">每分钟最大请求数 (RPM)</label>
          <input
            className="input"
            type="number" step="5" min={0} max={1000}
            value={vision.rpm_limit ?? 0}
            onChange={(e) => set({ rpm_limit: Math.max(0, Math.min(1000, e.target.valueAsNumber || 0)) })}
            placeholder="0 = 不限"
          />
          <p className="text-[10px] text-muted mt-1 leading-relaxed">
            滑动窗口精准节流，<strong className="text-white/80">推荐设成官方上限的 85%</strong>：
            NVIDIA NIM 免费 Kimi → <strong className="text-white/80">35</strong>，
            阿里 Qwen-VL → 50，Kimi 官方免费 → 3。比硬性间隔更聪明，能利用 burst。
          </p>
        </div>
        <div>
          <label className="label">Temperature</label>
          <input
            className="input"
            type="number" step="0.1" min={0} max={1}
            value={vision.temperature ?? 0.1}
            onChange={(e) => set({ temperature: Math.max(0, Math.min(1, e.target.valueAsNumber || 0.1)) })}
          />
          <p className="text-[10px] text-muted mt-1 leading-relaxed">
            OCR 任务建议固定为 0.1，让模型稳定按 schema 输出。
          </p>
        </div>
      </div>

      <details className="text-xs">
        <summary className="cursor-pointer text-muted hover:text-white/80 select-none">
          高级：图片最小间隔（兜底节流）
        </summary>
        <div className="mt-2 pl-3 border-l-2 border-line/40 space-y-3">
          <div>
            <label className="label">单图硬超时 wall_timeout (秒)</label>
            <input
              className="input"
              type="number" step="30" min={30} max={1800}
              value={vision.wall_timeout ?? vision.timeout ?? 300}
              onChange={(e) => set({ wall_timeout: Math.max(30, Math.min(1800, e.target.valueAsNumber || (vision.timeout ?? 300))) })}
            />
            <p className="text-[10px] text-muted mt-1 leading-relaxed">
              OCR Agent 的墙钟总上限，超过会强制放弃当前图片。默认跟随上方 Timeout；NVIDIA NIM 排队慢时应与 Timeout 保持一致。
            </p>
          </div>
          <div>
            <label className="label">图片最小间隔 (秒)</label>
            <input
              className="input"
              type="number" step="1" min={0} max={120}
              value={vision.min_interval_sec ?? 0}
              onChange={(e) => set({ min_interval_sec: Math.max(0, Math.min(120, e.target.valueAsNumber || 0)) })}
            />
            <p className="text-[10px] text-muted mt-1 leading-relaxed">
              两张图之间硬性最小间隔。已被 RPM 限流覆盖大多数场景，<strong className="text-white/80">通常留 0 即可</strong>。
              仅当某些代理 RPM 看似够用却仍要求请求间隔最少 N 秒时才需要设。两个限速谁严格谁说了算。
            </p>
          </div>
        </div>

      </details>

      <div className="space-y-2 pt-1 border-t border-line/40">
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox" className="accent-accent w-4 h-4"
            checked={vision.json_mode ?? true}
            onChange={(e) => set({ json_mode: e.target.checked })}
          />
          <span className="text-sm">强制 JSON 输出模式（推荐开启）</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox" className="accent-accent w-4 h-4"
            checked={vision.auto_fill_code ?? true}
            onChange={(e) => set({ auto_fill_code: e.target.checked })}
          />
          <span className="text-sm">识别后自动补全基金/股票代码、市场和交易所（推荐开启）</span>
        </label>
      </div>
      <p className="text-muted leading-relaxed">
        OCR 现在按 <strong className="text-white/80">Skill + Agent Harness</strong> 执行：
        先让多模态模型严格抽取持仓 JSON，再用天天基金 / 腾讯证券 / 新浪 / 雪球多源查码，自动补全
        <code className="text-white/80"> code / market / exchange</code>。
      </p>
      <p className="text-muted">
        默认使用非流式安全路径，适合 NVIDIA NIM Kimi、本地 Qwen/Gemma 等模型；若超时，优先检查 base_url、API Key、RPM 限速和模型是否支持图片输入。
      </p>

    </div>
  );
}

// ============================================================
// UpdateCard：Docker 在线更新
// ============================================================
function UpdateCard() {
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["update-status"],
    queryFn: UpdateApi.status,
    retry: false,
  });

  const trigger = useMutation({
    mutationFn: () => UpdateApi.trigger(data?.confirm_text || "UPDATE_NOW"),
    onSuccess: (r) => {
      toast.success(r.message, { duration: 9000 });
      setTimeout(() => refetch(), 8000);
    },
    onError: (e: any) => toast.error(e?.message || "触发更新失败"),
  });

  const updateBadge = data?.update_available === true
    ? "border-emerald2/40 bg-emerald2/10 text-emerald2"
    : data?.update_available === false
      ? "border-accent/40 bg-accent/10 text-accent-soft"
      : "border-amber2/40 bg-amber2/10 text-amber2";

  const startUpdate = () => {
    if (!data?.web_update_enabled) return;
    const ok = window.confirm(
      "将通过 Watchtower 拉取最新 Docker 镜像并重启当前容器。更新过程中页面会短暂断开，确认继续？",
    );
    if (ok) trigger.mutate();
  };

  return (
    <div className="card p-5 lg:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <Rocket className="w-4 h-4 text-accent" /> 在线更新
          </h3>
          <p className="text-xs text-muted leading-relaxed">
            支持两条路径：绿联 Docker GUI 直接拉取 Docker Hub 镜像，或在本页通过 Watchtower HTTP API 一键更新并重启容器。
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn !px-3 !py-1.5 text-xs" onClick={() => refetch()} disabled={isFetching} type="button">
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} /> 刷新状态
          </button>
          <button
            className="btn-primary !px-3 !py-1.5 text-xs"
            onClick={startUpdate}
            disabled={!data?.web_update_enabled || trigger.isPending}
            type="button"
            title={!data?.web_update_enabled ? "需启用 UPDATE_ENABLE_WEB_TRIGGER 并配置 Watchtower Token" : undefined}
          >
            {trigger.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
            立即更新
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-sm text-muted">正在读取更新状态…</div>
      ) : error ? (
        <div className="rounded-xl border border-rose2/30 bg-rose2/10 p-3 text-sm text-rose2">
          更新状态读取失败：{(error as any)?.message || String(error)}
        </div>
      ) : data ? (
        <div className="space-y-4">
          <div className="grid md:grid-cols-3 gap-3">
            <div className="rounded-xl border border-line bg-bg-soft/30 p-3">
              <div className="text-[11px] text-muted mb-1">当前版本</div>
              <div className="font-mono text-sm text-white/90 break-all">{data.current_version || "local"}</div>
              <div className="text-[11px] text-muted mt-1 break-all">{data.current_revision || "无提交信息"}</div>
            </div>
            <div className="rounded-xl border border-line bg-bg-soft/30 p-3">
              <div className="text-[11px] text-muted mb-1">Docker Hub 最新</div>
              <div className="font-mono text-sm text-white/90 break-all">{data.latest_version || "未获取"}</div>
              <div className="text-[11px] text-muted mt-1 break-all">{data.checked_repo || data.dockerhub_repo || "未配置仓库"}</div>
            </div>
            <div className="rounded-xl border border-line bg-bg-soft/30 p-3">
              <div className="text-[11px] text-muted mb-2">更新状态</div>
              <span className={`badge ${updateBadge}`}>
                {data.update_available === true ? "发现新版本" : data.update_available === false ? "已是最新" : "需人工确认"}
              </span>
              <div className="text-[11px] text-muted mt-2">网页更新：{data.web_update_enabled ? "已启用" : "未启用"}</div>
            </div>
          </div>

          <div className="rounded-xl border border-line bg-bg-soft/30 p-3 text-xs leading-relaxed text-muted">
            <div className="text-white/85 mb-1">{data.message}</div>
            {data.check_error && <div className="text-amber2 break-all">Docker Hub 检查错误：{data.check_error}</div>}
            <div className="mt-2 grid md:grid-cols-2 gap-2">
              <div>镜像：<code className="text-white/80 break-all">{data.image || "未配置 UPDATE_IMAGE"}</code></div>
              <div>Watchtower：<code className="text-white/80 break-all">{data.watchtower_configured ? data.watchtower_url : "未配置 Token"}</code></div>
            </div>
          </div>

          {!data.web_update_enabled && (
            <div className="rounded-xl border border-amber2/30 bg-amber2/10 p-3 text-xs leading-relaxed text-amber2">
              如需网页点击更新：在 `.env` 中设置 `UPDATE_ENABLE_WEB_TRIGGER=true`、`UPDATE_WATCHTOWER_TOKEN=长随机字符串`，并用 `docker compose --profile update up -d` 启动 Watchtower。
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

// ============================================================
// BackupCard：资产数据备份 / 恢复

// ------------------------------------------------------------
// 设计动机：
//   - 用户换电脑/重装系统前想带走自己录入的基金股票；以后随时能还原
//   - JSON 完整备份（含交易 + 快照），CSV 便于用 Excel 核对
//   - 导入时三种策略覆盖主要使用场景：
//       merge    = 合并（新电脑+旧备份，补空字段，追加新交易）
//       skip     = 只新建，不动已有（最保守）
//       replace  = 清空重建（迁移场景；需二次确认）
// ============================================================
function BackupCard() {
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [pickedFile, setPickedFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"merge" | "replace" | "skip">("merge");
  const [includeTxns, setIncludeTxns] = useState(true);
  const [includeSnaps, setIncludeSnaps] = useState(true);
  const [includeSettings, setIncludeSettings] = useState(false);
  const [replaceConfirm, setReplaceConfirm] = useState("");

  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<ImportResult | null>(null);

  const resetDialog = () => {
    setImportOpen(false);
    setPickedFile(null);
    setMode("merge");
    setIncludeTxns(true);
    setIncludeSnaps(true);
    setReplaceConfirm("");
    setLastResult(null);
  };

  const onPickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".json")) {
      toast.error("请选择 .json 备份文件");
      return;
    }
    setPickedFile(f);
    setImportOpen(true);
  };

  const doImport = async () => {
    if (!pickedFile) return;
    if (mode === "replace" && replaceConfirm !== "REPLACE") {
      toast.error('replace 模式需要输入 "REPLACE" 确认');
      return;
    }
    setBusy(true);
    try {
      const r = await Admin.importData(pickedFile, {
        mode,
        includeTransactions: includeTxns,
        includeSnapshots: includeSnaps,
        includeSettings,
      });

      setLastResult(r);
      const n = r.assets_created + r.assets_updated + r.transactions_added + r.snapshots_added;
      toast.success(
        `导入完成：${n} 项变更（资产 +${r.assets_created} / ✎${r.assets_updated} / 跳${r.assets_skipped}，交易 +${r.transactions_added}，快照 +${r.snapshots_added}）`,
        { duration: 7000 },
      );
      // 刷新所有页面的缓存，让资产列表立刻看到新数据
      qc.invalidateQueries();
    } catch (e: any) {
      toast.error(`导入失败：${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card p-5 lg:col-span-2">
        <h3 className="font-semibold mb-1 flex items-center gap-2">
          <Database className="w-4 h-4 text-accent" /> 数据备份与恢复
        </h3>
        <p className="text-xs text-muted mb-4">
          把你的全部资产、交易、持仓快照打包成一个文件，换设备或出事故时用它恢复。
          <strong className="text-white/80"> 建议每月导出一份</strong>。
        </p>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* 导出 JSON */}

          <button
            className="card !p-4 flex flex-col items-start gap-1 text-left hover:border-accent/40 transition"
            onClick={() => Admin.exportDownload("json", true)}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileJson className="w-4 h-4 text-accent" />
              导出 JSON 完整备份
            </div>
            <div className="text-[11px] text-muted leading-relaxed">
              含资产 + 交易 + 持仓快照，<strong className="text-white/80">可用于恢复</strong>。
              推荐作为日常备份格式。
            </div>
          </button>

          {/* 导出所有 */}
          <button
            className="card !p-4 flex flex-col items-start gap-1 text-left hover:border-emerald2/40 transition"
            onClick={() => Admin.exportDownload("json", true, true)}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileJson className="w-4 h-4 text-emerald2" />
              导出所有（含设置）
            </div>
            <div className="text-[11px] text-muted leading-relaxed">
              含资产 + 交易 + 快照 + 设置页配置 + Skills。会包含 API Key，<strong className="text-amber2">请妥善保管</strong>。
            </div>
          </button>

          {/* 导出 CSV 资产 */}
          <button

            className="card !p-4 flex flex-col items-start gap-1 text-left hover:border-accent/40 transition"
            onClick={() => Admin.exportDownload("csv", false)}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileSpreadsheet className="w-4 h-4 text-accent" />
              导出资产 CSV
            </div>
            <div className="text-[11px] text-muted leading-relaxed">
              只含资产基础字段，扁平表格，<strong className="text-white/80">Excel 直接打开</strong>。
              不能用于恢复，仅用于查看/对账。
            </div>
          </button>

          {/* 导出 CSV 交易 */}
          <button
            className="card !p-4 flex flex-col items-start gap-1 text-left hover:border-accent/40 transition"
            onClick={() => Admin.exportTransactionsDownload()}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              <FileSpreadsheet className="w-4 h-4 text-accent" />
              导出交易流水 CSV
            </div>
            <div className="text-[11px] text-muted leading-relaxed">
              所有买入/卖出的流水明细，扁平表格。适合在 Excel 里按时间/标的筛选统计。
            </div>
          </button>
        </div>

        <div className="border-t border-line/40 mt-4 pt-4">
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium flex items-center gap-2">
                <Upload className="w-4 h-4 text-accent" /> 从备份恢复
              </div>
              <div className="text-[11px] text-muted mt-0.5 leading-relaxed">
                上传之前导出的 <code className="text-accent">.json</code> 文件。
                默认「合并」模式——已有资产只补空字段，交易按日期+份额去重追加。
              </div>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={onPickFile}
            />
            <button
              className="btn"
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload className="w-4 h-4" /> 选择 JSON 文件…
            </button>
          </div>
        </div>
      </div>

      {/* ============ 导入确认对话框 ============ */}
      {importOpen && pickedFile && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => { if (e.target === e.currentTarget && !busy) resetDialog(); }}
        >
          <div className="w-full max-w-lg rounded-2xl border border-line/60 bg-bg p-6 shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2">
                <Download className="w-5 h-5 text-accent" /> 导入备份
              </h3>
              <button className="text-muted hover:text-white" onClick={resetDialog} disabled={busy}>
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="text-xs text-muted mb-4">
              文件：<span className="text-white">{pickedFile.name}</span>
              （{(pickedFile.size / 1024).toFixed(1)} KB）
            </div>

            {/* 模式选择 */}
            <div className="space-y-2 mb-4">
              <div className="label">合并模式</div>
              {[
                {
                  v: "merge" as const,
                  label: "合并（推荐）",
                  desc: "按 (类型, 代码) 识别已有资产，只补空字段；交易按日期+份额去重追加",
                  color: "emerald2",
                },
                {
                  v: "skip" as const,
                  label: "仅新增（最保守）",
                  desc: "已存在的资产完全跳过（交易/快照也不追加）",
                  color: "muted",
                },
                {
                  v: "replace" as const,
                  label: "替换（危险）",
                  desc: "先清空现有所有业务数据再全量导入。需要输入 REPLACE 二次确认。",
                  color: "rose2",
                },
              ].map((opt) => (
                <label
                  key={opt.v}
                  className={`flex gap-2 cursor-pointer select-none rounded-lg border p-3 transition ${
                    mode === opt.v ? "border-accent/60 bg-accent/10" : "border-line/60 hover:border-line"
                  }`}
                >
                  <input
                    type="radio"
                    className="accent-accent mt-0.5"
                    checked={mode === opt.v}
                    onChange={() => setMode(opt.v)}
                    disabled={busy}
                  />
                  <div className="flex-1 text-xs">
                    <div className={`font-medium text-${opt.color === "emerald2" ? "emerald2" : opt.color === "rose2" ? "rose2" : "white"}`}>
                      {opt.label}
                    </div>
                    <div className="text-muted mt-0.5 leading-relaxed">{opt.desc}</div>
                  </div>
                </label>
              ))}
            </div>

            {/* 子表/配置开关 */}
            <div className="grid sm:grid-cols-2 gap-3 mb-4">
              <label className="flex items-center gap-2 cursor-pointer text-xs">
                <input
                  type="checkbox" className="accent-accent w-4 h-4"
                  checked={includeTxns}
                  onChange={(e) => setIncludeTxns(e.target.checked)}
                  disabled={busy}
                />
                导入交易流水
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-xs">
                <input
                  type="checkbox" className="accent-accent w-4 h-4"
                  checked={includeSnaps}
                  onChange={(e) => setIncludeSnaps(e.target.checked)}
                  disabled={busy}
                />
                导入持仓快照
              </label>
              <label className="sm:col-span-2 flex items-start gap-2 cursor-pointer text-xs rounded-lg border border-amber2/30 bg-amber2/5 p-3">
                <input
                  type="checkbox" className="accent-accent w-4 h-4 mt-0.5"
                  checked={includeSettings}
                  onChange={(e) => setIncludeSettings(e.target.checked)}
                  disabled={busy}
                />
                <span>
                  导入设置页配置和 Skills（仅「导出所有」文件包含）。会覆盖当前 AI / 视觉模型 / 定时分析 / 预算等配置，可能包含 API Key。
                </span>
              </label>
            </div>


            {/* replace 模式的二次确认输入 */}
            {mode === "replace" && (
              <div className="mb-4 rounded-lg border border-rose2/40 bg-rose2/5 p-3">
                <div className="flex items-start gap-2 text-xs text-rose2 mb-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                  <div>
                    <strong>此操作会先清空当前所有资产/交易/快照</strong>，再从文件全量恢复。
                    不可恢复。请输入 <code className="px-1 bg-rose2/20 rounded">REPLACE</code> 确认。
                  </div>
                </div>
                <input
                  className="input"
                  placeholder="REPLACE"
                  value={replaceConfirm}
                  onChange={(e) => setReplaceConfirm(e.target.value)}
                  disabled={busy}
                />
              </div>
            )}

            {/* 上次结果显示 */}
            {lastResult && (
              <div className="mb-4 rounded-lg border border-emerald2/40 bg-emerald2/5 p-3 text-xs">
                <div className="text-emerald2 font-medium mb-1">导入完成</div>
                <div className="text-muted space-y-0.5">
                  <div>资产：新建 <span className="text-white">{lastResult.assets_created}</span> / 更新 <span className="text-white">{lastResult.assets_updated}</span> / 跳过 <span className="text-white">{lastResult.assets_skipped}</span></div>
                  <div>交易追加：<span className="text-white">{lastResult.transactions_added}</span> 条</div>
                  <div>快照追加：<span className="text-white">{lastResult.snapshots_added}</span> 条</div>
                  {(lastResult.settings_imported || lastResult.skills_imported) ? (
                    <div>配置恢复：设置 <span className="text-white">{lastResult.settings_imported || 0}</span> 项 / Skills <span className="text-white">{lastResult.skills_imported || 0}</span> 个</div>
                  ) : null}
                  {lastResult.errors.length > 0 && (

                    <div className="mt-2 pt-2 border-t border-line/40">
                      <div className="text-amber2">警告 {lastResult.errors.length} 条：</div>
                      {lastResult.errors.slice(0, 5).map((err, i) => (
                        <div key={i} className="text-[10px] leading-tight mt-0.5">· {err}</div>
                      ))}
                      {lastResult.errors.length > 5 && (
                        <div className="text-[10px] text-muted mt-0.5">…另 {lastResult.errors.length - 5} 条</div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2 mt-2">
              <button className="btn" onClick={resetDialog} disabled={busy}>
                {lastResult ? "关闭" : "取消"}
              </button>
              {!lastResult && (
                <button
                  className={`btn ${mode === "replace" ? "!bg-rose2/20 !border-rose2/60 !text-rose2 hover:!bg-rose2/30" : "btn-primary"} disabled:opacity-50`}
                  onClick={doImport}
                  disabled={busy || (mode === "replace" && replaceConfirm !== "REPLACE")}
                >
                  {busy ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> 导入中…</>
                  ) : (
                    <><Download className="w-4 h-4" /> 确认导入</>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}


// ============================================================
// DangerZoneCard：清除所有数据。双重确认（modal + 输入 DELETE）
// ------------------------------------------------------------
// 设计动机：
//   - 这是不可逆操作（删 assets/transactions/holding_snapshots/advices/...）
//   - 单次点击太容易误触，必须让用户"主动慢一拍"
//   - 提供两档：仅业务数据 vs 全部（含 AI 配置 / Skills），分别对应不同回归场景
// ============================================================
function DangerZoneCard() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [includeSettings, setIncludeSettings] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setOpen(false);
    setIncludeSettings(false);
    setConfirmText("");
  };

  const wipe = async () => {
    if (confirmText !== "DELETE") {
      toast.error("请准确输入 DELETE 才能继续");
      return;
    }
    setBusy(true);
    try {
      const r = await Admin.wipeAll(includeSettings);
      const lines = Object.entries(r.deleted)
        .filter(([, n]) => n > 0)
        .map(([t, n]) => `${t}: ${n}`)
        .join("，");
      toast.success(
        `${r.message}${lines ? `（${lines}）` : ""}`,
        { duration: 6000 },
      );
      // 让所有页面下一次进入时重新拉数据
      qc.invalidateQueries();
      reset();
    } catch (e: any) {
      toast.error(`清空失败：${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card p-5 lg:col-span-2 border-rose2/40 bg-rose2/5">
        <h3 className="font-semibold mb-1 flex items-center gap-2 text-rose2">
          <AlertTriangle className="w-4 h-4" /> 危险区
        </h3>
        <p className="text-xs text-muted mb-4">
          以下操作<strong className="text-rose2"> 不可恢复</strong>，请谨慎使用。
          建议先到「资产管理」导出 / 备份后再操作。
        </p>

        <div className="flex flex-col sm:flex-row sm:items-center gap-3 rounded-xl border border-rose2/40 bg-bg-soft/40 p-3">
          <div className="flex-1">
            <div className="text-sm font-medium">清除所有数据</div>
            <div className="text-[11px] text-muted mt-0.5 leading-relaxed">
              清空数据库里的资产、交易、持仓快照、AI 建议等业务数据；可选连同 AI 配置 / Skills 元数据一起清。
            </div>
          </div>
          <button
            className="btn !text-rose2 hover:!border-rose2/60 hover:!bg-rose2/10"
            onClick={() => setOpen(true)}
          >
            <Trash2 className="w-4 h-4" /> 清除…
          </button>
        </div>
      </div>

      {/* ============ 二次确认 modal ============ */}
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => {
            // 点击遮罩关闭，但 busy 中不响应
            if (e.target === e.currentTarget && !busy) reset();
          }}
        >
          <div className="w-full max-w-md rounded-2xl border border-rose2/40 bg-bg p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold flex items-center gap-2 text-rose2">
                <AlertTriangle className="w-5 h-5" /> 确认清除数据
              </h3>
              <button
                className="text-muted hover:text-white"
                onClick={reset}
                disabled={busy}
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-sm text-muted leading-relaxed mb-4">
              此操作<strong className="text-rose2">不可恢复</strong>。
              请勾选清空范围，并在下方输入框中输入
              <code className="mx-1 px-1.5 py-0.5 rounded bg-rose2/15 text-rose2 font-bold">DELETE</code>
              再点确认。
            </p>

            <label className="flex items-start gap-2 cursor-pointer mb-4 select-none rounded-lg border border-line/60 p-3 hover:border-rose2/40 transition">
              <input
                type="checkbox"
                className="accent-rose2 w-4 h-4 mt-0.5"
                checked={includeSettings}
                onChange={(e) => setIncludeSettings(e.target.checked)}
                disabled={busy}
              />
              <div className="flex-1 text-xs">
                <div className="text-white">
                  连同设置和 Skills 一起清空
                </div>
                <div className="text-muted mt-0.5">
                  勾选 = 把 AI / 视觉模型 / Cloudflare Token / 投资性格 / 已安装 Skills 全清。
                  不勾 = 只清业务数据，配置保留（默认）。
                </div>
              </div>
            </label>

            <label className="label">输入 <code className="text-rose2 font-bold">DELETE</code> 确认</label>
            <input
              className="input"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="DELETE"
              autoFocus
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter" && confirmText === "DELETE" && !busy) {
                  wipe();
                }
              }}
            />

            <div className="flex justify-end gap-2 mt-5">
              <button className="btn" onClick={reset} disabled={busy}>
                取消
              </button>
              <button
                className="btn !bg-rose2/20 !border-rose2/60 !text-rose2 hover:!bg-rose2/30 disabled:opacity-50"
                onClick={wipe}
                disabled={busy || confirmText !== "DELETE"}
              >
                {busy ? <><Loader2 className="w-4 h-4 animate-spin" /> 清空中…</> : <><Trash2 className="w-4 h-4" /> 我已了解，确认清空</>}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
