import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit, RefreshCw, ChevronDown, ChevronRight, Clock,
  TrendingUp, TrendingDown, Minus, Check, Zap,
  LayoutList, LayoutGrid, PanelsTopLeft, Sparkles,
  Play, Square, Terminal,
} from "lucide-react";

import PageHeader from "../components/PageHeader";
import AssetAnalysisModal from "../components/AssetAnalysisModal";
import {
  AdviceApi, Assets, Advice as AdviceT,
} from "../api/client";
import { actionColor, actionLabel, fmtDateTime, fmtTime, parseLocalDate } from "../lib/format";
import { useAnalysisTask, AnalysisLog } from "../lib/analysisTask";

type FilterAction = "all" | "buy" | "sell" | "hold";
type ViewMode = "list" | "card" | "split";

interface BatchGroup {
  batchId: string;
  firstAt: string;
  lastAt: string;
  items: AdviceT[];
  stats: { buy: number; sell: number; hold: number };
}

function groupByBatch(items: AdviceT[]): BatchGroup[] {
  const map = new Map<string, AdviceT[]>();
  for (const a of items) {
    const key = a.batch_id || `legacy_${(a.created_at || "").slice(0, 19)}`;
    (map.get(key) || map.set(key, []).get(key)!).push(a);
  }
  const groups: BatchGroup[] = [];
  for (const [batchId, its] of map.entries()) {
    its.sort((a, b) => (a.created_at > b.created_at ? 1 : -1));
    const stats = { buy: 0, sell: 0, hold: 0 };
    for (const a of its) {
      if (a.action === "buy") stats.buy++;
      else if (a.action === "sell") stats.sell++;
      else stats.hold++;
    }
    groups.push({
      batchId,
      firstAt: its[0].created_at,
      lastAt: its[its.length - 1].created_at,
      items: its,
      stats,
    });
  }
  groups.sort((a, b) => (a.lastAt > b.lastAt ? -1 : 1));
  return groups;
}

