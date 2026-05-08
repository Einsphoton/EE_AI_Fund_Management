import { useEffect, useMemo, useState } from "react";
import Modal from "./Modal";
import { Asset, AssetType, Market, Transaction } from "../api/client";
import { ASSET_TYPE_META, metaOf, marketLabel } from "../lib/assetMeta";

export interface AssetFormData {
  name: string;
  code: string;
  asset_type: AssetType;
  market: Market;
  platform: string;
  note: string;
  watch_only: boolean;

  // 理财/货基/现金扩展字段
  yield_7d?: number | null;
  expected_apr?: number | null;
  start_date?: string | null;
  maturity_date?: string | null;
  principal_amount?: number | null;
  is_principal_guaranteed?: boolean;

  // 仅创建时使用：自动作为首笔买入交易（fund/stock/etf 用）
  initial_shares?: number;
  initial_price?: number;
  initial_fee?: number;
  initial_date?: string;

  // 仅编辑时使用：要更新的首笔交易（若有且只有一笔）
  edit_first_txn?: {
    id: number;
    shares?: number;
    price?: number;
    fee?: number;
    trade_date?: string;
  };
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (data: AssetFormData) => Promise<void> | void;
  initial?: Asset | null;
  initialTxns?: Transaction[];
  title?: string;
  editing?: boolean;
}

const empty: AssetFormData = {
  name: "", code: "", asset_type: "fund", market: "OTC",
  platform: "", note: "", watch_only: false,
  is_principal_guaranteed: true,
};

// 资产类型按 order 排序后展示
const TYPE_ORDER: AssetType[] = (Object.keys(ASSET_TYPE_META) as AssetType[])
  .sort((a, b) => ASSET_TYPE_META[a].order - ASSET_TYPE_META[b].order);

