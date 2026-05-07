import { createContext, useCallback, useContext, useRef, useState, ReactNode } from "react";
import toast from "react-hot-toast";
import { useQueryClient } from "@tanstack/react-query";

import { runAllStream, RunAllEvent } from "../api/client";
import { actionLabel } from "../lib/format";

// =====================================================================
//  类型
// =====================================================================

export interface AnalysisLog {
  kind: "info" | "asset_start" | "asset_done" | "asset_error" | "done" | "log";
  text: string;
  ts: number;
  action?: "buy" | "hold" | "sell";
  confidence?: number;
  summary?: string;
  /** 并发模式下用于给日志打上所属标的的标签（可选） */
  assetId?: number;
  assetName?: string;
}

/** 正在运行的单个标的（并发模式下可能有多个） */
export interface RunningAsset {
  assetId: number;
  name: string;
  code: string;
  index: number;
  startedAt: number;
}

export interface AnalysisTaskState {
  /** 是否曾经启动过（用于决定 UI 显示"开始"按钮还是任务详情） */
  started: boolean;
  /** 是否正在运行（控制 loading 态） */
  running: boolean;
  /** 完成时间戳；未完成为 null */
  finishedAt: number | null;
  /** 进度：current 以"完成数"计（成功 + 失败） */
  progress: { current: number; total: number; failed: number };
  /** 日志滚动 */
  logs: AnalysisLog[];
  /** 批次 ID（服务端下发后填） */
  batchId: string | null;
  /** 启动时刻 */
  startedAt: number | null;
  /** 服务端使用的并发度（从 start 事件拿到；没拿到则 1） */
  concurrency: number;
  /** 当前正在运行的标的集合（assetId -> info） */
  runningAssets: Record<number, RunningAsset>;
}

interface AnalysisTaskContextValue extends AnalysisTaskState {
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
  percent: number;
  /** 取当前在跑的标的列表（按 index 升序稳定排序） */
  runningList: RunningAsset[];
}

const defaultState: AnalysisTaskState = {
  started: false,
  running: false,
  finishedAt: null,
  progress: { current: 0, total: 0, failed: 0 },
  logs: [],
  batchId: null,
  startedAt: null,
  concurrency: 1,
  runningAssets: {},
};

const Ctx = createContext<AnalysisTaskContextValue | null>(null);

export function useAnalysisTask(): AnalysisTaskContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAnalysisTask must be used inside AnalysisTaskProvider");
  return v;
}

// =====================================================================
//  Provider
// =====================================================================

