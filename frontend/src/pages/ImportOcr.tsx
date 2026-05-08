/**
 * OCR 批量导入页：
 *   1) 拖拽 / 选择多张持仓页截图
 *   2) 选择平台提示（可不选）
 *   3) 上传 → 后端走视觉模型解析 → 返回每项 OCR 结果 + 现有资产候选 + 建议动作
 *   4) 用户在表格里逐行编辑：动作（创建/追加/减仓/更新/跳过）、绑定资产、份额、价格…
 *   5) 一键提交 → 事务性入库
 */
import { useMemo, useState } from "react";
import { Upload, Image as ImageIcon, X, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import {
  ImportApi, OcrItem, OcrParseResult, OcrCommitItem,
  AssetType, Market,
} from "../api/client";
import { ASSET_TYPE_META, metaOf } from "../lib/assetMeta";
import { fmtMoney } from "../lib/format";

interface UploadFile {
  file: File;
  preview: string;
  id: string;
}

interface RowState {
  fileIdx: number;
  itemIdx: number;
  origin: OcrItem;
  // 用户编辑后的最终决策
  decision: OcrCommitItem;
  // 用户当前选中的候选 asset_id（null 表示新建）
  bindAssetId: number | null;
}

const PLATFORM_HINTS = [
  "", "微信理财通", "支付宝财富", "招商银行", "中国银行", "平安银行",
  "工商银行", "富途", "招商证券", "中银国际", "雪盈证券",
];

export default function ImportOcr() {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [platformHint, setPlatformHint] = useState("");
  const [parsing, setParsing] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [parseResults, setParseResults] = useState<OcrParseResult[]>([]);
  const [rows, setRows] = useState<RowState[]>([]);

  // ----- 文件管理 -----
  const onPickFiles = (list: FileList | null) => {
    if (!list) return;
    const next: UploadFile[] = [];
    for (const f of Array.from(list)) {
      if (!f.type.startsWith("image/")) continue;
      next.push({
        file: f,
        preview: URL.createObjectURL(f),
        id: `${f.name}_${f.lastModified}_${f.size}`,
      });
    }
    setFiles((prev) => [...prev, ...next]);
  };

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((x) => x.id !== id));
  };

  const clearFiles = () => {
    files.forEach((f) => URL.revokeObjectURL(f.preview));
    setFiles([]);
    setParseResults([]);
    setRows([]);
  };

  // ----- 上传解析 -----
  const startParse = async () => {
    if (files.length === 0) {
      toast.error("请先选择至少一张截图");
      return;
    }
    setParsing(true);
    try {
      const r = await ImportApi.parse(files.map((f) => f.file), platformHint);
      setParseResults(r.results);
      // 把每张图的每个 item 摊平为表格行
      const newRows: RowState[] = [];
      r.results.forEach((res, fileIdx) => {
        res.items.forEach((it, itemIdx) => {
          const sug = it._suggestion;
          const top = (it._candidates && it._candidates[0]) || null;
          newRows.push({
            fileIdx, itemIdx, origin: it,
            bindAssetId: top?.asset_id ?? null,
            decision: {
              action: sug?.action || "create",
              asset_id: top?.asset_id ?? null,
              name: it.name || "",
              code: it.code || "",
              asset_type: it.asset_type,
              market: defaultMarketFor(it.asset_type),
              platform: res.platform || platformHint || "",
              shares: it.shares ?? undefined,
              delta_shares: sug?.delta_shares,
              delta_amount: sug?.delta_amount,
              avg_cost: it.avg_cost ?? undefined,
              current_price: it.current_price ?? undefined,
              market_value: it.market_value ?? undefined,
              profit: it.profit ?? undefined,
              profit_pct: it.profit_pct ?? undefined,
              principal_amount: it.amount ?? it.market_value ?? undefined,
              yield_7d: it.yield_7d ?? undefined,
              expected_apr: it.expected_apr ?? undefined,
              maturity_date: it.maturity_date ?? undefined,
              raw: { raw_text: it.raw_text || "" },
            },
          });
        });
      });
      setRows(newRows);
      const totalErrs = r.results.filter((x) => x.error).length;
      toast.success(`解析完成：${r.total} 项${totalErrs ? `（${totalErrs} 张异常）` : ""}`);
    } catch (e: any) {
      toast.error(`解析失败：${e?.message || e}`);
    } finally {
      setParsing(false);
    }
  };

  // ----- 行编辑 -----
  const updateRow = (idx: number, patch: Partial<RowState["decision"]>) => {
    setRows((prev) => prev.map((r, i) =>
      i === idx ? { ...r, decision: { ...r.decision, ...patch } } : r));
  };

  const updateBind = (idx: number, candId: number | null) => {
    setRows((prev) => prev.map((r, i) => {
      if (i !== idx) return r;
      // 切到"新建"时把 action 改成 create；否则给个合理的默认 action
      const action = candId == null ? "create" : (r.origin._suggestion?.action || "skip");
      return {
        ...r,
        bindAssetId: candId,
        decision: { ...r.decision, asset_id: candId, action },
      };
    }));
  };

  const stats = useMemo(() => {
    const s = { create: 0, append_buy: 0, append_sell: 0, update_field: 0, skip: 0 };
    for (const r of rows) s[r.decision.action] = (s[r.decision.action] || 0) + 1;
    return s;
  }, [rows]);

  const submit = async () => {
    if (rows.length === 0) return;
    setCommitting(true);
    try {
      const items = rows.map((r) => r.decision);
      const r = await ImportApi.commit(items);
      const msg = `已新建 ${r.created} 项 / 追加 ${r.appended} 项 / 跳过 ${r.skipped} 项`;
      if (r.errors.length > 0) {
        toast.error(`${msg}；但有 ${r.errors.length} 处错误：${r.errors[0]}`);
      } else {
        toast.success(msg);
      }
      // 提交完成清空
      clearFiles();
    } catch (e: any) {
      toast.error(`提交失败：${e?.message || e}`);
    } finally {
      setCommitting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="OCR 批量导入"
        subtitle="上传持仓页截图，AI 自动识别并匹配到现有资产，确认后入库"
      />

      {/* ====== 上传区 ====== */}
      <div className="card p-5 space-y-4">
        <div className="grid sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2">
            <label className="label">上传截图（支持多选 / 拖拽）</label>
            <label
              className="flex flex-col items-center justify-center gap-2 h-28 rounded-xl border-2 border-dashed border-line/60 bg-bg-soft/30 cursor-pointer hover:border-accent/40 transition"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); onPickFiles(e.dataTransfer.files); }}
            >
              <Upload className="w-6 h-6 text-muted" />
              <div className="text-xs text-muted">
                点击选择，或拖拽图片到这里（jpg / png / webp，单次可多张）
              </div>
              <input type="file" multiple accept="image/*" className="hidden"
                     onChange={(e) => { onPickFiles(e.target.files); e.target.value = ""; }} />
            </label>
          </div>
          <div>
            <label className="label">平台提示（可不选）</label>
            <select className="input" value={platformHint}
                    onChange={(e) => setPlatformHint(e.target.value)}>
              {PLATFORM_HINTS.map((p) => (
                <option key={p} value={p}>{p || "（不指定，让 AI 自己判断）"}</option>
              ))}
            </select>
            <div className="text-[10px] text-muted mt-1">指定平台能让 AI 更准确，尤其是不同平台对"朝朝宝/余额宝"等命名差异较大时</div>
          </div>
        </div>

        {/* 缩略图列表 */}
        {files.length > 0 && (
          <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-7 gap-2">
            {files.map((f) => (
              <div key={f.id} className="relative group">
                <img src={f.preview} alt={f.file.name}
                     className="w-full h-24 object-cover rounded-lg border border-line/60" />
                <button
                  className="absolute top-1 right-1 w-6 h-6 rounded-full bg-bg/80 border border-line/60 flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                  onClick={() => removeFile(f.id)}
                  title="移除"
                >
                  <X className="w-3 h-3 text-rose2" />
                </button>
                <div className="text-[10px] text-muted mt-1 truncate">{f.file.name}</div>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            className="btn-primary"
            disabled={parsing || files.length === 0}
            onClick={startParse}
          >
            {parsing ? <><Loader2 className="w-4 h-4 animate-spin" /> 识别中…</> : <><ImageIcon className="w-4 h-4" /> 开始识别 ({files.length} 张)</>}
          </button>
          {files.length > 0 && (
            <button className="btn" onClick={clearFiles} disabled={parsing || committing}>
              清空
            </button>
          )}
          {parseResults.length > 0 && (
            <span className="text-xs text-muted ml-auto">
              已解析：<span className="text-white">{rows.length} 项</span>
              {Object.entries(stats).filter(([, v]) => v > 0).map(([k, v]) => (
                <span key={k} className="ml-2">
                  · {actionLabel(k)} <span className="text-white">{v}</span>
                </span>
              ))}
            </span>
          )}
        </div>

        {/* 平台未识别提示 */}
        {parseResults.some((r) => r.error) && (
          <div className="rounded-lg border border-rose2/40 bg-rose2/5 p-3 text-xs text-rose2">
            {parseResults.filter((r) => r.error).map((r, i) => (
              <div key={i}>· {r.file}: {r.error}</div>
            ))}
          </div>
        )}
      </div>

      {/* ====== 对账确认表 ====== */}
      {rows.length > 0 && (
        <div className="card mt-5 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 border-b border-line/60 bg-bg-soft/30">
            <h2 className="font-semibold">确认清单（共 {rows.length} 项）</h2>
            <button
              className="btn-primary"
              disabled={committing || rows.length === 0}
              onClick={submit}
            >
              {committing ? <><Loader2 className="w-4 h-4 animate-spin" /> 提交中…</> : <><CheckCircle2 className="w-4 h-4" /> 确认导入</>}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-bg-soft/30 text-xs text-muted">
                <tr>
                  <th className="text-left px-3 py-2 font-normal">资产</th>
                  <th className="text-left px-3 py-2 font-normal">类型</th>
                  <th className="text-left px-3 py-2 font-normal">绑定</th>
                  <th className="text-right px-3 py-2 font-normal">OCR 数据</th>
                  <th className="text-left px-3 py-2 font-normal">动作</th>
                  <th className="text-left px-3 py-2 font-normal">建议</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => (
                  <RowEditor
                    key={`${r.fileIdx}-${r.itemIdx}`}
                    row={r}
                    onUpdate={(patch) => updateRow(idx, patch)}
                    onBind={(id) => updateBind(idx, id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 视觉模型未配置提醒 */}
      {parseResults.length === 0 && !parsing && files.length === 0 && (
        <div className="card mt-5 p-5 text-sm text-muted">
          <div className="flex items-center gap-2 mb-2">
            <AlertCircle className="w-4 h-4 text-amber2" />
            <span className="text-white">使用前请先配置视觉模型</span>
          </div>
          <ol className="list-decimal pl-6 space-y-1 text-xs">
            <li>到「设置 → 视觉模型」填入 base_url / model / api_key（推荐：阿里 qwen-vl-max 或智谱 GLM-4V）</li>
            <li>把支付宝、微信理财通、银行 App、券商 App 等平台的"持仓页"截图保存下来</li>
            <li>回到本页，多张截图一次性上传，AI 会自动识别并对账</li>
          </ol>
        </div>
      )}
    </>
  );
}

// ============== 子组件：单行编辑器 ==============

function RowEditor({ row, onUpdate, onBind }: {
  row: RowState;
  onUpdate: (patch: Partial<RowState["decision"]>) => void;
  onBind: (assetId: number | null) => void;
}) {
  const it = row.origin;
  const cands = it._candidates || [];
  const sug = it._suggestion;
  const meta = metaOf(row.decision.asset_type);

  return (
    <tr className="border-t border-line/40 hover:bg-bg-soft/20">
      {/* 资产名 + 代码 */}
      <td className="px-3 py-2 align-top">
        <input
          className="input text-xs h-8 w-44"
          value={row.decision.name || ""}
          onChange={(e) => onUpdate({ name: e.target.value })}
          placeholder="名称"
        />
        <input
          className="input text-xs h-7 w-44 mt-1"
          value={row.decision.code || ""}
          onChange={(e) => onUpdate({ code: e.target.value })}
          placeholder="代码"
        />
      </td>

      {/* 类型 */}
      <td className="px-3 py-2 align-top">
        <select
          className="input text-xs h-8 w-28"
          value={row.decision.asset_type}
          onChange={(e) => onUpdate({
            asset_type: e.target.value as AssetType,
            market: defaultMarketFor(e.target.value as AssetType),
          })}
        >
          {(Object.keys(ASSET_TYPE_META) as AssetType[]).map((t) => (
            <option key={t} value={t}>{ASSET_TYPE_META[t].label}</option>
          ))}
        </select>
      </td>

      {/* 绑定的现有资产 */}
      <td className="px-3 py-2 align-top">
        <select
          className="input text-xs h-8 w-44"
          value={row.bindAssetId == null ? "" : String(row.bindAssetId)}
          onChange={(e) => onBind(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">＋ 新建资产</option>
          {cands.map((c) => (
            <option key={c.asset_id} value={c.asset_id}>
              {c.name}（{c.platform || "-"}） · {(c.match_score * 100).toFixed(0)}%
            </option>
          ))}
        </select>
      </td>

      {/* OCR 数据预览 */}
      <td className="px-3 py-2 align-top text-xs text-right font-mono whitespace-nowrap">
        {meta.hasShares ? (
          <>
            <div>份额 <span className="text-white">{fmtNum(it.shares)}</span></div>
            <div>成本 <span className="text-white">{fmtNum(it.avg_cost)}</span></div>
            <div>市值 <span className="text-white">{it.market_value != null ? fmtMoney(it.market_value) : "-"}</span></div>
          </>
        ) : (
          <>
            <div>金额 <span className="text-white">{it.amount != null ? fmtMoney(it.amount) : "-"}</span></div>
            {meta.needsYield7d && <div>7日年化 <span className="text-white">{fmtNum(it.yield_7d)}%</span></div>}
            {meta.needsExpectedApr && <div>预期年化 <span className="text-white">{fmtNum(it.expected_apr)}%</span></div>}
          </>
        )}
        {it.profit != null && (
          <div className={it.profit >= 0 ? "text-emerald2" : "text-rose2"}>
            收益 {it.profit >= 0 ? "+" : ""}{fmtMoney(it.profit)}
            {it.profit_pct != null && <span className="ml-1">({fmtNum(it.profit_pct)}%)</span>}
          </div>
        )}
      </td>

      {/* 动作 */}
      <td className="px-3 py-2 align-top">
        <select
          className="input text-xs h-8 w-28"
          value={row.decision.action}
          onChange={(e) => onUpdate({ action: e.target.value as RowState["decision"]["action"] })}
        >
          {row.bindAssetId == null && <option value="create">新建</option>}
          {row.bindAssetId != null && meta.hasShares && <option value="append_buy">追加买入</option>}
          {row.bindAssetId != null && meta.hasShares && <option value="append_sell">减仓</option>}
          {row.bindAssetId != null && !meta.hasShares && <option value="update_field">更新本金</option>}
          <option value="skip">跳过</option>
        </select>
        {row.decision.action === "append_buy" || row.decision.action === "append_sell" ? (
          <input
            className="input text-xs h-7 w-28 mt-1 font-mono"
            type="number" step="0.0001"
            value={row.decision.delta_shares ?? ""}
            onChange={(e) => onUpdate({ delta_shares: e.target.valueAsNumber || undefined })}
            placeholder="差额份额"
          />
        ) : row.decision.action === "update_field" ? (
          <input
            className="input text-xs h-7 w-28 mt-1 font-mono"
            type="number" step="0.01"
            value={row.decision.principal_amount ?? ""}
            onChange={(e) => onUpdate({ principal_amount: e.target.valueAsNumber || undefined })}
            placeholder="新本金"
          />
        ) : null}
      </td>

      {/* AI 建议提示 */}
      <td className="px-3 py-2 align-top text-xs text-muted max-w-[220px]">
        {sug?.reason || "—"}
      </td>
    </tr>
  );
}

// ============== 工具 ==============

function defaultMarketFor(t: AssetType): Market {
  return ASSET_TYPE_META[t].defaultMarket;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return "-";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function actionLabel(action: string): string {
  switch (action) {
    case "create": return "新建";
    case "append_buy": return "追加";
    case "append_sell": return "减仓";
    case "update_field": return "更新";
    case "skip": return "跳过";
    default: return action;
  }
}