export default function AssetForm({ open, onClose, onSubmit, initial, initialTxns, title, editing }: Props) {
  const [data, setData] = useState<AssetFormData>(empty);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      const base = { ...empty, ...initial } as AssetFormData;
      // 日期字段 ISO -> yyyy-mm-dd
      if (base.start_date) base.start_date = base.start_date.slice(0, 10);
      if (base.maturity_date) base.maturity_date = base.maturity_date.slice(0, 10);
      if (editing && initialTxns && initialTxns.length === 1) {
        const t = initialTxns[0];
        base.edit_first_txn = {
          id: t.id,
          shares: t.shares || undefined,
          price: t.price || undefined,
          fee: t.fee || undefined,
          trade_date: t.trade_date ? t.trade_date.slice(0, 10) : undefined,
        };
      }
      setData(base);
    } else {
      setData(empty);
    }
  }, [open, initial, editing, initialTxns]);

  const meta = useMemo(() => metaOf(data.asset_type), [data.asset_type]);

  const set = <K extends keyof AssetFormData>(k: K, v: AssetFormData[K]) =>
    setData((d) => ({ ...d, [k]: v }));

  const setEditTxn = (patch: Partial<NonNullable<AssetFormData["edit_first_txn"]>>) =>
    setData((d) => ({
      ...d,
      edit_first_txn: { ...(d.edit_first_txn as any), ...patch },
    }));

  /** 切换资产类型时：把 market 拉回该类型的默认值（如果当前 market 不在可选列表内） */
  const switchType = (t: AssetType) => {
    const m = ASSET_TYPE_META[t];
    setData((d) => ({
      ...d,
      asset_type: t,
      market: m.availableMarkets.includes(d.market) ? d.market : m.defaultMarket,
    }));
  };

  const submit = async () => {
    if (!data.name.trim() || !data.code.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit(data);
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  const editTxnCount = initialTxns?.length ?? 0;
  const hasOneTxn = editing && editTxnCount === 1;
  const hasMultiTxn = editing && editTxnCount > 1;
  const isQuoteAsset = meta.hasQuote; // fund/stock/etf

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title || (initial ? "编辑标的" : "添加标的")}
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {/* ============ 类型选择（含 7 类） ============ */}
        <div>
          <label className="label">资产类型</label>
          <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
            {TYPE_ORDER.map((t) => {
              const m = ASSET_TYPE_META[t];
              const active = data.asset_type === t;
              return (
                <button
                  key={t}
                  type="button"
                  disabled={editing}
                  className={`btn flex-col items-start text-left h-auto py-2 ${
                    active ? "border-accent/60 bg-accent/15 text-white" : ""
                  } ${editing ? "opacity-60 cursor-not-allowed" : ""}`}
                  onClick={() => !editing && switchType(t)}
                  title={m.description}
                >
                  <div className="text-sm font-medium">{m.label}</div>
                  <div className="text-[10px] text-muted leading-tight">{m.description}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* ============ 市场 / 币种 ============ */}
          <div>
            <label className="label">{isQuoteAsset ? "市场" : "币种"}</label>
            {meta.availableMarkets.length === 1 ? (
              <input className="input" value={marketLabel(meta.availableMarkets[0])} readOnly />
            ) : (
              <div className={`grid gap-2 ${meta.availableMarkets.length > 3 ? "grid-cols-4" : "grid-cols-3"}`}>
                {meta.availableMarkets.map((m) => (
                  <button
                    key={m}
                    type="button"
                    className={`btn ${data.market === m ? "border-accent/60 bg-accent/15 text-white" : ""}`}
                    onClick={() => set("market", m)}
                  >
                    {marketLabel(m)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className="label">买入平台</label>
            <input className="input" value={data.platform}
                   onChange={(e) => set("platform", e.target.value)}
                   placeholder="如 蚂蚁财富 / 招行 / 富途" />
          </div>

          {/* ============ 名称 / 代码 ============ */}
          <div>
            <label className="label">名称 *</label>
            <input className="input" value={data.name}
                   onChange={(e) => set("name", e.target.value)}
                   placeholder={
                     meta.label === "场外基金" ? "如：兴全合宜"
                     : meta.label === "股票" ? "如：腾讯控股"
                     : meta.label === "ETF / 场内基金" ? "如：沪深300ETF"
                     : meta.label === "货币基金" ? "如：余额宝 / 朝朝宝"
                     : meta.label === "理财产品" ? "如：招银日日盈"
                     : meta.label === "现金 / 活期" ? "如：招行活期"
                     : "请输入名称"
                   } />
          </div>
          <div>
            <label className="label">代码 / 编号 *</label>
            <input className="input" value={data.code}
                   onChange={(e) => set("code", e.target.value.trim())}
                   placeholder={
                     data.asset_type === "fund" ? "如 163406"
                     : data.asset_type === "stock" || data.asset_type === "etf"
                       ? (data.market === "US" ? "AAPL" : data.market === "HK" ? "00700" : "600519")
                       : "可填产品编号或自定义短码"
                   } />
          </div>

          <div className="col-span-2">
            <label className="flex items-center gap-2 text-sm select-none cursor-pointer">
              <input type="checkbox" className="accent-accent w-4 h-4"
                     checked={data.watch_only}
                     onChange={(e) => set("watch_only", e.target.checked)} />
              仅观察 / 暂未实质买入
            </label>
          </div>
        </div>

        {/* ============ 货基 / 理财 / 现金 / 债券 专用字段 ============ */}
        {!isQuoteAsset && !data.watch_only && (
          <>
            <div className="flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-muted">{meta.label} 信息</span>
              <div className="h-px flex-1 bg-line" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              {meta.needsPrincipal && (
                <div>
                  <label className="label">本金金额 *</label>
                  <input className="input" type="number" step="0.01"
                         value={data.principal_amount ?? ""}
                         onChange={(e) => set("principal_amount", e.target.valueAsNumber || null)} />
                  <div className="text-[10px] text-muted mt-1">直接录入持有金额，无需走交易流水</div>
                </div>
              )}
              {meta.needsYield7d && (
                <div>
                  <label className="label">7 日年化（%）</label>
                  <input className="input" type="number" step="0.01"
                         value={data.yield_7d ?? ""}
                         onChange={(e) => set("yield_7d", e.target.valueAsNumber || null)}
                         placeholder="如 1.85" />
                </div>
              )}
              {meta.needsExpectedApr && (
                <div>
                  <label className="label">预期年化（%）</label>
                  <input className="input" type="number" step="0.01"
                         value={data.expected_apr ?? ""}
                         onChange={(e) => set("expected_apr", e.target.valueAsNumber || null)}
                         placeholder="如 3.50" />
                </div>
              )}
              {meta.needsTerm && (
                <>
                  <div>
                    <label className="label">起息日</label>
                    <input className="input" type="date"
                           value={data.start_date || ""}
                           onChange={(e) => set("start_date", e.target.value || null)} />
                  </div>
                  <div>
                    <label className="label">到期日</label>
                    <input className="input" type="date"
                           value={data.maturity_date || ""}
                           onChange={(e) => set("maturity_date", e.target.value || null)} />
                  </div>
                </>
              )}
              <div className="col-span-2">
                <label className="flex items-center gap-2 text-sm select-none cursor-pointer">
                  <input type="checkbox" className="accent-accent w-4 h-4"
                         checked={data.is_principal_guaranteed ?? true}
                         onChange={(e) => set("is_principal_guaranteed", e.target.checked)} />
                  保本（非保本会被风险评分扣分）
                </label>
              </div>
            </div>
          </>
        )}

        {/* ============ 创建模式 + 行情类资产：初始买入信息 ============ */}
        {!editing && !data.watch_only && isQuoteAsset && (
          <>
            <div className="flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-muted">初始买入信息（可选）</span>
              <div className="h-px flex-1 bg-line" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">{data.asset_type === "fund" ? "买入份额" : "买入股数"}</label>
                <input className="input" type="number" step={data.asset_type === "fund" ? "0.0001" : "1"}
                       value={data.initial_shares ?? ""}
                       onChange={(e) => set("initial_shares", e.target.valueAsNumber || undefined)} />
              </div>
              <div>
                <label className="label">{data.asset_type === "fund" ? "买入单价 / 净值" : "买入单价"}</label>
                <input className="input" type="number" step="0.0001"
                       value={data.initial_price ?? ""}
                       onChange={(e) => set("initial_price", e.target.valueAsNumber || undefined)} />
              </div>
              <div>
                <label className="label">买入费用</label>
                <input className="input" type="number" step="0.01"
                       value={data.initial_fee ?? ""}
                       onChange={(e) => set("initial_fee", e.target.valueAsNumber || undefined)} />
              </div>
              <div>
                <label className="label">买入日期（可不填）</label>
                <input className="input" type="date"
                       value={data.initial_date?.slice(0, 10) || ""}
                       onChange={(e) => set("initial_date", e.target.value || undefined)} />
              </div>
            </div>
          </>
        )}

        {/* ============ 编辑模式：首笔交易（若只有一笔，且行情类）============ */}
        {hasOneTxn && data.edit_first_txn && isQuoteAsset && (
          <>
            <div className="flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-muted">首笔买入信息</span>
              <div className="h-px flex-1 bg-line" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">{data.asset_type === "fund" ? "买入份额" : "买入股数"}</label>
                <input className="input" type="number" step={data.asset_type === "fund" ? "0.0001" : "1"}
                       value={data.edit_first_txn.shares ?? ""}
                       onChange={(e) => setEditTxn({ shares: e.target.valueAsNumber || undefined })} />
              </div>
              <div>
                <label className="label">{data.asset_type === "fund" ? "买入单价 / 净值" : "买入单价"}</label>
                <input className="input" type="number" step="0.0001"
                       value={data.edit_first_txn.price ?? ""}
                       onChange={(e) => setEditTxn({ price: e.target.valueAsNumber || undefined })} />
              </div>
              <div>
                <label className="label">买入费用</label>
                <input className="input" type="number" step="0.01"
                       value={data.edit_first_txn.fee ?? ""}
                       onChange={(e) => setEditTxn({ fee: e.target.valueAsNumber || undefined })} />
              </div>
              <div>
                <label className="label">买入日期</label>
                <input className="input" type="date"
                       value={data.edit_first_txn.trade_date || ""}
                       onChange={(e) => setEditTxn({ trade_date: e.target.value || undefined })} />
              </div>
            </div>
          </>
        )}

        {hasMultiTxn && (
          <div className="rounded-xl border border-amber2/40 bg-amber2/5 p-3 text-xs text-amber2">
            该标的有 {editTxnCount} 笔交易记录，单笔金额 / 日期请到详情页的「交易记录」中分别编辑。
          </div>
        )}

        <div>
          <label className="label">备注</label>
          <textarea className="input min-h-[64px]" value={data.note}
                    onChange={(e) => set("note", e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
