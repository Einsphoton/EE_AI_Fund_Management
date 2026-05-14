import type { ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowUpRight, BrainCircuit, ListTodo, RefreshCw, TrendingUp } from "lucide-react";

import PageHeader from "../components/PageHeader";
import StatCard from "../components/StatCard";
import { Advice as AdviceT, Asset, Assets, AdviceApi, Holding, TodoApi, TodoItem } from "../api/client";
import { fmtMoney, fmtPct, actionColor, actionLabel, dateOnly } from "../lib/format";
import { useAnalysisTask } from "../lib/analysisTask";

export default function Dashboard() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const task = useAnalysisTask();
  const assetsQuery = useQuery({
    queryKey: ["assets"],
    queryFn: Assets.list,
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
  });
  const advices = useQuery({
    queryKey: ["advice", "recent", "batch"],
    queryFn: () => AdviceApi.recent(80, "batch"),
  });
  const todos = useQuery({
    queryKey: ["todos", "pending", "dashboard"],
    queryFn: () => TodoApi.list("pending"),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const onRunAll = () => {
    // 启动全局任务并跳转到 AI 分析页查看实时进度
    task.start();
    nav("/advice");
  };

  const placeholderHolding = (asset: Asset): Holding => ({
    asset,
    total_shares: 0,
    total_cost: 0,
    avg_cost: 0,
    total_fee: 0,
    realized_pnl: 0,
    current_price: null,
    market_value: null,
    profit: null,
    profit_pct: null,
  });
  const visibleAssets = (assetsQuery.data || []).filter((a) => !a.watch_only);
  const holdingQueries = useQueries({
    queries: visibleAssets.map((asset) => ({
      queryKey: ["holding", asset.id],
      queryFn: () => Assets.holding(asset.id),
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      refetchInterval: (query: any) => {
        const h = query.state.data as Holding | undefined;
        return h && h.total_shares > 0 && h.market_value == null ? 15_000 : false;
      },
    })),
  });
  const marketLoading = holdingQueries.some((q) => q.isFetching);
  const list: Holding[] = visibleAssets.map((asset, idx) => (holdingQueries[idx]?.data as Holding | undefined) || placeholderHolding(asset));
  const totalCost = list.reduce((s, h) => s + (h.total_cost || 0), 0);
  const totalValue = list.reduce((s, h) => s + (h.market_value || 0), 0);
  const holdingReceivable = list.reduce((s, h) => s + (h.profit || 0), 0);
  const realizedReceivable = list.reduce((s, h) => s + (h.realized_pnl || 0), 0);
  const holdingReceivablePct = totalCost > 0 ? (holdingReceivable / totalCost) * 100 : null;
  const holdingMap = new Map(list.map((h) => [h.asset.id, h]));
  const heldAssetIds = new Set(list.filter((h) => !h.asset.watch_only && h.total_shares > 0).map((h) => h.asset.id));
  const latestByHeldAsset = new Map<number, AdviceT>();
  for (const a of advices.data || []) {
    if (!a.asset_id || !heldAssetIds.has(a.asset_id) || latestByHeldAsset.has(a.asset_id)) continue;
    latestByHeldAsset.set(a.asset_id, a);
  }
  const urgentAssetActions = Array.from(latestByHeldAsset.values())
    .filter((a) => (a.action === "buy" || a.action === "sell") && a.confidence >= 0.65)
    .slice(0, 6);
  const investmentTodos = (todos.data || [])
    .filter((t) => t.todo_type === "ai_investment")
    .slice(0, 6);

  return (
    <>
      <PageHeader
        title="仪表盘"
        subtitle="一览总资产、当日表现与最新 AI 分析"
        actions={
          <>
            <button
              className="btn"
              onClick={() => qc.invalidateQueries({ queryKey: ["holdings"] })}
            >
              <RefreshCw className="w-4 h-4" /> 刷新行情
            </button>
            <button
              className="btn-primary"
              disabled={task.running}
              onClick={onRunAll}
            >
              <BrainCircuit className="w-4 h-4" />
              {task.running ? "AI 分析中…" : "一键 AI 分析"}
            </button>
          </>
        }
      />

      {(assetsQuery.isLoading || marketLoading) && (
        <div className="card p-3 mb-4 text-xs text-muted flex items-center justify-between gap-3">
          <span>{assetsQuery.isLoading ? "正在加载资产清单…" : "仪表盘已先显示，正在后台补充行情、市值和盈亏…"}</span>
          <span className="text-accent-soft">已显示 {list.length} 个资产</span>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard label="总成本" value={fmtMoney(totalCost)} hint={`持有 ${list.length} 个资产`} />
        <StatCard
          label="持仓市值"
          value={totalValue ? fmtMoney(totalValue) : "—"}
          tone="accent"
        />
        <StatCard
          label="持仓应收"
          value={totalValue ? fmtMoney(holdingReceivable) : "—"}
          tone={holdingReceivable >= 0 ? "success" : "danger"}
          delta={
            holdingReceivablePct !== null ? (
              <span className={holdingReceivable >= 0 ? "text-emerald2" : "text-rose2"}>
                {fmtPct(holdingReceivablePct)}
              </span>
            ) : null
          }
        />
        <Link to="/realized-revenue" className="block group rounded-2xl focus:outline-none focus:ring-2 focus:ring-accent/50">
          <div className="transition group-hover:-translate-y-0.5 group-hover:shadow-glow">
            <StatCard
              label="已实现营收"
              value={fmtMoney(realizedReceivable)}
              tone={realizedReceivable >= 0 ? "success" : "danger"}
              hint="点击查看每次卖出明细"
            />
          </div>
        </Link>

        <StatCard
          label="最新 AI 分析"
          value={
            advices.data?.[0] ? (
              <span className={actionColor(advices.data[0].action)}>
                {actionLabel(advices.data[0].action)}
              </span>
            ) : "—"
          }
          hint={advices.data?.[0] ? dateOnly(advices.data[0].created_at) : "尚未运行"}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-6 mt-6">
        <DashboardAdviceList
          title="我的资产 · 紧急买卖"
          subtitle="来自 AI 资产分析中高置信的买入/卖出结论"
          icon={<AlertTriangle className="w-4 h-4 text-amber2" />}
          empty="暂无高置信紧急买卖动作"
          items={urgentAssetActions}
          holdingMap={holdingMap}
        />
        <DashboardTodoList items={investmentTodos} loading={todos.isLoading} />
      </div>

      <div className="grid lg:grid-cols-3 gap-6 mt-6">
        <div className="card lg:col-span-2 p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-accent" /> 持仓概览
            </h3>
            <Link to="/assets" className="text-xs text-accent-soft hover:text-white inline-flex items-center gap-1">
              全部 <ArrowUpRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {list.length === 0 ? (
            <div className="text-center text-muted py-10 text-sm">
              还没有资产，去「我的资产」添加吧
            </div>
          ) : (
            <>
              <HoldingMiniTable
                title="场外基金"
                items={list.filter((h) => h.asset.asset_type === "fund")}
              />
              <div className="h-3" />
              <HoldingMiniTable
                title="股票 / 场内基金"
                items={list.filter((h) => h.asset.asset_type === "stock" || h.asset.asset_type === "etf")}
              />
            </>
          )}
        </div>

        <div className="card p-5">
          <h3 className="font-semibold flex items-center gap-2 mb-4">
            <BrainCircuit className="w-4 h-4 text-accent" /> 最新 AI 分析
          </h3>
          <div className="space-y-3">
            {(advices.data || []).slice(0, 6).map((a) => (
              <div key={a.id} className="rounded-xl border border-line p-3 hover:border-accent/50 transition">
                <div className="flex items-center justify-between">
                  <span className={`text-sm font-semibold ${actionColor(a.action)}`}>
                    {actionLabel(a.action)} · {(a.confidence * 100).toFixed(0)}%
                  </span>
                  <span className="text-[11px] text-muted">{dateOnly(a.created_at)}</span>
                </div>
                <div className="text-xs text-muted mt-1 line-clamp-2">{a.summary}</div>
                <div className="text-[10px] text-muted/70 mt-1">via {a.skill_used || "—"}</div>
              </div>
            ))}
            {(advices.data || []).length === 0 && (
              <div className="text-center text-muted text-sm py-8">尚未生成建议</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function DashboardAdviceList({ title, subtitle, icon, empty, items, holdingMap }: {
  title: string;
  subtitle: string;
  icon: ReactNode;
  empty: string;
  items: AdviceT[];
  holdingMap: Map<number, Holding>;
}) {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold flex items-center gap-2">{icon} {title}</h3>
          <p className="text-[11px] text-muted mt-1">{subtitle}</p>
        </div>
        <Link to="/advice" className="text-xs text-accent-soft hover:text-white inline-flex items-center gap-1">
          全部 <ArrowUpRight className="w-3.5 h-3.5" />
        </Link>
      </div>
      <div className="space-y-2">
        {items.map((a) => {
          const holding = a.asset_id ? holdingMap.get(a.asset_id) : undefined;
          return (
            <Link key={a.id} to={a.asset_id ? `/assets/${a.asset_id}` : "/advice"} className="block rounded-xl border border-line p-3 hover:border-accent/50 transition">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{holding?.asset.name || `资产 #${a.asset_id}`}</div>
                  <div className="text-[11px] text-muted mt-0.5 line-clamp-1">{a.summary}</div>
                </div>
                <div className="text-right shrink-0">
                  <div className={`text-sm font-semibold ${actionColor(a.action)}`}>{actionLabel(a.action)}</div>
                  <div className="text-[10px] text-muted">{(a.confidence * 100).toFixed(0)}%</div>
                </div>
              </div>
            </Link>
          );
        })}
        {items.length === 0 && <div className="text-center text-muted text-sm py-8">{empty}</div>}
      </div>
    </div>
  );
}

function DashboardTodoList({ items, loading }: { items: TodoItem[]; loading: boolean }) {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold flex items-center gap-2">
            <ListTodo className="w-4 h-4 text-accent" /> AI 投资建议项目
          </h3>
          <p className="text-[11px] text-muted mt-1">来自 AI 投资建议页、等待确认采纳的动作</p>
        </div>
        <Link to="/todos" className="text-xs text-accent-soft hover:text-white inline-flex items-center gap-1">
          处理 <ArrowUpRight className="w-3.5 h-3.5" />
        </Link>
      </div>
      <div className="space-y-2">
        {items.map((t) => (
          <Link key={t.id} to="/todos" className="block rounded-xl border border-line p-3 hover:border-accent/50 transition">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">{t.title}</div>
                <div className="text-[11px] text-muted mt-0.5 line-clamp-1">{t.description}</div>
              </div>
              <div className="text-right shrink-0">
                <div className={`text-sm font-semibold ${actionColor(t.action)}`}>{actionLabel(t.action)}</div>
                <div className="text-[10px] text-muted">{dateOnly(t.created_at)}</div>
              </div>
            </div>
          </Link>
        ))}
        {items.length === 0 && (
          <div className="text-center text-muted text-sm py-8">{loading ? "加载中…" : "暂无待确认的 AI 投资建议"}</div>
        )}
      </div>
    </div>
  );
}

function HoldingMiniTable({ title, items }: { title: string; items: Holding[] }) {
  if (items.length === 0) {
    return (
      <div>
        <div className="text-xs text-muted mb-1.5 px-1">{title}</div>
        <div className="text-[11px] text-muted/70 px-1 py-3 border border-dashed border-line rounded-lg text-center">
          暂无{title}
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="text-xs text-muted mb-1.5 px-1 flex items-center gap-2">
        <span>{title}</span>
        <span className="text-[10px] text-muted/70">({items.length})</span>
      </div>
      <div className="overflow-x-auto -mx-2">
        <table className="w-full text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="text-left px-2 py-1.5 font-normal">资产</th>
              <th className="text-right px-2 py-1.5 font-normal">成本</th>
              <th className="text-right px-2 py-1.5 font-normal">市值</th>
              <th className="text-right px-2 py-1.5 font-normal">盈亏</th>
              <th className="text-right px-2 py-1.5 font-normal w-12"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <tr key={h.asset.id} className="border-t border-line/40 hover:bg-line/20">
                <td className="px-2 py-2.5">
                  <div className="font-medium">{h.asset.name}</div>
                  <div className="text-[11px] text-muted">
                    {h.asset.code} · {h.asset.market} {h.asset.watch_only && "· 仅观察"}
                  </div>
                </td>
                <td className="text-right px-2 py-2.5 font-mono tabular-nums">{fmtMoney(h.total_cost)}</td>
                <td className="text-right px-2 py-2.5 font-mono tabular-nums">{fmtMoney(h.market_value)}</td>
                <td className={`text-right px-2 py-2.5 font-mono tabular-nums ${(h.profit ?? 0) >= 0 ? "text-emerald2" : "text-rose2"}`}>
                  {fmtMoney(h.profit)}
                  <div className="text-[11px]">{fmtPct(h.profit_pct)}</div>
                </td>
                <td className="text-right px-2 py-2.5">
                  <Link to={`/assets/${h.asset.id}`} className="text-accent-soft hover:text-white text-xs">
                    详情 →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
