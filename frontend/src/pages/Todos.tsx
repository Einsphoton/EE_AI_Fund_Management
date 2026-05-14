import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  AlertTriangle, BrainCircuit, CalendarClock, CheckCircle2, Eye, ListTodo, RefreshCw, Wallet, XCircle,
} from "lucide-react";

import PageHeader from "../components/PageHeader";
import AssetFundamentalsPanel from "../components/AssetFundamentalsPanel";
import { BudgetStatusItem, Quotes, TodoApi, TodoItem } from "../api/client";
import { fmtDateTime, fmtMoney, fmtNum } from "../lib/format";

type StatusFilter = "pending" | "accepted" | "rejected" | "all";

const STATUS_OPTIONS: { key: StatusFilter; label: string }[] = [
  { key: "pending", label: "待确认" },
  { key: "accepted", label: "已采纳" },
  { key: "rejected", label: "未采纳" },
  { key: "all", label: "全部" },
];

export default function Todos() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>("pending");
  const todos = useQuery({
    queryKey: ["todos", status],
    queryFn: () => TodoApi.list(status),
  });
  const budgetStatus = useQuery({
    queryKey: ["todos", "budget-status"],
    queryFn: TodoApi.budgetStatus,
  });

  const runPlan = useMutation({
    mutationFn: TodoApi.runAiInvestmentPlan,
    onSuccess: (r) => {
      toast.success(r.created > 0 ? `已生成 ${r.created} 条 AI 投资建议` : (r.summary || "暂无可执行建议"));
      qc.invalidateQueries({ queryKey: ["todos"] });
      qc.invalidateQueries({ queryKey: ["todos", "budget-status"] });
      setStatus("pending");
    },
    onError: (e: any) => toast.error(e?.message || "AI 投资经理分析失败"),
  });

  const resolve = useMutation({
    mutationFn: ({ id, decision, shares }: { id: number; decision: "accept" | "reject"; shares?: number }) =>
      TodoApi.resolve(id, { decision, shares }),
    onSuccess: (todo) => {
      toast.success(todo.status === "accepted" ? "已采纳并记录交易" : "已标记为不采纳");
      qc.invalidateQueries({ queryKey: ["todos"] });
      qc.invalidateQueries({ queryKey: ["todos", "budget-status"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e?.message || "处理待办失败"),
  });

  const items = todos.data || [];
  const pendingCount = useMemo(() => items.filter((x) => x.status === "pending").length, [items]);

  return (
    <>
      <PageHeader
        title="AI 投资建议"
        subtitle="AI 或规则产生的追投、调仓、建仓、卖出等动作会先进入这里；确认采纳后会直接更新我的资产。"
        actions={
          <>
            <button className="btn" onClick={() => qc.invalidateQueries({ queryKey: ["todos"] })}>
              <RefreshCw className="w-4 h-4" /> 刷新
            </button>
            <button className="btn-primary" disabled={runPlan.isPending} onClick={() => runPlan.mutate()}>
              <BrainCircuit className="w-4 h-4" /> {runPlan.isPending ? "分析中…" : "AI 投资建议分析"}
            </button>
          </>
        }
      />

      <BudgetStatusPanel items={budgetStatus.data?.items || []} />

      <div className="card p-3 mb-4 flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 text-xs">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              className={`px-3 py-1.5 rounded-lg border transition ${
                status === opt.key
                  ? "border-accent/60 bg-accent/15 text-white"
                  : "border-line text-muted hover:text-white hover:border-line/80"
              }`}
              onClick={() => setStatus(opt.key)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="ml-auto text-xs text-muted">
          当前 <span className="text-white">{items.length}</span> 条
          {status === "pending" && <> · 待确认 <span className="text-accent-soft">{pendingCount}</span></>}
        </div>
      </div>

      {todos.isLoading ? (
        <div className="card p-12 text-center text-muted">正在加载建议列表… 页面会在拿到部分数据后立即显示。</div>
      ) : items.length === 0 ? (
        <div className="card p-12 text-center text-muted">
          <ListTodo className="w-8 h-8 mx-auto mb-3 text-muted/60" />
          当前没有{status === "pending" ? "待确认" : "符合筛选条件的"} AI 投资建议。
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((todo) => (
            <TodoCard
              key={todo.id}
              todo={todo}
              busy={resolve.isPending}
              onAccept={(shares) => resolve.mutate({ id: todo.id, decision: "accept", shares })}
              onReject={() => resolve.mutate({ id: todo.id, decision: "reject" })}
            />
          ))}
        </div>
      )}
    </>
  );
}

function BudgetStatusPanel({ items }: { items: BudgetStatusItem[] }) {
  if (items.length === 0) {
    return (
      <div className="card p-4 mb-4 text-sm text-muted">
        尚未配置平台月投资额度。请先到「设置」里添加预算，AI 投资经理才会生成可执行建议。
      </div>
    );
  }
  return (
    <div className="card p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <Wallet className="w-4 h-4 text-accent" />
        <span className="text-sm font-medium">本月平台预算剩余</span>
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {items.map((it, idx) => (
          <div key={`${it.platform}-${it.currency}-${idx}`} className="rounded-xl border border-line bg-bg-soft/30 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm text-white truncate">{it.platform}</span>
              <span className="text-[10px] text-muted font-mono">{it.currency}</span>
            </div>
            <div className="text-lg font-semibold font-mono mt-1">
              {fmtMoney(it.remaining_budget, it.currency)}
            </div>
            <div className="text-[10px] text-muted mt-0.5">
              月度 {fmtMoney(it.monthly_amount, it.currency)} · 已用 {fmtMoney(it.used_this_month, it.currency)}
            </div>
            <div className="text-[10px] text-accent-soft mt-1 truncate">
              {(it.asset_types || []).map(assetTypeLabelOf).join(" / ") || "未指定类型"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TodoCard({ todo, busy, onAccept, onReject }: {
  todo: TodoItem;
  busy: boolean;
  onAccept: (shares: number) => void;
  onReject: () => void;
}) {
  const txn = (todo.payload?.transaction || {}) as Record<string, any>;
  const suggestion = (todo.payload?.suggestion || {}) as Record<string, any>;
  const price = Number(txn.price || suggestion.last_price || 0);
  const defaultShares = Number(txn.shares || suggestion.suggest_shares || 0);
  const [shares, setShares] = useState(defaultShares);
  const amount = price > 0 && shares > 0 ? shares * price : 0;
  const canAccept = todo.status === "pending" && price > 0 && shares > 0;
  const isDca = todo.todo_type === "dca_due";
  const isAiInvestment = todo.todo_type === "ai_investment";
  const fundamentals = useQuery({
    queryKey: ["fundamentals", todo.asset_id, "todo"],
    queryFn: () => Quotes.fundamentals(todo.asset_id!),
    enabled: isAiInvestment && !!todo.asset_id,
    staleTime: 10 * 60_000,
  });
  const platform = String(todo.payload?.platform || txn.platform || "");
  const currency = String(todo.payload?.currency || txn.currency || "");
  const assetTypeLabel = assetTypeLabelOf(String(todo.payload?.asset_type || todo.asset?.asset_type || ""));

  return (
    <div className="card p-5 border-l-4 border-l-accent/70">
      <div className="flex items-start gap-4">
        <div className="w-11 h-11 rounded-xl bg-accent/15 border border-accent/30 flex items-center justify-center shrink-0">
          {isDca ? <CalendarClock className="w-5 h-5 text-accent" /> : isAiInvestment ? <BrainCircuit className="w-5 h-5 text-accent" /> : <ListTodo className="w-5 h-5 text-accent" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-semibold truncate">{todo.title}</h2>
                <StatusBadge status={todo.status} />
                {todo.action && <ActionBadge action={todo.action} />}
              </div>
              <div className="text-[11px] text-muted mt-1 flex items-center gap-2 flex-wrap">
                <span className="inline-flex items-center gap-1">
                  <Wallet className="w-3 h-3" /> {todo.asset?.name || (todo.asset_id ? `资产 #${todo.asset_id}` : "未绑定资产")}
                </span>
                {todo.asset_id && (
                  <Link to={`/assets/${todo.asset_id}`} className="text-accent-soft hover:text-white inline-flex items-center gap-1">
                    <Eye className="w-3 h-3" /> 详情/基本盘
                  </Link>
                )}
                <span>生成：{fmtDateTime(todo.due_date || todo.created_at)}</span>
                {todo.expires_at && todo.status === "pending" && <span className="text-amber2">过期：{fmtDateTime(todo.expires_at)}</span>}
                {todo.resolved_at && <span>处理：{fmtDateTime(todo.resolved_at)}</span>}
              </div>
            </div>
          </div>

          {todo.description && (
            <p className="text-sm text-white/85 leading-relaxed mt-3">{todo.description}</p>
          )}

          {(isDca || isAiInvestment) && (
            <div className="grid md:grid-cols-4 gap-3 mt-4 text-sm">
              <InfoCell label={isDca ? "基础金额" : "平台/币种"} value={isDca ? fmtMoney(Number(todo.payload?.base_amount || suggestion.base_amount || 0)) : `${platform || "—"} ${currency || ""}`} />
              <InfoCell label="建议金额" value={fmtMoney(Number(suggestion.suggest_amount || txn.amount || 0), currency || "CNY")} />
              <InfoCell label="资产类型" value={assetTypeLabel || "—"} />
              <InfoCell label="估算净值" value={price > 0 ? fmtNum(price, 4) : "—"} />
            </div>
          )}

          {isAiInvestment && todo.asset_id && (
            <div className="mt-4">
              <AssetFundamentalsPanel data={fundamentals.data} loading={fundamentals.isLoading} compact />
            </div>
          )}

          {todo.status === "pending" ? (
            <div className="mt-4 rounded-xl border border-line bg-bg-soft/30 p-4">
              <div className="grid md:grid-cols-4 gap-3 items-end">
                <div>
                  <label className="label">确认份额</label>
                  <input
                    className="input font-mono"
                    type="number"
                    min={0}
                    step="0.0001"
                    value={Number.isFinite(shares) ? shares : ""}
                    onChange={(e) => setShares(e.currentTarget.valueAsNumber || 0)}
                  />
                </div>
                <InfoCell label="确认净值" value={price > 0 ? fmtNum(price, 4) : "—"} />
                <InfoCell label="确认金额" value={amount > 0 ? fmtMoney(amount, currency || "CNY") : "—"} />
                <div className="flex gap-2 md:justify-end">
                  <button className="btn !text-rose2 hover:!border-rose2/60" disabled={busy} onClick={onReject}>
                    <XCircle className="w-4 h-4" /> 不采纳
                  </button>
                  <button className="btn-primary" disabled={busy || !canAccept} onClick={() => onAccept(shares)}>
                    <CheckCircle2 className="w-4 h-4" /> 采纳
                  </button>
                </div>
              </div>
              {!canAccept && (
                <div className="text-[11px] text-amber2 mt-2 flex items-center gap-1">
                  <AlertTriangle className="w-3 h-3" /> 采纳前需要有效的净值和大于 0 的份额；也可以选择不采纳。
                </div>
              )}
            </div>
          ) : (
            <ResolvedResult todo={todo} />
          )}
        </div>
      </div>
    </div>
  );
}

function assetTypeLabelOf(type: string): string {
  if (type === "fund") return "场外基金";
  if (type === "stock") return "股票";
  if (type === "etf") return "ETF / 场内基金";
  return type;
}

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-bg/40 px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="font-mono text-white mt-0.5">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls = status === "accepted"
    ? "border-emerald2/40 text-emerald2 bg-emerald2/5"
    : status === "rejected"
      ? "border-rose2/40 text-rose2 bg-rose2/5"
      : "border-accent/40 text-accent-soft bg-accent/5";
  const label = status === "accepted" ? "已采纳" : status === "rejected" ? "未采纳" : "待确认";
  return <span className={`text-[10px] px-2 py-0.5 rounded-md border ${cls}`}>{label}</span>;
}

function ActionBadge({ action }: { action: string }) {
  const label = action === "buy" ? "买入" : action === "sell" ? "卖出" : action === "skip" ? "暂缓" : action;
  const cls = action === "buy" ? "text-emerald2" : action === "sell" ? "text-rose2" : "text-amber2";
  return <span className={`text-xs font-medium ${cls}`}>{label}</span>;
}

function ResolvedResult({ todo }: { todo: TodoItem }) {
  const txn = todo.result?.transaction;
  if (todo.status === "accepted" && txn) {
    return (
      <div className="mt-4 rounded-xl border border-emerald2/20 bg-emerald2/5 p-3 text-xs text-white/80">
        已记录{txn.txn_type === "sell" ? "卖出" : "买入"}交易：份额 <span className="font-mono text-white">{fmtNum(txn.shares, 4)}</span>
        ，金额 <span className="font-mono text-white">{fmtMoney(txn.amount)}</span>
        ，手续费 <span className="font-mono text-white">{fmtMoney(txn.fee)}</span>
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-xl border border-rose2/20 bg-rose2/5 p-3 text-xs text-white/70">
      已不采纳该待办。
    </div>
  );
}
