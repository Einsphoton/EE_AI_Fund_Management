import { FormEvent, useState } from "react";
import { Eye, EyeOff, LockKeyhole, Sparkles, UserPlus } from "lucide-react";
import toast from "react-hot-toast";
import { useAuth } from "../lib/auth";

export default function AuthPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username, password);
        toast.success("登录成功");
      } else {
        await register(username, password, email || undefined);
        toast.success("注册成功");
      }
    } catch (err: any) {
      toast.error(err?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-bg bg-grid-fade flex items-center justify-center px-4">
      <div className="w-full max-w-md card p-7 relative overflow-hidden">
        <div className="absolute -top-24 -right-24 w-56 h-56 rounded-full bg-accent/20 blur-3xl" />
        <div className="relative">
          <div className="flex items-center gap-3 mb-8">
            <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-accent to-emerald2 flex items-center justify-center shadow-glow">
              <Sparkles className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="text-lg font-semibold">EE Fund</div>
              <div className="text-xs text-muted">登录后管理你的独立资产与设置</div>
            </div>
          </div>

          <div className="grid grid-cols-2 p-1 rounded-xl bg-bg-soft border border-line mb-6">
            <button
              type="button"
              className={`py-2 rounded-lg text-sm transition ${mode === "login" ? "bg-accent/20 text-white" : "text-muted hover:text-white"}`}
              onClick={() => setMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              className={`py-2 rounded-lg text-sm transition ${mode === "register" ? "bg-accent/20 text-white" : "text-muted hover:text-white"}`}
              onClick={() => setMode("register")}
            >
              注册
            </button>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="label">用户名</label>
              <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="3-32 位字母/数字" autoComplete="username" required />
            </div>
            {mode === "register" && (
              <div>
                <label className="label">邮箱（可选）</label>
                <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" autoComplete="email" />
              </div>
            )}
            <div>
              <label className="label">密码</label>
              <div className="relative">
                <input
                  className="input pr-10"
                  type={showPwd ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="至少 6 位"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  required
                />
                <button type="button" className="absolute right-3 top-2.5 text-muted hover:text-white" onClick={() => setShowPwd((v) => !v)}>
                  {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
            <button className="btn-primary w-full !py-2.5" disabled={busy}>
              {mode === "login" ? <LockKeyhole className="w-4 h-4" /> : <UserPlus className="w-4 h-4" />}
              {busy ? "处理中…" : mode === "login" ? "登录" : "创建账号"}
            </button>
          </form>

          <p className="mt-5 text-xs text-muted leading-relaxed">
            每个账号会看到独立的资产、交易记录、OCR 导入结果、AI 建议以及设置数据。首个注册账号会自动接管升级前的本地数据。
          </p>
        </div>
      </div>
    </div>
  );
}
