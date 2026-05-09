/**
 * OCR 批量导入页：
 *   1) 拖拽 / 选择多张持仓页截图
 *   2) 选择平台提示（可不选）
 *   3) 上传 → 后台异步跑视觉模型，前端 SSE 实时滚动"AI 思考过程" + 进度条
 *   4) 切走再回来不会丢任务（OcrTaskProvider 跨路由保活）
 *   5) 解析完后表格里逐行编辑、确认入库
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Upload, Image as ImageIcon, X, CheckCircle2, AlertCircle, Loader2,
  Brain, Eye, EyeOff, RotateCcw, Square, FileJson, Info,
  ChevronDown, ChevronRight,
} from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import {
  OcrItem, OcrCommitItem, AssetType, Market, Assets,
} from "../api/client";
import { ASSET_TYPE_META, metaOf } from "../lib/assetMeta";
import { fmtMoney } from "../lib/format";
import { useOcrTask, OcrThought } from "../lib/ocrTask";

interface UploadFile {
  file: File;
  preview: string;
  id: string;
}

interface RowState {
  fileIdx: number;
  itemIdx: number;
  origin: OcrItem;
  decision: OcrCommitItem;
  bindAssetId: number | null;
}

const PLATFORM_HINTS = [
  "", "微信理财通", "支付宝财富", "招商银行", "中国银行", "平安银行",
  "工商银行", "富途", "招商证券", "中银国际", "雪盈证券",
];

export default function ImportOcr() {
  const ocr = useOcrTask();

  // 上传文件本地态（这部分不需要跨路由保活；提交完会清空）
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [platformHint, setPlatformHint] = useState("");

  // 行编辑态：从 ocr.results 派生 → 用 useState 缓存以便用户编辑
  const [rows, setRows] = useState<RowState[]>([]);

  // 当 ocr.results 更新时（解析完成 / 重新挂回任务）→ 重建行
  useEffect(() => {
    if (ocr.results.length === 0) {
      setRows([]);
      return;
    }
    const newRows: RowState[] = [];
    ocr.results.forEach((res, fileIdx) => {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ocr.results]);

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
  };

  // ----- 启动解析 -----
  const startParse = async () => {
    if (files.length === 0) {
      toast.error("请先选择至少一张截图");
      return;
    }
    await ocr.startParse(files.map((f) => f.file), platformHint);
  };

  // ----- 通过 Skill JSON 文件导入（不走视觉模型） -----
  const onPickJsonFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const arr = Array.from(list).filter((f) =>
      f.name.toLowerCase().endsWith(".json") || f.type === "application/json"
    );
    if (arr.length === 0) {
      toast.error("请选择 .json 文件（portfolio-ocr Skill 产物）");
      return;
    }
    await ocr.loadFromJson(arr, platformHint);
  };


  // ----- 行编辑 -----
  const updateRow = (idx: number, patch: Partial<RowState["decision"]>) => {
    setRows((prev) => prev.map((r, i) =>
      i === idx ? { ...r, decision: { ...r.decision, ...patch } } : r));
  };

  const updateBind = (idx: number, candId: number | null) => {
    setRows((prev) => prev.map((r, i) => {
      if (i !== idx) return r;
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
    try {
      await ocr.commit(rows.map((r) => r.decision));
      clearFiles(); // 提交成功 → 顺手把上传的图片缩略图也清掉
    } catch {
      // toast 已在 hook 里出
    }
  };

  return (
    <>
      <PageHeader
        title="OCR 批量导入"
        subtitle="上传持仓页截图让内置 AI 自动识别；或导入由 portfolio-ocr Skill 在其他 ChatBot 里生成的 JSON 文件"
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
            disabled={ocr.running || files.length === 0}
            onClick={startParse}
          >
            {ocr.running
              ? <><Loader2 className="w-4 h-4 animate-spin" /> 识别中…（{ocr.progress.finished}/{ocr.progress.total}）</>
              : <><ImageIcon className="w-4 h-4" /> 开始识别 ({files.length} 张)</>}
          </button>
          {ocr.running && (
            <button
              className="btn !text-rose2 hover:!border-rose2/60"
              onClick={ocr.cancel}
              title="停止识别。已识别完的图片仍会保留供你确认导入"
            >
              <Square className="w-4 h-4 fill-current" /> 停止识别
            </button>
          )}
          {files.length > 0 && (
            <button className="btn" onClick={clearFiles} disabled={ocr.running || ocr.committing}>
              清空文件
            </button>
          )}

          {/* 通过 Skill JSON 文件导入：分隔线 + 入口 */}
          <span className="text-muted text-xs">|</span>
          <label
            className={`btn ${ocr.running || ocr.committing ? "opacity-50 pointer-events-none" : ""}`}
            title="不走视觉模型；上传 portfolio-ocr Skill 在网页 ChatBot 里跑出来的 JSON 文件，直接进入对账确认"
          >
            <FileJson className="w-4 h-4" />
            导入 JSON 文件
            <input
              type="file" multiple accept="application/json,.json" className="hidden"
              onChange={(e) => { onPickJsonFiles(e.target.files); e.target.value = ""; }}
            />
          </label>

          {ocr.started && (
            <button className="btn" onClick={ocr.reset} disabled={ocr.running || ocr.committing}
                    title="清掉当前任务，重新开始">
              <RotateCcw className="w-4 h-4" /> 重置任务
            </button>
          )}
          {rows.length > 0 && (
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
        {ocr.results.some((r) => r.error) && (
          <div className="rounded-lg border border-rose2/40 bg-rose2/5 p-3 text-xs text-rose2">
            {ocr.results.filter((r) => r.error).map((r, i) => (
              <div key={i}>· {r.file}: {r.error}</div>
            ))}
          </div>
        )}
      </div>

      {/* ====== AI 思考过程 + 进度 ====== */}
      {ocr.started && (
        <ThoughtCard
          running={ocr.running}
          percent={ocr.percent}
          finished={ocr.progress.finished}
          total={ocr.progress.total}
          thoughts={ocr.thoughts}
          jobId={ocr.jobId}
        />
      )}

      {/* ====== 对账确认表 ====== */}
      {rows.length > 0 && (
        <div className="card mt-5 overflow-hidden">
          {/* 平台候选 datalist：每行编辑器里的平台 input 共用 */}
          <datalist id="ocr-platform-suggestions">
            {PLATFORM_HINTS.filter(Boolean).map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <div className="flex items-center justify-between px-5 py-3 border-b border-line/60 bg-bg-soft/30">
            <h2 className="font-semibold">确认清单（共 {rows.length} 项）</h2>
            <button
              className="btn-primary"
              disabled={ocr.committing || rows.length === 0}
              onClick={submit}
            >
              {ocr.committing ? <><Loader2 className="w-4 h-4 animate-spin" /> 提交中…</> : <><CheckCircle2 className="w-4 h-4" /> 确认导入</>}
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
      {!ocr.started && files.length === 0 && (
        <div className="card mt-5 p-5 text-sm text-muted space-y-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle className="w-4 h-4 text-amber2" />
              <span className="text-white">方式 A：App 内置 OCR（推荐日常使用）</span>
            </div>
            <ol className="list-decimal pl-6 space-y-1 text-xs">
              <li>到「设置 → 视觉模型」填入 base_url / model / api_key（推荐：阿里 qwen-vl-max 或智谱 GLM-4V）</li>
              <li>把支付宝、微信理财通、银行 App、券商 App 等平台的"持仓页"截图保存下来</li>
              <li>回到本页，多张截图一次性上传，AI 会自动识别并对账</li>
              <li>识别过程中可以放心切到其他页面，回来不会丢进度</li>
            </ol>
          </div>

          <div className="border-t border-line/40 pt-4">
            <div className="flex items-center gap-2 mb-2">
              <Info className="w-4 h-4 text-accent" />
              <span className="text-white">方式 B：用 Skill 在任意 ChatBot 里手动跑，再导入 JSON</span>
            </div>
            <ol className="list-decimal pl-6 space-y-1 text-xs">
              <li>打开仓库内 <code className="text-accent">skills/portfolio-ocr/SKILL.md</code>，复制里面的 System Prompt</li>
              <li>贴到任意多模态 ChatBot（Kimi 网页版 / 通义千问 / ChatGPT-4V / Claude / 本地 Qwen-VL…），上传持仓截图</li>
              <li>保存模型返回的 JSON 为 <code className="text-accent">.json</code> 文件</li>
              <li>回到本页，点击右上角 <span className="text-white">「导入 JSON 文件」</span> 按钮选中 JSON，进入对账流程</li>
            </ol>
            <div className="text-[11px] text-muted mt-2">
              适合：视觉模型限流严重 / 想用 App 没接入的模型做 OCR / 隐私隔离场景。
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ============== 子组件：AI 思考过程 + 进度 ==============

function ThoughtCard({ running, percent, finished, total, thoughts, jobId }: {
  running: boolean;
  percent: number;
  finished: number;
  total: number;
  thoughts: OcrThought[];
  jobId: string | null;
}) {
  const [expanded, setExpanded] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickyBottomRef = useRef(true);

  // 自动滚到底（除非用户主动往上滚）
  useEffect(() => {
    if (!stickyBottomRef.current || !scrollRef.current) return;
    const el = scrollRef.current;
    el.scrollTop = el.scrollHeight;
  }, [thoughts.length]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickyBottomRef.current = distance < 24;
  };

  return (
    <div className="card mt-5 overflow-hidden">
      {/* 标题 + 进度条 */}
      <div className="px-5 py-3 border-b border-line/60 bg-bg-soft/30">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Brain className={`w-5 h-5 ${running ? "text-accent animate-pulse" : "text-emerald2"}`} />
          </div>
          <h2 className="font-semibold text-sm">
            AI 识别过程
            {running && <span className="ml-2 text-xs text-muted">（处理中，可切换页面，不会中断）</span>}
            {!running && total > 0 && <span className="ml-2 text-xs text-emerald2">完成</span>}
          </h2>
          <div className="ml-auto flex items-center gap-3 text-xs text-muted">
            {jobId && <span className="font-mono opacity-60">job {jobId}</span>}
            <span>{finished}/{total}</span>
            <button
              className="btn !h-7 !px-2 !text-xs"
              onClick={() => setExpanded((x) => !x)}
              title={expanded ? "收起" : "展开"}
            >
              {expanded ? <><EyeOff className="w-3 h-3" /> 收起</> : <><Eye className="w-3 h-3" /> 展开</>}
            </button>
          </div>
        </div>

        {/* 进度条 */}
        <div className="mt-2 h-1.5 rounded-full bg-line/40 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${running ? "bg-gradient-to-r from-accent to-emerald2" : "bg-emerald2"}`}
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      {/* 思考流 */}
      {expanded && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="max-h-[280px] overflow-y-auto px-5 py-3 text-xs font-mono space-y-1 bg-[rgba(0,0,0,0.15)]"
        >
          {thoughts.length === 0 ? (
            <div className="text-muted italic">等待视觉模型响应…</div>
          ) : (
            thoughts.map((t, i) => <ThoughtLine key={i} t={t} />)
          )}
          {running && (
            <div className="text-muted italic flex items-center gap-2 pt-1">
              <Loader2 className="w-3 h-3 animate-spin" />
              处理中…
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ThoughtLine({ t }: { t: OcrThought }) {
  const time = new Date(t.ts).toLocaleTimeString("zh-CN", { hour12: false });
  let cls = "text-fg/80";
  if (t.kind === "image_done") cls = "text-emerald2";
  else if (t.kind === "image_error" || t.kind === "fatal") cls = "text-rose2";
  else if (t.kind === "image_start") cls = "text-accent";
  else if (t.kind === "start" || t.kind === "done") cls = "text-amber2 font-semibold";
  else if (t.kind === "thought") cls = "text-muted";

  return (
    <div className={`leading-relaxed ${cls} whitespace-pre-wrap break-words`}>
      <span className="opacity-50 mr-2">{time}</span>{t.text}
    </div>
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
  const [lookingUp, setLookingUp] = useState(false);
  const [expanded, setExpanded] = useState(false);

  // 是否需要"查代码"提示：行情类（fund/etf/stock）且 code 为空
  const codeMissing = !row.decision.code &&
    ["fund", "etf", "stock"].includes(row.decision.asset_type);

  const lookupCode = async () => {
    const name = (row.decision.name || "").trim();
    if (!name) {
      toast.error("请先填写名称");
      return;
    }
    setLookingUp(true);
    try {
      const r = await Assets.lookupCode(name, row.decision.asset_type);
      if (r.ok && r.suggestion?.code) {
        const s = r.suggestion;
        onUpdate({ code: s.code });
        // 如果模型查回来的官方全名跟用户编辑的名字不太一样且置信度高，提示用户考虑替换
        if (s.matched_name && s.matched_name !== name && s.score >= 0.9) {
          toast.success(
            `代码：${s.code}（来自 ${s.source === "eastmoney" ? "天天基金" : "AI"}，匹配 "${s.matched_name}"）`,
            { duration: 5000 },
          );
        } else {
          toast.success(
            `代码：${s.code}（${s.source === "eastmoney" ? "天天基金" : "AI"} · ${(s.score * 100).toFixed(0)}%）`,
          );
        }
      } else {
        toast.error("没找到匹配的代码，请手动填写");
      }
    } catch (e: any) {
      toast.error(`查询失败：${e?.message || e}`);
    } finally {
      setLookingUp(false);
    }
  };

  return (
    <>
    <tr className="border-t border-line/40 hover:bg-bg-soft/20">
      {/* 资产名 + 代码 + 展开按钮 */}
      <td className="px-3 py-2 align-top">
        <div className="flex items-start gap-1">
          <button
            type="button"
            className="mt-1 text-muted hover:text-white shrink-0"
            onClick={() => setExpanded((x) => !x)}
            title={expanded ? "收起详情" : "展开：编辑平台 / 备注 / 起始日 / 到期日 等"}
          >
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
          <div className="flex-1 min-w-0">
            <input
              className="input text-xs h-8 w-44"
              value={row.decision.name || ""}
              onChange={(e) => onUpdate({ name: e.target.value })}
              placeholder="名称"
            />
            <div className="flex items-center gap-1 mt-1">
              <input
                className={`input text-xs h-7 w-32 ${codeMissing ? "border-amber2/60" : ""}`}
                value={row.decision.code || ""}
                onChange={(e) => onUpdate({ code: e.target.value })}
                placeholder={codeMissing ? "未识别" : "代码"}
              />
              {(["fund", "etf", "stock"].includes(row.decision.asset_type)) && (
                <button
                  type="button"
                  className="btn !h-7 !px-1.5 !text-[10px]"
                  onClick={lookupCode}
                  disabled={lookingUp || !row.decision.name}
                  title="多源并行查代码：天天基金 + 腾讯证券 + 新浪 + 雪球（支持 A股/港股/美股/基金/ETF）"
                >
                  {lookingUp ? <Loader2 className="w-3 h-3 animate-spin" /> : <>🔍 查码</>}
                </button>
              )}
            </div>
            {/* 平台直接显示在这里：高频字段不藏在展开里 */}
            <input
              className="input text-xs h-7 w-44 mt-1"
              value={row.decision.platform || ""}
              onChange={(e) => onUpdate({ platform: e.target.value })}
              placeholder="平台（如：微信理财通）"
              list="ocr-platform-suggestions"
            />
          </div>
        </div>
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
    {/* 展开行：更多字段编辑（平台/备注/起始日/到期日/购买日期/初始份额成本/特殊字段） */}
    {expanded && (
      <tr className="border-t border-line/40 bg-bg-soft/20">
        <td colSpan={6} className="px-6 py-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <label className="block text-muted mb-1">购买日期 / 截图日期</label>
              <input
                className="input text-xs h-8 w-full"
                type="date"
                value={asDateInput(row.decision.snapshot_date)}
                onChange={(e) => onUpdate({ snapshot_date: e.target.value || undefined })}
              />
              <div className="text-[10px] text-muted mt-0.5">追加交易/初始买入会用这个日期</div>
            </div>

            {/* 行情类才显示初始份额/成本（用于"新建"动作） */}
            {row.decision.action === "create" && meta.hasShares && (
              <>
                <div>
                  <label className="block text-muted mb-1">初始份额</label>
                  <input
                    className="input text-xs h-8 w-full font-mono"
                    type="number" step="0.0001"
                    value={row.decision.shares ?? ""}
                    onChange={(e) => onUpdate({ shares: e.target.valueAsNumber || undefined })}
                    placeholder="如 1234.5678"
                  />
                </div>
                <div>
                  <label className="block text-muted mb-1">平均成本</label>
                  <input
                    className="input text-xs h-8 w-full font-mono"
                    type="number" step="0.0001"
                    value={row.decision.avg_cost ?? ""}
                    onChange={(e) => onUpdate({ avg_cost: e.target.valueAsNumber || undefined })}
                    placeholder="单位净值/单价"
                  />
                </div>
              </>
            )}

            {/* 货基/理财/现金/债券：本金 + 收益相关 */}
            {!meta.hasShares && (
              <div>
                <label className="block text-muted mb-1">本金（元）</label>
                <input
                  className="input text-xs h-8 w-full font-mono"
                  type="number" step="0.01"
                  value={row.decision.principal_amount ?? ""}
                  onChange={(e) => onUpdate({ principal_amount: e.target.valueAsNumber || undefined })}
                />
              </div>
            )}

            {meta.needsYield7d && (
              <div>
                <label className="block text-muted mb-1">7 日年化（%）</label>
                <input
                  className="input text-xs h-8 w-full font-mono"
                  type="number" step="0.001"
                  value={row.decision.yield_7d ?? ""}
                  onChange={(e) => onUpdate({ yield_7d: e.target.valueAsNumber || undefined })}
                />
              </div>
            )}

            {meta.needsExpectedApr && (
              <div>
                <label className="block text-muted mb-1">预期年化（%）</label>
                <input
                  className="input text-xs h-8 w-full font-mono"
                  type="number" step="0.001"
                  value={row.decision.expected_apr ?? ""}
                  onChange={(e) => onUpdate({ expected_apr: e.target.valueAsNumber || undefined })}
                />
              </div>
            )}

            {/* 理财 / 债券：起始 / 到期日 */}
            {(row.decision.asset_type === "wealth" ||
              row.decision.asset_type === "bond" ||
              row.decision.asset_type === "money_fund") && (
              <>
                <div>
                  <label className="block text-muted mb-1">起始日</label>
                  <input
                    className="input text-xs h-8 w-full"
                    type="date"
                    value={asDateInput(row.decision.start_date)}
                    onChange={(e) => onUpdate({ start_date: e.target.value || undefined })}
                  />
                </div>
                <div>
                  <label className="block text-muted mb-1">到期日</label>
                  <input
                    className="input text-xs h-8 w-full"
                    type="date"
                    value={asDateInput(row.decision.maturity_date)}
                    onChange={(e) => onUpdate({ maturity_date: e.target.value || undefined })}
                  />
                </div>
              </>
            )}

            {/* 备注：占满整行 */}
            <div className="col-span-2 md:col-span-4">
              <label className="block text-muted mb-1">备注</label>
              <input
                className="input text-xs h-8 w-full"
                value={row.decision.note || ""}
                onChange={(e) => onUpdate({ note: e.target.value })}
                placeholder="如：朝朝宝活期 / Q3 到期 / 非保本浮动收益"
              />
            </div>
          </div>
        </td>
      </tr>
    )}
    </>
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

/**
 * 把可能是 string / Date / undefined 的值，归一成 <input type="date"> 能吃的 "YYYY-MM-DD"。
 * 保险起见容忍 ISO datetime，截前 10 位即可。
 */
function asDateInput(v: any): string {
  if (!v) return "";
  if (typeof v !== "string") return "";
  // "2026-05-08T..." or "2026-05-08"
  return v.length >= 10 ? v.slice(0, 10) : v;
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
