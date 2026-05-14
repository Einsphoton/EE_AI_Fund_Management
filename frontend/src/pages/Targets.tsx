import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { BrainCircuit, Eye, Pencil, Plus, Sparkles, Target, Trash2 } from "lucide-react";
import toast from "react-hot-toast";

import PageHeader from "../components/PageHeader";
import AssetForm, { AssetFormData } from "../components/AssetForm";
import { Assets as AssetApi, Asset, AssetType } from "../api/client";
import { ASSET_TYPE_META } from "../lib/assetMeta";

type GroupMode = "asset_type" | "platform";
type TargetSource = "manual" | "ai";

interface TargetGroup {
  key: string;
  title: string;
  list: Asset[];
}

function targetSourceOf(a: Asset): TargetSource {
  const source = (a.target_source || "").toLowerCase();
  const note = a.note || "";
  if (source === "ai" || note.startsWith("AI加入标的池") || note.startsWith("AI推荐标的")) return "ai";
  return "manual";
}

function buildTargetGroups(targets: Asset[], groupMode: GroupMode): TargetGroup[] {
  if (groupMode === "platform") {
    const byPlatform: Record<string, Asset[]> = {};
    for (const a of targets) {
      const p = (a.platform || "未填写平台").trim() || "未填写平台";
      (byPlatform[p] ||= []).push(a);
    }
    return Object.keys(byPlatform)
      .sort((a, b) => a.localeCompare(b, "zh-CN"))
      .map((platform) => ({ key: `platform:${platform}`, title: platform, list: byPlatform[platform] }));
  }

  const byType: Record<string, Asset[]> = {};
  for (const a of targets) {
    const t = a.asset_type || "fund";
    (byType[t] ||= []).push(a);
  }
  return Object.keys(ASSET_TYPE_META)
    .sort((a, b) => ASSET_TYPE_META[a as AssetType].order - ASSET_TYPE_META[b as AssetType].order)
    .filter((t) => byType[t]?.length)
    .map((t) => ({ key: `type:${t}`, title: ASSET_TYPE_META[t as AssetType].label, list: byType[t] }));
}

