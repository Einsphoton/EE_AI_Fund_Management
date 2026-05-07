import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X, BrainCircuit, Sparkles, TrendingUp, TrendingDown, Minus,
  Shield, AlertTriangle, Target, Zap, Clock, Activity, Gauge,
  ArrowRight, RefreshCw,
} from "lucide-react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";

import PriceChart from "./PriceChart";
import { Asset, Advice, Assets, AdviceApi, Quotes } from "../api/client";
import {
  fmtMoney, fmtPct, fmtNum, actionColor, actionLabel, fmtDateTime,
} from "../lib/format";

interface Props {
  assetId: number;
  onClose: () => void;
}

const RANGES = [
  { d: 30, label: "1月" },
  { d: 90, label: "3月" },
  { d: 180, label: "6月" },
  { d: 365, label: "1年" },
];

export default function AssetAnalysisModal({ assetId, onClose }: Props) {
  const qc = useQueryClient();
  const [days, setDays] = useState(180);

  const holdings = useQuery({ queryKey: ["holdings"], queryFn: Assets.holdings });
  const holding = holdings.data?.find((h) => h.asset.id === assetId);

  const quote = useQuery({
    queryKey: ["quote", assetId, days],
    queryFn: () => Quotes.byAsset(assetId, days),
    enabled: !!assetId,
  });

  const snapshot = useQuery({
    queryKey: ["snapshot", assetId],
    queryFn: () => Quotes.snapshot(assetId),
    enabled: !!assetId,
  });

  const advices = useQuery({
    queryKey: ["advice", "asset", assetId],
    queryFn: () => AdviceApi.byAsset(assetId),
    enabled: !!assetId,
  });

  const runAi = useMutation({
    mutationFn: () => AdviceApi.runOne(assetId),
    onSuccess: () => {
      toast.success("AI 分析完成");
      qc.invalidateQueries({ queryKey: ["advice", "asset", assetId] });
    },
    onError: (e: any) => toast.error(e.message || "分析失败"),
  });

  if (!holding) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
        <div className="card px-8 py-6 text-muted" onClick={(e) => e.stopPropagation()}>加载中…</div>
      </div>
    );
  }

  const a: Asset = holding.asset;
  const latest: Advice | undefined = advices.data?.[0];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ============ 头部 ============ */}
        <div className="px-6 py-4 border-b border-line/60 flex items-center gap-4 shrink-0">
          <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-accent to-emerald2 flex items-center justify-center shadow-glow shrink-0">
            <BrainCircuit className="w-6 h-6 text-white" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-lg font-semibold truncate">{a.name}</div>
            <div className="text-xs text-muted truncate">
              {a.code} · {a.market} · {a.asset_type === "fund" ? "场外基金" : "股票/场内"}
              {a.platform && ` · ${a.platform}`}
              {a.watch_only && <span className="badge ml-2">仅观察</span>}
            </div>
          </div>
          <button
            className="btn-primary"
            disabled={runAi.isPending}
            onClick={() => runAi.mutate()}
          >
            <BrainCircuit className="w-4 h-4" />
            {runAi.isPending ? "AI 分析中…" : latest ? "重新分析" : "AI 分析"}
          </button>
          <Link
            to={`/assets/${assetId}`}
            className="btn"
            onClick={onClose}
            title="查看完整详情页"
          >
            <ArrowRight className="w-4 h-4" /> 详情页
          </Link>
          <button className="text-muted hover:text-white p-2 rounded-lg hover:bg-line/40" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ============ 滚动区 ============ */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {/* ---------- 1. 基本盘 5 指标 ---------- */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <KPI label="持仓" value={holding.total_shares > 0 ? fmtNum(holding.total_shares) : "未持有"} />
            <KPI label="均价" value={holding.avg_cost ? fmtNum(holding.avg_cost, 4) : "—"} />
            <KPI
              label="当前价"
              value={holding.current_price ? fmtNum(holding.current_price, 4) : "—"}
              tone="accent"
              hint={snapshot.data?.change_pct != null
                ? `${snapshot.data.change_pct >= 0 ? "+" : ""}${snapshot.data.change_pct.toFixed(2)}%`
                : undefined}
            />
            <KPI
              label="市值"
              value={fmtMoney(holding.market_value)}
              hint={holding.total_cost ? `成本 ${fmtMoney(holding.total_cost)}` : undefined}
            />
            <KPI
              label="浮动盈亏"
              value={holding.profit !== null ? fmtMoney(holding.profit) : "—"}
              tone={(holding.profit ?? 0) >= 0 ? "success" : "danger"}
              hint={fmtPct(holding.profit_pct)}
            />
          </div>

          {/* ---------- 2. 价格图表 ---------- */}
          <div className="card p-4 bg-bg-soft/30">
            <div className="flex items-center justify-between mb-2">
              <h4 className="font-medium text-sm flex items-center gap-2">
                <Activity className="w-4 h-4 text-accent" />
                {a.asset_type === "fund" && a.market === "OTC" ? "净值曲线" : "K 线"}
              </h4>
              <div className="flex gap-1.5">
                {RANGES.map(({ d, label }) => (
                  <button
                    key={d}
                    className={`btn !px-2 !py-0.5 text-[11px] ${days === d ? "border-accent/50 bg-accent/10 text-accent-soft" : ""}`}
                    onClick={() => setDays(d)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ height: 320 }}>
              {quote.isLoading ? (
                <div className="h-full flex items-center justify-center text-muted text-sm">加载行情中…</div>
              ) : quote.data && quote.data.points.length === 0 ? (
                <div className="h-full flex items-center justify-center text-muted text-sm">
                  未获取到行情数据{quote.data.error ? `：${quote.data.error}` : ""}
                </div>
              ) : (
                quote.data && <PriceChart quote={quote.data} height={320} />
              )}
            </div>
          </div>

          {/* ---------- 3. AI 分析富卡片 ---------- */}
          {latest ? (
            <AnalysisCard advice={latest} holding={holding} />
          ) : advices.isLoading ? (
            <div className="card p-8 text-center text-muted">加载历史建议中…</div>
          ) : (
            <EmptyAnalysis onRun={() => runAi.mutate()} running={runAi.isPending} />
          )}

          {/* ---------- 4. 历史分析记录（近 5 条） ---------- */}
          {(advices.data?.length || 0) > 1 && (
            <div className="card p-4 bg-bg-soft/20">
              <div className="flex items-center gap-2 mb-2">
                <Clock className="w-4 h-4 text-muted" />
                <span className="text-sm font-medium">历史分析</span>
                <span className="text-[11px] text-muted">（近 {Math.min(5, (advices.data?.length || 1) - 1)} 条）</span>
              </div>
              <div className="space-y-1.5">
                {advices.data!.slice(1, 6).map((h) => (
                  <div key={h.id} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded hover:bg-line/20">
                    <span className="text-muted font-mono shrink-0">{fmtDateTime(h.created_at)}</span>
                    <span className={`font-medium ${actionColor(h.action)} shrink-0`}>{actionLabel(h.action)}</span>
                    <span className="text-muted shrink-0">{(h.confidence * 100).toFixed(0)}%</span>
                    <span className="text-muted/80 truncate">{h.summary}</span>
                    <span className="text-[10px] text-muted/60 ml-auto shrink-0">
                      {h.source === "single" ? "单独" : "批量"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// =======================================================================
//  核心 AI 富卡片
// =======================================================================

function AnalysisCard({ advice, holding }: { advice: Advice; holding: any }) {
  const extra = advice.extra || {};
  const score = extra.score || {};
  const action = advice.action;
  const isHeuristic = (advice.skill_used || "").toLowerCase().includes("fallback");

  return (
    <div className="space-y-4">
      {/* ===== 3.1 顶部结论条 ===== */}
      <div className={`rounded-xl border p-5 ${
        action === "buy" ? "bg-emerald2/5 border-emerald2/40"
          : action === "sell" ? "bg-rose2/5 border-rose2/40"
          : "bg-amber2/5 border-amber2/40"
      }`}>
        <div className="flex items-start gap-4">
          <div className={`w-14 h-14 rounded-2xl flex items-center justify-center shrink-0 ${
            action === "buy" ? "bg-emerald2/20 border border-emerald2/40"
              : action === "sell" ? "bg-rose2/20 border border-rose2/40"
              : "bg-amber2/20 border border-amber2/40"
          }`}>
            {action === "buy"
              ? <TrendingUp className="w-7 h-7 text-emerald2" />
              : action === "sell"
                ? <TrendingDown className="w-7 h-7 text-rose2" />
                : <Minus className="w-7 h-7 text-amber2" />}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`text-2xl font-bold ${actionColor(action)}`}>
                {actionLabel(action)}
              </span>
              <div className="flex items-center gap-1.5 text-[11px] text-muted">
                <Gauge className="w-3.5 h-3.5" />
                置信度
                <span className="text-white font-medium font-mono">
                  {(advice.confidence * 100).toFixed(0)}%
                </span>
              </div>
              {extra.time_horizon && (
                <div className="flex items-center gap-1.5 text-[11px] text-muted">
                  <Clock className="w-3.5 h-3.5" />
                  <span className="text-white">{horizonLabel(extra.time_horizon)}</span>
                </div>
              )}
              {isHeuristic && (
                <span className="text-[10px] px-2 py-0.5 rounded-md border border-amber2/40 text-amber2 bg-amber2/5">
                  启发式回退
                </span>
              )}
            </div>
            <p className="text-sm mt-2 leading-relaxed text-white/95">{advice.summary}</p>
            <div className="text-[11px] text-muted mt-2">
              {fmtDateTime(advice.created_at)} · via {advice.skill_used}
            </div>
          </div>

          {/* 价位目标 */}
          {(extra.target_price != null || extra.stop_loss != null) && (
            <div className="shrink-0 space-y-2 min-w-[130px]">
              {extra.target_price != null && (
                <div className="rounded-lg border border-emerald2/30 bg-emerald2/5 px-3 py-2">
                  <div className="text-[10px] text-muted flex items-center gap-1">
                    <Target className="w-3 h-3" /> 目标价
                  </div>
                  <div className="text-emerald2 font-mono font-semibold mt-0.5">
                    {fmtNum(extra.target_price, 4)}
                  </div>
                  {holding.current_price && (
                    <div className="text-[10px] text-muted">
                      空间 {fmtPct((extra.target_price / holding.current_price - 1) * 100)}
                    </div>
                  )}
                </div>
              )}
              {extra.stop_loss != null && (
                <div className="rounded-lg border border-rose2/30 bg-rose2/5 px-3 py-2">
                  <div className="text-[10px] text-muted flex items-center gap-1">
                    <Shield className="w-3 h-3" /> 止损位
                  </div>
                  <div className="text-rose2 font-mono font-semibold mt-0.5">
                    {fmtNum(extra.stop_loss, 4)}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ===== 3.2 四维评分雷达 ===== */}
      {Object.keys(score).length > 0 && (
        <div className="card p-4 bg-bg-soft/30">
          <div className="flex items-center gap-2 mb-3">
            <Gauge className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">四维评分</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <ScoreBar label="技术面" value={score.technical} color="accent" />
            <ScoreBar label="基本面" value={score.fundamental} color="emerald2" />
            <ScoreBar label="情绪面" value={score.sentiment} color="cyan" />
            <ScoreBar label="风险度" value={score.risk} color="rose2" reverse />
          </div>
        </div>
      )}

      {/* ===== 3.3 宏观 / 微观 / 基本面 ===== */}
      <div className="grid md:grid-cols-3 gap-3">
        <InsightCard
          icon={<Sparkles className="w-4 h-4" />}
          title="基本面"
          text={extra.fundamentals}
          tone="emerald"
        />
        <InsightCard
          icon={<Activity className="w-4 h-4" />}
          title="宏观"
          text={extra.macro}
          tone="cyan"
        />
        <InsightCard
          icon={<Zap className="w-4 h-4" />}
          title="微观"
          text={extra.micro}
          tone="purple"
        />
      </div>

      {/* ===== 3.4 优势 vs 风险 ===== */}
      {((extra.pros && extra.pros.length > 0) || (extra.risks && extra.risks.length > 0)) && (
        <div className="grid md:grid-cols-2 gap-3">
          {extra.pros && extra.pros.length > 0 && (
            <ProConList
              title="优势 / 看多理由"
              items={extra.pros}
              icon={<TrendingUp className="w-4 h-4 text-emerald2" />}
              tone="emerald"
            />
          )}
          {extra.risks && extra.risks.length > 0 && (
            <ProConList
              title="风险 / 看空警示"
              items={extra.risks}
              icon={<AlertTriangle className="w-4 h-4 text-rose2" />}
              tone="rose"
            />
          )}
        </div>
      )}

      {/* ===== 3.5 操作建议 ===== */}
      {extra.advice && (
        <div className="rounded-xl border border-accent/30 bg-gradient-to-br from-accent/10 to-emerald2/5 p-4">
          <div className="flex items-center gap-2 mb-1.5">
            <Target className="w-4 h-4 text-accent" />
            <span className="text-sm font-medium">具体操作建议</span>
          </div>
          <p className="text-sm leading-relaxed text-white/95">{extra.advice}</p>
        </div>
      )}

      {/* ===== 3.6 兜底：原始 detail（若 extra 为空但后端有 detail） ===== */}
      {!hasStructured(extra) && advice.detail && (
        <details className="card p-3">
          <summary className="text-xs text-muted cursor-pointer hover:text-white">查看原始分析文本</summary>
          <pre className="text-[11px] text-muted mt-2 whitespace-pre-wrap">{advice.detail}</pre>
        </details>
      )}
    </div>
  );
}

function hasStructured(extra: Advice["extra"]): boolean {
  if (!extra) return false;
  return !!(extra.fundamentals || extra.macro || extra.micro ||
    (extra.risks && extra.risks.length) || (extra.pros && extra.pros.length) ||
    extra.advice);
}

function horizonLabel(h: string): string {
  if (h === "short") return "短线";
  if (h === "long") return "长线";
  return "中线";
}

// =======================================================================
//  小组件
// =======================================================================

function KPI({ label, value, hint, tone = "default" }: {
  label: string; value: string; hint?: string;
  tone?: "default" | "accent" | "success" | "danger";
}) {
  const c = tone === "accent" ? "text-accent-soft"
    : tone === "success" ? "text-emerald2"
    : tone === "danger" ? "text-rose2"
    : "text-white";
  return (
    <div className="rounded-xl border border-line/60 bg-bg-soft/40 px-3 py-2.5">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`text-base font-semibold font-mono mt-0.5 ${c}`}>{value}</div>
      {hint && <div className="text-[10px] text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

function ScoreBar({ label, value, color, reverse }: {
  label: string; value?: number; color: "accent" | "emerald2" | "cyan" | "rose2"; reverse?: boolean;
}) {
  const v = Math.max(0, Math.min(100, value ?? 50));
  // reverse: 风险 70+ 显红，越低越好
  const good = reverse ? v < 40 : v >= 60;
  const bad = reverse ? v >= 70 : v < 40;
  const barColor = bad
    ? "from-rose2 to-rose2/70"
    : good
      ? "from-emerald2 to-emerald2/70"
      : `from-${color} to-${color}/60`;
  // 注意 tailwind JIT：为了确保 class 被识别，直接枚举 class 会更稳；这里用内联 style 兜底
  const rgbMap: Record<string, string> = {
    accent: "#7c5cff",
    emerald2: "#34d399",
    cyan: "#22d3ee",
    rose2: "#fb7185",
  };
  const tintColor = bad ? rgbMap.rose2 : good ? rgbMap.emerald2 : rgbMap[color];
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className="text-muted">{label}</span>
        <span className="font-mono text-white">{v}</span>
      </div>
      <div className="h-2 rounded-full bg-bg overflow-hidden border border-line/40">
        <div
          className={`h-full bg-gradient-to-r ${barColor} transition-all`}
          style={{ width: `${v}%`, backgroundColor: tintColor }}
        />
      </div>
    </div>
  );
}

function InsightCard({ icon, title, text, tone }: {
  icon: React.ReactNode; title: string; text?: string;
  tone: "emerald" | "cyan" | "purple";
}) {
  const classes = {
    emerald: "border-emerald2/30 bg-emerald2/5 text-emerald2",
    cyan: "border-cyan-400/30 bg-cyan-400/5 text-cyan-400",
    purple: "border-purple-400/30 bg-purple-400/5 text-purple-400",
  };
  return (
    <div className="card p-3 bg-bg-soft/30">
      <div className={`flex items-center gap-1.5 text-xs font-medium mb-1.5 ${classes[tone].split(" ")[2]}`}>
        {icon}
        {title}
      </div>
      <p className="text-xs leading-relaxed text-white/80 min-h-[3rem]">
        {text || <span className="text-muted/60">暂无数据</span>}
      </p>
    </div>
  );
}

function ProConList({ title, items, icon, tone }: {
  title: string; items: string[]; icon: React.ReactNode;
  tone: "emerald" | "rose";
}) {
  const cls = tone === "emerald"
    ? "border-emerald2/20 bg-emerald2/5"
    : "border-rose2/20 bg-rose2/5";
  const dotCls = tone === "emerald" ? "bg-emerald2" : "bg-rose2";
  return (
    <div className={`rounded-xl border p-4 ${cls}`}>
      <div className="flex items-center gap-2 text-sm font-medium mb-2">
        {icon}
        {title}
      </div>
      <ul className="space-y-1.5">
        {items.map((x, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-white/90 leading-relaxed">
            <span className={`w-1 h-1 rounded-full mt-2 shrink-0 ${dotCls}`} />
            <span>{x}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EmptyAnalysis({ onRun, running }: { onRun: () => void; running: boolean }) {
  return (
    <div className="card p-10 text-center bg-bg-soft/30">
      <div className="w-14 h-14 mx-auto rounded-2xl bg-gradient-to-br from-accent to-emerald2 flex items-center justify-center shadow-glow mb-3">
        <BrainCircuit className="w-7 h-7 text-white" />
      </div>
      <h4 className="font-semibold mb-1">尚未分析过这个标的</h4>
      <p className="text-sm text-muted mb-4">点击下方按钮让 Hermes-Lite 生成一份包含基本面、宏观、微观和操作建议的完整分析</p>
      <button className="btn-primary" onClick={onRun} disabled={running}>
        {running ? <RefreshCw className="w-4 h-4 animate-spin" /> : <BrainCircuit className="w-4 h-4" />}
        {running ? "分析中…" : "立即 AI 分析"}
      </button>
    </div>
  );
}
