export function fmtMoney(n: number | null | undefined, currency = "CNY") {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n < 0 ? "-" : "";
  const v = Math.abs(n);
  const symbol = currency === "USD" ? "$" : currency === "HKD" ? "HK$" : "¥";
  return `${sign}${symbol}${v.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

export function fmtPct(n: number | null | undefined) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const s = n >= 0 ? "+" : "";
  return `${s}${n.toFixed(2)}%`;
}

export function fmtNum(n: number | null | undefined, digits = 4) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 0, maximumFractionDigits: digits,
  });
}

export function clsx(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(" ");
}

export function actionColor(a: string) {
  if (a === "buy") return "text-emerald2";
  if (a === "sell") return "text-rose2";
  return "text-amber2";
}

export function actionLabel(a: string) {
  return a === "buy" ? "买入" : a === "sell" ? "卖出" : "持有";
}

export function dateOnly(d?: string | null) {
  if (!d) return "";
  return d.slice(0, 10);
}

const PAD = (n: number) => String(n).padStart(2, "0");

/** 后端返回的 naive ISO 字符串（无时区后缀）被当作本地时间解析。
 *  如果字符串以 Z 结尾才按 UTC 处理。 */
export function parseLocalDate(s?: string | null): Date | null {
  if (!s) return null;
  // 带 Z 或 +HH:MM 的按原样让 Date 解析即可
  if (/Z$|[+-]\d{2}:?\d{2}$/.test(s)) return new Date(s);
  // naive 情况：YYYY-MM-DDTHH:MM:SS[.ffffff] → 拆开当本地时间构造
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/.exec(s);
  if (!m) return new Date(s);
  return new Date(
    +m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6],
  );
}

export function fmtDateTime(s?: string | null): string {
  const d = parseLocalDate(s);
  if (!d || isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${PAD(d.getMonth() + 1)}-${PAD(d.getDate())} ${PAD(d.getHours())}:${PAD(d.getMinutes())}`;
}

export function fmtTime(s?: string | null): string {
  const d = parseLocalDate(s);
  if (!d || isNaN(d.getTime())) return "";
  return `${PAD(d.getHours())}:${PAD(d.getMinutes())}`;
}

export function fmtDate(s?: string | null): string {
  const d = parseLocalDate(s);
  if (!d || isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${PAD(d.getMonth() + 1)}-${PAD(d.getDate())}`;
}
