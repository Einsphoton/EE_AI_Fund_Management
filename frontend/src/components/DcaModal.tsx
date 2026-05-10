import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { TrendingDown, TrendingUp, Minus, AlertTriangle, Sparkles, ListTodo } from "lucide-react";
import Modal from "./Modal";
import { Asset, DcaApi, DcaSuggestion } from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
  asset: Asset | null;
}

const DECISION_META: Record<DcaSuggestion["decision"], {
  label: string; color: string; icon: any; bg: string;
}> = {
  buy_more:   { label: "加大投入",   color: "text-emerald2", icon: TrendingDown, bg: "bg-emerald2/10 border-emerald2/40" },
  buy_normal: { label: "正常定投",   color: "text-accent-soft", icon: Minus, bg: "bg-accent/10 border-accent/40" },
  buy_less:   { label: "减少投入",   color: "text-amber2",   icon: TrendingUp,   bg: "bg-amber2/10 border-amber2/40" },
  skip:       { label: "本期暂缓",   color: "text-rose2",    icon: AlertTriangle, bg: "bg-rose2/10 border-rose2/40" },
};

export default function DcaModal({ open, onClose, asset }: Props) {
  const qc = useQueryClient();
  const [base, setBase] = useState(1000);
  const [feeRate, setFeeRate] = useState(0.001);
  const [data, setData] = useState<DcaSuggestion | null>(null);
  const [loading, setLoading] = useState(false);
  const [creatingTodo, setCreatingTodo] = useState(false);

  const fetchSuggest = async () => {
    if (!asset) return;
    setLoading(true);
    try {
      const r = await DcaApi.suggest(asset.id, base, feeRate);
      setData(r);
    } catch (e: any) {
      toast.error(e.message || "请求失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open && asset) fetchSuggest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, asset?.id]);

  const meta = data ? DECISION_META[data.decision] : null;

  const fmt = (v: number | null | undefined, d = 4) =>
    v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

  const createTodo = async () => {
    if (!asset || !data) return;
    setCreatingTodo(true);
    try {
      await DcaApi.createTodo(asset.id, base, feeRate);
      toast.success("已加入 To-do，等待你确认是否采纳");
      qc.invalidateQueries({ queryKey: ["todos"] });
      onClose();
    } catch (e: any) {
      toast.error(e?.message || "加入 To-do 失败");
    } finally {
      setCreatingTodo(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`基金定投建议 · ${asset?.name || ""}`}
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose}>关闭</button>
          <button
            className="btn-primary"
            disabled={!data || loading || creatingTodo}
            onClick={createTodo}
          >
            <ListTodo className="w-4 h-4" /> {creatingTodo ? "加入中…" : "加入 To-do 等待确认"}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label className="label">基础定投金额（元/期）</label>
          <input className="input" type="number" step="100" min={100}
                 value={base}
                 onChange={(e) => setBase(e.target.valueAsNumber || 1000)} />
        </div>
        <div>
          <label className="label">申购费率</label>
          <select className="input" value={feeRate} onChange={(e) => setFeeRate(Number(e.target.value))}>
            <option value={0}>0%（C 类 / 免申购费）</option>
            <option value={0.0005}>0.05%</option>
            <option value={0.001}>0.10%（默认）</option>
            <option value={0.0012}>0.12%</option>
            <option value={0.0015}>0.15%</option>
          </select>
        </div>
        <div className="col-span-2">
          <button className="btn w-full" onClick={fetchSuggest} disabled={loading}>
            <Sparkles className="w-4 h-4" />
            {loading ? "正在结合实时净值与历史均线计算…" : "重新生成建议"}
          </button>
        </div>
      </div>

      {data && meta && (
        <>
          <div className={`rounded-2xl border ${meta.bg} p-5 mb-4`}>
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center gap-3">
                <meta.icon className={`w-6 h-6 ${meta.color}`} />
                <div>
                  <div className={`text-xl font-semibold ${meta.color}`}>{meta.label}</div>
                  <div className="text-[11px] text-muted mt-1">
                    Hermes-Lite · 国内常见智能定投策略
                  </div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-xs text-muted">本期建议金额</div>
                <div className="text-2xl font-semibold text-white font-mono">
                  ¥{data.suggest_amount.toLocaleString()}
                </div>
              </div>
            </div>
            <p className="text-sm text-white/85 leading-relaxed mt-3">{data.reason}</p>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <Cell label="实时净值"      value={fmt(data.last_price, 4)} />
            <Cell label="近 1 年均线"   value={fmt(data.ma250, 4)} />
            <Cell label="MA20"         value={fmt(data.ma20, 4)} />
            <Cell label="MA60"         value={fmt(data.ma60, 4)} />
            <Cell label="偏离度"        value={data.deviation != null ? `${(data.deviation * 100).toFixed(2)}%` : "—"}
                  tone={data.deviation != null ? (data.deviation < 0 ? "down" : "up") : "default"} />
            <Cell label="价格因子 × 趋势" value={`${data.price_factor.toFixed(2)} × ${data.trend_factor.toFixed(2)}`} />
            <Cell label="估算可买份额" value={fmt(data.suggest_shares, 4)} />
            <Cell label="估算手续费"   value={`¥${data.estimated_fee.toFixed(2)}`} />
          </div>
        </>
      )}
    </Modal>
  );
}

function Cell({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "up" | "down" }) {
  const c = tone === "up" ? "text-rose2" : tone === "down" ? "text-emerald2" : "text-white";
  return (
    <div className="rounded-xl border border-line bg-bg-soft/40 px-3 py-2 flex items-baseline justify-between">
      <span className="text-xs text-muted">{label}</span>
      <span className={`font-mono tabular-nums ${c}`}>{value}</span>
    </div>
  );
}
