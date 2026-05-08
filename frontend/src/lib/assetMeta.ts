/**
 * 资产类型 / 市场的元信息——单一来源，避免在各页面散落硬编码。
 *
 * 添加新资产类型时只需在这里追加一行，UI 各处会自动适配：
 *   - AssetForm 类型/字段切换
 *   - Dashboard / Assets 列表分组
 *   - AssetDetail 文案
 *   - 仪表盘"总资产"分类汇总
 */
import type { AssetType, Market } from "../api/client";

export interface AssetTypeMeta {
  /** 中文名（菜单/卡片用） */
  label: string;
  /** 一句话简介 */
  description: string;
  /** 默认市场（创建时回填） */
  defaultMarket: Market;
  /** 该类型可选的市场列表；空表示固定 defaultMarket */
  availableMarkets: Market[];
  /** 该类型是否需要每日抓行情（false = 货基/理财/现金，市值由配置字段给出） */
  hasQuote: boolean;
  /** 是否显示"份额"字段（false 时改用"金额/本金"） */
  hasShares: boolean;
  /** 是否支持定投建议 */
  dcaSupport: boolean;
  /** 是否需要"起息日 / 到期日" */
  needsTerm: boolean;
  /** 是否需要"七日年化" */
  needsYield7d: boolean;
  /** 是否需要"预期年化" */
  needsExpectedApr: boolean;
  /** 是否需要"本金金额"（直接录入而非交易流水） */
  needsPrincipal: boolean;
  /** 仪表盘分组排序权重（小的在前） */
  order: number;
  /** 强调色（用于卡片左侧条/徽章） */
  accent: "fund" | "stock" | "etf" | "cash" | "wealth" | "bond";
}

export const ASSET_TYPE_META: Record<AssetType, AssetTypeMeta> = {
  fund: {
    label: "场外基金",
    description: "公募开放式基金，按净值申赎",
    defaultMarket: "OTC",
    availableMarkets: ["OTC"],
    hasQuote: true,
    hasShares: true,
    dcaSupport: true,
    needsTerm: false,
    needsYield7d: false,
    needsExpectedApr: false,
    needsPrincipal: false,
    order: 20,
    accent: "fund",
  },
  stock: {
    label: "股票",
    description: "A 股 / 港股 / 美股",
    defaultMarket: "A",
    availableMarkets: ["A", "HK", "US"],
    hasQuote: true,
    hasShares: true,
    dcaSupport: false,
    needsTerm: false,
    needsYield7d: false,
    needsExpectedApr: false,
    needsPrincipal: false,
    order: 10,
    accent: "stock",
  },
  etf: {
    label: "ETF / 场内基金",
    description: "场内交易的 ETF / LOF",
    defaultMarket: "A",
    availableMarkets: ["A", "HK", "US"],
    hasQuote: true,
    hasShares: true,
    dcaSupport: false,
    needsTerm: false,
    needsYield7d: false,
    needsExpectedApr: false,
    needsPrincipal: false,
    order: 15,
    accent: "etf",
  },
  money_fund: {
    label: "货币基金",
    description: "余额宝 / 朝朝宝 / 零钱通 等",
    defaultMarket: "CNY",
    availableMarkets: ["CNY", "USD", "HKD"],
    hasQuote: false,
    hasShares: false,
    dcaSupport: false,
    needsTerm: false,
    needsYield7d: true,
    needsExpectedApr: false,
    needsPrincipal: true,
    order: 40,
    accent: "cash",
  },
  wealth: {
    label: "理财产品",
    description: "银行 / 平台理财（定期 / 净值型）",
    defaultMarket: "CNY",
    availableMarkets: ["CNY", "USD", "HKD"],
    hasQuote: false,
    hasShares: false,
    dcaSupport: false,
    needsTerm: true,
    needsYield7d: false,
    needsExpectedApr: true,
    needsPrincipal: true,
    order: 30,
    accent: "wealth",
  },
  cash: {
    label: "现金 / 活期",
    description: "活期存款、零钱、外币现金",
    defaultMarket: "CNY",
    availableMarkets: ["CNY", "USD", "HKD"],
    hasQuote: false,
    hasShares: false,
    dcaSupport: false,
    needsTerm: false,
    needsYield7d: false,
    needsExpectedApr: false,
    needsPrincipal: true,
    order: 50,
    accent: "cash",
  },
  bond: {
    label: "债券",
    description: "国债 / 企业债 / 国债逆回购",
    defaultMarket: "A",
    availableMarkets: ["A", "HK", "US", "CNY"],
    hasQuote: false,
    hasShares: false,
    dcaSupport: false,
    needsTerm: true,
    needsYield7d: false,
    needsExpectedApr: true,
    needsPrincipal: true,
    order: 25,
    accent: "bond",
  },
};

/** 便捷访问器：未知类型回退到 fund 配置 */
export function metaOf(t: AssetType | string | undefined | null): AssetTypeMeta {
  if (!t) return ASSET_TYPE_META.fund;
  return ASSET_TYPE_META[t as AssetType] || ASSET_TYPE_META.fund;
}

/** 中文市场名（含币种） */
export function marketLabel(m: Market | string): string {
  const map: Record<string, string> = {
    A: "A 股", HK: "港股", US: "美股", OTC: "场外",
    CNY: "人民币", USD: "美元", HKD: "港币",
  };
  return map[m as string] || String(m);
}

/** 资产类型的颜色 token（与 tailwind 主题色匹配） */
export function accentColorClass(accent: AssetTypeMeta["accent"]): {
  text: string; bg: string; border: string;
} {
  switch (accent) {
    case "fund":   return { text: "text-accent",   bg: "bg-accent/10",   border: "border-accent/40" };
    case "stock":  return { text: "text-emerald2", bg: "bg-emerald2/10", border: "border-emerald2/40" };
    case "etf":    return { text: "text-cyan-400", bg: "bg-cyan-400/10", border: "border-cyan-400/40" };
    case "cash":   return { text: "text-amber2",   bg: "bg-amber2/10",   border: "border-amber2/40" };
    case "wealth": return { text: "text-fuchsia-400", bg: "bg-fuchsia-400/10", border: "border-fuchsia-400/40" };
    case "bond":   return { text: "text-sky-400",  bg: "bg-sky-400/10",  border: "border-sky-400/40" };
  }
}
