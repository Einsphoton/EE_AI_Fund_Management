import { useEffect, useMemo, useRef, useState } from "react";
import {
  Send, Sparkles, Trash2, MessageCircleMore, StopCircle, BrainCircuit,
  Plus, Pencil, Check, X, PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import { ChatMsg, chatStream } from "../api/client";

// ---- 持久化 ----
const STORAGE_KEY_V2 = "ee-fund.chat.conversations.v2";
const STORAGE_KEY_V1 = "ee-fund.chat.history.v1"; // 旧版，只有一个 messages 数组

const SUGGESTED_PROMPTS = [
  "整体盘点一下我的资产，重点风险有哪些？",
  "我现在持仓里有哪些标的应该考虑加仓？哪些应该减仓？",
  "结合最近 AI 建议，给我一个本周的操作计划",
  "我的基金组合是否过于集中在某一行业？",
  "假设我有 1 万元闲钱，应该买入我观察列表中的哪只？",
];

interface MessageBubble {
  role: "user" | "assistant";
  content: string;
  ts: number;
}

interface Conversation {
  id: string;
  title: string;
  messages: MessageBubble[];
  createdAt: number;
  updatedAt: number;
}

interface Store {
  activeId: string;
  conversations: Conversation[];
}

// ---- 工具 ----
const uid = () =>
  `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;

const deriveTitle = (msgs: MessageBubble[]): string => {
  const first = msgs.find((m) => m.role === "user" && m.content.trim());
  if (!first) return "新对话";
  const t = first.content.trim().replace(/\s+/g, " ");
  return t.length > 26 ? t.slice(0, 26) + "…" : t;
};

const newConversation = (): Conversation => ({
  id: uid(),
  title: "新对话",
  messages: [],
  createdAt: Date.now(),
  updatedAt: Date.now(),
});

const loadStore = (): Store => {
  // v2 优先
  try {
    const raw = localStorage.getItem(STORAGE_KEY_V2);
    if (raw) {
      const s = JSON.parse(raw) as Store;
      if (s && Array.isArray(s.conversations) && s.conversations.length) {
        return s;
      }
    }
  } catch {}
  // v1 迁移
  try {
    const raw = localStorage.getItem(STORAGE_KEY_V1);
    if (raw) {
      const msgs: MessageBubble[] = JSON.parse(raw);
      if (Array.isArray(msgs) && msgs.length) {
        const c: Conversation = {
          id: uid(),
          title: deriveTitle(msgs),
          messages: msgs,
          createdAt: msgs[0]?.ts || Date.now(),
          updatedAt: msgs[msgs.length - 1]?.ts || Date.now(),
        };
        return { activeId: c.id, conversations: [c] };
      }
    }
  } catch {}
  // 初始：一个空对话
  const c = newConversation();
  return { activeId: c.id, conversations: [c] };
};

const saveStore = (s: Store) => {
  try { localStorage.setItem(STORAGE_KEY_V2, JSON.stringify(s)); } catch {}
};

const fmtRelTime = (ts: number): string => {
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  const d = new Date(ts);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const now = new Date();
  if (y === now.getFullYear()) return `${m}-${day}`;
  return `${y}-${m}-${day}`;
};

// =======================================================================

export default function AIChat() {
  const [store, setStore] = useState<Store>(loadStore);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 当前会话（确保永远存在）
  const active = useMemo<Conversation>(() => {
    const found = store.conversations.find((c) => c.id === store.activeId);
    return found || store.conversations[0];
  }, [store]);

  // 按更新时间倒序
  const sortedConvs = useMemo(
    () => [...store.conversations].sort((a, b) => b.updatedAt - a.updatedAt),
    [store.conversations],
  );

  const messages = active?.messages || [];

  // 持久化 & 滚动到底
  useEffect(() => { saveStore(store); }, [store]);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, active?.id]);

  // ========= 会话操作 =========
  const createConv = () => {
    // 如果当前已经是空会话，直接聚焦输入即可，避免堆积
    if (active && active.messages.length === 0) {
      inputRef.current?.focus();
      return;
    }
    const c = newConversation();
    setStore((s) => ({ activeId: c.id, conversations: [c, ...s.conversations] }));
    setInput("");
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const switchConv = (id: string) => {
    if (streaming) {
      toast.error("请等当前回复结束或停止生成");
      return;
    }
    if (id !== store.activeId) {
      setStore((s) => ({ ...s, activeId: id }));
      setInput("");
      setRenamingId(null);
    }
  };

  const deleteConv = (id: string) => {
    const target = store.conversations.find((c) => c.id === id);
    if (!target) return;
    if (target.messages.length > 0 && !confirm(`删除对话「${target.title}」？该操作不可恢复`)) return;
    setStore((s) => {
      const rest = s.conversations.filter((c) => c.id !== id);
      if (rest.length === 0) {
        const c = newConversation();
        return { activeId: c.id, conversations: [c] };
      }
      const activeId = s.activeId === id ? rest[0].id : s.activeId;
      return { activeId, conversations: rest };
    });
  };

  const startRename = (c: Conversation) => {
    setRenamingId(c.id);
    setRenameDraft(c.title);
  };
  const commitRename = () => {
    const name = renameDraft.trim() || "未命名对话";
    setStore((s) => ({
      ...s,
      conversations: s.conversations.map((c) =>
        c.id === renamingId ? { ...c, title: name, updatedAt: Date.now() } : c,
      ),
    }));
    setRenamingId(null);
  };
  const cancelRename = () => setRenamingId(null);

  const clearCurrent = () => {
    if (!messages.length) return;
    if (!confirm("清空当前对话的所有消息？")) return;
    setStore((s) => ({
      ...s,
      conversations: s.conversations.map((c) =>
        c.id === active.id
          ? { ...c, messages: [], title: "新对话", updatedAt: Date.now() }
          : c,
      ),
    }));
  };

  // ========= 更新当前会话 =========
  const updateActive = (
    mutator: (msgs: MessageBubble[]) => MessageBubble[],
    opts?: { autoTitle?: boolean },
  ) => {
    setStore((s) => {
      const conv = s.conversations.find((c) => c.id === active.id);
      if (!conv) return s;
      const newMsgs = mutator(conv.messages);
      const nextTitle =
        opts?.autoTitle && (conv.title === "新对话" || !conv.title)
          ? deriveTitle(newMsgs)
          : conv.title;
      return {
        ...s,
        conversations: s.conversations.map((c) =>
          c.id === active.id
            ? { ...c, messages: newMsgs, title: nextTitle, updatedAt: Date.now() }
            : c,
        ),
      };
    });
  };

  // ========= 发送消息 =========
  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streaming) return;

    const newUser: MessageBubble = { role: "user", content, ts: Date.now() };
    const aiPlaceholder: MessageBubble = { role: "assistant", content: "", ts: Date.now() };

    // 原子更新
    const beforeSend = [...messages, newUser, aiPlaceholder];
    updateActive(() => beforeSend, { autoTitle: true });

    setInput("");
    setStreaming(true);

    const history: ChatMsg[] = beforeSend
      .filter((_, i) => i < beforeSend.length - 1) // 剔除末尾占位 assistant
      .map((m) => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    abortRef.current = controller;

    let acc = "";
    try {
      await chatStream(
        history,
        (token) => {
          acc += token;
          updateActive((msgs) => {
            const copy = [...msgs];
            const last = copy[copy.length - 1];
            if (last && last.role === "assistant") {
              copy[copy.length - 1] = { ...last, content: acc };
            }
            return copy;
          });
        },
        controller.signal,
      );
    } catch (e: any) {
      if (e.name !== "AbortError") {
        toast.error(e.message || "AI 调用失败");
        updateActive((msgs) => {
          const copy = [...msgs];
          const last = copy[copy.length - 1];
          if (last && last.role === "assistant" && !last.content) {
            copy[copy.length - 1] = { ...last, content: `⚠️ ${e.message || "AI 调用失败"}` };
          }
          return copy;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
      inputRef.current?.focus();
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  // =======================================================================

  return (
    <>
      <PageHeader
        title="AI Chat"
        subtitle="基于你的全部资产 + 已启用 Skill，与 Hermes-Lite Agent 自由问答"
        actions={
          <>
            <button
              className="btn"
              onClick={() => setSidebarOpen((v) => !v)}
              title={sidebarOpen ? "隐藏会话列表" : "显示会话列表"}
            >
              {sidebarOpen ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeftOpen className="w-4 h-4" />}
            </button>
            <button className="btn-primary" onClick={createConv}>
              <Plus className="w-4 h-4" /> 新建对话
            </button>
          </>
        }
      />

      <div className="flex gap-4" style={{ height: "calc(100vh - 220px)", minHeight: 540 }}>
        {/* ==== 侧边栏：会话列表 ==== */}
        {sidebarOpen && (
          <aside className="card w-64 shrink-0 flex flex-col overflow-hidden">
            <div className="px-3 py-2.5 border-b border-line/60 flex items-center justify-between">
              <div className="text-xs text-muted">对话列表 ({sortedConvs.length})</div>
              <button
                className="text-muted hover:text-accent-soft transition"
                onClick={createConv}
                title="新建对话"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {sortedConvs.map((c) => {
                const isActive = c.id === active?.id;
                const isRenaming = renamingId === c.id;
                return (
                  <div
                    key={c.id}
                    className={`group rounded-lg px-2.5 py-2 transition cursor-pointer border ${
                      isActive
                        ? "bg-accent/15 border-accent/40"
                        : "border-transparent hover:bg-line/30"
                    }`}
                    onClick={() => !isRenaming && switchConv(c.id)}
                  >
                    {isRenaming ? (
                      <div className="flex items-center gap-1">
                        <input
                          className="input !py-1 !px-2 text-xs flex-1"
                          value={renameDraft}
                          autoFocus
                          onChange={(e) => setRenameDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename();
                            else if (e.key === "Escape") cancelRename();
                          }}
                          onClick={(e) => e.stopPropagation()}
                        />
                        <button
                          className="text-emerald2 hover:bg-emerald2/15 rounded p-1"
                          onClick={(e) => { e.stopPropagation(); commitRename(); }}
                        >
                          <Check className="w-3.5 h-3.5" />
                        </button>
                        <button
                          className="text-muted hover:bg-line/40 rounded p-1"
                          onClick={(e) => { e.stopPropagation(); cancelRename(); }}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-start gap-2">
                          <MessageCircleMore
                            className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${
                              isActive ? "text-accent" : "text-muted"
                            }`}
                          />
                          <div className="flex-1 min-w-0">
                            <div className={`text-sm truncate ${isActive ? "text-white" : "text-white/80"}`}>
                              {c.title}
                            </div>
                            <div className="text-[10px] text-muted mt-0.5 flex items-center gap-2">
                              <span>{c.messages.length} 条</span>
                              <span>·</span>
                              <span>{fmtRelTime(c.updatedAt)}</span>
                            </div>
                          </div>
                          <div
                            className={`flex items-center gap-0.5 shrink-0 transition ${
                              isActive ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                            }`}
                          >
                            <button
                              className="text-muted hover:text-accent-soft p-1 rounded hover:bg-line/40"
                              onClick={(e) => { e.stopPropagation(); startRename(c); }}
                              title="重命名"
                            >
                              <Pencil className="w-3 h-3" />
                            </button>
                            <button
                              className="text-muted hover:text-rose2 p-1 rounded hover:bg-rose2/10"
                              onClick={(e) => { e.stopPropagation(); deleteConv(c.id); }}
                              title="删除对话"
                            >
                              <Trash2 className="w-3 h-3" />
                            </button>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </aside>
        )}

        {/* ==== 主聊天区 ==== */}
        <div className="card flex-1 flex flex-col min-w-0">
          {/* 顶栏：当前会话标题 + 清空 */}
          <div className="px-5 py-2.5 border-b border-line/60 flex items-center gap-2">
            <div className="text-sm font-medium truncate flex-1">
              {active?.title || "新对话"}
            </div>
            <button
              className="btn !py-1 !px-2 text-xs"
              onClick={clearCurrent}
              disabled={!messages.length || streaming}
              title="清空当前对话"
            >
              <Trash2 className="w-3.5 h-3.5" /> 清空
            </button>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
            {messages.length === 0 ? <Welcome onPick={send} /> : (
              messages.map((m, i) => (
                <Bubble key={i} msg={m} streaming={streaming && i === messages.length - 1} />
              ))
            )}
          </div>

          <div className="border-t border-line/60 px-4 py-3">
            {messages.length > 0 && messages.length < 10 && !streaming && (
              <div className="flex flex-wrap gap-1.5 mb-2.5">
                {SUGGESTED_PROMPTS.slice(0, 3).map((p) => (
                  <button
                    key={p}
                    className="text-[11px] px-2.5 py-1 rounded-full border border-line text-muted hover:border-accent/50 hover:text-accent-soft transition"
                    onClick={() => send(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}

            <div className="flex gap-2 items-end">
              <textarea
                ref={inputRef}
                className="input min-h-[44px] max-h-32 resize-none flex-1"
                placeholder="问点什么吧 ⌘ 比如：'帮我梳理本周的操作计划'。Enter 发送，Shift+Enter 换行"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={streaming}
                rows={1}
              />
              {streaming ? (
                <button className="btn-danger" onClick={stop} title="停止生成">
                  <StopCircle className="w-4 h-4" /> 停止
                </button>
              ) : (
                <button
                  className="btn-primary"
                  onClick={() => send()}
                  disabled={!input.trim()}
                  title="发送 (Enter)"
                >
                  <Send className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-6">
      <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-accent to-emerald2 flex items-center justify-center shadow-glow mb-4">
        <Sparkles className="w-7 h-7 text-white" />
      </div>
      <h3 className="text-xl font-semibold mb-1">Hermes-Lite 资产顾问</h3>
      <p className="text-sm text-muted max-w-md mb-6">
        我可以查看你<span className="text-accent-soft">全部</span>的基金 / 股票持仓、最近 AI 建议、已启用的 Skill，
        然后基于这些上下文回答你的问题。
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-2xl w-full">
        {SUGGESTED_PROMPTS.map((p) => (
          <button
            key={p}
            className="text-left text-sm px-4 py-3 rounded-xl border border-line bg-bg-soft/40 hover:border-accent/50 hover:bg-accent/5 transition flex items-start gap-2"
            onClick={() => onPick(p)}
          >
            <MessageCircleMore className="w-4 h-4 text-accent mt-0.5 shrink-0" />
            <span>{p}</span>
          </button>
        ))}
      </div>
      <div className="text-[11px] text-muted/70 mt-6">
        ⚠️ 请先在「设置」配置好大模型 API。所有输出仅供参考，不构成投资建议。
      </div>
    </div>
  );
}

function Bubble({ msg, streaming }: { msg: MessageBubble; streaming?: boolean }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div className={`w-8 h-8 rounded-xl shrink-0 flex items-center justify-center text-xs font-semibold
        ${isUser ? "bg-bg-soft border border-line text-muted" : "bg-gradient-to-br from-accent to-emerald2 text-white"}`}>
        {isUser ? "我" : <BrainCircuit className="w-4 h-4" />}
      </div>
      <div className={`max-w-[78%] rounded-2xl px-4 py-3 text-sm leading-relaxed
        ${isUser
          ? "bg-accent/15 border border-accent/30 text-white"
          : "bg-bg-soft/60 border border-line text-white/90"}`}>
        <MarkdownLite text={msg.content} />
        {streaming && !msg.content && (
          <span className="inline-flex gap-1 items-center text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" style={{ animationDelay: "0.15s" }} />
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" style={{ animationDelay: "0.3s" }} />
          </span>
        )}
        {streaming && msg.content && (
          <span className="inline-block w-1.5 h-3.5 bg-accent ml-0.5 align-middle animate-pulse" />
        )}
      </div>
    </div>
  );
}

/**
 * 极简 markdown 渲染：标题 / 加粗 / 行内代码 / 列表 / 段落 / 表格。
 * 没引入额外依赖以保持包体积；足够呈现 AI 输出。
 */
function MarkdownLite({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  const out: JSX.Element[] = [];
  let i = 0;

  const renderInline = (s: string, key: string) => {
    const parts: (string | JSX.Element)[] = [];
    let rest = s;
    let idx = 0;
    while (rest.length) {
      const bm = /\*\*(.+?)\*\*/.exec(rest);
      const cm = /`([^`]+)`/.exec(rest);
      const next = [bm, cm].filter(Boolean).sort(
        (a, b) => (a as RegExpExecArray).index - (b as RegExpExecArray).index,
      )[0] as RegExpExecArray | undefined;
      if (!next) { parts.push(rest); break; }
      if (next.index > 0) parts.push(rest.slice(0, next.index));
      if (next === bm) {
        parts.push(<strong key={`${key}-b-${idx++}`} className="text-white">{next[1]}</strong>);
      } else {
        parts.push(
          <code key={`${key}-c-${idx++}`}
                className="px-1.5 py-0.5 rounded bg-bg/70 border border-line/60 text-[12px] font-mono text-accent-soft">
            {next[1]}
          </code>,
        );
      }
      rest = rest.slice(next.index + next[0].length);
    }
    return parts;
  };

  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*-+/.test(lines[i + 1])) {
      const header = ln.split("|").map((c) => c.trim()).filter(Boolean);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].split("|").map((c) => c.trim()).filter(Boolean));
        i++;
      }
      out.push(
        <div key={`tbl-${i}`} className="my-2 overflow-x-auto">
          <table className="text-xs border border-line/60 rounded-lg overflow-hidden">
            <thead className="bg-bg-soft/60 text-muted">
              <tr>{header.map((h, k) => <th key={k} className="px-3 py-1.5 text-left font-medium">{renderInline(h, `h${k}`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-t border-line/40">
                  {r.map((c, ci) => <td key={ci} className="px-3 py-1.5">{renderInline(c, `r${ri}c${ci}`)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    const h = /^(#{1,4})\s+(.+)$/.exec(ln);
    if (h) {
      const level = h[1].length;
      const cls = level === 1 ? "text-base font-semibold mt-2 mb-1"
        : level === 2 ? "text-sm font-semibold mt-2 mb-1"
        : "text-sm font-medium mt-1.5 mb-0.5 text-accent-soft";
      out.push(<div key={`h-${i}`} className={cls}>{renderInline(h[2], `h${i}`)}</div>);
      i++; continue;
    }
    if (/^\s*[-*]\s+/.test(ln)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      out.push(
        <ul key={`ul-${i}`} className="list-disc list-inside space-y-0.5 my-1">
          {items.map((it, k) => <li key={k}>{renderInline(it, `li${i}${k}`)}</li>)}
        </ul>,
      );
      continue;
    }
    if (/^\s*\d+\.\s+/.test(ln)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      out.push(
        <ol key={`ol-${i}`} className="list-decimal list-inside space-y-0.5 my-1">
          {items.map((it, k) => <li key={k}>{renderInline(it, `oi${i}${k}`)}</li>)}
        </ol>,
      );
      continue;
    }
    if (/^>\s+/.test(ln)) {
      out.push(
        <blockquote key={`q-${i}`} className="border-l-2 border-accent/50 pl-3 text-muted my-1">
          {renderInline(ln.replace(/^>\s+/, ""), `q${i}`)}
        </blockquote>,
      );
      i++; continue;
    }
    if (ln.trim() === "") {
      out.push(<div key={`sp-${i}`} className="h-1.5" />);
      i++; continue;
    }
    out.push(<div key={`p-${i}`} className="my-0.5">{renderInline(ln, `p${i}`)}</div>);
    i++;
  }
  return <div className="whitespace-pre-wrap break-words">{out}</div>;
}
