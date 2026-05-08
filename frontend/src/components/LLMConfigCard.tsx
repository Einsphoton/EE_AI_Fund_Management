/**
 * LLMConfigCard - AI / 视觉模型 共用的配置卡片
 *
 * 复用功能：
 *   1. 预设按钮（一键填 base_url + model）
 *   2. Base URL / API Key / Model / Temperature
 *   3. 性能调优：max_tokens / timeout（可选 batch_concurrency）
 *   4. Cloudflare Access（共享于 ai 配置，vision 也走同域名时可启用）
 *   5. 测试连接 + 列出模型
 *   6. 测试结果展示（含模型点击切换）
 *
 * 通过 props 控制：
 *   - mode: "ai" | "vision"  ——决定是否显示 batch_concurrency
 *   - presets: 预设列表
 *   - showCfAccess: 是否显示 CF Access 高级区块（vision 共用 ai 的 CF）
 */
import { useState } from "react";
import { Zap, PlugZap, Check, AlertCircle, Shield } from "lucide-react";
import { Settings as SettingsApi } from "../api/client";

export interface LLMPreset {
  name: string;
  base_url: string;
  /** 仅作占位提示用（如 "qwen-..."），不会自动填到 model 输入框，避免预设带过期模型名 */
  model_hint?: string;
}

/** 通用 LLM 配置数据结构（AI / Vision 共用）。 */
export interface LLMConfigState {
  base_url: string;
  api_key: string;
  model: string;
  temperature?: number;
  max_tokens?: number;
  timeout?: number;
  batch_concurrency?: number;     // AI 专有
  concurrency?: number;            // Vision 专有
  cf_access_client_id?: string;    // 仅 AI 卡片维护，Vision 卡片复用 AI 的
  cf_access_client_secret?: string;
  cf_access_hosts?: string;
  // 思考 / Reasoning 控制（AI 专有，Vision 不需要）
  thinking_mode?: "auto" | "on" | "off";
  thinking_budget?: number;
  reasoning_effort?: "minimal" | "low" | "medium" | "high" | string;
}

export interface LLMConfigCardProps {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  presets: LLMPreset[];
  value: LLMConfigState;
  onChange: (v: LLMConfigState) => void;

  /** "ai" 显示 batch_concurrency；"vision" 显示 concurrency */
  mode?: "ai" | "vision";

  /** 是否显示 CF Access 区块（一般只在 ai 卡片显示，避免重复编辑） */
  showCfAccess?: boolean;

  /** 测试连接时额外的提示信息（例如视觉模型："已配置 ai 的 CF Access 会自动复用"） */
  testHint?: string;
}