export default function Targets() {
  const qc = useQueryClient();
  const assetsQuery = useQuery({
    queryKey: ["assets"],
    queryFn: AssetApi.list,
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
  });
  const assets = assetsQuery.data || [];
  const targets = assets.filter((a) => a.watch_only);

  const [groupMode, setGroupMode] = useState<GroupMode>("asset_type");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);

  const create = useMutation({
    mutationFn: (p: AssetFormData) => AssetApi.create({ ...p, watch_only: true }),
    onSuccess: () => {
      toast.success("已添加标的");
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const update = useMutation({
    mutationFn: ({ id, data }: { id: number; data: AssetFormData }) => AssetApi.update(id, { ...data, watch_only: true }),
    onSuccess: () => {
      toast.success("已更新标的");
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: AssetApi.remove,
    onSuccess: () => {
      toast.success("已删除标的");
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e.message),
  });

  const aiAdd = useMutation({
    mutationFn: () => AssetApi.aiTargets(5),
    onSuccess: (items) => {
      toast.success(items.length > 0 ? `AI 已更新/推荐 ${items.length} 个标的` : "AI 暂未找到需要更新的推荐标的");
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["holdings"] });
    },
    onError: (e: any) => toast.error(e.message || "AI 更新推荐标的失败"),
  });

  const columns = useMemo(() => {
    const manualTargets = targets.filter((a) => targetSourceOf(a) === "manual");
    const aiTargets = targets.filter((a) => targetSourceOf(a) === "ai");
    return [
      {
        key: "manual",
        title: "用户手动添加的标的",
        subtitle: "你主动加入观察池的标的，AI 不会覆盖这些记录。",
        count: manualTargets.length,
        groups: buildTargetGroups(manualTargets, groupMode),
      },
      {
        key: "ai",
        title: "AI 推荐的标的",
        subtitle: "由 AI 按投资性格和预算推荐；每次推荐会刷新已有 AI 标的。",
        count: aiTargets.length,
        groups: buildTargetGroups(aiTargets, groupMode),
      },
    ];
  }, [targets, groupMode]);

  const submit = async (data: AssetFormData) => {
    if (editing) await update.mutateAsync({ id: editing.id, data });
    else await create.mutateAsync(data);
  };

  return (
    <>
      <PageHeader
        title="我的标的"
        subtitle="维护观察池：手动添加或让 AI 根据投资性格更新推荐标的；AI 投资建议会参考这里。"
        actions={
          <>
            <div className="inline-flex rounded-xl border border-line bg-bg-soft/40 p-0.5 text-xs">
              <button
                className={`px-3 py-1.5 rounded-lg transition ${groupMode === "asset_type" ? "bg-accent/15 text-white border border-accent/40" : "text-muted hover:text-white"}`}
                onClick={() => setGroupMode("asset_type")}
              >按类型</button>
              <button
                className={`px-3 py-1.5 rounded-lg transition ${groupMode === "platform" ? "bg-accent/15 text-white border border-accent/40" : "text-muted hover:text-white"}`}
                onClick={() => setGroupMode("platform")}
              >按平台</button>
            </div>
            <button className="btn" disabled={aiAdd.isPending} onClick={() => aiAdd.mutate()}>
              <BrainCircuit className="w-4 h-4" /> {aiAdd.isPending ? "AI 更新中…" : "AI 更新推荐标的"}
            </button>
            <button className="btn-primary" onClick={() => { setEditing(null); setOpen(true); }}>
              <Plus className="w-4 h-4" /> 手动添加标的
            </button>
          </>
        }
      />

      {(assetsQuery.isLoading || assetsQuery.isFetching) && (
        <div className="card p-3 mb-4 text-xs text-muted flex items-center justify-between gap-3">
          <span>{assetsQuery.isLoading ? "正在加载标的清单…" : "标的已先显示，正在后台刷新最新清单…"}</span>
          <span className="text-accent-soft">已显示 {targets.length} 个标的</span>
        </div>
      )}

      {targets.length === 0 && !assetsQuery.isLoading ? (
        <div className="card p-12 text-center text-muted">
          <Target className="w-8 h-8 mx-auto mb-3 text-muted/60" />
          还没有观察标的。可以手动添加，或点击右上角让 AI 更新推荐。
        </div>
      ) : (
        <div className="grid lg:grid-cols-2 gap-5 items-start">
          {columns.map((col) => (
            <section key={col.key} className="card overflow-hidden">
              <div className="px-5 py-4 border-b border-line/60 bg-bg-soft/30">
                <h2 className="font-semibold flex items-center gap-2">
                  {col.key === "ai" ? <Sparkles className="w-4 h-4 text-accent" /> : <Target className="w-4 h-4 text-accent" />}
                  {col.title}
                  <span className="text-xs text-muted ml-1">({col.count})</span>
                </h2>
                <p className="text-[11px] text-muted mt-1 leading-relaxed">{col.subtitle}</p>
              </div>
              {col.groups.length === 0 ? (
                <div className="p-8 text-center text-sm text-muted">
                  暂无{col.key === "ai" ? " AI 推荐" : "手动添加"}标的。
                </div>
              ) : (
                <div className="space-y-4 p-4">
                  {col.groups.map((g) => (
                    <TargetGroupTable
                      key={`${col.key}:${g.key}`}
                      group={g}
                      source={col.key as TargetSource}
                      onEdit={(a) => { setEditing(a); setOpen(true); }}
                      onRemove={(a) => confirm(`确认删除标的 ${a.name}？`) && remove.mutate(a.id)}
                    />
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      )}

      <AssetForm
        open={open}
        onClose={() => setOpen(false)}
        onSubmit={submit}
        initial={editing}
        initialDraft={{ watch_only: true }}
        title={editing ? "编辑标的" : "添加标的"}
        editing={!!editing}
      />
    </>
  );
}

function TargetGroupTable({ group, source, onEdit, onRemove }: {
  group: TargetGroup;
  source: TargetSource;
  onEdit: (asset: Asset) => void;
  onRemove: (asset: Asset) => void;
}) {
  return (
    <div className="rounded-xl border border-line/70 overflow-hidden bg-bg/30">
      <div className="flex items-center justify-between px-4 py-2.5 bg-bg-soft/40 border-b border-line/50">
        <h3 className="text-sm font-medium flex items-center gap-2">
          <Target className="w-3.5 h-3.5 text-accent" /> {group.title}
          <span className="text-[10px] text-muted">({group.list.length})</span>
        </h3>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs text-muted bg-bg-soft/50">
          <tr>
            <th className="text-left px-4 py-3">标的</th>
            <th className="text-left px-4 py-3">平台</th>
            <th className="text-left px-4 py-3">类型</th>
            <th className="text-right px-4 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          {group.list.map((a) => (
            <tr key={a.id} className="border-t border-line/60 hover:bg-line/15 transition">
              <td className="px-4 py-3">
                <div className="font-medium flex items-center gap-1.5">
                  {a.name}
                  {source === "ai" && <Sparkles className="w-3.5 h-3.5 text-accent opacity-70" />}
                </div>
                <div className="text-[11px] text-muted">{a.code} · {a.market}</div>
                {a.note && <div className="text-[11px] text-muted/80 mt-1 line-clamp-1">{a.note}</div>}
              </td>
              <td className="px-4 py-3 text-muted">{a.platform || "—"}</td>
              <td className="px-4 py-3 text-muted">{ASSET_TYPE_META[a.asset_type]?.label || a.asset_type}</td>
              <td className="text-right px-4 py-3">
                <div className="inline-flex gap-1">
                  <Link to={`/assets/${a.id}`} className="btn !px-2 !py-1.5" title="详情页">
                    <Eye className="w-3.5 h-3.5" />
                  </Link>
                  <button className="btn !px-2 !py-1.5" title="编辑" onClick={() => onEdit(a)}>
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                  <button
                    className="btn-danger !px-2 !py-1.5"
                    title="删除"
                    onClick={() => onRemove(a)}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

