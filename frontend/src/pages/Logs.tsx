import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, FileArchive, FileText, RefreshCw, Terminal } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import { LogsApi } from "../api/client";

function formatSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(ts: number) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function triggerDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

export default function Logs() {
  const [selected, setSelected] = useState("ai.log");
  const [lines, setLines] = useState(500);
  const listQ = useQuery({ queryKey: ["logs"], queryFn: LogsApi.list, refetchInterval: 30_000 });
  const tailQ = useQuery({
    queryKey: ["logs", "tail", selected, lines],
    queryFn: () => LogsApi.tail(selected, lines),
    enabled: Boolean(selected),
    refetchInterval: 15_000,
  });

  const files = listQ.data?.files || [];
  const selectedExists = useMemo(() => files.some((f) => f.name === selected), [files, selected]);

  useEffect(() => {
    if (!selectedExists && files.length) {
      const preferred = files.find((f) => f.name === "ai.log") || files.find((f) => f.name === "app.log") || files[0];
      setSelected(preferred.name);
    }
  }, [files, selectedExists]);

  return (
    <>
      <PageHeader
        title="运行日志"
        subtitle="NAS / Docker 部署后用于排查问题；AI 相关优先看 ai.log，也可一键导出诊断包发给助手。"
        actions={
          <>
            <button className="btn" onClick={() => { listQ.refetch(); tailQ.refetch(); }}>
              <RefreshCw className="w-4 h-4" /> 刷新
            </button>
            <button className="btn-primary" onClick={() => triggerDownload(LogsApi.bundleUrl())}>
              <FileArchive className="w-4 h-4" /> 导出诊断包
            </button>
          </>
        }
      />

      <div className="grid lg:grid-cols-[320px,1fr] gap-5">
        <div className="card p-4 space-y-4">
          <div>
            <div className="flex items-center gap-2 text-white font-medium mb-1">
              <FileText className="w-4 h-4 text-accent" /> 日志文件
            </div>
            <div className="text-xs text-muted break-all">{listQ.data?.log_dir || "加载中…"}</div>
          </div>

          <div className="space-y-2 max-h-[520px] overflow-auto pr-1">
            {files.map((f) => (
              <button
                key={f.name}
                className={`w-full text-left rounded-xl border px-3 py-2 transition ${selected === f.name ? "border-accent/60 bg-accent/10" : "border-line bg-bg-soft/40 hover:bg-line/30"}`}
                onClick={() => setSelected(f.name)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm text-white truncate">{f.name}</span>
                  <span className="text-[11px] text-muted shrink-0">{formatSize(f.size)}</span>
                </div>
                <div className="text-[11px] text-muted mt-1">{formatTime(f.modified_at)}</div>
              </button>
            ))}
            {!files.length && <div className="text-sm text-muted">暂无日志文件</div>}
          </div>
        </div>

        <div className="card p-4 min-w-0">
          <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
            <div className="flex items-center gap-2 min-w-0">
              <Terminal className="w-4 h-4 text-accent" />
              <span className="font-medium text-white truncate">{selected}</span>
              {tailQ.isFetching && <span className="text-xs text-muted">更新中…</span>}
            </div>
            <div className="flex items-center gap-2">
              <select className="input !w-28 !py-1.5 text-xs" value={lines} onChange={(e) => setLines(Number(e.target.value))}>
                <option value={200}>200 行</option>
                <option value={500}>500 行</option>
                <option value={1000}>1000 行</option>
                <option value={3000}>3000 行</option>
              </select>
              <button
                className="btn !px-3 !py-1.5 text-xs"
                onClick={() => {
                  navigator.clipboard.writeText(tailQ.data || "").then(() => toast.success("已复制日志片段"));
                }}
              >复制</button>
              <button className="btn !px-3 !py-1.5 text-xs" onClick={() => triggerDownload(LogsApi.downloadUrl(selected))}>
                <Download className="w-3.5 h-3.5" /> 下载
              </button>
            </div>
          </div>

          <pre className="bg-black/40 border border-line rounded-xl p-4 overflow-auto max-h-[70vh] text-[11px] leading-relaxed whitespace-pre-wrap break-words text-emerald-50/90">
            {tailQ.isError ? `读取失败：${(tailQ.error as Error)?.message || tailQ.error}` : (tailQ.data || "暂无内容")}
          </pre>
        </div>
      </div>
    </>
  );
}