export default function LLMConfigCard({
  title, subtitle, icon, presets, value, onChange,
  mode = "ai", showCfAccess = false, testHint,
}: LLMConfigCardProps) {
  const [showCfAdvanced, setShowCfAdvanced] = useState(!!value.cf_access_client_id);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | {
    ok: boolean;
    endpoint?: string;
    models?: string[];
    model_exists?: boolean;
    hint?: string;
    error?: string;
  }>(null);

  const set = (patch: Partial<LLMConfigState>) => onChange({ ...value, ...patch });

  const testConnection = async () => {
    if (!value.base_url) {
      setTestResult({ ok: false, error: "请先填写 Base URL" });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await SettingsApi.testAi(value.base_url, value.api_key, value.model, {
        cf_access_client_id: value.cf_access_client_id,
        cf_access_client_secret: value.cf_access_client_secret,
        cf_access_hosts: value.cf_access_hosts,
      });
      setTestResult(r);
    } catch (e: any) {
      setTestResult({ ok: false, error: e.message || "请求失败" });
    } finally {
      setTesting(false);
    }
  };

  const concurrencyKey: "batch_concurrency" | "concurrency" =
    mode === "ai" ? "batch_concurrency" : "concurrency";
  const concurrencyDefault = mode === "ai" ? 1 : 2;
  const concurrencyMax = mode === "ai" ? 16 : 5;

  return (
    <div className="card p-5">
      <h3 className="font-semibold mb-1 flex items-center gap-2">
        {icon || <Zap className="w-4 h-4 text-accent" />}
        {title}
      </h3>
      {subtitle && <p className="text-xs text-muted mb-4">{subtitle}</p>}

      {/* ============ 预设 ============ */}
      {presets.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2 mb-1">
            {presets.map((p) => (
              <button
                key={p.name}
                type="button"
                className="btn !px-3 !py-1.5 text-xs"
                onClick={() => set({ base_url: p.base_url })}
                title={`填入 Base URL：${p.base_url}\n（模型名请连接后从下方列表里选）`}
              >
                {p.name}
              </button>
            ))}
          </div>
          <p className="text-[10px] text-muted mb-3">
            点击预设只填 Base URL；模型名请填好 API Key 后点「测试连接」，从返回的列表里选。
          </p>
        </>
      )}

      <label className="label">Base URL</label>
      <input
        className="input"
        placeholder={presets[0]?.base_url || "https://api.example.com/v1"}
        value={value.base_url || ""}
        onChange={(e) => set({ base_url: e.target.value })}
      />

      <label className="label mt-3">API Key</label>
      <input
        className="input"
        type="password"
        placeholder="sk-xxxx（本地 Ollama 任意填）"
        value={value.api_key || ""}
        onChange={(e) => set({ api_key: e.target.value })}
      />

      <div className="grid grid-cols-2 gap-3 mt-3">
        <div>
          <label className="label">Model</label>
          <input
            className="input"
            placeholder="点测试连接后从列表选"
            value={value.model || ""}
            onChange={(e) => set({ model: e.target.value })}
          />
        </div>
        <div>
          <label className="label">Temperature</label>
          <input
            className="input"
            type="number" step="0.1" min={0} max={2}
            value={value.temperature ?? (mode === "vision" ? 0.1 : 0.4)}
            onChange={(e) => set({ temperature: e.target.valueAsNumber || (mode === "vision" ? 0.1 : 0.4) })}
          />
        </div>
      </div>

      {/* ============ 性能调优 ============ */}
      <div className="mt-4 rounded-lg border border-line/60 bg-bg-soft/40 p-3">
        <div className="text-xs font-medium text-white/90 mb-2 flex items-center gap-2">
          <Zap className="w-3.5 h-3.5 text-accent" />
          性能调优
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label">并发度</label>
            <input
              className="input"
              type="number" step="1" min={1} max={concurrencyMax}
              value={(value as any)[concurrencyKey] ?? concurrencyDefault}
              onChange={(e) =>
                set({
                  [concurrencyKey]: Math.max(
                    1, Math.min(concurrencyMax, e.target.valueAsNumber || concurrencyDefault)
                  ),
                } as Partial<LLMConfigState>)
              }
            />
            <p className="text-[10px] text-muted mt-1 leading-relaxed">
              {mode === "ai"
                ? "同时分析的标的数。reasoning 模型 + Cloudflare 建议 1；普通模型可调 3-6。"
                : "并发解析的图片数。视觉模型计费贵，建议 1-2。"}
            </p>
          </div>
          <div>
            <label className="label">Max Tokens</label>
            <input
              className="input"
              type="number" step="256" min={0} max={16384}
              value={value.max_tokens ?? 4096}
              onChange={(e) => set({ max_tokens: Math.max(0, e.target.valueAsNumber || 0) })}
            />
            <p className="text-[10px] text-muted mt-1 leading-relaxed">
              {mode === "ai"
                ? "推荐 4096：reasoning 模型需要 3000+ 写思考；0 = 不限。"
                : "单图响应 token 上限，4096 足够覆盖 10-15 项持仓。"}
            </p>
          </div>
          <div>
            <label className="label">Timeout (秒)</label>
            <input
              className="input"
              type="number" step="10" min={10} max={600}
              value={value.timeout ?? 180}
              onChange={(e) => set({ timeout: Math.max(10, e.target.valueAsNumber || 180) })}
            />
            <p className="text-[10px] text-muted mt-1 leading-relaxed">
              单次调用超时。本地 Ollama 慢模型可调大。
            </p>
          </div>
        </div>
      </div>

      {/* ============ 思考 / Reasoning（仅 ai 模式） ============ */}
      {mode === "ai" && (
        <div className="mt-4 rounded-lg border border-line/60 bg-bg-soft/40 p-3">
          <div className="text-xs font-medium text-white/90 mb-2 flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-accent" />
            思考 / Reasoning
            <span className="text-[10px] text-muted font-normal ml-auto">
              兼容 DeepSeek V4 / Qwen3.5 / GLM-5 / GPT-5 / Claude / Kimi 等
            </span>
          </div>

          <label className="label">思考模式</label>
          <div className="grid grid-cols-3 gap-2 mb-2">
            {([
              { v: "auto", label: "自动", desc: "不传任何思考参数（推荐）" },
              { v: "on",   label: "强制开",  desc: "hybrid 模型开启深度思考" },
              { v: "off",  label: "强制关",  desc: "hybrid 模型转为快速对话" },
            ] as const).map((opt) => {
              const active = (value.thinking_mode || "auto") === opt.v;
              return (
                <button
                  key={opt.v}
                  type="button"
                  className={`btn flex-col items-start text-left h-auto py-2 ${
                    active ? "border-accent/60 bg-accent/15 text-white" : ""
                  }`}
                  onClick={() => set({ thinking_mode: opt.v })}
                  title={opt.desc}
                >
                  <div className="text-sm font-medium">{opt.label}</div>
                  <div className="text-[10px] text-muted leading-tight">{opt.desc}</div>
                </button>
              );
            })}
          </div>

          {(value.thinking_mode || "auto") === "on" && (
            <div className="grid grid-cols-2 gap-3 mt-2">
              <div>
                <label className="label">思考预算（tokens）</label>
                <input
                  className="input"
                  type="number" min={0} max={32000} step={512}
                  value={value.thinking_budget ?? 0}
                  onChange={(e) => set({ thinking_budget: Math.max(0, e.target.valueAsNumber || 0) })}
                />
                <p className="text-[10px] text-muted mt-1 leading-relaxed">
                  0 = 不限。1024 浅思考 / 4096 标准 / 16384 深度。<br />
                  对应：DeepSeek V4 / Qwen3.5 的 <code>thinking_budget</code>、Claude 的 <code>budget_tokens</code>。
                </p>
              </div>
              <div>
                <label className="label">推理强度</label>
                <select
                  className="input"
                  value={value.reasoning_effort || "medium"}
                  onChange={(e) => set({ reasoning_effort: e.target.value })}
                >
                  <option value="minimal">minimal（最快，GPT-5 专属）</option>
                  <option value="low">low（轻度推理）</option>
                  <option value="medium">medium（标准）</option>
                  <option value="high">high（深度推理）</option>
                </select>
                <p className="text-[10px] text-muted mt-1 leading-relaxed">
                  对应：OpenAI o1/o3/GPT-5 / Kimi K2 / Grok 4 的 <code>reasoning_effort</code>。<br />
                  不支持的模型会被服务端忽略。
                </p>
              </div>
            </div>
          )}

          {(value.thinking_mode || "auto") === "off" && (
            <p className="text-[11px] text-amber2/90 mt-1 leading-relaxed">
              ⚠️ 强制关闭思考。适合 hybrid 模型（DeepSeek V4 / Qwen3.5 / GLM-5）想用快速对话模式时。
              对纯推理模型（DeepSeek-R2 / o1）无效。
            </p>
          )}
        </div>
      )}

      {/* ============ Cloudflare Access ============ */}
      {showCfAccess && (
        <div className="mt-4 border-t border-line pt-3">
          <button
            type="button"
            className="flex items-center gap-2 text-xs text-muted hover:text-accent transition select-none w-full"
            onClick={() => setShowCfAdvanced((v) => !v)}
          >
            <Shield className="w-3.5 h-3.5" />
            <span>Cloudflare Access（高级，自建 API 被 CF 拦截时用）</span>
            <span className="ml-auto font-mono">{showCfAdvanced ? "▼" : "▶"}</span>
          </button>

          {showCfAdvanced && (
            <div className="mt-3 space-y-3 rounded-lg border border-line/60 bg-bg-soft/40 p-3">
              <p className="text-[11px] text-muted leading-relaxed">
                如果你的 Base URL 走 Cloudflare Tunnel / CDN 并遇到 <code>Your request was blocked</code>，
                在 Zero Trust 后台为该子域绑定 Access 应用 + Service Token 策略，然后把 Client ID / Secret 填到这里。
              </p>
              <div>
                <label className="label">CF-Access-Client-Id</label>
                <input
                  className="input font-mono text-xs"
                  placeholder="xxxxxxxx.access"
                  value={value.cf_access_client_id || ""}
                  onChange={(e) => set({ cf_access_client_id: e.target.value })}
                />
              </div>
              <div>
                <label className="label">CF-Access-Client-Secret</label>
                <input
                  className="input font-mono text-xs"
                  type="password"
                  placeholder="（只显示一次，丢失只能重建）"
                  value={value.cf_access_client_secret || ""}
                  onChange={(e) => set({ cf_access_client_secret: e.target.value })}
                />
              </div>
              <div>
                <label className="label">仅对这些域名注入（留空 = 所有请求都注入）</label>
                <input
                  className="input font-mono text-xs"
                  placeholder="einsphoton.ren,my-other-domain.com"
                  value={value.cf_access_hosts || ""}
                  onChange={(e) => set({ cf_access_hosts: e.target.value })}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* ============ 测试连接 ============ */}
      <div className="mt-4 flex items-center gap-2">
        <button className="btn flex-1" onClick={testConnection} disabled={testing}>
          <PlugZap className="w-4 h-4" />
          {testing ? "测试中…" : "测试连接 / 列出可用模型"}
        </button>
      </div>
      {testHint && (
        <p className="text-[11px] text-muted mt-2">{testHint}</p>
      )}

      {/* ============ 测试结果 ============ */}
      {testResult && (
        <div className={`mt-3 rounded-xl border p-3 text-xs ${
          testResult.ok && testResult.model_exists !== false
            ? "border-emerald2/40 bg-emerald2/5"
            : testResult.ok
              ? "border-amber2/40 bg-amber2/5"
              : "border-rose2/40 bg-rose2/5"
        }`}>
          <div className="flex items-center gap-2 mb-1.5 font-medium">
            {testResult.ok && testResult.model_exists !== false ? (
              <><Check className="w-4 h-4 text-emerald2" /><span className="text-emerald2">连接成功</span></>
            ) : testResult.ok ? (
              <><AlertCircle className="w-4 h-4 text-amber2" /><span className="text-amber2">服务可达，但模型不存在</span></>
            ) : (
              <><AlertCircle className="w-4 h-4 text-rose2" /><span className="text-rose2">连接失败</span></>
            )}
            {testResult.endpoint && (
              <span className="text-[10px] text-muted ml-auto font-mono">{testResult.endpoint}</span>
            )}
          </div>

          {testResult.error && (
            <pre className="text-rose2/90 whitespace-pre-wrap break-all">{testResult.error}</pre>
          )}
          {testResult.hint && (
            <pre className="text-muted whitespace-pre-wrap break-words mb-2">{testResult.hint}</pre>
          )}
          {testResult.models && testResult.models.length > 0 && (
            <div>
              <div className="text-muted mb-1">可用模型 ({testResult.models.length})：</div>
              <div className="flex flex-wrap gap-1">
                {testResult.models.map((m) => (
                  <button
                    key={m}
                    className={`px-2 py-0.5 rounded-md font-mono text-[10px] border transition ${
                      m === value.model
                        ? "border-accent/60 bg-accent/15 text-white"
                        : "border-line bg-bg-soft text-muted hover:border-accent/40 hover:text-accent-soft"
                    }`}
                    onClick={() => set({ model: m })}
                    title="点击使用此模型名"
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
