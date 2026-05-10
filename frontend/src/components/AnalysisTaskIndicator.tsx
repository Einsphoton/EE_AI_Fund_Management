import { useLocation, useNavigate } from "react-router-dom";
import { BrainCircuit, Check, X } from "lucide-react";

import { useAnalysisTask } from "../lib/analysisTask";

/**
 * 全局悬浮指示器：
 * - 分析进行中时在右下角出现一个胶囊
 * - 显示进度 X / Y + 百分比
 * - 点击跳转到 AI 分析页查看详情
 * - 不在 /advice 页显示（那里已经有嵌入面板了）
 * - 分析完成后 6 秒自动消失（允许手动 dismiss）
 */
export default function AnalysisTaskIndicator() {
  const nav = useNavigate();
  const loc = useLocation();
  const task = useAnalysisTask();

  // /advice 页面有完整嵌入面板，不需要再重复显示
  if (loc.pathname === "/advice") return null;

  // 从未启动过、或主动 reset 回到初始态：不显示
  if (!task.started) return null;

  const done = !task.running && task.finishedAt !== null;
  // 完成后短暂保留 15s 用户若切过来能看到；不主动做定时移除（让 reset 掌握）
  const sinceDone = done ? (Date.now() - (task.finishedAt || 0)) / 1000 : 0;
  if (done && sinceDone > 15) return null;

  const p = task.progress;

  return (
    <div
      className="fixed bottom-5 right-5 z-40 animate-in fade-in slide-in-from-bottom-4"
      style={{ minWidth: 240, maxWidth: 320 }}
    >
      <button
        className={`card px-4 py-3 shadow-glow w-full text-left flex items-center gap-3 hover:border-accent/60 transition ${
          done ? "border-emerald2/40" : task.running ? "border-accent/50" : "border-amber2/40"
        }`}
        onClick={() => nav("/advice")}
        title="点击查看分析详情"
      >
        <div
          className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${
            done
              ? "bg-emerald2/20"
              : "bg-gradient-to-br from-accent to-emerald2 shadow-glow"
          }`}
        >
          {done ? (
            <Check className="w-4 h-4 text-emerald2" />
          ) : (
            <BrainCircuit className={`w-4 h-4 text-white ${task.running ? "animate-pulse" : ""}`} />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">
            {done
              ? "分析已完成"
              : task.running
                ? "Hermes-Lite 分析中…"
                : "分析已中止"}
          </div>
          <div className="text-[11px] text-muted truncate">
            {p.total > 0 ? (
              <>
                {p.current}/{p.total}
                {p.failed > 0 && <span className="text-rose2 ml-1">· 失败 {p.failed}</span>}
                <span className="ml-1.5 font-mono">{task.percent}%</span>
              </>
            ) : (
              "正在初始化…"
            )}
          </div>
          {task.running && (
            <div className="h-1 rounded-full bg-bg-soft overflow-hidden border border-line/40 mt-1.5">
              <div
                className="h-full bg-gradient-to-r from-accent to-emerald2 transition-all"
                style={{ width: `${task.percent}%` }}
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
              task.reset();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                task.reset();
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
