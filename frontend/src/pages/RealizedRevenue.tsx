import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, ReceiptText, TrendingUp } from "lucide-react";

import PageHeader from "../components/PageHeader";
import StatCard from "../components/StatCard";
import { Assets, RealizedPnlItem } from "../api/client";
import { fmtDate, fmtMoney, fmtNum } from "../lib/format";

const TYPE_LABEL: Record<string, string> = {
  fund: "场外基金",
  stock: "股票",
  etf: "场内基金",
  money_fund: "货币基金",
  wealth: "理财",
  cash: "现金",
  bond: "债券",
};

export default function RealizedRevenue() {
  const q = useQuery({ queryKey: ["assets", "realized-pnl"], queryFn: Assets.realizedPnl });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const income = useMemo(() => items.filter((x) => x.realized_pnl > 0).reduce((s, x) => s + x.realized_pnl, 0), [items]);
  const loss = useMemo(() => items.filter((x) => x.realized_pnl < 0).reduce((s, x) => s + x.realized_pnl, 0), [items]);

  return (
    <>
      <PageHeader
        title="已实现营收明细"
        subtitle="按每一次卖出操作拆分，说明由哪个资产、哪次操作产生了多少已实现营收 / 亏损。"
        actions={
          <Link to="/" className="btn">
            <ArrowLeft className="w-4 h-4" /> 返回仪表盘
          </Link>
        }
      />

      <div className="grid md:grid-cols-4 gap-4 mb-6">
        <StatCard label="已实现合计" value={fmtMoney(total)} tone={total >= 0 ? "success" : "danger"} />
        <StatCard label="盈利操作" value={fmtMoney(income)} tone="success" hint={`${items.filter((x) => x.realized_pnl > 0).length} 次`} />
        <StatCard label="亏损操作" value={fmtMoney(loss)} tone={loss >= 0 ? "default" : "danger"} hint={`${items.filter((x) => x.realized_pnl < 0).length} 次`} />
        <StatCard label="操作次数" value={`${items.length}`} tone="accent" hint="仅统计卖出产生的已实现盈亏" />
      </div>

      <div className="card overflow-hidden">
        <div className="px-5 py-3 border-b border-line/60 bg-bg-soft/30 flex items-center justify-between gap-3">
          <h3 className="font-semibold flex items-center gap-2">
            <ReceiptText className="w-4 h-4 text-accent" /> 营收来源列表
          </h3>
          <div className="text-xs text-muted">共 {items.length} 条</div>
        </div>

        {q.isLoading ? (
          <div className="p-10 text-center text-muted text-sm">正在加载已实现营收明细…</div>
        ) : q.error ? (
          <div className="p-10 text-center text-rose2 text-sm">加载失败：{(q.error as any)?.message || String(q.error)}</div>
        ) : items.length === 0 ? (
          <div className="p-12 text-center text-muted">
            <TrendingUp className="w-8 h-8 mx-auto mb-3 text-muted/50" />
            <div className="text-sm">还没有已实现营收</div>
            <div className="text-xs mt-1">当你记录卖出交易后，这里会显示每次卖出对应的已实现盈亏。</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-muted bg-bg-soft/50">
                <tr>
                  <th className="text-left px-4 py-3">操作</th>
                  <th className="text-left px-4 py-3">资产</th>
                  <th className="text-right px-4 py-3">份额</th>
                  <th className="text-right px-4 py-3">卖出价</th>
                  <th className="text-right px-4 py-3">成本价</th>
                  <th className="text-right px-4 py-3">卖出金额</th>
                  <th className="text-right px-4 py-3">手续费</th>
                  <th className="text-right px-4 py-3">营收</th>
                  <th className="text-right px-4 py-3">详情</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <RevenueRow key={it.transaction_id} item={it} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function RevenueRow({ item }: { item: RealizedPnlItem }) {
  const positive = item.realized_pnl >= 0;
  return (
    <tr className="border-t border-line/60 hover:bg-line/15 transition">
      <td className="px-4 py-3">
        <div className="font-medium">{item.operation || "卖出"}</div>
        <div className="text-[11px] text-muted">
          {fmtDate(item.trade_date)} · 交易 #{item.transaction_id}
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="font-medium">{item.asset_name}</div>
        <div className="text-[11px] text-muted">
          {item.asset_code} · {item.market} · {TYPE_LABEL[item.asset_type] || item.asset_type}
          {item.platform ? ` · ${item.platform}` : ""}
        </div>
        {item.note && <div className="text-[11px] text-muted/70 mt-1 line-clamp-1">备注：{item.note}</div>}
      </td>
      <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtNum(item.shares, 4)}</td>
      <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtNum(item.sell_price, 4)}</td>
      <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtNum(item.avg_cost, 4)}</td>
      <td className="text-right px-4 py-3 font-mono tabular-nums">{fmtMoney(item.sell_amount)}</td>
      <td className="text-right px-4 py-3 font-mono tabular-nums text-muted">{fmtMoney(item.fee)}</td>
      <td className={`text-right px-4 py-3 font-mono tabular-nums font-semibold ${positive ? "text-emerald2" : "text-rose2"}`}>
        {positive ? "+" : ""}{fmtMoney(item.realized_pnl)}
      </td>
      <td className="text-right px-4 py-3">
        <Link to={`/assets/${item.asset_id}`} className="btn !px-2 !py-1.5" title="查看资产详情">
          <ExternalLink className="w-3.5 h-3.5" />
        </Link>
      </td>
    </tr>
  );
}
