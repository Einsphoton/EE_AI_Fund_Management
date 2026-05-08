import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, Zap, PlugZap, Check, AlertCircle, Shield, User, BookOpen } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import { Settings as SettingsApi, AppSettings } from "../api/client";

const PRESETS: Record<string, string> = {
  hourly: "每小时",
  every6h: "每 6 小时",
  daily: "每天 09:00",
  weekly: "每周一 09:00",
  custom: "自定义 cron",
};

const MODEL_PRESETS = [
  { name: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { name: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { name: "Ollama (本地)", base_url: "http://192.168.1.100:11434/v1", model: "qwen3:14b" },
  { name: "LM Studio (本地)", base_url: "http://192.168.1.100:1234/v1", model: "local-model" },
  // oMLX 是 Apple Silicon 上的 MLX 生态模型服务宿主，默认暴露 OpenAI 兼容的 /v1 接口（常用 8080 端口）。
  // 实际 model 名以你本地加载的为准（例如 mlx-community/Qwen3.x-xxB-Instruct-4bit）。
  { name: "oMLX (本地·Apple Silicon)", base_url: "http://127.0.0.1:8080/v1", model: "mlx-community/Qwen3-14B-Instruct-4bit" },
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: SettingsApi.getAll });
  const { data: profilesData } = useQuery({ queryKey: ["profiles"], queryFn: SettingsApi.getProfiles });

  const [ai, setAi] = useState<AppSettings["ai"]>({
    base_url: "",
    api_key: "",
    model: "",
    temperature: 0.4,
    batch_concurrency: 1,
    max_tokens: 4096,
    timeout: 180,
    investor_profile: "balanced",
    report_style: "pro",
    cf_access_client_id: "",
    cf_access_client_secret: "",
    cf_access_hosts: "",
  });
  const [schedule, setSchedule] = useState<AppSettings["schedule"]>({ enabled: false, cron: "0 9 * * *", preset: "daily" });
  const [vision, setVision] = useState<NonNullable<AppSettings["vision"]>>({
    base_url: "", api_key: "", model: "",
    temperature: 0.1, max_tokens: 4096, timeout: 180, concurrency: 2,
  });
  const [showCfAdvanced, setShowCfAdvanced] = useState(false);

  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | {
    ok: boolean;
    endpoint?: string;
    models?: string[];
    model_exists?: boolean;
    hint?: string;
    error?: string;
  }>(null);

  useEffect(() => {
    if (!data) return;
    setAi({
      base_url: data.ai.base_url ?? "",
      api_key: data.ai.api_key ?? "",
      model: data.ai.model ?? "",
      temperature: data.ai.temperature ?? 0.4,
      batch_concurrency: data.ai.batch_concurrency ?? 1,
      max_tokens: data.ai.max_tokens ?? 4096,
      timeout: data.ai.timeout ?? 180,
      investor_profile: data.ai.investor_profile ?? "balanced",
      report_style: data.ai.report_style ?? "pro",
      cf_access_client_id: data.ai.cf_access_client_id ?? "",
      cf_access_client_secret: data.ai.cf_access_client_secret ?? "",
      cf_access_hosts: data.ai.cf_access_hosts ?? "",
    });
    setSchedule(data.schedule);
    if (data.vision) {
      setVision({
        base_url: data.vision.base_url ?? "",
        api_key: data.vision.api_key ?? "",
        model: data.vision.model ?? "",
        temperature: data.vision.temperature ?? 0.1,
        max_tokens: data.vision.max_tokens ?? 4096,
        timeout: data.vision.timeout ?? 180,
        concurrency: data.vision.concurrency ?? 2,
      });
    }
    // 如果已经配置过 CF，默认展开高级区块
    if (data.ai.cf_access_client_id) setShowCfAdvanced(true);
  }, [data]);

  const save = useMutation({
    mutationFn: async () => {
      await SettingsApi.put("ai", ai);
      await SettingsApi.put("schedule", schedule);
      await SettingsApi.put("vision", vision);
    },
    onSuccess: () => {
      toast.success("配置已保存并生效");
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const testConnection = async () => {
    if (!ai.base_url) {
      toast.error("请先填写 Base URL");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await SettingsApi.testAi(ai.base_url, ai.api_key, ai.model, {
        cf_access_client_id: ai.cf_access_client_id,
        cf_access_client_secret: ai.cf_access_client_secret,
        cf_access_hosts: ai.cf_access_hosts,
      });
      setTestResult(r);
      if (r.ok && (r.model_exists !== false)) {
        toast.success("连接成功！");
      } else if (r.ok) {
        toast.error("服务可达，但模型不存在");
      } else {
        toast.error("连接失败，请查看下方诊断");
      }
    } catch (e: any) {
      setTestResult({ ok: false, error: e.message || "请求失败" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="设置"
        subtitle="配置 AI 大模型 API 与定时分析任务"
        actions={
          <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>
            <Save className="w-4 h-4" /> {save.isPending ? "保存中…" : "保存全部"}
          </button>
        }
      />

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card p-5">
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <Zap className="w-4 h-4 text-accent" /> AI 大模型
          </h3>
          <p className="text-xs text-muted mb-4">
            支持任意 OpenAI 兼容协议的大模型（DeepSeek / OpenAI / 本地 Ollama / LM Studio 等）。本地模型也走 OpenAI 协议即可。
          </p>

          <div className="flex flex-wrap gap-2 mb-3">
            {MODEL_PRESETS.map((p) => (
              <button
                key={p.name}
                className="btn !px-3 !py-1.5 text-xs"
                onClick={() => setAi((a) => ({ ...a, base_url: p.base_url, model: p.model }))}
              >
                {p.name}
              </button>
            ))}
          </div>

          <label className="label">API Base URL</label>
          <input
            className="input"
            placeholder="https://api.deepseek.com/v1"
            value={ai.base_url}
            onChange={(e) => setAi({ ...ai, base_url: e.target.value })}
          />

          <label className="label mt-3">API Key</label>
          <input
            className="input"
            type="password"
            placeholder="sk-xxxx（本地 Ollama 任意填）"
            value={ai.api_key}
            onChange={(e) => setAi({ ...ai, api_key: e.target.value })}
          />

          <div className="grid grid-cols-2 gap-3 mt-3">
            <div>
              <label className="label">Model</label>
              <input
                className="input"
                placeholder="deepseek-chat"
                value={ai.model}
                onChange={(e) => setAi({ ...ai, model: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Temperature</label>
              <input
                className="input"
                type="number" step="0.1" min={0} max={2}
                value={ai.temperature}
                onChange={(e) => setAi({ ...ai, temperature: e.target.valueAsNumber || 0.4 })}
              />
            </div>
          </div>

          {/* ---- 性能调优 ---- */}
          <div className="mt-4 rounded-lg border border-line/60 bg-bg-soft/40 p-3">
            <div className="text-xs font-medium text-white/90 mb-2 flex items-center gap-2">
              <Zap className="w-3.5 h-3.5 text-accent" />
              批量分析性能
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="label">并发度</label>
                <input
                  className="input"
                  type="number" step="1" min={1} max={16}
                  value={ai.batch_concurrency ?? 1}
                  onChange={(e) =>
                    setAi({ ...ai, batch_concurrency: Math.max(1, Math.min(16, e.target.valueAsNumber || 1)) })
                  }
                />
                <p className="text-[10px] text-muted mt-1 leading-relaxed">
                  同时分析的标的数。<br />
                  reasoning 模型 + Cloudflare 建议 1（串行）；<br />
                  普通模型 + 内网直连可调 3-6。
                </p>
              </div>
              <div>
                <label className="label">Max Tokens</label>
                <input
                  className="input"
                  type="number" step="256" min={0} max={16384}
                  value={ai.max_tokens ?? 4096}
                  onChange={(e) =>
                    setAi({ ...ai, max_tokens: Math.max(0, e.target.valueAsNumber || 0) })
                  }
                />
                <p className="text-[10px] text-muted mt-1 leading-relaxed">
                  单次输出 token 上限。推荐 4096：<br />
                  普通对话模型只用 1500-2500，reasoning 模型（R1/Qwen3-thinking/o1）<br />
                  会用 3000+ 写思考过程，设得太低（如 800）会被截断写不出最终答案。<br />
                  0 表示不限制（不推荐经过 Cloudflare 时使用，可能 524 超时）。
                </p>
              </div>
              <div>
                <label className="label">Timeout (秒)</label>
                <input
                  className="input"
                  type="number" step="10" min={10} max={600}
                  value={ai.timeout ?? 180}
                  onChange={(e) =>
                    setAi({ ...ai, timeout: Math.max(10, e.target.valueAsNumber || 180) })
                  }
                />
                <p className="text-[10px] text-muted mt-1 leading-relaxed">
                  单次调用超时。<br />
                  Ollama 慢模型可调大；<br />
                  DeepSeek 60 即可。
                </p>
              </div>
            </div>
          </div>

          {/* ---- Cloudflare Access Service Token（高级） ---- */}
          <div className="mt-4 border-t border-line pt-3">
            <button
              type="button"
              className="flex items-center gap-2 text-xs text-muted hover:text-accent transition select-none"
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
                  请求会自动带上 <code>CF-Access-Client-Id</code> 和 <code>CF-Access-Client-Secret</code>。
                </p>

                <div>
                  <label className="label">CF-Access-Client-Id</label>
                  <input
                    className="input font-mono text-xs"
                    placeholder="xxxxxxxx.access"
                    value={ai.cf_access_client_id || ""}
                    onChange={(e) => setAi({ ...ai, cf_access_client_id: e.target.value })}
                  />
                </div>

                <div>
                  <label className="label">CF-Access-Client-Secret</label>
                  <input
                    className="input font-mono text-xs"
                    type="password"
                    placeholder="（只显示一次，丢失只能重建）"
                    value={ai.cf_access_client_secret || ""}
                    onChange={(e) => setAi({ ...ai, cf_access_client_secret: e.target.value })}
                  />
                </div>

                <div>
                  <label className="label">
                    仅对这些域名注入（留空 = 所有请求都注入）
                  </label>
                  <input
                    className="input font-mono text-xs"
                    placeholder="einsphoton.ren,my-other-domain.com"
                    value={ai.cf_access_hosts || ""}
                    onChange={(e) => setAi({ ...ai, cf_access_hosts: e.target.value })}
                  />
                  <p className="text-[11px] text-muted mt-1">
                    多个域名用逗号分隔。建议填你的自建域名，避免把 Token 误发到 DeepSeek / OpenAI。
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="mt-4 flex items-center gap-2">
            <button className="btn flex-1" onClick={testConnection} disabled={testing}>
              <PlugZap className="w-4 h-4" />
              {testing ? "测试中…" : "测试连接 / 列出可用模型"}
            </button>
          </div>

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
                          m === ai.model
                            ? "border-accent/60 bg-accent/15 text-white"
                            : "border-line bg-bg-soft text-muted hover:border-accent/40 hover:text-accent-soft"
                        }`}
                        onClick={() => setAi({ ...ai, model: m })}
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

        <div className="card p-5">
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <Zap className="w-4 h-4 text-accent" />
            视觉模型（OCR 截图导入）
          </h3>
          <p className="text-xs text-muted mb-4">
            用于「OCR 导入」页识别持仓截图。推荐：阿里 <code>qwen-vl-max</code>、智谱 <code>glm-4v</code>、OpenAI <code>gpt-4o</code>。
            留空表示未启用 OCR 功能。
          </p>

          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="label">Base URL</label>
              <input className="input" placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1"
                     value={vision.base_url}
                     onChange={(e) => setVision({ ...vision, base_url: e.target.value })} />
            </div>
            <div>
              <label className="label">Model</label>
              <input className="input" placeholder="如 qwen-vl-max / glm-4v / gpt-4o"
                     value={vision.model}
                     onChange={(e) => setVision({ ...vision, model: e.target.value })} />
            </div>
            <div>
              <label className="label">API Key</label>
              <input className="input" type="password" placeholder="sk-..."
                     value={vision.api_key}
                     onChange={(e) => setVision({ ...vision, api_key: e.target.value })} />
            </div>
            <div>
              <label className="label">Max Tokens</label>
              <input className="input" type="number" min={512} max={16384} step={256}
                     value={vision.max_tokens ?? 4096}
                     onChange={(e) => setVision({ ...vision, max_tokens: e.target.valueAsNumber || 4096 })} />
            </div>
            <div>
              <label className="label">并发度</label>
              <input className="input" type="number" min={1} max={5}
                     value={vision.concurrency ?? 2}
                     onChange={(e) => setVision({ ...vision, concurrency: e.target.valueAsNumber || 2 })} />
              <div className="text-[10px] text-muted mt-1">视觉模型计费贵，建议 1-2</div>
            </div>
          </div>
        </div>

        <div className="card p-5">
          <h3 className="font-semibold mb-1">定时 AI 分析</h3>
          <p className="text-xs text-muted mb-4">
            启用后，AI Agent 会按设定频次自动分析所有标的，并生成「AI 建议」。
          </p>

          <label className="flex items-center gap-2 cursor-pointer mb-4 select-none">
            <input
              type="checkbox" className="accent-accent w-4 h-4"
              checked={schedule.enabled}
              onChange={(e) => setSchedule({ ...schedule, enabled: e.target.checked })}
            />
            启用定时分析
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
            <>
              <label className="label mt-4">Cron 表达式</label>
              <input
                className="input"
                placeholder="0 9 * * *"
                value={schedule.cron}
                onChange={(e) => setSchedule({ ...schedule, cron: e.target.value })}
              />
              <p className="text-[11px] text-muted mt-1">
                示例：<code>0 9 * * *</code> 每天 9 点；<code>0 */4 * * *</code> 每 4 小时
              </p>
            </>
          )}
        </div>

        {/* ---------- 投资者性格 ---------- */}
        <div className="card p-5 lg:col-span-2">
          <h3 className="font-semibold mb-1 flex items-center gap-2">
            <User className="w-4 h-4 text-accent" /> 投资者性格
          </h3>
          <p className="text-xs text-muted mb-4">
            AI 在生成建议时会结合你的风险偏好：稳健型偏保守、进攻型偏积极、收息养老型优先现金流……选一个最贴近你的。
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

        {/* ---------- 分析报告风格 ---------- */}
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
      </div>
    </>
  );
}
