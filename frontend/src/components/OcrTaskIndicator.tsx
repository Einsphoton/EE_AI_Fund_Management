import { useLocation, useNavigate } from "react-router-dom";
import { Camera, Check, X, Loader2 } from "lucide-react";

import { useOcrTask } from "../lib/ocrTask";

/**
 * 全局悬浮指示器：
 * - OCR 任务进行中时在右下角（错开 AnalysisTaskIndicator）显示进度
 * - 任何页面都能看到，点击跳回 /import 继续操作
 * - /import 页本身已有大卡片，不重复显示
 */
export default function OcrTaskIndicator() {
  const nav = useNavigate();
  const loc = useLocation();
  const ocr = useOcrTask();

  if (loc.pathname === "/import") return null;
  if (!ocr.started) return null;

  const done = !ocr.running && ocr.finishedAt !== null;
  const sinceDone = done ? (Date.now() - (ocr.finishedAt || 0)) / 1000 : 0;
  // 已完成且未提交时一直保留（让用户能去 /import 确认）；纯失败 15s 后自动消失
  if (done && !ocr.hasResults && sinceDone > 15) return null;

  return (
    <div
      // 上移一点，错开 AnalysisTaskIndicator
      className="fixed bottom-24 right-5 z-40 animate-in fade-in slide-in-from-bottom-4"
      style={{ minWidth: 240, maxWidth: 320 }}
    >
      <button
        className={`card px-4 py-3 shadow-glow w-full text-left flex items-center gap-3 hover:border-accent/60 transition ${
          done ? "border-emerald2/40" : ocr.running ? "border-accent/50" : "border-amber2/40"
        }`}
        onClick={() => nav("/import")}
        title={done ? "点击去确认导入清单" : "点击查看识别详情"}
      >
        <div
          className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${
            done && ocr.hasResults
              ? "bg-emerald2/20"
              : "bg-gradient-to-br from-accent to-emerald2 shadow-glow"
          }`}
        >
          {done ? (
            <Check className="w-4 h-4 text-emerald2" />
          ) : ocr.running ? (
            <Loader2 className="w-4 h-4 text-white animate-spin" />
          ) : (
            <Camera className="w-4 h-4 text-white" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">
            {done && ocr.hasResults
              ? `识别完成，待确认 ${ocr.results.reduce((s, r) => s + r.items.length, 0)} 项`
              : ocr.running
                ? "OCR 识别中…"
                : ocr.error
                  ? "OCR 任务出错"
                  : "OCR 任务"}
          </div>
          <div className="text-[11px] text-muted truncate">
            {ocr.progress.total > 0 ? (
              <>
                {ocr.progress.finished}/{ocr.progress.total}
                <span className="ml-1.5 font-mono">{ocr.percent}%</span>
              </>
            ) : (
              "正在启动…"
            )}
          </div>
          {ocr.running && (
            <div className="h-1 rounded-full bg-bg-soft overflow-hidden border border-line/40 mt-1.5">
              <div
                className="h-full bg-gradient-to-r from-accent to-emerald2 transition-all"
                style={{ width: `${ocr.percent}%` }}
              />
            </div>
          )}
        </div>

        {done && (
          <div
            role="button"
            tabIndex={0}
            className="text-muted hover:text-white p-1 rounded hover:bg-line/40 shrink-0"
            onClick={(e) => {
              e.stopPropagation();
              ocr.reset();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                ocr.reset();
              }
            }}
            title="关闭提示"
          >
            <X className="w-3.5 h-3.5" />
          </div>
        )}
      </button>
    </div>
  );
}
