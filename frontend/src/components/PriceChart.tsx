import ReactECharts from "echarts-for-react";
import * as echarts from "echarts";
import { Quote, Transaction } from "../api/client";
import { useMemo } from "react";

interface Props {
  quote: Quote;
  height?: number;
}

export default function PriceChart({ quote, height = 460 }: Props) {
  const option = useMemo(() => buildOption(quote), [quote]);
  return (
    <ReactECharts
      option={option}
      style={{ height }}
      theme="dark"
      notMerge
      lazyUpdate
      opts={{ renderer: "canvas" }}
    />
  );
}

function buildOption(quote: Quote) {
  const isFund = quote.asset_type === "fund" && quote.market === "OTC";
  const dates = quote.points.map((p) => p.date);
  const close = quote.points.map((p) => p.close);
  const candles = quote.points.map((p) => [p.open ?? p.close, p.close, p.low ?? p.close, p.high ?? p.close]);
  const volumes = quote.points.map((p, i) => [i, p.volume ?? 0, (p.close >= (p.open ?? p.close)) ? 1 : -1]);

  const txnPoints = (quote.transactions || [])
    .map((t) => buildTxnMark(t, quote.points))
    .filter(Boolean) as any[];

  const baseAccent = "#7c5cff";
  const upColor = "#10b981";
  const downColor = "#ef4444";

  return {
    backgroundColor: "transparent",
    animation: true,
    legend: {
      data: isFund ? ["净值"] : ["K 线", "成交量"],
      textStyle: { color: "#a3b3d4" },
      top: 8,
    },
    grid: isFund
      ? [{ left: 60, right: 30, top: 50, bottom: 60 }]
      : [
          { left: 60, right: 30, top: 50, height: "62%" },
          { left: 60, right: 30, top: "74%", height: "16%" },
        ],
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(18,26,46,0.95)",
      borderColor: "#1f2a44",
      textStyle: { color: "#e6ecff", fontSize: 12 },
      axisPointer: { type: "cross", lineStyle: { color: "#3f4a72" } },
      extraCssText: "border-radius: 10px; box-shadow: 0 8px 30px -10px rgba(0,0,0,0.5);",
      // markPoint 单独以 item 形式触发（自带的 tooltip.formatter 会生效）
      enterable: false,
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    xAxis: isFund
      ? [{
          type: "category", data: dates, boundaryGap: false,
          axisLine: { lineStyle: { color: "#2b3a60" } },
          axisLabel: { color: "#7587a8" },
          splitLine: { show: false },
        }]
      : [
          {
            type: "category", data: dates, gridIndex: 0,
            axisLine: { lineStyle: { color: "#2b3a60" } },
            axisLabel: { color: "#7587a8" },
            splitLine: { show: false },
          },
          {
            type: "category", data: dates, gridIndex: 1,
            axisLine: { lineStyle: { color: "#2b3a60" } },
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        ],
    yAxis: isFund
      ? [{
          scale: true,
          axisLine: { show: false },
          axisLabel: { color: "#7587a8" },
          splitLine: { lineStyle: { color: "#1f2a44", type: "dashed" } },
        }]
      : [
          {
            scale: true, gridIndex: 0,
            axisLine: { show: false },
            axisLabel: { color: "#7587a8" },
            splitLine: { lineStyle: { color: "#1f2a44", type: "dashed" } },
          },
          {
            scale: true, gridIndex: 1,
            axisLine: { show: false },
            axisLabel: { show: false },
            splitLine: { show: false },
          },
        ],
    dataZoom: [
      { type: "inside", xAxisIndex: isFund ? [0] : [0, 1], start: 0, end: 100 },
      {
        type: "slider", xAxisIndex: isFund ? [0] : [0, 1], height: 16, bottom: 8,
        start: 0, end: 100,
        backgroundColor: "transparent",
        fillerColor: "rgba(124,92,255,0.15)",
        borderColor: "#1f2a44",
        handleStyle: { color: baseAccent },
        textStyle: { color: "#7587a8" },
      },
    ],
    series: isFund
      ? [
          {
            name: "净值",
            type: "line",
            data: close,
            smooth: true,
            showSymbol: false,
            lineStyle: { width: 2, color: baseAccent },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: "rgba(124,92,255,0.45)" },
                { offset: 1, color: "rgba(124,92,255,0.02)" },
              ]),
            },
            markPoint: txnPoints.length ? {
              symbol: "pin", symbolSize: 50, data: txnPoints,
              tooltip: { trigger: "item", formatter: (p: any) => p?.data?.txnHtml || "" },
            } : undefined,
          },
        ]
      : [
          {
            name: "K 线",
            type: "candlestick",
            data: candles,
            itemStyle: {
              color: upColor, color0: downColor,
              borderColor: upColor, borderColor0: downColor,
            },
            markPoint: txnPoints.length ? {
              symbol: "pin", symbolSize: 50, data: txnPoints,
              tooltip: { trigger: "item", formatter: (p: any) => p?.data?.txnHtml || "" },
            } : undefined,
          },
          {
            name: "成交量",
            type: "bar",
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes.map((v) => ({
              value: v[1],
              itemStyle: { color: v[2] === 1 ? "rgba(16,185,129,0.55)" : "rgba(239,68,68,0.55)" },
            })),
          },
        ],
  };
}

