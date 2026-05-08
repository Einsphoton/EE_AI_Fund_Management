/**
 * OCR 任务跨路由保活 Context。
 *
 * 设计：
 *   - 进程级单例 Provider，挂在 App 根上
 *   - 启动一次 OCR job 后，把 jobId / 思考日志 / 进度 / 最终结果都放在 Context state
 *   - 用户切换路由时该 state 不会卸载（Provider 比 Routes 更上层）
 *   - 切回 OCR 页时直接读 context；连接断了就用 jobId 自动重连 SSE（重连时后端会 replay 历史）
 *
 * 兼容老接口：暴露 `parseFiles` / `result` / `committing` 等 API，让 ImportOcr 页面用起来跟原本本地
 * useState 几乎一样。
 */
import {
  createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode,
} from "react";
import toast from "react-hot-toast";

import {
  ImportApi, OcrJobSnapshot, OcrJobEvent, OcrParseResult, OcrCommitItem,
} from "../api/client";

// ====================== 类型 ======================

export interface OcrThought {
  ts: number;
  text: string;
  /** 关联文件名（可空表示全局日志） */
  file?: string;
  /** 事件原始 type，用于 UI 展示 icon/颜色 */
  kind: "thought" | "image_start" | "image_done" | "image_error" | "progress" | "start" | "done" | "fatal";
}

export interface OcrTaskState {
  /** 是否曾经启动过（决定 UI 显示空态还是任务详情） */
  started: boolean;
  /** 是否正在运行中（解析阶段） */
  running: boolean;
  /** 当前 job_id（用于重连/拉取最终结果） */
  jobId: string | null;
  /** 进度（finished / total） */
  progress: { finished: number; total: number };
  /** 滚动思考日志 */
  thoughts: OcrThought[];
  /** 已完成识别的最终 results；解析完才有 */
  results: OcrParseResult[];
  /** 启动/结束时间 */
  startedAt: number | null;
  finishedAt: number | null;
  /** 致命错误 */
  error: string | null;
  /** 提交阶段忙碌 */
  committing: boolean;
}

interface OcrTaskCtxValue extends OcrTaskState {
  /** 启动一次新的解析任务（先取消旧的） */
  startParse: (files: File[], platformHint: string) => Promise<void>;
  /** 把识别结果清空，回到上传态 */
  reset: () => void;
  /** 提交对账后的清单 */
  commit: (items: OcrCommitItem[]) => Promise<{ created: number; appended: number; skipped: number; errors: string[] }>;
  /** 已完成 + 总数百分比（0~100 整数） */
  percent: number;
  /** 当前是否处于"识别完成 + 等用户确认"阶段 */
  hasResults: boolean;
}

const defaultState: OcrTaskState = {
  started: false,
  running: false,
  jobId: null,
  progress: { finished: 0, total: 0 },
  thoughts: [],
  results: [],
  startedAt: null,
  finishedAt: null,
  error: null,
  committing: false,
};

const Ctx = createContext<OcrTaskCtxValue | null>(null);

export function useOcrTask(): OcrTaskCtxValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useOcrTask must be used inside OcrTaskProvider");
  return v;
}

// ====================== Provider ======================

const MAX_THOUGHTS = 800; // 思考日志最多保留多少条（防内存爆炸）

