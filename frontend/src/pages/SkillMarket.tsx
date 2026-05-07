import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Boxes, Check, Download, ExternalLink, Power, Search, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { useState } from "react";

import PageHeader from "../components/PageHeader";
import { Skills, MarketSkill } from "../api/client";

export default function SkillMarket() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const installed = useQuery({ queryKey: ["skills", "installed"], queryFn: Skills.installed });
  const market = useQuery({
    queryKey: ["skills", "market", q],
    queryFn: () => Skills.marketplace(q),
  });

  const installMut = useMutation({
    mutationFn: Skills.install,
    onSuccess: () => {
      toast.success("已安装");
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: any) => toast.error(e.message),
  });
  const uninstallMut = useMutation({
    mutationFn: Skills.uninstall,
    onSuccess: () => { toast.success("已卸载"); qc.invalidateQueries({ queryKey: ["skills"] }); },
  });
  const toggleMut = useMutation({
    mutationFn: ({ id, en }: { id: string; en: boolean }) => Skills.toggle(id, en),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills", "installed"] }),
  });

  const installedIds = new Set((installed.data || []).map((s) => s.skill_id));

  return (
    <>
      <PageHeader
        title="Skill 市场"
        subtitle="集成 skillhub.cloud.tencent.com 财经类 Skill。已默认安装 Stock Analysis 与 Tushare Finance"
        actions={
          <a
            href="https://skillhub.cloud.tencent.com/"
            target="_blank"
            rel="noreferrer"
            className="btn"
          >
            <ExternalLink className="w-4 h-4" /> 打开 SkillHub
          </a>
        }
      />

      <div className="card p-4 mb-5">
        <h3 className="font-semibold mb-3 flex items-center gap-2">
          <Check className="w-4 h-4 text-emerald2" /> 已安装（{(installed.data || []).length}）
        </h3>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(installed.data || []).map((s) => (
            <div key={s.skill_id} className="rounded-xl border border-line p-4 hover:border-accent/50 transition">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="font-medium">{s.name}</div>
                  <div className="text-[11px] text-muted">{s.skill_id} · {s.source}</div>
                </div>
                <span className={`badge ${s.enabled ? "border-emerald2/40 text-emerald2 bg-emerald2/10" : ""}`}>
                  {s.enabled ? "启用" : "停用"}
                </span>
              </div>
              <p className="text-xs text-muted mt-2 line-clamp-3 min-h-[3em]">{s.description}</p>
              <div className="flex gap-2 mt-3">
                <button
                  className="btn !px-2 !py-1.5 text-xs"
                  onClick={() => toggleMut.mutate({ id: s.skill_id, en: !s.enabled })}
                >
                  <Power className="w-3.5 h-3.5" />
                  {s.enabled ? "停用" : "启用"}
                </button>
                <button
                  className="btn-danger !px-2 !py-1.5 text-xs"
                  onClick={() => confirm(`卸载 ${s.name}?`) && uninstallMut.mutate(s.skill_id)}
                >
                  <Trash2 className="w-3.5 h-3.5" /> 卸载
                </button>
              </div>
            </div>
          ))}
          {(installed.data || []).length === 0 && (
            <div className="col-span-full text-center text-muted text-sm py-6">尚未安装任何 Skill</div>
          )}
        </div>
      </div>

      <div className="card p-4">
        <div className="flex items-center justify-between mb-3 gap-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Boxes className="w-4 h-4 text-accent" /> 推荐 / 可安装
          </h3>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input !pl-9 w-64"
              placeholder="搜索财经 Skill"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(market.data || []).map((s: MarketSkill) => {
            const inst = installedIds.has(s.skill_id);
            return (
              <div key={s.skill_id} className="rounded-xl border border-line p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-medium">{s.name}</div>
                    <div className="text-[11px] text-muted">{s.skill_id} · {s.source}</div>
                  </div>
                  {s.default && <span className="badge border-accent/40 text-accent-soft">默认</span>}
                </div>
                <p className="text-xs text-muted mt-2 line-clamp-3 min-h-[3em]">{s.description}</p>
                <button
                  className={inst ? "btn !px-3 !py-1.5 text-xs mt-3 w-full opacity-60 cursor-not-allowed" : "btn-primary !px-3 !py-1.5 text-xs mt-3 w-full"}
                  disabled={inst}
                  onClick={() => installMut.mutate({
                    skill_id: s.skill_id, name: s.name,
                    description: s.description, category: s.category, source: s.source,
                  })}
                >
                  <Download className="w-3.5 h-3.5" />
                  {inst ? "已安装" : "安装"}
                </button>
              </div>
            );
          })}
          {(market.data || []).length === 0 && !market.isLoading && (
            <div className="col-span-full text-center text-muted text-sm py-6">无匹配 Skill</div>
          )}
        </div>
      </div>
    </>
  );
}
