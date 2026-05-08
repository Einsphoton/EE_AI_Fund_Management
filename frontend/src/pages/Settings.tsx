import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Save, Camera, Check, User, BookOpen, Zap } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import LLMConfigCard, { LLMPreset, LLMConfigState } from "../components/LLMConfigCard";
import { Settings as SettingsApi, AppSettings } from "../api/client";

const PRESETS: Record<string, string> = {
  hourly: "每小时",
  every6h: "每 6 小时",
  daily: "每天 09:00",
  weekly: "每周一 09:00",
  custom: "自定义 cron",
};

const AI_PRESETS: LLMPreset[] = [
  { name: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  { name: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { name: "Ollama (本地)", base_url: "http://192.168.1.100:11434/v1", model: "qwen3:14b" },
  { name: "LM Studio (本地)", base_url: "http://192.168.1.100:1234/v1", model: "local-model" },
  { name: "oMLX (Apple Silicon)", base_url: "http://127.0.0.1:8080/v1", model: "mlx-community/Qwen3-14B-Instruct-4bit" },
];

const VISION_PRESETS: LLMPreset[] = [
  { name: "通义千问 VL Max", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-vl-max" },
  { name: "通义 VL Plus（便宜）", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-vl-plus" },
  { name: "智谱 GLM-4V", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4v" },
  { name: "智谱 GLM-4V Plus", base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4v-plus" },
  { name: "OpenAI GPT-4o", base_url: "https://api.openai.com/v1", model: "gpt-4o" },
  { name: "OpenAI GPT-4o mini", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { name: "Ollama qwen2.5-vl (本地)", base_url: "http://192.168.1.100:11434/v1", model: "qwen2.5-vl:7b" },
];

export default function SettingsPage() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: SettingsApi.getAll });
  const { data: profilesData } = useQuery({ queryKey: ["profiles"], queryFn: SettingsApi.getProfiles });

  const [ai, setAi] = useState<AppSettings["ai"]>({
    base_url: "", api_key: "", model: "",
    temperature: 0.4, batch_concurrency: 1, max_tokens: 4096, timeout: 180,
    investor_profile: "balanced", report_style: "pro",
    cf_access_client_id: "", cf_access_client_secret: "", cf_access_hosts: "",
  });
  const [vision, setVision] = useState<NonNullable<AppSettings["vision"]>>({
    base_url: "", api_key: "", model: "",
    temperature: 0.1, max_tokens: 4096, timeout: 180, concurrency: 2,
  });
  const [schedule, setSchedule] = useState<AppSettings["schedule"]>({ enabled: false, cron: "0 9 * * *", preset: "daily" });

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
    setSchedule(data.schedule);
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

  // ---- LLMConfigCard 双向绑定的 helper ----
  // AI 卡片维护完整状态（含 CF / profile / report_style），但 LLMConfigCard 只接它认识的字段子集
  const aiAsLLM: LLMConfigState = useMemo(() => ({
    base_url: ai.base_url, api_key: ai.api_key, model: ai.model,
    temperature: ai.temperature, batch_concurrency: ai.batch_concurrency,
    max_tokens: ai.max_tokens, timeout: ai.timeout,
    cf_access_client_id: ai.cf_access_client_id,
    cf_access_client_secret: ai.cf_access_client_secret,
    cf_access_hosts: ai.cf_access_hosts,
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
        subtitle="AI 大模型 / 视觉模型 / 定时分析 / 投资性格"
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
        <LLMConfigCard
          title="视觉模型（OCR 截图导入）"
          subtitle="用于「OCR 导入」识别持仓截图。需选支持 image_url 的多模态模型。"
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

        {/* ============ 定时分析 ============ */}
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

        {/* ============ 投资者性格 ============ */}
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
      </div>
    </>
  );
}
