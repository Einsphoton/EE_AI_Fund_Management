import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X, BrainCircuit, Clock, Activity, ArrowRight, RefreshCw,
} from "lucide-react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";

import PriceChart from "./PriceChart";
import AnalysisCard from "./AnalysisCard";
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
