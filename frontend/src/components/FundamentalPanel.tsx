import { Snapshot } from "../api/client";

interface Props {
  snapshot: Snapshot | null | undefined;
  market: string;
  isFund: boolean;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 0, maximumFractionDigits: digits,
  });
}

function fmtMktCap(v: number | null | undefined, currency: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  // 后端返回的单位为"亿"
  const yi = Number(v);
  if (yi >= 10000) return `${(yi / 10000).toFixed(2)} 万亿 ${currency}`;
  return `${yi.toFixed(2)} 亿 ${currency}`;
}

function fmtAmount(v: number | null | undefined, unit: string, currency: string): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (unit === "万") {
    if (n >= 10000) return `${(n / 10000).toFixed(2)} 亿 ${currency}`;
    return `${n.toFixed(2)} 万 ${currency}`;
  }
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)} 亿 ${currency}`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(2)} 万 ${currency}`;
  return `${n.toFixed(2)} ${currency}`;
}

interface Cell {
  label: string;
  value: string;
  tone?: "default" | "up" | "down" | "muted";
}

function Cell({ label, value, tone = "default" }: Cell) {
  const colorClass =
    tone === "up" ? "text-emerald2"
    : tone === "down" ? "text-rose2"
    : tone === "muted" ? "text-muted"
    : "text-white";
  return (
    <div className="flex justify-between items-baseline px-3 py-2 rounded-lg hover:bg-line/30 transition">
      <span className="text-xs text-muted">{label}</span>
      <span className={`text-sm font-medium ${colorClass} font-mono tabular-nums`}>{value}</span>
    </div>
  );
}

export default function FundamentalPanel({ snapshot, market, isFund }: Props) {
  if (isFund) return null;
  if (!snapshot || !snapshot.last) {
    return (
      <div className="card p-5">
        <h3 className="font-semibold mb-2">基本盘</h3>
        <p className="text-xs text-muted">暂未获取到基本盘数据。可能为非交易时段或代码不识别。</p>
      </div>
    );
  }

  const cur = snapshot.currency || "";
  const change = snapshot.change ?? 0;
  const changePct = snapshot.change_pct ?? 0;
  const tone: Cell["tone"] = change >= 0 ? "up" : "down";
  const sign = change >= 0 ? "+" : "";

  const cells: Cell[] = [
    { label: "今开",    value: fmtNum(snapshot.open,  4) },
    { label: "昨收",    value: fmtNum(snapshot.prev_close, 4) },
    { label: "最高",    value: fmtNum(snapshot.high,  4), tone: "up" },
    { label: "最低",    value: fmtNum(snapshot.low,   4), tone: "down" },
    { label: "振幅",    value: snapshot.amplitude != null ? `${snapshot.amplitude.toFixed(2)}%` : "—" },
    { label: "成交额",  value: fmtAmount(snapshot.amount, snapshot.amount_unit || "", cur) },
  ];

  if (market === "A") {
    cells.push(
      { label: "换手率",      value: snapshot.turnover != null ? `${snapshot.turnover.toFixed(2)}%` : "—" },
      { label: "市盈率(TTM)", value: fmtNum(snapshot.pe_ttm, 2) },
      { label: "市净率",      value: fmtNum(snapshot.pb,     2) },
      { label: "总市值",      value: fmtMktCap(snapshot.total_mktcap, cur) },
      { label: "流通市值",    value: fmtMktCap(snapshot.circ_mktcap,  cur) },
      { label: "52周最高",    value: fmtNum(snapshot.high_52w, 4), tone: "muted" },
      { label: "52周最低",    value: fmtNum(snapshot.low_52w,  4), tone: "muted" },
    );
  } else if (market === "HK") {
    cells.push(
      { label: "市盈率(TTM)", value: fmtNum(snapshot.pe_ttm, 2) },
      { label: "总市值",      value: fmtMktCap(snapshot.total_mktcap, cur) },
      { label: "流通市值",    value: fmtMktCap(snapshot.circ_mktcap,  cur) },
      { label: "52周最高",    value: fmtNum(snapshot.high_52w, 4), tone: "muted" },
      { label: "52周最低",    value: fmtNum(snapshot.low_52w,  4), tone: "muted" },
    );
  } else if (market === "US") {
    cells.push(
      { label: "换手率",      value: snapshot.turnover != null ? `${snapshot.turnover.toFixed(2)}%` : "—" },
      { label: "市盈率(TTM)", value: fmtNum(snapshot.pe_ttm, 2) },
      { label: "总市值",      value: fmtMktCap(snapshot.total_mktcap, cur) },
      { label: "52周最高",    value: fmtNum(snapshot.high_52w, 4), tone: "muted" },
      { label: "52周最低",    value: fmtNum(snapshot.low_52w,  4), tone: "muted" },
    );
  }

  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="font-semibold">基本盘</h3>
        <div className="flex items-baseline gap-2">
          <span className={`text-xl font-semibold ${tone === "up" ? "text-emerald2" : "text-rose2"} font-mono tabular-nums`}>
            {fmtNum(snapshot.last, 4)}
          </span>
          <span className={`text-xs font-medium ${tone === "up" ? "text-emerald2" : "text-rose2"}`}>
            {sign}{fmtNum(change, 4)}  {sign}{changePct.toFixed(2)}%
          </span>
          <span className="text-[10px] text-muted">{cur}</span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
        {cells.map((c) => (
          <Cell key={c.label} {...c} />
        ))}
      </div>
    </div>
  );
}
