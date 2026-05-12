import { Routes, Route, NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, Wallet, Boxes, Sparkles, Settings as Cog, BrainCircuit, MessageSquare, Camera, ListTodo, Target, LogOut,
} from "lucide-react";

import Dashboard from "./pages/Dashboard";
import Assets from "./pages/Assets";
import AssetDetail from "./pages/AssetDetail";
import SkillMarket from "./pages/SkillMarket";
import SettingsPage from "./pages/Settings";
import Advice from "./pages/Advice";
import AIChat from "./pages/AIChat";
import ImportOcr from "./pages/ImportOcr";
import Todos from "./pages/Todos";
import Targets from "./pages/Targets";
import RealizedRevenue from "./pages/RealizedRevenue";
import AuthPage from "./pages/Auth";
import { useAuth } from "./lib/auth";
import { AnalysisTaskProvider } from "./lib/analysisTask";


import { OcrTaskProvider } from "./lib/ocrTask";
import AnalysisTaskIndicator from "./components/AnalysisTaskIndicator";
import OcrTaskIndicator from "./components/OcrTaskIndicator";

const NAV = [
  { to: "/", label: "仪表盘", icon: LayoutDashboard, end: true },
  { to: "/assets", label: "我的资产", icon: Wallet },
  { to: "/targets", label: "我的标的", icon: Target },
  { to: "/import", label: "OCR 导入", icon: Camera },
  { to: "/chat", label: "AI Chat", icon: MessageSquare },
  { to: "/advice", label: "AI 分析我的资产", icon: BrainCircuit },
  { to: "/todos", label: "AI 投资建议", icon: ListTodo },
  { to: "/skills", label: "Skill 市场", icon: Boxes },
  { to: "/settings", label: "设置", icon: Cog },
];

export default function App() {
  const loc = useLocation();
  const { user, loading, logout } = useAuth();
  void loc;

  if (loading) {
    return <div className="min-h-screen bg-bg flex items-center justify-center text-muted">正在恢复登录状态…</div>;
  }
  if (!user) {
    return <AuthPage />;
  }

  return (

    <AnalysisTaskProvider>
      <OcrTaskProvider>
      <div className="min-h-screen flex bg-bg bg-grid-fade">
        <aside className="w-60 shrink-0 border-r border-line/60 bg-bg/60 backdrop-blur-md hidden md:flex flex-col">
          <div className="px-5 py-6 flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-accent to-emerald2 flex items-center justify-center shadow-glow">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide">EE Fund</div>
              <div className="text-[11px] text-muted">AI 资产管理平台</div>
            </div>
          </div>
          <nav className="px-3 mt-2 flex-1 space-y-1">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `group flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition
                   ${isActive
                     ? "bg-accent/15 text-white border border-accent/30 shadow-glow"
                     : "text-muted hover:text-white hover:bg-line/40 border border-transparent"}`
                }
              >
                <Icon className="w-4 h-4" />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="mx-3 mb-3 p-3 rounded-xl border border-line bg-bg-soft/60">
            <div className="text-xs text-white truncate">{user.username}</div>
            <button className="mt-2 text-[11px] text-muted hover:text-white inline-flex items-center gap-1" onClick={logout}>
              <LogOut className="w-3 h-3" /> 退出登录
            </button>
          </div>
          <div className="p-4 mx-3 mb-4 rounded-xl border border-line bg-bg-soft/60 text-[11px] text-muted leading-relaxed">
            ⚠️ 平台仅作研究参考，AI 输出不构成投资建议。
          </div>

        </aside>

        <main className="flex-1 min-w-0">
          <div className="max-w-[1400px] mx-auto px-6 py-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/assets" element={<Assets />} />
              <Route path="/targets" element={<Targets />} />
              <Route path="/realized-revenue" element={<RealizedRevenue />} />
              <Route path="/assets/:id" element={<AssetDetail />} />

              <Route path="/import" element={<ImportOcr />} />
              <Route path="/chat" element={<AIChat />} />
              <Route path="/advice" element={<Advice />} />
              <Route path="/todos" element={<Todos />} />
              <Route path="/skills" element={<SkillMarket />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </div>
        </main>

        {/* 全局分析任务悬浮指示器：任何页面进行中都可见 */}
        <AnalysisTaskIndicator />
        {/* OCR 任务悬浮指示器：识别中/完成待确认时全局可见 */}
        <OcrTaskIndicator />
      </div>
      </OcrTaskProvider>
    </AnalysisTaskProvider>
  );
}
