/**
 * AI 分析富卡片（可复用）——从 AssetAnalysisModal 抽出，供详情页/弹窗/建议页共用。
 *
 * 视觉结构：
 *   1. 顶部结论条（action + confidence + time_horizon + summary + target/stop）
 *   2. 四维评分（technical / fundamental / sentiment / risk）
 *   3. 【AI 深度点评】 commentary —— 主观长文，最突出
 *   4. 基本面 / 宏观 / 微观 三栏洞察
 *   5. 优势 / 风险 双栏
 *   6. 操作建议（advice 短清单）
 *   7. 兜底：解析失败时展示原始 detail
 */
import {
  Sparkles, TrendingUp, TrendingDown, Minus, Shield, AlertTriangle,
  Target, Zap, Clock, Activity, Gauge, MessageSquareQuote,
} from "lucide-react";
import type { Advice, Holding } from "../api/client";
import { fmtNum, fmtPct, actionColor, actionLabel, fmtDateTime } from "../lib/format";
import MarkdownView from "./MarkdownView";

interface Props {
  advice: Advice;
  holding?: Holding | null;
}

export default function AnalysisCard({ advice, holding }: Props) {
  const extra = advice.extra || {};
  const score = extra.score || {};
  const action = advice.action;
  const skillUsed = (advice.skill_used || "").toLowerCase();
  const isHeuristic = skillUsed.includes("fallback");
  const isPartial = skillUsed.includes("partial");

  return (
    <div className="space-y-4">
      {/* ===== 1. 顶部结论条 ===== */}
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
              {isPartial && (
                <span className="text-[10px] px-2 py-0.5 rounded-md border border-amber2/40 text-amber2 bg-amber2/5" title="LLM 返回解析失败，已从原文救援部分字段">
                  部分解析
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
                  {holding?.current_price && (
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

      {/* ===== 2. 四维评分 ===== */}
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

      {/* ===== 3. AI 深度点评（commentary，主观长文，最显眼）===== */}
      {extra.commentary && (
        <div className="rounded-2xl border-2 border-accent/40 bg-gradient-to-br from-accent/15 via-accent/5 to-bg-soft/30 p-5 shadow-lg shadow-accent/10">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-8 h-8 rounded-lg bg-accent/20 border border-accent/40 flex items-center justify-center">
              <MessageSquareQuote className="w-4 h-4 text-accent" />
            </div>
            <div className="flex-1">
              <div className="text-base font-semibold text-white">AI 深度点评</div>
              <div className="text-[11px] text-muted">分析师主观视角，自由发挥的判断与态度</div>
            </div>
          </div>
          <div className="pl-1">
            <MarkdownView content={extra.commentary} />
          </div>
        </div>
      )}

      {/* ===== 4. 基本面 / 宏观 / 微观 ===== */}
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

      {/* ===== 5. 优势 vs 风险 ===== */}
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

      {/* ===== 6. 操作清单（advice，短可执行）===== */}
      {extra.advice && (
        <div className="rounded-xl border border-emerald2/30 bg-gradient-to-br from-emerald2/10 to-accent/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-4 h-4 text-emerald2" />
            <span className="text-sm font-medium">操作清单</span>
            <span className="text-[10px] text-muted ml-1">仓位 / 节奏 / 触发 / 止盈止损</span>
          </div>
          <MarkdownView content={extra.advice} />
        </div>
      )}

      {/* ===== 7. 兜底：解析失败时的原始分析文本 ===== */}
      {!hasStructured(extra) && advice.detail && (
        <details className="card p-3">
          <summary className="text-xs text-muted cursor-pointer hover:text-white">查看原始分析文本</summary>
          <pre className="text-[11px] text-muted mt-2 whitespace-pre-wrap">{advice.detail}</pre>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 工具函数 & 子组件
// ---------------------------------------------------------------------------

export function hasStructured(extra: Advice["extra"]): boolean {
  if (!extra) return false;
  return !!(extra.fundamentals || extra.macro || extra.micro ||
    (extra.risks && extra.risks.length) || (extra.pros && extra.pros.length) ||
    extra.advice || extra.commentary);
}

function horizonLabel(h: string): string {
  if (h === "short") return "短线";
  if (h === "long") return "长线";
  return "中线";
}

function ScoreBar({ label, value, color, reverse }: {
  label: string; value?: number; color: "accent" | "emerald2" | "cyan" | "rose2"; reverse?: boolean;
}) {
  const v = Math.max(0, Math.min(100, value ?? 50));
  const good = reverse ? v < 40 : v >= 60;
  const bad = reverse ? v >= 70 : v < 40;
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
          className="h-full transition-all"
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
  const textCls = tone === "emerald" ? "text-emerald2"
    : tone === "cyan" ? "text-cyan-400"
    : "text-purple-400";
  return (
    <div className="card p-3 bg-bg-soft/30">
      <div className={`flex items-center gap-1.5 text-xs font-medium mb-1.5 ${textCls}`}>
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
