import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

interface Props {
  content: string;
  /** 紧凑模式：用于卡片/行内展示，文字稍小、行距稍紧 */
  compact?: boolean;
  className?: string;
}

/**
 * 统一的 Markdown 渲染器：
 * - 支持 GFM（表格、删除线、任务列表、URL 自动识别）
 * - 外链统一 `target="_blank" rel="noreferrer noopener"`
 * - 默认禁用原始 HTML，防止注入
 * - 深色主题内嵌样式，不依赖额外 CSS 文件
 */
export default function MarkdownView({ content, compact = false, className = "" }: Props) {
  const base = compact ? "text-[12px] leading-relaxed" : "text-sm leading-relaxed";

  const components: Components = {
    p: ({ children }) => <p className="my-1.5 text-white/95">{children}</p>,
    strong: ({ children }) => (
      <strong className="text-white font-semibold">{children}</strong>
    ),
    em: ({ children }) => <em className="text-accent-soft">{children}</em>,
    ul: ({ children }) => (
      <ul className="my-1.5 pl-4 list-disc marker:text-accent/70 space-y-0.5">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="my-1.5 pl-5 list-decimal marker:text-accent/70 space-y-0.5">{children}</ol>
    ),
    li: ({ children }) => <li className="text-white/90">{children}</li>,
    h1: ({ children }) => <h3 className="mt-2 mb-1 text-base font-semibold text-white">{children}</h3>,
    h2: ({ children }) => <h3 className="mt-2 mb-1 text-base font-semibold text-white">{children}</h3>,
    h3: ({ children }) => <h4 className="mt-2 mb-1 text-sm font-semibold text-white">{children}</h4>,
    h4: ({ children }) => <h4 className="mt-2 mb-1 text-sm font-semibold text-white/95">{children}</h4>,
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-accent/60 pl-3 text-muted italic">
        {children}
      </blockquote>
    ),
    code: (props) => {
      const { className: cn, children, ...rest } = props as {
        className?: string;
        children?: React.ReactNode;
        inline?: boolean;
      };
      const isBlock = /language-/.test(cn || "");
      if (!isBlock) {
        return (
          <code className="px-1 py-0.5 rounded bg-bg-soft/70 border border-line/40 font-mono text-[0.85em] text-accent-soft" {...rest}>
            {children}
          </code>
        );
      }
      return (
        <pre className="my-2 p-2.5 rounded-lg bg-bg/70 border border-line/40 overflow-x-auto">
          <code className="font-mono text-[12px] text-white/90" {...rest}>
            {children}
          </code>
        </pre>
      );
    },
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        className="text-accent-soft hover:text-accent underline underline-offset-2"
      >
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className="my-2 overflow-x-auto rounded-lg border border-line/40">
        <table className="w-full text-[12px]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="bg-bg-soft/60">{children}</thead>,
    th: ({ children }) => (
      <th className="px-2 py-1.5 text-left font-medium text-white border-b border-line/40">{children}</th>
    ),
    td: ({ children }) => (
      <td className="px-2 py-1.5 text-white/85 border-b border-line/20">{children}</td>
    ),
    hr: () => <hr className="my-3 border-line/40" />,
  };

  return (
    <div className={`markdown-view ${base} ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content || ""}
      </ReactMarkdown>
    </div>
  );
}
