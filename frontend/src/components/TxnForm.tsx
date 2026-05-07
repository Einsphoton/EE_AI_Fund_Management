import { useEffect, useState } from "react";
import Modal from "./Modal";
import { Asset, TxnType, Transaction } from "../api/client";

export interface TxnFormData {
  txn_type: TxnType;
  shares: number;
  price: number;
  amount?: number;
  fee?: number;
  trade_date?: string;
  note?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  asset: Asset | null;
  /** 传入则为编辑模式 */
  initial?: Transaction | null;
  /** 用于"基金定投"自动填充 */
  prefill?: Partial<TxnFormData>;
  title?: string;
  onSubmit: (data: TxnFormData) => Promise<void> | void;
}

const defaultData = (): TxnFormData => ({
  txn_type: "buy",
  shares: 0,
  price: 0,
  fee: 0,
  trade_date: new Date().toISOString().slice(0, 10),
});

export default function TxnForm({ open, onClose, asset, initial, prefill, title, onSubmit }: Props) {
  const [data, setData] = useState<TxnFormData>(defaultData());
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (initial) {
      setData({
        txn_type: initial.txn_type,
        shares: initial.shares || 0,
        price: initial.price || 0,
        amount: initial.amount || 0,
        fee: initial.fee || 0,
        trade_date: initial.trade_date ? initial.trade_date.slice(0, 10) : "",
        note: initial.note || "",
      });
    } else {
      setData({ ...defaultData(), ...(prefill || {}) });
    }
  }, [open, initial, prefill]);

  const isFund = asset?.asset_type === "fund";
  const isEdit = !!initial;

  const submit = async () => {
    if (!asset) return;
    setSubmitting(true);
    try {
      await onSubmit(data);
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title || (isEdit ? `编辑交易 · ${asset?.name || ""}` : `记录交易 · ${asset?.name || ""}`)}
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
        <div className="col-span-2">
          <label className="label">类型</label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              className={`btn ${data.txn_type === "buy" ? "border-emerald2/60 bg-emerald2/10 text-emerald2" : ""}`}
              onClick={() => setData((d) => ({ ...d, txn_type: "buy" }))}
            >
              买入 / 追加
            </button>
            <button
              type="button"
              className={`btn ${data.txn_type === "sell" ? "border-rose2/60 bg-rose2/10 text-rose2" : ""}`}
              onClick={() => setData((d) => ({ ...d, txn_type: "sell" }))}
            >
              卖出
            </button>
          </div>
        </div>

        <div>
          <label className="label">{isFund ? "份额" : "股数"}</label>
          <input className="input" type="number" step={isFund ? "0.0001" : "1"} value={data.shares || ""}
                 onChange={(e) => setData((d) => ({ ...d, shares: e.target.valueAsNumber || 0 }))} />
        </div>
        <div>
          <label className="label">{isFund ? "净值 / 单价" : "成交单价"}</label>
          <input className="input" type="number" step="0.0001" value={data.price || ""}
                 onChange={(e) => setData((d) => ({ ...d, price: e.target.valueAsNumber || 0 }))} />
        </div>

        <div>
          <label className="label">手续费</label>
          <input className="input" type="number" step="0.01" value={data.fee || ""}
                 onChange={(e) => setData((d) => ({ ...d, fee: e.target.valueAsNumber || 0 }))} />
        </div>
        <div>
          <label className="label">交易日期</label>
          <input className="input" type="date" value={data.trade_date || ""}
                 onChange={(e) => setData((d) => ({ ...d, trade_date: e.target.value }))} />
        </div>

        <div className="col-span-2">
          <label className="label">备注</label>
          <input className="input" value={data.note || ""}
                 onChange={(e) => setData((d) => ({ ...d, note: e.target.value }))} />
        </div>

        {(data.shares > 0 && data.price > 0) && (
          <div className="col-span-2 text-xs text-muted bg-bg-soft/50 rounded-lg px-3 py-2">
            预计成交金额：<span className="text-white font-mono">
              ¥{(data.shares * data.price).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            {data.fee ? `（含手续费 ¥${data.fee.toFixed(2)}）` : ""}
          </div>
        )}
      </div>
    </Modal>
  );
}
