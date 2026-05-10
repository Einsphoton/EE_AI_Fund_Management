import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BrainCircuit, Plus, Trash2, Pencil, CalendarClock } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import StatCard from "../components/StatCard";
import PriceChart from "../components/PriceChart";
import FundamentalPanel from "../components/FundamentalPanel";
import TxnForm, { TxnFormData } from "../components/TxnForm";
import DcaModal from "../components/DcaModal";
import AnalysisCard from "../components/AnalysisCard";
import { Assets as AssetApi, Quotes, AdviceApi, Transaction } from "../api/client";
import {
  fmtMoney, fmtPct, fmtNum, dateOnly, actionColor, actionLabel,
} from "../lib/format";

export default function AssetDetail() {
  const { id } = useParams();
  const assetId = Number(id);
  const qc = useQueryClient();

  const [txnOpen, setTxnOpen] = useState(false);
  const [editingTxn, setEditingTxn] = useState<Transaction | null>(null);
  const [txnPrefill, setTxnPrefill] = useState<Partial<TxnFormData> | undefined>();
  const [dcaOpen, setDcaOpen] = useState(false);
  const [days, setDays] = useState(180);

  const RANGES: { d: number; label: string }[] = [
    { d: 30,   label: "1月" },
    { d: 90,   label: "3月" },
    { d: 180,  label: "6月" },
    { d: 365,  label: "1年" },
    { d: 1095, label: "3年" },
    { d: 1825, label: "5年" },
    { d: 3650, label: "10年" },
  ];

  const holdings = useQuery({ queryKey: ["holdings"], queryFn: AssetApi.holdings });
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
    refetchInterval: 30_000,   // 30s 自动刷新基本盘
  });

  const advices = useQuery({
    queryKey: ["advice", "asset", assetId],
    queryFn: () => AdviceApi.byAsset(assetId),
    enabled: !!assetId,
  });

  const saveTxn = useMutation({
    mutationFn: async (p: TxnFormData) => {
      // trade_date 转 ISO
      const payload: any = { ...p };
      if (payload.trade_date) {
        payload.trade_date = new Date(payload.trade_date + "T00:00:00").toISOString();
      }
      if (editingTxn) {
        return AssetApi.updateTxn(assetId, editingTxn.id, payload);
      }
      return AssetApi.addTxn(assetId, payload);
    },
    onSuccess: () => {
      toast.success(editingTxn ? "已更新交易" : "交易已记录");
      qc.invalidateQueries({ queryKey: ["quote", assetId] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
      setEditingTxn(null);
      setTxnPrefill(undefined);
    },
    onError: (e: any) => toast.error(e.message),
  });

  const removeTxn = useMutation({
    mutationFn: (txnId: number) => AssetApi.removeTxn(assetId, txnId),
    onSuccess: () => {
      toast.success("已删除交易");
      qc.invalidateQueries({ queryKey: ["quote", assetId] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
  });

  const runAi = useMutation({
    mutationFn: () => AdviceApi.runOne(assetId),
    onSuccess: () => {
      toast.success("AI 分析完成");
      qc.invalidateQueries({ queryKey: ["advice", "asset", assetId] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  if (!holding) {
    return (
      <div className="text-center text-muted py-20">
        加载中…  <Link to="/assets" className="text-accent-soft">返回</Link>
      </div>
    );
  }

  const a = holding.asset;
  const latestAdvice = advices.data?.[0];

  return (
    <>
      <Link to="/assets" className="text-xs text-muted hover:text-white inline-flex items-center gap-1 mb-2">
        <ArrowLeft className="w-3.5 h-3.5" /> 返回
      </Link>

      <PageHeader
        title={a.name}
        subtitle={`${a.code} · ${a.market} · ${a.asset_type === "fund" ? "场外基金" : "股票/场内"} · ${a.platform || "未填写平台"}`}
        actions={
          <>
            {a.asset_type === "fund" && a.market === "OTC" && (
              <button className="btn" onClick={() => setDcaOpen(true)}>
                <CalendarClock className="w-4 h-4" /> 定投建议
              </button>
            )}
            <button
              className="btn"
              onClick={() => { setEditingTxn(null); setTxnPrefill(undefined); setTxnOpen(true); }}
            >
              <Plus className="w-4 h-4" /> 记录交易
            </button>
            <button
              className="btn-primary"
              disabled={runAi.isPending}
              onClick={() => runAi.mutate()}
            >
              <BrainCircuit className="w-4 h-4" />
              {runAi.isPending ? "AI 分析中…" : "AI 分析此标的"}
            </button>
          </>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-5">
        <StatCard label="持仓" value={holding.total_shares > 0 ? fmtNum(holding.total_shares) : "未持有"} />
        <StatCard
          label="平均成本"
          value={holding.avg_cost ? fmtNum(holding.avg_cost, 4) : "—"}
          hint={holding.total_cost ? `本金 ${fmtMoney(holding.total_cost)}` : undefined}
        />
        <StatCard
          label="当前价"
          value={holding.current_price ? fmtNum(holding.current_price, 4) : "—"}
          tone="accent"
          hint={holding.market_value ? `市值 ${fmtMoney(holding.market_value)}` : undefined}
        />
        <StatCard
          label="浮动盈亏"
          value={holding.profit !== null ? fmtMoney(holding.profit) : "—"}
          tone={(holding.profit ?? 0) >= 0 ? "success" : "danger"}
          delta={<span>{fmtPct(holding.profit_pct)}</span>}
        />
        <StatCard
          label="累计费用"
          value={holding.total_fee ? fmtMoney(holding.total_fee) : "0"}
          tone="default"
          hint={
            holding.realized_pnl
              ? `已实现 ${holding.realized_pnl >= 0 ? "+" : ""}${fmtMoney(holding.realized_pnl)}`
              : "—"
          }
        />
      </div>

      <div className={`grid gap-6 mb-6 ${a.asset_type === "fund" ? "" : "lg:grid-cols-3"}`}>
        <div className={`card p-5 ${a.asset_type === "fund" ? "" : "lg:col-span-2"}`}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">
              {a.asset_type === "fund" && a.market === "OTC" ? "净值曲线" : "K 线图"}
              <span className="text-xs text-muted ml-2">买卖点已自动标注，悬停查看详情</span>
            </h3>
            <div className="flex gap-1.5 flex-wrap">
              {RANGES.map(({ d, label }) => (
                <button
                  key={d}
                  className={`btn !px-2.5 !py-1 text-xs ${days === d ? "border-accent/50 bg-accent/10 text-accent-soft" : ""}`}
                  onClick={() => setDays(d)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {quote.isLoading ? (
            <div className="text-center text-muted py-20">加载行情中…</div>
          ) : quote.data && quote.data.points.length === 0 ? (
            <div className="text-center text-muted py-20">
              未获取到行情数据{quote.data.error ? `：${quote.data.error}` : "，请检查代码或市场是否正确"}
            </div>
          ) : (
            quote.data && <PriceChart quote={quote.data} />
          )}
        </div>
        {a.asset_type !== "fund" && (
          <FundamentalPanel
            snapshot={snapshot.data}
            market={a.market}
            isFund={false}
          />
        )}
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">交易记录</h3>
            <button
              className="btn !px-2 !py-1.5 text-xs"
              onClick={() => { setEditingTxn(null); setTxnPrefill(undefined); setTxnOpen(true); }}
            >
              <Plus className="w-3.5 h-3.5" /> 添加
            </button>
          </div>
          <div className="space-y-2">
            {(quote.data?.transactions || []).length === 0 && (
              <div className="text-center text-muted py-8 text-sm">尚无交易，点击右上方「记录交易」添加</div>
            )}
            {(quote.data?.transactions || []).map((t) => (
              <div key={t.id} className="flex items-center justify-between rounded-xl border border-line p-3 hover:border-accent/40 transition">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {t.txn_type === "buy" ? (
                      <span className="badge-buy">买入</span>
                    ) : (
                      <span className="badge-sell">卖出</span>
                    )}
                    <span className="ml-2 font-mono">{fmtNum(t.shares)}</span>
                    <span className="text-muted"> @ </span>
                    <span className="font-mono">{fmtNum(t.price, 4)}</span>
                  </div>
                  <div className="text-[11px] text-muted truncate">
                    {dateOnly(t.trade_date)} · 费用 {fmtMoney(t.fee)}
                    {t.note && ` · ${t.note}`}
                  </div>
                </div>
                <div className="inline-flex gap-1 ml-2 shrink-0">
                  <button
                    className="btn !px-2 !py-1.5"
                    title="编辑"
                    onClick={() => { setEditingTxn(t); setTxnPrefill(undefined); setTxnOpen(true); }}
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button
                    className="btn-danger !px-2 !py-1.5"
                    title="删除"
                    onClick={() => confirm("删除这笔交易？") && removeTxn.mutate(t.id)}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5">
          <h3 className="font-semibold mb-3 flex items-center gap-2">
            <BrainCircuit className="w-4 h-4 text-accent" /> AI 投资建议
          </h3>
          {latestAdvice ? (
            <AnalysisCard advice={latestAdvice} holding={holding} />
          ) : (
            <div className="text-center text-muted py-6 text-sm">尚未生成建议，点击右上方按钮触发分析</div>
          )}

          {(advices.data || []).slice(1, 5).length > 0 && (
            <div className="mt-4 pt-3 border-t border-line/40">
              <div className="text-[11px] text-muted mb-2">历史建议</div>
              {(advices.data || []).slice(1, 5).map((a2) => (
                <div key={a2.id} className="text-xs py-1.5 flex items-center gap-2">
                  <span className={`shrink-0 font-medium ${actionColor(a2.action)}`}>{actionLabel(a2.action)}</span>
                  <span className="text-muted shrink-0 font-mono">{dateOnly(a2.created_at)}</span>
                  <span className="text-muted/80 line-clamp-1">{a2.summary}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <TxnForm
        open={txnOpen}
        onClose={() => { setTxnOpen(false); setEditingTxn(null); setTxnPrefill(undefined); }}
        asset={a}
        initial={editingTxn}
        prefill={txnPrefill}
        onSubmit={async (d) => { await saveTxn.mutateAsync(d); }}
      />

      <DcaModal
        open={dcaOpen}
        onClose={() => setDcaOpen(false)}
        asset={a}
      />
    </>
  );
}