/**
 * 把一笔交易映射到图上的标注。
 *  - 交易日可能是非交易日（周末/休市）-> 选最接近的交易日
 *  - 标注 y 用对应日期的实际收盘，避免成本价偏离曲线
 *  - 早于行情区间起点（视图被裁剪），返回 null 不标注
 *  - 同一天多笔交易在同一坐标会被 echarts 自动堆叠，互不遮挡
 */
function buildTxnMark(t: Transaction, points: { date: string; close: number }[]) {
  if (!points.length) return null;
  const day = (t.trade_date || "").slice(0, 10);
  if (!day) return null;
  if (day < points[0].date) return null;

  let idx = -1;
  for (let i = points.length - 1; i >= 0; i--) {
    if (points[i].date <= day) { idx = i; break; }
  }
  if (idx < 0) idx = 0;

  const xValue = points[idx].date;
  const yValue = points[idx].close;

  const isBuy = t.txn_type === "buy";
  const label = isBuy ? "买" : "卖";
  const color = isBuy ? "#10b981" : "#ef4444";
  const titleZh = isBuy ? "买入" : "卖出";

  const fmtNum = (v: any, d = 4) =>
    v == null || isNaN(Number(v))
      ? "—"
      : Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: d });
  const fmtMoney = (v: any) => fmtNum(v, 2);

  const computedAmount = t.amount && t.amount > 0
    ? t.amount
    : (t.shares || 0) * (t.price || 0);

  const rows = [
    { k: "交易日期", v: day },
    { k: "份额/股数", v: t.shares ? fmtNum(t.shares, 4) : "—" },
    { k: "成交单价", v: t.price ? fmtNum(t.price, 4) : "—" },
    { k: "成交金额", v: computedAmount ? fmtMoney(computedAmount) : "—" },
    { k: "手续费",   v: t.fee ? fmtMoney(t.fee) : "0" },
    ...(t.note ? [{ k: "备注", v: t.note }] : []),
  ];

  const txnHtml = `
    <div style="min-width:200px;">
      <div style="
        font-weight:700;color:${color};font-size:13px;margin-bottom:8px;
        padding-bottom:6px;border-bottom:1px solid #1f2a44;
        display:flex;align-items:center;gap:6px;">
        <span style="
          display:inline-block;width:18px;height:18px;line-height:18px;text-align:center;
          background:${color};color:#fff;border-radius:4px;font-size:11px;">${label}</span>
        ${titleZh}
      </div>
      <table style="font-size:12px;width:100%;border-collapse:collapse;">
        ${rows.map(r => `
          <tr>
            <td style="color:#7587a8;padding:2px 8px 2px 0;">${r.k}</td>
            <td style="color:#e6ecff;font-family:ui-monospace,SFMono-Regular,monospace;text-align:right;">${r.v}</td>
          </tr>
        `).join("")}
      </table>
    </div>
  `;

  return {
    name: titleZh,
    coord: [xValue, yValue],
    value: label,
    symbolOffset: [0, isBuy ? -10 : 10],
    symbolRotate: isBuy ? 0 : 180,
    itemStyle: { color, shadowColor: color, shadowBlur: 12 },
    label: {
      show: true, color: "#fff", fontSize: 11, fontWeight: 700, formatter: label,
    },
    txnHtml,
  } as any;
}
