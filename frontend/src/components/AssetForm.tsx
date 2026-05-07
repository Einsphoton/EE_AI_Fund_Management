import { useEffect, useState } from "react";
import Modal from "./Modal";
import { Asset, AssetType, Market, Transaction } from "../api/client";

export interface AssetFormData {
  name: string;
  code: string;
  asset_type: AssetType;
  market: Market;
  platform: string;
  note: string;
  watch_only: boolean;

  // 仅创建时使用：自动作为首笔买入交易
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
  /** 编辑模式下用于决定是否展示首笔交易编辑区 */
  initialTxns?: Transaction[];
  title?: string;
  editing?: boolean;
}

const empty: AssetFormData = {
  name: "", code: "", asset_type: "fund", market: "OTC",
  platform: "", note: "", watch_only: false,
};

export default function AssetForm({ open, onClose, onSubmit, initial, initialTxns, title, editing }: Props) {
  const [data, setData] = useState<AssetFormData>(empty);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      const base = { ...empty, ...initial } as AssetFormData;
      // 编辑模式下，如果只有一笔交易，把它带进表单方便编辑
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

  const isFund = data.asset_type === "fund";

  const set = <K extends keyof AssetFormData>(k: K, v: AssetFormData[K]) =>
    setData((d) => ({ ...d, [k]: v }));

  const setEditTxn = (patch: Partial<NonNullable<AssetFormData["edit_first_txn"]>>) =>
    setData((d) => ({
      ...d,
      edit_first_txn: { ...(d.edit_first_txn as any), ...patch },
    }));

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
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="label">类型</label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={editing}
              className={`btn ${data.asset_type === "fund" ? "border-accent/60 bg-accent/15 text-white" : ""} ${editing ? "opacity-60 cursor-not-allowed" : ""}`}
              onClick={() => {
                if (editing) return;
                set("asset_type", "fund");
                set("market", "OTC");
              }}
            >
              场外基金
            </button>
            <button
              type="button"
              disabled={editing}
              className={`btn ${data.asset_type === "stock" ? "border-accent/60 bg-accent/15 text-white" : ""} ${editing ? "opacity-60 cursor-not-allowed" : ""}`}
              onClick={() => {
                if (editing) return;
                set("asset_type", "stock");
                if (data.market === "OTC") set("market", "A");
              }}
            >
              股票 / 场内
            </button>
          </div>
        </div>

        <div>
          <label className="label">市场</label>
          {isFund ? (
            <input className="input" value="OTC（场外）" readOnly />
          ) : (
            <div className="grid grid-cols-3 gap-2">
              {(["A", "HK", "US"] as Market[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`btn ${data.market === m ? "border-accent/60 bg-accent/15 text-white" : ""}`}
                  onClick={() => set("market", m)}
                >
                  {m === "A" ? "A 股" : m === "HK" ? "港股" : "美股"}
                </button>
              ))}
            </div>
          )}
        </div>

        <div>
          <label className="label">名称 *</label>
          <input className="input" value={data.name}
                 onChange={(e) => set("name", e.target.value)}
                 placeholder={isFund ? "如：兴全合宜" : "如：腾讯控股"} />
        </div>
        <div>
          <label className="label">代码 *</label>
          <input className="input" value={data.code}
                 onChange={(e) => set("code", e.target.value.trim())}
                 placeholder={isFund ? "如 163406" : data.market === "US" ? "AAPL" : data.market === "HK" ? "00700" : "600519"} />
        </div>

        <div>
          <label className="label">买入平台</label>
          <input className="input" value={data.platform}
                 onChange={(e) => set("platform", e.target.value)}
                 placeholder="如 蚂蚁财富 / 富途 / 雪盈" />
        </div>
        <div className="flex items-end pb-1">
          <label className="flex items-center gap-2 text-sm select-none cursor-pointer">
            <input type="checkbox" className="accent-accent w-4 h-4"
                   checked={data.watch_only}
                   onChange={(e) => set("watch_only", e.target.checked)} />
            仅观察 / 暂未实质买入
          </label>
        </div>

        {/* ============ 创建模式：初始买入信息 ============ */}
        {!editing && !data.watch_only && (
          <>
            <div className="col-span-2 flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-muted">初始买入信息（可选）</span>
              <div className="h-px flex-1 bg-line" />
            </div>

            <div>
              <label className="label">{isFund ? "买入份额" : "买入股数"}</label>
              <input className="input" type="number" step={isFund ? "0.0001" : "1"}
                     value={data.initial_shares ?? ""}
                     onChange={(e) => set("initial_shares", e.target.valueAsNumber || undefined)} />
            </div>
            <div>
              <label className="label">{isFund ? "买入单价 / 净值" : "买入单价"}</label>
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
          </>
        )}

        {/* ============ 编辑模式：首笔交易（若只有一笔）============ */}
        {hasOneTxn && data.edit_first_txn && (
          <>
            <div className="col-span-2 flex items-center gap-3 pt-2">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-muted">首笔买入信息</span>
              <div className="h-px flex-1 bg-line" />
            </div>
            <div>
              <label className="label">{isFund ? "买入份额" : "买入股数"}</label>
              <input className="input" type="number" step={isFund ? "0.0001" : "1"}
                     value={data.edit_first_txn.shares ?? ""}
                     onChange={(e) => setEditTxn({ shares: e.target.valueAsNumber || undefined })} />
            </div>
            <div>
              <label className="label">{isFund ? "买入单价 / 净值" : "买入单价"}</label>
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
          </>
        )}

        {hasMultiTxn && (
          <div className="col-span-2 mt-2 rounded-xl border border-amber2/40 bg-amber2/5 p-3 text-xs text-amber2">
            该标的有 {editTxnCount} 笔交易记录，单笔金额 / 日期请到详情页的「交易记录」中分别编辑。
          </div>
        )}

        <div className="col-span-2">
          <label className="label">备注</label>
          <textarea className="input min-h-[64px]" value={data.note}
                    onChange={(e) => set("note", e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
