import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 启动脚本会通过环境变量 VITE_BACKEND_URL 把当前选定的后端端口告诉前端，
// 这样即便 Windows 上的"僵尸 LISTEN 套接字"导致默认 8000 不可用、
// 启动脚本自动改用 8001 / 8002 等替代端口，前端代理也能跟着切换。
//
// 端口选择策略和原因详见 start-dev.ps1::Find-FreePort。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendUrl = env.VITE_BACKEND_URL || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      // 端口被占就报错而不是悄悄换端口（启动脚本已经探测过空闲端口；
      // 走到这里仍然冲突说明并发启动了多个实例，应该让用户感知）
      strictPort: false,
      proxy: {
        "/api": {
          target: backendUrl,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
