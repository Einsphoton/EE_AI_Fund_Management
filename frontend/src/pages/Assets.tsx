import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Pencil, Trash2, Eye, Wallet, LineChart, Sparkles } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import AssetForm, { AssetFormData } from "../components/AssetForm";
import AssetAnalysisModal from "../components/AssetAnalysisModal";
import { Assets as AssetApi, Asset, Holding, Transaction } from "../api/client";
import { fmtMoney, fmtPct } from "../lib/format";

export default function Assets() {
  const qc = useQueryClient();
  const { data: holdings = [] } = useQuery({
    queryKey: ["holdings"], queryFn: AssetApi.holdings,
  });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [editingTxns, setEditingTxns] = useState<Transaction[] | undefined>();
  const [analyzingId, setAnalyzingId] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: (p: AssetFormData) => AssetApi.create(p),
    onSuccess: (newAsset) => {
      toast.success("已添加标的");
      // 乐观写入：立即把新标的塞进 holdings 缓存，UI 不等行情刷新
      qc.setQueryData<Holding[]>(["holdings"], (old = []) => [
        ...old,
        {
          asset: newAsset,
          total_shares: 0, total_cost: 0, avg_cost: 0,
          current_price: null, market_value: null,
          profit: null, profit_pct: null,
        } as Holding,
      ]);
      // 后台静默重取以补上行情数据
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const update = useMutation({
    mutationFn: async ({ data, originalId }: { data: AssetFormData; originalId: number }) => {
      const { initial_amount: _amt, initial_date: _d, initial_fee: _f,
              initial_price: _p, initial_shares: _s, edit_first_txn, ...rest } = data as any;
      void _amt; void _d; void _f; void _p; void _s;
      const updated = await AssetApi.update(originalId, rest);
      // 同时更新首笔交易（若表单有变更）
      if (edit_first_txn && edit_first_txn.id) {
        const { id, ...txnPatch } = edit_first_txn;
        // 把 trade_date 转 ISO
        const patch: any = { ...txnPatch };
        if (patch.trade_date) {
          patch.trade_date = new Date(patch.trade_date + "T00:00:00").toISOString();
        }
        await AssetApi.updateTxn(originalId, id, patch);
      }
      return updated;
    },
    onMutate: async ({ data, originalId }) => {
      // 乐观更新元数据
      await qc.cancelQueries({ queryKey: ["holdings"] });
      const prev = qc.getQueryData<Holding[]>(["holdings"]);
      qc.setQueryData<Holding[]>(["holdings"], (old = []) =>
        old.map((h) => h.asset.id === originalId
          ? { ...h, asset: { ...h.asset, ...data } as Asset }
          : h)
      );
      return { prev };
    },
    onError: (e: any, _v, ctx) => {
      toast.error(e.message);
      if (ctx?.prev) qc.setQueryData(["holdings"], ctx.prev);
    },
    onSuccess: () => {
      toast.success("已更新");
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
  });

  const remove = useMutation({
    mutationFn: AssetApi.remove,
    onMutate: async (id: number) => {
      await qc.cancelQueries({ queryKey: ["holdings"] });
      const prev = qc.getQueryData<Holding[]>(["holdings"]);
      qc.setQueryData<Holding[]>(["holdings"], (old = []) =>
        old.filter((h) => h.asset.id !== id)
      );
      return { prev };
    },
    onError: (e: any, _v, ctx) => {
      toast.error(e.message);
      if (ctx?.prev) qc.setQueryData(["holdings"], ctx.prev);
    },
    onSuccess: () => toast.success("已删除"),
  });

  const submit = async (data: AssetFormData) => {
    if (editing) {
      await update.mutateAsync({ data, originalId: editing.id });
    } else {
      await create.mutateAsync(data);
    }
  };

  const openEdit = async (a: Asset) => {
    setEditing(a);
    setEditingTxns(undefined);
    setOpen(true);
    // 异步加载该标的交易，用于在表单内决定是否展示首笔交易编辑区
    try {
      const txns = await AssetApi.txns(a.id);
      setEditingTxns(txns);
    } catch {
      setEditingTxns([]);
    }
  };

  const groups = useMemo(() => {
    const fund: Holding[] = [];
    const stock: Holding[] = [];
    for (const h of holdings) {
      (h.asset.asset_type === "fund" ? fund : stock).push(h);
    }
    return { fund, stock };
  }, [holdings]);

  const totalsOf = (list: Holding[]) => {
    const cost = list.reduce((s, h) => s + (h.total_cost || 0), 0);
    const value = list.reduce((s, h) => s + (h.market_value || 0), 0);
    const profit = value ? value - cost : 0;
    const pct = cost > 0 && value ? (profit / cost) * 100 : null;
    return { cost, value, profit, pct };
  };

  return (
    <>
      <PageHeader
        title="我的标的"
        subtitle="基金与股票分组管理；支持仅观察未买入"
        actions={
          <button
            className="btn-primary"
            onClick={() => { setEditing(null); setEditingTxns(undefined); setOpen(true); }}
          >
            <Plus className="w-4 h-4" /> 添加标的
          </button>
        }
      />

      <Section
        title="场外基金"
        icon={<Wallet className="w-4 h-4 text-accent" />}
        list={groups.fund}
        totals={totalsOf(groups.fund)}
        onEdit={openEdit}
        onAnalyze={(a) => setAnalyzingId(a.id)}
        onDelete={(a) => {
          if (confirm(`确认删除 ${a.name}? 所有交易记录会一并删除。`)) {
            remove.mutate(a.id);
          }
        }}
        emptyText="还没有场外基金，点右上角「添加标的」开始"
      />

      <div className="h-6" />

      <Section
        title="股票 / 场内基金"
        icon={<LineChart className="w-4 h-4 text-emerald2" />}
        list={groups.stock}
        totals={totalsOf(groups.stock)}
        onEdit={openEdit}
        onAnalyze={(a) => setAnalyzingId(a.id)}
        onDelete={(a) => {
          if (confirm(`确认删除 ${a.name}? 所有交易记录会一并删除。`)) {
            remove.mutate(a.id);
          }
        }}
        emptyText="还没有股票/场内 ETF，点右上角「添加标的」开始"
      />

      <AssetForm
        open={open}
        onClose={() => setOpen(false)}
        onSubmit={submit}
        initial={editing}
        initialTxns={editingTxns}
        editing={!!editing}
      />

      {analyzingId !== null && (
        <AssetAnalysisModal
          assetId={analyzingId}
          onClose={() => setAnalyzingId(null)}
        />
      )}
    </>
  );
}

// ---------- Section ----------
interface SectionProps {
  title: string;
  icon: React.ReactNode;
  list: Holding[];
  totals: { cost: number; value: number; profit: number; pct: number | null };
  onEdit: (a: Asset) => void;
  onAnalyze: (a: Asset) => void;
  onDelete: (a: Asset) => void;
  emptyText: string;
}

function Section({ title, icon, list, totals, onEdit, onAnalyze, onDelete, emptyText }: SectionProps) {
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-line/60 bg-bg-soft/30">
        <h2 className="font-semibold flex items-center gap-2">
          {icon}
          {title}
          <span className="text-xs text-muted ml-1">({list.length})</span>
        </h2>
        {list.length > 0 && (
          <div className="flex items-center gap-4 text-xs">
            <span className="text-muted">
              成本 <span className="text-white font-medium font-mono">{fmtMoney(totals.cost)}</span>
            </span>
            <span className="text-muted">
              市值 <span className="text-accent-soft font-medium font-mono">{fmtMoney(totals.value)}</span>
            </span>
            <span className={totals.profit >= 0 ? "text-emerald2" : "text-rose2"}>
              {fmtMoney(totals.profit)}
              {totals.pct !== null && <span className="ml-1">({fmtPct(totals.pct)})</span>}
            </span>
          </div>
        )}
      </div>

      <table className="w-full text-sm">
        <thead className="text-xs text-muted bg-bg-soft/50">
          <tr>
            <th className="text-left px-4 py-3">标的</th>
            <th className="text-left px-4 py-3">平台</th>
            <th className="text-right px-4 py-3">持仓</th>
            <th className="text-right px-4 py-3">成本</th>
            <th className="text-right px-4 py-3">市值</th>
            <th className="text-right px-4 py-3">盈亏</th>
            <th className="text-right px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {list.length === 0 && (
            <tr><td colSpan={7} className="text-center text-muted py-10">{emptyText}</td></tr>
          )}
          {list.map((h) => (
            <tr key={h.asset.id} className="border-t border-line/60 hover:bg-line/15 transition">
              <td className="px-4 py-3">
                <button
                  className="text-left group"
                  onClick={() => onAnalyze(h.asset)}
                  title="点击查看 AI 分析 + 基本盘 + 图表"
                >
                  <div className="font-medium group-hover:text-accent-soft transition flex items-center gap-1.5">
                    {h.asset.name}
                    <Sparkles className="w-3.5 h-3.5 text-muted group-hover:text-accent opacity-0 group-hover:opacity-100 transition" />
                  </div>
                  <div className="text-[11px] text-muted">
                    {h.asset.code} · {h.asset.market}
                    {h.asset.watch_only && (<span className="badge ml-1">仅观察</span>)}
                  </div>
                </button>
              </td>
              <td className="px-4 py-3 text-muted">{h.asset.platform || "—"}</td>
              <td className="text-right px-4 py-3 font-mono tabular-nums">
                {h.total_shares > 0 ? h.total_shares.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "—"}
              </td>
              <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtMoney(h.total_cost)}</td>
              <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtMoney(h.market_value)}</td>
              <td className={`text-right px-4 py-3 font-mono tabular-nums ${(h.profit ?? 0) >= 0 ? "text-emerald2" : "text-rose2"}`}>
                {fmtMoney(h.profit)}
                <div className="text-[11px]">{fmtPct(h.profit_pct)}</div>
              </td>
              <td className="text-right px-4 py-3">
                <div className="inline-flex gap-1">
                  <button
                    className="btn !px-2 !py-1.5"
                    title="AI 分析"
                    onClick={() => onAnalyze(h.asset)}
                  >
                    <Sparkles className="w-3.5 h-3.5 text-accent" />
                  </button>
                  <Link to={`/assets/${h.asset.id}`} className="btn !px-2 !py-1.5" title="详情页">
                    <Eye className="w-3.5 h-3.5" />
                  </Link>
                  <button
                    className="btn !px-2 !py-1.5"
                    title="编辑"
                    onClick={() => onEdit(h.asset)}
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button
                    className="btn-danger !px-2 !py-1.5"
                    title="删除"
                    onClick={() => onDelete(h.asset)}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