function batchDuration(g: BatchGroup): string {
  const s = parseLocalDate(g.firstAt);
  const e = parseLocalDate(g.lastAt);
  if (!s || !e) return "";
  const sec = Math.max(1, Math.round((e.getTime() - s.getTime()) / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

const VIEW_STORAGE_KEY = "ee-fund.advice.viewMode";

// ===========================================================================

export default function Advice() {
  const qc = useQueryClient();
  const task = useAnalysisTask();
  const advices = useQuery({
    queryKey: ["advice", "recent", "batch"],
    queryFn: () => AdviceApi.recent(300, "batch"),
  });
  const holdings = useQuery({ queryKey: ["holdings"], queryFn: Assets.holdings });

  const [filter, setFilter] = useState<FilterAction>("all");
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try { return (localStorage.getItem(VIEW_STORAGE_KEY) as ViewMode) || "list"; }
    catch { return "list"; }
  });
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [analyzingId, setAnalyzingId] = useState<number | null>(null);

  const setView = (v: ViewMode) => {
    setViewMode(v);
    try { localStorage.setItem(VIEW_STORAGE_KEY, v); } catch {}
  };

  const nameOf = (id: number | null) => {
    if (!id) return "—";
    return holdings.data?.find((h) => h.asset.id === id)?.asset.name || `#${id}`;
  };

  const groups = useMemo(() => {
    let items = advices.data || [];
    if (filter !== "all") items = items.filter((a) => a.action === filter);
    return groupByBatch(items);
  }, [advices.data, filter]);

  useMemo(() => {
    if (viewMode === "split" && groups.length && !groups.find((g) => g.batchId === selectedBatch)) {
      setSelectedBatch(groups[0].batchId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, groups.length]);

  useMemo(() => {
    if (viewMode === "list" && groups.length && Object.keys(expanded).length === 0) {
      setExpanded({ [groups[0].batchId]: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, groups.length]);

  const toggle = (k: string) => setExpanded((m) => ({ ...m, [k]: !m[k] }));
  const expandAll = () => setExpanded(Object.fromEntries(groups.map((g) => [g.batchId, true])));
  const collapseAll = () => setExpanded({});

  const total = advices.data?.length || 0;
  const shownCount = groups.reduce((n, g) => n + g.items.length, 0);
  const selectedGroup = groups.find((g) => g.batchId === selectedBatch);

  const openAsset = (id: number | null) => {
    if (id) setAnalyzingId(id);
  };

  return (
    <>
      <PageHeader
        title="AI 建议"
        subtitle="Hermes-Lite Agent 按分析批次沉淀的历史建议"
        actions={
          <>
            <button
              className="btn"
              onClick={() => qc.invalidateQueries({ queryKey: ["advice"] })}
            >
              <RefreshCw className="w-4 h-4" /> 刷新
            </button>
            <button
              className="btn-primary"
              disabled={task.running}
              onClick={() => task.start()}
            >
              <BrainCircuit className="w-4 h-4" />
              {task.running ? "分析进行中…" : "立即分析所有标的"}
            </button>
          </>
        }
      />

      {/* ---- 嵌入式分析任务面板（有任务就出现，不阻断页面） ---- */}
      {task.started && <EmbeddedTaskPanel />}

      {/* ---- 工具条 ---- */}
      <div className="card p-3 mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 text-xs">
          {([
            { k: "all", label: "全部", cls: "" },
            { k: "buy", label: "买入", cls: "text-emerald2" },
            { k: "sell", label: "卖出", cls: "text-rose2" },
            { k: "hold", label: "持有", cls: "text-amber2" },
          ] as { k: FilterAction; label: string; cls: string }[]).map((f) => (
            <button
              key={f.k}
              className={`px-3 py-1.5 rounded-lg border transition ${
                filter === f.k
                  ? "border-accent/60 bg-accent/15 text-white"
                  : "border-line text-muted hover:text-white hover:border-line/80"
              }`}
              onClick={() => setFilter(f.k)}
            >
              <span className={filter === f.k ? "" : f.cls}>{f.label}</span>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-0.5 ml-3 p-0.5 rounded-lg border border-line bg-bg-soft/40">
          <ViewBtn mode="list" current={viewMode} onClick={setView} icon={<LayoutList className="w-3.5 h-3.5" />} label="列表" />
          <ViewBtn mode="card" current={viewMode} onClick={setView} icon={<LayoutGrid className="w-3.5 h-3.5" />} label="卡片" />
          <ViewBtn mode="split" current={viewMode} onClick={setView} icon={<PanelsTopLeft className="w-3.5 h-3.5" />} label="双栏" />
        </div>

        <div className="ml-auto flex items-center gap-2 text-xs text-muted">
          <span>
            共 <span className="text-white">{shownCount}</span>
            {filter !== "all" && <> / {total}</>} 条 · {groups.length} 批
          </span>
          {viewMode === "list" && (
            <>
              <button className="btn !py-1 !px-2 text-xs" onClick={expandAll} disabled={!groups.length}>
                全部展开
              </button>
              <button className="btn !py-1 !px-2 text-xs" onClick={collapseAll} disabled={!groups.length}>
                全部折叠
              </button>
            </>
          )}
        </div>
      </div>

      {/* ---- 内容区 ---- */}
      {groups.length === 0 ? (
        <div className="card p-12 text-center text-muted">
          {advices.isLoading
            ? "加载中…"
            : filter !== "all"
              ? "当前筛选下没有建议，切回「全部」或执行一次分析看看"
              : "还没有建议。点击右上角「立即分析所有标的」开始"}
        </div>
      ) : viewMode === "list" ? (
        <ListView
          groups={groups}
          expanded={expanded}
          toggle={toggle}
          nameOf={nameOf}
          onAsset={openAsset}
        />
      ) : viewMode === "card" ? (
        <CardView groups={groups} nameOf={nameOf} onAsset={openAsset} />
      ) : (
        <SplitView
          groups={groups}
          selectedBatch={selectedBatch}
          onSelect={setSelectedBatch}
          selectedGroup={selectedGroup}
          nameOf={nameOf}
          onAsset={openAsset}
        />
      )}

      {analyzingId !== null && (
        <AssetAnalysisModal
          assetId={analyzingId}
          onClose={() => setAnalyzingId(null)}
        />
      )}
    </>
  );
}

function ViewBtn({ mode, current, onClick, icon, label }: {
  mode: ViewMode; current: ViewMode; onClick: (m: ViewMode) => void;
  icon: React.ReactNode; label: string;
}) {
  const active = mode === current;
  return (
    <button
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition ${
        active ? "bg-accent/15 text-white border border-accent/40" : "text-muted hover:text-white"
      }`}
      onClick={() => onClick(mode)}
      title={`${label}视图`}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

// ===========================================================================
//  嵌入式任务面板（替代原模态框）
// ===========================================================================
function EmbeddedTaskPanel() {
  const task = useAnalysisTask();
  const [showLogs, setShowLogs] = useState(true); // 默认展开日志，方便用户看进度
  const logRef = useRef<HTMLDivElement>(null);

  // 日志追加时自动滚到底
  useEffect(() => {
    if (showLogs && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [task.logs.length, showLogs]);

  const done = !task.running && task.finishedAt !== null;
  const aborted = !task.running && !done;
  const p = task.progress;

  return (
    <div
      className={`card mb-4 border-l-4 transition ${
        done
          ? "border-l-emerald2"
          : task.running
            ? "border-l-accent"
            : "border-l-amber2"
      }`}
    >
      {/* 头部：状态 + 进度条 + 操作按钮 */}
      <div className="px-4 py-3 flex items-center gap-3">
        <div
          className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${
            done
              ? "bg-emerald2/20 border border-emerald2/40"
              : task.running
                ? "bg-gradient-to-br from-accent to-emerald2 shadow-glow"
                : "bg-amber2/20 border border-amber2/40"
          }`}
        >
          {done ? (
            <Check className="w-5 h-5 text-emerald2" />
          ) : (
            <BrainCircuit className={`w-5 h-5 text-white ${task.running ? "animate-pulse" : ""}`} />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="font-medium">
              {done
                ? "分析已完成"
                : task.running
                  ? "Hermes-Lite 批量分析进行中"
                  : "分析已中止"}
            </span>
            <span className="text-xs text-muted">
              {p.total > 0 ? (
                <>
                  {p.current}/{p.total}
                  {p.failed > 0 && <span className="text-rose2 ml-1">· 失败 {p.failed}</span>}
                  {task.concurrency > 1 && (
                    <span className="ml-1 text-accent-soft">· 并发 {task.concurrency}</span>
                  )}
                </>
              ) : "正在初始化…"}
            </span>
            {task.batchId && (
              <span className="text-[10px] text-muted font-mono truncate">
                批次 {task.batchId.slice(0, 16)}…
              </span>
            )}
          </div>
          {/* 进度条 */}
          <div className="h-1 mt-2 rounded-full bg-bg-soft overflow-hidden border border-line/40">
            <div
              className={`h-full transition-all ${
                task.running
                  ? "bg-gradient-to-r from-accent to-emerald2"
                  : done
                    ? "bg-emerald2"
                    : "bg-amber2"
              }`}
              style={{ width: `${task.percent}%` }}
            />
          </div>
          {/* 并发模式下展示正在跑的多个标的 */}
          {task.running && task.runningList.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {task.runningList.map((r) => (
                <span
                  key={r.assetId}
                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-accent/40 bg-accent/10 text-[11px] text-accent-soft"
                  title={`#${r.index} ${r.name}（${r.code}）`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                  <span className="truncate max-w-[140px]">{r.name}</span>
                  <span className="text-[10px] text-muted font-mono">
                    {Math.max(0, Math.round((Date.now() - r.startedAt) / 1000))}s
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="shrink-0 flex items-center gap-2">
          <span className="font-mono text-xs text-muted">{task.percent}%</span>
          <button
            className="btn !py-1.5 !px-2 text-xs"
            onClick={() => setShowLogs((v) => !v)}
            title={showLogs ? "折叠日志" : "展开日志"}
          >
            <Terminal className="w-3.5 h-3.5" />
            {showLogs ? "折叠" : "查看日志"}
          </button>
          {task.running ? (
            <button className="btn-danger !py-1.5 !px-3 text-xs" onClick={() => task.stop()}>
              <Square className="w-3.5 h-3.5" /> 停止
            </button>
          ) : done || aborted ? (
            <>
              <button className="btn !py-1.5 !px-3 text-xs" onClick={() => task.start()}>
                <Play className="w-3.5 h-3.5" /> 再次分析
              </button>
              <button className="btn !py-1.5 !px-2 text-xs" onClick={() => task.reset()} title="关闭任务面板">
                关闭
              </button>
            </>
          ) : null}
        </div>
      </div>

      {/* 滚动日志 */}
      {showLogs && (
        <div
          ref={logRef}
          className="border-t border-line/40 max-h-[240px] overflow-y-auto px-4 py-3 font-mono text-[12px] leading-relaxed bg-bg/40"
        >
          {task.logs.length === 0 ? (
            <div className="text-muted flex items-center gap-2 py-2">
              <Zap className="w-3.5 h-3.5" /> 等待服务端推送…
            </div>
          ) : (
            <>
              {task.logs.map((l, i) => (
                <LogLineItem key={i} l={l} />
              ))}
              {task.running && (
                <div className="flex items-center gap-2 text-muted mt-1">
                  <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                  <span className="text-[11px]">等待下一条…</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function LogLineItem({ l }: { l: AnalysisLog }) {
  return (
    <div className={logClass(l.kind)}>
      <span className="text-muted/70 mr-2">{hhmmss(l.ts)}</span>
      <span>{l.text}</span>
      {l.kind === "asset_done" && l.summary && (
        <div className="ml-[70px] mt-0.5 text-white/70 font-sans text-[11px] whitespace-pre-wrap break-words">
          {l.summary}
        </div>
      )}
    </div>
  );
}

function logClass(kind: AnalysisLog["kind"]): string {
  switch (kind) {
    case "asset_start": return "text-accent-soft";
    case "asset_done": return "text-emerald2";
    case "asset_error": return "text-rose2";
    case "done": return "text-emerald2 font-semibold mt-1";
    case "info": return "text-white";
    case "log":
    default: return "text-muted";
  }
}

function hhmmss(ts: number): string {
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

// ===========================================================================
//  视图 A: 列表
// ===========================================================================
function ListView({ groups, expanded, toggle, nameOf, onAsset }: {
  groups: BatchGroup[];
  expanded: Record<string, boolean>;
  toggle: (k: string) => void;
  nameOf: (id: number | null) => string;
  onAsset: (id: number | null) => void;
}) {
  return (
    <div className="space-y-3">
      {groups.map((g) => {
        const open = !!expanded[g.batchId];
        const isLegacy = g.batchId.startsWith("legacy_");
        return (
          <div key={g.batchId} className="card overflow-hidden">
            <button
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-line/20 transition text-left"
              onClick={() => toggle(g.batchId)}
            >
              {open
                ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
                : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
              <Clock className="w-4 h-4 text-accent-soft shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {fmtDateTime(g.lastAt)}
                  {isLegacy && <span className="ml-2 text-[10px] text-muted font-normal">（历史数据）</span>}
                </div>
                <div className="text-[11px] text-muted mt-0.5 font-mono truncate">
                  批次 {g.batchId.slice(0, 16)}… · 耗时 {batchDuration(g)}
                </div>
              </div>
              <BatchStats stats={g.stats} count={g.items.length} />
            </button>
            {open && (
              <div className="border-t border-line/40 divide-y divide-line/30">
                {g.items.map((a) => (
                  <AdviceRow key={a.id} a={a} nameOf={nameOf} onAsset={onAsset} />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ===========================================================================
//  视图 B: 卡片流
// ===========================================================================
function CardView({ groups, nameOf, onAsset }: {
  groups: BatchGroup[];
  nameOf: (id: number | null) => string;
  onAsset: (id: number | null) => void;
}) {
  return (
    <div className="space-y-5">
      {groups.map((g) => (
        <div key={g.batchId} className="card p-4">
          <div className="flex items-center gap-3 mb-3 pb-3 border-b border-line/40">
            <Clock className="w-4 h-4 text-accent-soft" />
            <div className="flex-1">
              <div className="font-medium">{fmtDateTime(g.lastAt)}</div>
              <div className="text-[11px] text-muted font-mono">
                {g.batchId.slice(0, 16)}… · 耗时 {batchDuration(g)}
              </div>
            </div>
            <BatchStats stats={g.stats} count={g.items.length} />
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {g.items.map((a) => (
              <AdviceMiniCard key={a.id} a={a} nameOf={nameOf} onAsset={onAsset} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ===========================================================================
//  视图 C: 双栏
// ===========================================================================
function SplitView({ groups, selectedBatch, onSelect, selectedGroup, nameOf, onAsset }: {
  groups: BatchGroup[];
  selectedBatch: string | null;
  onSelect: (id: string) => void;
  selectedGroup: BatchGroup | undefined;
  nameOf: (id: number | null) => string;
  onAsset: (id: number | null) => void;
}) {
  return (
    <div className="grid grid-cols-12 gap-4" style={{ minHeight: 500 }}>
      <aside className="col-span-12 md:col-span-4 lg:col-span-3">
        <div className="card p-2 max-h-[72vh] overflow-y-auto">
          {groups.map((g) => {
            const active = g.batchId === selectedBatch;
            return (
              <button
                key={g.batchId}
                onClick={() => onSelect(g.batchId)}
                className={`w-full text-left px-3 py-2.5 rounded-lg mb-1 transition border ${
                  active
                    ? "bg-accent/15 border-accent/40"
                    : "border-transparent hover:bg-line/30"
                }`}
              >
                <div className="flex items-center gap-2">
                  <Clock className={`w-3.5 h-3.5 shrink-0 ${active ? "text-accent" : "text-muted"}`} />
                  <div className="font-medium text-sm truncate flex-1">
                    {fmtDateTime(g.lastAt)}
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-muted">
                  <span>{g.items.length} 条</span>
                  <span>·</span>
                  <span>{batchDuration(g)}</span>
                  <div className="ml-auto flex items-center gap-1.5">
                    {g.stats.buy > 0 && <span className="text-emerald2">↑{g.stats.buy}</span>}
                    {g.stats.sell > 0 && <span className="text-rose2">↓{g.stats.sell}</span>}
                    {g.stats.hold > 0 && <span className="text-amber2">={g.stats.hold}</span>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </aside>

      <section className="col-span-12 md:col-span-8 lg:col-span-9">
        {selectedGroup ? (
          <>
            <div className="card p-3 mb-3 flex items-center gap-3">
              <Clock className="w-4 h-4 text-accent-soft" />
              <div className="flex-1">
                <div className="font-medium">{fmtDateTime(selectedGroup.lastAt)}</div>
                <div className="text-[11px] text-muted font-mono">
                  批次 {selectedGroup.batchId} · 耗时 {batchDuration(selectedGroup)}
                </div>
              </div>
              <BatchStats stats={selectedGroup.stats} count={selectedGroup.items.length} />
            </div>
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {selectedGroup.items.map((a) => (
                <AdviceMiniCard key={a.id} a={a} nameOf={nameOf} onAsset={onAsset} />
              ))}
            </div>
          </>
        ) : (
          <div className="card p-12 text-center text-muted">从左侧选择一个批次</div>
        )}
      </section>
    </div>
  );
}

// ===========================================================================
//  子组件
// ===========================================================================

function BatchStats({ stats, count }: { stats: BatchGroup["stats"]; count: number }) {
  return (
    <div className="flex items-center gap-3 text-[11px] text-muted shrink-0">
      {stats.buy > 0 && (
        <span className="flex items-center gap-1">
          <TrendingUp className="w-3 h-3 text-emerald2" />
          <span className="text-emerald2 font-medium">{stats.buy}</span>
        </span>
      )}
      {stats.sell > 0 && (
        <span className="flex items-center gap-1">
          <TrendingDown className="w-3 h-3 text-rose2" />
          <span className="text-rose2 font-medium">{stats.sell}</span>
        </span>
      )}
      {stats.hold > 0 && (
        <span className="flex items-center gap-1">
          <Minus className="w-3 h-3 text-amber2" />
          <span className="text-amber2 font-medium">{stats.hold}</span>
        </span>
      )}
      <span className="text-muted">共 {count} 条</span>
    </div>
  );
}

function AdviceRow({ a, nameOf, onAsset }: {
  a: AdviceT;
  nameOf: (id: number | null) => string;
  onAsset: (id: number | null) => void;
}) {
  const [showDetail, setShowDetail] = useState(false);
  return (
    <div className="px-4 py-3 hover:bg-line/10 transition">
      <div className="flex items-start gap-3">
        <div className="font-mono text-[11px] text-muted w-12 shrink-0 pt-0.5">{fmtTime(a.created_at)}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <button
              className="font-medium hover:text-accent-soft truncate text-left"
              onClick={() => onAsset(a.asset_id)}
              title="点击查看 AI 分析卡"
            >
              {nameOf(a.asset_id)}
            </button>
            <span className={`text-xs font-semibold ${actionColor(a.action)}`}>
              {actionLabel(a.action)}
            </span>
            <span className="text-[11px] text-muted">{(a.confidence * 100).toFixed(0)}%</span>
            <span className="text-[11px] text-muted ml-auto truncate max-w-[40%]">
              via {a.skill_used}
            </span>
          </div>
          <p className="text-sm mt-1 leading-relaxed text-white/90">{a.summary}</p>
          {a.detail && (
            <>
              <button
                className="text-[11px] text-muted hover:text-accent-soft mt-1.5 transition"
                onClick={() => setShowDetail((v) => !v)}
              >
                {showDetail ? "收起详细分析" : "查看详细分析"}
              </button>
              {showDetail && (
                <pre className="text-[11px] text-muted mt-2 whitespace-pre-wrap bg-bg/40 rounded-lg p-3 border border-line/40">
                  {a.detail}
                </pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function AdviceMiniCard({ a, nameOf, onAsset }: {
  a: AdviceT;
  nameOf: (id: number | null) => string;
  onAsset: (id: number | null) => void;
}) {
  const actionCls =
    a.action === "buy" ? "bg-emerald2/10 border-emerald2/30"
      : a.action === "sell" ? "bg-rose2/10 border-rose2/30"
      : "bg-amber2/10 border-amber2/30";
  return (
    <button
      className={`text-left rounded-xl border p-3.5 transition hover:border-accent/50 hover:bg-accent/5 ${actionCls}`}
      onClick={() => onAsset(a.asset_id)}
    >
      <div className="flex items-start gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="font-medium truncate flex items-center gap-1">
            {nameOf(a.asset_id)}
            <Sparkles className="w-3 h-3 text-accent opacity-60" />
          </div>
          <div className="text-[10px] text-muted font-mono mt-0.5">
            {fmtTime(a.created_at)}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-sm font-semibold ${actionColor(a.action)}`}>
            {actionLabel(a.action)}
          </div>
          <div className="text-[10px] text-muted">{(a.confidence * 100).toFixed(0)}%</div>
        </div>
      </div>
      <p className="text-xs leading-relaxed text-white/85 line-clamp-3">{a.summary}</p>
    </button>
  );
}
