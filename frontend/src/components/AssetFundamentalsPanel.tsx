import type { AssetFundamentals, AssetType } from "../api/client";
import { fmtNum } from "../lib/format";

interface Props {
  data?: AssetFundamentals | null;
  loading?: boolean;
  compact?: boolean;
}

function typeLabel(type: string) {
  if (type === "fund") return "场外基金";
  if (type === "stock") return "股票";
  if (type === "etf") return "ETF / 场内基金";
  return type || "—";
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-bg/40 px-3 py-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="font-mono text-white mt-0.5 truncate">{value}</div>
    </div>
  );
}

export default function AssetFundamentalsPanel({ data, loading, compact = false }: Props) {
  if (loading) {
    return <div className="card p-5 text-sm text-muted">基础数据加载中…</div>;
  }
  if (!data) {
    return <div className="card p-5 text-sm text-muted">暂未获取到基础数据。</div>;
  }

  const stats = data.stats || {};
  const dividends = data.dividends || {};
  const type = String(data.asset_type || "") as AssetType;
  const dividendYield = dividends.dividend_yield_pct != null ? `${fmtNum(dividends.dividend_yield_pct, 2)}%` : "—";

  return (
    <div className={`card ${compact ? "p-4" : "p-5"}`}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="font-semibold">基础数据</h3>
          <p className="text-[11px] text-muted mt-1">
            {typeLabel(type)} · {data.market || "—"} · 数据源 {stats.source || "—"}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-2">
        <Cell label="最新价格/净值" value={fmtNum(stats.latest_price, 4)} />
        <Cell label="最近日期" value={stats.latest_date || "—"} />
        <Cell label="近一年最高" value={fmtNum(stats.high_52w, 4)} />
        <Cell label="近一年最低" value={fmtNum(stats.low_52w, 4)} />
        <Cell label="估算分红率" value={dividendYield} />
      </div>
    </div>
  );
}