export function OcrTaskProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OcrTaskState>(defaultState);
  const abortRef = useRef<AbortController | null>(null);
  const seenEventsRef = useRef<Set<string>>(new Set());

  const patch = (u: (s: OcrTaskState) => OcrTaskState) => setState(u);

  /** 把后端事件落到 thoughts / progress / results 状态。 */
  const handleEvent = useCallback((evt: OcrJobEvent & { ts?: number }) => {
    // 简单去重：以 type+file+ts 为 key（重连 replay 时会重复推一遍）
    const key = `${evt.type}|${(evt as any).file || ""}|${evt.ts || 0}|${(evt as any).text || ""}`;
    if (seenEventsRef.current.has(key)) return;
    seenEventsRef.current.add(key);
    if (seenEventsRef.current.size > 5000) {
      // GC：太多就重建一份
      seenEventsRef.current = new Set(Array.from(seenEventsRef.current).slice(-3000));
    }

    const ts = (evt.ts || Date.now() / 1000) * 1000;

    switch (evt.type) {
      case "start":
        patch((s) => ({
          ...s,
          progress: { finished: 0, total: evt.total },
          thoughts: [
            ...s.thoughts,
            {
              ts,
              kind: "start",
              text: `🚀 开始识别 ${evt.total} 张截图${evt.platform_hint ? ` · 平台提示「${evt.platform_hint}」` : ""}`,
            },
          ],
        }));
        break;

      case "thought":
        patch((s) => {
          const next = [
            ...s.thoughts,
            { ts, kind: "thought" as const, text: evt.text, file: evt.file },
          ];
          // 截断
          return { ...s, thoughts: next.length > MAX_THOUGHTS ? next.slice(-MAX_THOUGHTS) : next };
        });
        break;

      case "image_start":
        patch((s) => ({
          ...s,
          thoughts: [
            ...s.thoughts,
            {
              ts, kind: "image_start", file: evt.file,
              text: `[${evt.index + 1}/${evt.total}] ▶ 开始识别 ${evt.file}`,
            },
          ],
        }));
        break;

      case "image_done":
        patch((s) => ({
          ...s,
          thoughts: [
            ...s.thoughts,
            {
              ts, kind: "image_done", file: evt.file,
              text: `[${evt.file}] ✅ 识别 ${evt.items_count} 项 / 命中 ${evt.matched_count} 项现有资产 · 平台「${evt.platform || "未知"}」 · 用时 ${evt.elapsed}s`,
            },
          ],
        }));
        break;

      case "image_error":
        patch((s) => ({
          ...s,
          thoughts: [
            ...s.thoughts,
            { ts, kind: "image_error", file: evt.file, text: `[${evt.file}] ❌ ${evt.error}` },
          ],
        }));
        break;

      case "progress":
        patch((s) => ({ ...s, progress: { finished: evt.finished, total: evt.total } }));
        break;

      case "done":
        patch((s) => ({
          ...s,
          thoughts: [
            ...s.thoughts,
            {
              ts, kind: "done",
              text: `🎉 全部完成：${evt.files} 张图 · 共 ${evt.total_items} 项${evt.errors ? `（${evt.errors} 张异常）` : ""}`,
            },
          ],
        }));
        break;

      case "fatal":
        patch((s) => ({
          ...s,
          error: evt.error,
          running: false,
          thoughts: [
            ...s.thoughts,
            { ts, kind: "fatal", text: `💥 任务中断：${evt.error}` },
          ],
        }));
        break;
    }
  }, []);

  /** 通过 fetch + getReader 订阅 SSE，保持跨 tab 的事件。 */
  const openStream = useCallback(async (jobId: string, signal: AbortSignal) => {
    const resp = await fetch(`/api/import/ocr/jobs/${jobId}/stream`, { method: "GET", signal });
    if (!resp.ok || !resp.body) {
      throw new Error(`订阅 OCR 进度失败：${resp.status}`);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += dec.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const evt = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!evt.startsWith("data:")) continue;
        const payload = evt.slice(5).trim();
        if (payload === "[DONE]") return;
        try {
          const obj = JSON.parse(payload) as OcrJobEvent;
          handleEvent(obj);
        } catch {
          // ignore
        }
      }
    }
  }, [handleEvent]);

  /** 任务结束后拉一次最终 result（含候选/建议）。 */
  const fetchFinalResult = useCallback(async (jobId: string) => {
    try {
      const r = await ImportApi.getJob(jobId);
      const snap: OcrJobSnapshot = r.snapshot;
      if (snap.status === "done" && r.result) {
        patch((s) => ({
          ...s,
          running: false,
          finishedAt: snap.finished_at ? snap.finished_at * 1000 : Date.now(),
          results: r.result!.results,
          progress: { finished: snap.finished, total: snap.total },
        }));
        const errCount = r.result.results.filter((x) => x.error).length;
        toast.success(`解析完成：共 ${r.result.total} 项${errCount ? `（${errCount} 张异常）` : ""}`);
      } else if (snap.status === "error") {
        patch((s) => ({ ...s, running: false, error: snap.error || "任务失败" }));
        toast.error(`OCR 任务失败：${snap.error}`);
      }
    } catch (e: any) {
      // 任务可能被 GC，静默
      console.warn("[OCR] fetchFinalResult 失败：", e?.message || e);
    }
  }, []);

  const startParse = useCallback(async (files: File[], platformHint: string) => {
    if (state.running) {
      toast.error("已有 OCR 任务在跑，请等待或刷新页面");
      return;
    }
    if (files.length === 0) {
      toast.error("请先选择至少一张截图");
      return;
    }

    // 重置（保留 started=true 以便 UI 立即切换到任务态）
    seenEventsRef.current = new Set();
    setState({
      ...defaultState,
      started: true,
      running: true,
      startedAt: Date.now(),
      progress: { finished: 0, total: files.length },
    });

    // 1) start 接口建任务
    let jobId: string;
    try {
      const r = await ImportApi.start(files, platformHint);
      jobId = r.job_id;
      patch((s) => ({ ...s, jobId }));
    } catch (e: any) {
      patch((s) => ({ ...s, running: false, error: e?.message || String(e) }));
      toast.error(`启动 OCR 任务失败：${e?.message || e}`);
      return;
    }

    // 2) 起 SSE 订阅
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await openStream(jobId, ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError") {
        // 流断了不一定是失败 — 任务可能已经在后端跑完，下面 fetchFinalResult 会兜底
        console.warn("[OCR] stream 断开：", e?.message || e);
      }
    } finally {
      abortRef.current = null;
    }

    // 3) 流结束 → 拉最终结果
    await fetchFinalResult(jobId);
  }, [state.running, openStream, fetchFinalResult]);

  /** 用户在中途回到页面时：检测是否有进行中的 jobId，如果有就重连 SSE 并拉最新快照。 */
  const reattach = useCallback(async () => {
    const jobId = state.jobId;
    if (!jobId || !state.running) return;
    if (abortRef.current) return; // 已经在订阅
    // 先拉一次最新事件历史（其实 stream 重连时后端会 replay，这里冗余调一次主要是为了快速更新 UI）
    try {
      const r = await ImportApi.getJob(jobId);
      // 把历史事件灌进去（去重逻辑会过滤已知）
      for (const ev of r.events as (OcrJobEvent & { ts: number })[]) {
        handleEvent(ev);
      }
      const snap = r.snapshot;
      if (snap.status === "done" || snap.status === "error") {
        await fetchFinalResult(jobId);
        return;
      }
    } catch {
      /* job 已被 GC，忽略 */
      patch((s) => ({ ...s, running: false }));
      return;
    }

    // 重新订阅 SSE
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await openStream(jobId, ctrl.signal);
    } catch (e: any) {
      console.warn("[OCR] reattach stream 失败：", e?.message || e);
    } finally {
      abortRef.current = null;
    }
    await fetchFinalResult(jobId);
  }, [state.jobId, state.running, handleEvent, openStream, fetchFinalResult]);

  // App 启动时探测一次：是否有刚才正在跑的任务（例如刷新了浏览器页面）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await ImportApi.listJobs(5);
        if (cancelled) return;
        const active = r.items.find((j) => j.status === "parsing");
        if (active && !state.jobId) {
          // 自动挂回去
          patch((s) => ({
            ...s,
            started: true,
            running: true,
            jobId: active.job_id,
            startedAt: active.created_at * 1000,
            progress: { finished: active.finished, total: active.total },
          }));
        }
      } catch {
        /* 后端没启动也没事 */
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 一旦发现 running && jobId 但没活跃订阅 → 立即重连
  useEffect(() => {
    if (state.running && state.jobId && !abortRef.current) {
      reattach();
    }
  }, [state.running, state.jobId, reattach]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    seenEventsRef.current = new Set();
    setState(defaultState);
  }, []);

  const commit = useCallback(async (items: OcrCommitItem[]) => {
    setState((s) => ({ ...s, committing: true }));
    try {
      const r = await ImportApi.commit(items);
      const msg = `已新建 ${r.created} 项 / 追加 ${r.appended} 项 / 跳过 ${r.skipped} 项`;
      if (r.errors.length > 0) {
        toast.error(`${msg}；但有 ${r.errors.length} 处错误：${r.errors[0]}`);
      } else {
        toast.success(msg);
      }
      // 提交完成 → 清空状态
      setState(defaultState);
      seenEventsRef.current = new Set();
      return r;
    } catch (e: any) {
      toast.error(`提交失败：${e?.message || e}`);
      setState((s) => ({ ...s, committing: false }));
      throw e;
    }
  }, []);

  const percent = state.progress.total > 0
    ? Math.min(100, Math.round((state.progress.finished / state.progress.total) * 100))
    : 0;

  const value: OcrTaskCtxValue = {
    ...state,
    startParse,
    reset,
    commit,
    percent,
    hasResults: state.results.length > 0,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
