import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, Pencil, Trash2, Eye, Wallet, LineChart, Sparkles } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import AssetForm, { AssetFormData } from "../components/AssetForm";
import AssetAnalysisModal from "../components/AssetAnalysisModal";
import { Assets as AssetApi, Asset, AssetType, Holding, Transaction } from "../api/client";
import { ASSET_TYPE_META } from "../lib/assetMeta";
import { fmtMoney, fmtPct } from "../lib/format";

type GroupMode = "asset_type" | "platform";

interface HoldingTotals {
  cost: number;
  value: number;
  holdingReceivable: number;
  realizedReceivable: number;
  holdingPct: number | null;
}

export default function Assets() {
  const qc = useQueryClient();
  const { data: holdingsRaw = [] } = useQuery({
    queryKey: ["holdings"], queryFn: AssetApi.holdings,
  });
  const holdings = holdingsRaw.filter((h) => !h.asset.watch_only);

  const [groupMode, setGroupMode] = useState<GroupMode>("asset_type");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [editingTxns, setEditingTxns] = useState<Transaction[] | undefined>();
  const [analyzingId, setAnalyzingId] = useState<number | null>(null);

  const create = useMutation({
    mutationFn: (p: AssetFormData) => AssetApi.create(p),
    onSuccess: (newAsset) => {
      toast.success("已添加资产");
      // 乐观写入：立即把新资产塞进 holdings 缓存，UI 不等行情刷新
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
    if (groupMode === "platform") {
      const byPlatform: Record<string, Holding[]> = {};
      for (const h of holdings) {
        const p = (h.asset.platform || "未填写平台").trim() || "未填写平台";
        (byPlatform[p] ||= []).push(h);
      }
      return Object.keys(byPlatform)
        .sort((a, b) => a.localeCompare(b, "zh-CN"))
        .map((platform) => ({
          key: `platform:${platform}`,
          title: platform,
          list: byPlatform[platform],
        }));
    }

    const byType: Record<string, Holding[]> = {};
    for (const h of holdings) {
      const t = h.asset.asset_type || "fund";
      (byType[t] ||= []).push(h);
    }
    return Object.keys(ASSET_TYPE_META)
      .sort((a, b) => ASSET_TYPE_META[a as AssetType].order - ASSET_TYPE_META[b as AssetType].order)
      .filter((t) => byType[t] && byType[t].length > 0)
      .map((t) => ({
        key: `type:${t}`,
        title: ASSET_TYPE_META[t as AssetType].label,
        list: byType[t],
      }));
  }, [holdings, groupMode]);

  const totalsOf = (list: Holding[]): HoldingTotals => {
    const cost = list.reduce((s, h) => s + (h.total_cost || 0), 0);
    const value = list.reduce((s, h) => s + (h.market_value || 0), 0);
    const holdingReceivable = list.reduce((s, h) => s + (h.profit || 0), 0);
    const realizedReceivable = list.reduce((s, h) => s + (h.realized_pnl || 0), 0);
    const holdingPct = cost > 0 ? (holdingReceivable / cost) * 100 : null;
    return { cost, value, holdingReceivable, realizedReceivable, holdingPct };
  };

  return (
    <>
      <PageHeader
        title="我的资产"
        subtitle="基金 / 股票 / 理财 / 现金等多类资产分组管理"
        actions={
          <>
            <div className="inline-flex rounded-xl border border-line bg-bg-soft/40 p-0.5 text-xs">
              <button
                className={`px-3 py-1.5 rounded-lg transition ${groupMode === "asset_type" ? "bg-accent/15 text-white border border-accent/40" : "text-muted hover:text-white"}`}
                onClick={() => setGroupMode("asset_type")}
              >
                按类型
              </button>
              <button
                className={`px-3 py-1.5 rounded-lg transition ${groupMode === "platform" ? "bg-accent/15 text-white border border-accent/40" : "text-muted hover:text-white"}`}
                onClick={() => setGroupMode("platform")}
              >
                按平台
              </button>
            </div>
            <button
              className="btn-primary"
              onClick={() => { setEditing(null); setEditingTxns(undefined); setOpen(true); }}
            >
              <Plus className="w-4 h-4" /> 添加资产
            </button>
          </>
        }
      />

      {groups.length === 0 && (
        <div className="card p-10 text-center text-muted">
          还没有任何持有资产，点右上角「添加资产」开始；观察池请到「我的标的」。
        </div>
      )}

      {groups.map((g, i) => (
        <div key={g.key} className={i > 0 ? "mt-6" : ""}>
          <Section
            title={g.title}
            icon={<Wallet className="w-4 h-4 text-accent" />}
            list={g.list}
            totals={totalsOf(g.list)}
            onEdit={openEdit}
            onAnalyze={(a) => setAnalyzingId(a.id)}
            onDelete={(a) => {
              if (confirm(`确认删除 ${a.name}? 所有交易记录会一并删除。`)) {
                remove.mutate(a.id);
              }
            }}
            emptyText=""
          />
        </div>
      ))}

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
  totals: HoldingTotals;
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
            <span className={totals.holdingReceivable >= 0 ? "text-emerald2" : "text-rose2"}>
              持仓应收 {fmtMoney(totals.holdingReceivable)}
              {totals.holdingPct !== null && <span className="ml-1">({fmtPct(totals.holdingPct)})</span>}
            </span>
            <span className={totals.realizedReceivable >= 0 ? "text-emerald2" : "text-rose2"}>
              已实现应收 {fmtMoney(totals.realizedReceivable)}
            </span>
          </div>
        )}
      </div>

      <table className="w-full text-sm">
        <thead className="text-xs text-muted bg-bg-soft/50">
          <tr>
            <th className="text-left px-4 py-3">资产</th>
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
              <td className={`text-right px-4 py-3 font-mono tabular-nums ${(h.realized_pnl ?? 0) >= 0 ? "text-emerald2" : "text-rose2"}`}>
                {fmtMoney(h.realized_pnl || 0)}
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