export function AnalysisTaskProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [state, setState] = useState<AnalysisTaskState>(defaultState);
  const abortRef = useRef<AbortController | null>(null);

  // 设置 state 的工具（直接用 setState 也行，这里只是类型上收敛）
  const patch = (u: (s: AnalysisTaskState) => AnalysisTaskState) => setState(u);

  const appendLog = useCallback((l: Omit<AnalysisLog, "ts">) => {
    patch((s) => ({ ...s, logs: [...s.logs, { ...l, ts: Date.now() }] }));
  }, []);

  const handleEvent = useCallback((e: RunAllEvent) => {
    switch (e.type) {
      case "start":
        patch((s) => ({
          ...s,
          batchId: e.batch_id,
          progress: { current: 0, total: e.total, failed: 0 },
          concurrency: Math.max(1, e.concurrency || 1),
          runningAssets: {},
        }));
        appendLog({
          kind: "info",
          text:
            `🚀 开始分析共 ${e.total} 个标的 · 并发 ${e.concurrency || 1} · ` +
            `批次 ${e.batch_id.slice(0, 16)}…`,
        });
        if (e.total === 0) {
          appendLog({ kind: "info", text: "⚠️ 没有找到任何标的，先去「我的标的」添加吧" });
        }
        break;
      case "asset_start":
        patch((s) => ({
          ...s,
          runningAssets: {
            ...s.runningAssets,
            [e.asset_id]: {
              assetId: e.asset_id,
              name: e.name,
              code: e.code,
              index: e.index,
              startedAt: Date.now(),
            },
          },
        }));
        appendLog({
          kind: "asset_start",
          text: `[${e.index}/${e.total}] ▶ ${e.name}（${e.code}）`,
          assetId: e.asset_id,
          assetName: e.name,
        });
        break;
      case "log":
        appendLog({
          kind: "log",
          text: e.name ? `   [${e.name}] ${e.text}` : `   ${e.text}`,
          assetId: e.asset_id,
          assetName: e.name,
        });
        break;
      case "asset_done":
        patch((s) => {
          // 并发模式下 index 无法单调递增 → current 用"完成数"
          const nextCurrent = Math.min(s.progress.total, s.progress.current + 1);
          const { [e.asset_id]: _omit, ...rest } = s.runningAssets;
          return {
            ...s,
            progress: { ...s.progress, current: nextCurrent },
            runningAssets: rest,
          };
        });
        appendLog({
          kind: "asset_done",
          text: `[${e.index}/${e.total}] ✅ ${e.name} → ${actionLabel(e.action)} (${(e.confidence * 100).toFixed(0)}%)`,
          action: e.action,
          confidence: e.confidence,
          summary: e.summary,
          assetId: e.asset_id,
          assetName: e.name,
        });
        // 每完成一个就刷新一下建议列表，体验更及时
        qc.invalidateQueries({ queryKey: ["advice"] });
        break;
      case "asset_error":
        patch((s) => {
          const nextCurrent = Math.min(s.progress.total, s.progress.current + 1);
          const { [e.asset_id]: _omit, ...rest } = s.runningAssets;
          return {
            ...s,
            progress: { ...s.progress, current: nextCurrent, failed: s.progress.failed + 1 },
            runningAssets: rest,
          };
        });
        appendLog({
          kind: "asset_error",
          text: `[${e.index}/${e.total}] ❌ ${e.name}：${e.error}`,
          assetId: e.asset_id,
          assetName: e.name,
        });
        break;
      case "done":
        appendLog({
          kind: "done",
          text: `🎉 全部完成 · 成功 ${e.analyzed} · 失败 ${e.failed}`,
        });
        patch((s) => ({ ...s, running: false, finishedAt: Date.now(), runningAssets: {} }));
        qc.invalidateQueries({ queryKey: ["advice"] });
        toast.success(`分析完成 · 成功 ${e.analyzed} / 失败 ${e.failed}`);
        break;
      case "fatal":
        appendLog({ kind: "asset_error", text: `💥 执行中断：${e.error}` });
        patch((s) => ({ ...s, running: false, runningAssets: {} }));
        break;
    }
  }, [appendLog, qc]);

  const start = useCallback(async () => {
    if (state.running) return;

    // 重置为新任务
    setState({
      ...defaultState,
      started: true,
      running: true,
      startedAt: Date.now(),
    });

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await runAllStream(handleEvent, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") {
        appendLog({ kind: "asset_error", text: `💥 请求失败：${e.message || e}` });
        toast.error(e.message || "分析请求失败");
      } else {
        appendLog({ kind: "asset_error", text: "⏹ 用户已中止分析" });
      }
      patch((s) => ({ ...s, running: false, runningAssets: {} }));
    } finally {
      abortRef.current = null;
    }
  }, [state.running, handleEvent, appendLog]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    patch((s) => ({ ...s, running: false, runningAssets: {} }));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState(defaultState);
  }, []);

  const percent = state.progress.total > 0
    ? Math.min(100, Math.round((state.progress.current / state.progress.total) * 100))
    : 0;

  const runningList = Object.values(state.runningAssets).sort((a, b) => a.index - b.index);

  return (
    <Ctx.Provider value={{ ...state, start, stop, reset, percent, runningList }}>
      {children}
    </Ctx.Provider>
  );
}
