import { ReactNode } from "react";
import { clsx } from "../lib/format";

interface Props {
  label: string;
  value: ReactNode;
  delta?: ReactNode;
  hint?: string;
  tone?: "default" | "success" | "danger" | "warn" | "accent";
}

const toneMap: Record<string, string> = {
  default: "text-white",
  success: "text-emerald2",
  danger: "text-rose2",
  warn: "text-amber2",
  accent: "text-accent-soft",
};

export default function StatCard({ label, value, delta, hint, tone = "default" }: Props) {
  return (
    <div className="card p-5">
      <div className="text-xs text-muted">{label}</div>
      <div className={clsx("mt-2 text-2xl font-semibold tracking-tight", toneMap[tone])}>
        {value}
      </div>
      {delta && <div className="mt-1 text-xs">{delta}</div>}
      {hint && <div className="mt-2 text-[11px] text-muted/80">{hint}</div>}
    </div>
  );
}
