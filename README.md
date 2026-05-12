# EE AI Fund Management

> 一站式个人基金 / 股票资产管理 + AI 投资建议平台。本地可跑、NAS 可部署、支持任意 OpenAI 兼容大模型（DeepSeek / OpenAI / 自建 Ollama / LM Studio / oMLX）。

---

## ✨ 功能亮点

### 📊 资产管理
- 支持 **OTC 场外基金 / A 股 / 港股 / 美股 / 场内基金 ETF** 五类标的
- 每一笔**买入、追加、卖出**完整记录，自动计算持仓成本、浮盈浮亏、累计手续费
- 支持 **「仅观察」** 模式：关注但未买入的标的也能参与 AI 分析
- **批量导入/编辑交易记录**，详情页内直接改

### 📈 行情可视化
- 联网抓取 OTC 基金净值 / A 股 / 港股 / 美股 **K 线** + 技术指标
- 交易点位直接在 K 线上高亮显示（买入 / 卖出 / 加仓 / 减仓）
- 个股详情页自动展示估值快照：PE-TTM / PB / 市值 / 52 周高低点 / 振幅 / 换手率……

### 🧠 内置 AI Agent（Hermes-Lite）
- 定时或手动触发分析，产出**结构化建议 JSON**：action / confidence / score / 基本面 / 宏观 / 微观 / 风险 / pros / advice / target_price / stop_loss
- **批量并发分析**（可调 1-16 路），单次批量几十个标的 1-2 分钟搞定
- **SSE 流式进度**：每个标的完成立刻推送到前端，不用等全部跑完
- **启发式回退**：LLM 不可用 / 解析失败时自动退回基于 MA/盈亏阈值的规则建议，Agent 永远能出结果
- **Skill 机制**：每个 Skill = 一段 system prompt 片段，可组合多个 Skill 让 Agent 变成多因子专家

### 🎭 投资者性格（7 种预设）
选了哪种性格，**会直接改变 AI 建议的倾向**（action / 止盈止损位 / 仓位变动幅度）：

| 性格 | 定位 |
| --- | --- |
| 均衡型（默认） | 进攻防守兼顾，不偏科 |
| 稳健型 | 本金安全第一，拒绝大回撤 |
| 进攻型 | 接受高波动，追逐超额收益 |
| 收息养老型 | 分红/票息为核心，稳定现金流 |
| 成长型 | 长线陪伴好公司，享受复利 |
| 价值型 | 低估买入、估值回归卖出 |
| 短线交易型 | 快进快出，吃趋势或吃反转 |

### 📝 分析报告风格（专业 / 新手）
- **专业模式**：保留 MA / MACD / RSI / PE-TTM / 戴维斯双击等术语，精炼直接
- **新手模式**：全部翻译成大白话，强制附带「怎么做 + 多少仓位」的行动建议，禁止"梭哈""清仓"等激进用词

### 💰 DCA 定投助手
- 自动给出当期建议定投金额 + 份额 + 预估手续费
- 基于 MA20/MA60/MA250 的 **偏离度 + 趋势因子** 双打分：偏离越深、趋势越好，**加码买入**；反之少买甚至跳过

### 💬 AI 对话
- 上下文可选：全部持仓 / 单个标的 / 某条建议
- 流式输出（SSE），断线自动重连
- 走同一个 LLM 配置，不需要再填一次

### 🛒 Skill 市场
- 对接 [skillhub.cloud.tencent.com](https://skillhub.cloud.tencent.com/)，筛选财经类 Skill 一键安装
- 内置 `stock-analysis` / `tushare-finance`，开箱即用
- 启用 / 停用 / 卸载 均在前端完成

### 🔐 Cloudflare Access 适配
- 自建 LLM API 走 Cloudflare Tunnel + Zero Trust 时，在设置页填 `CF-Access-Client-Id / Secret`，请求自动注入
- 支持按域名白名单注入（避免把 Token 误发给 DeepSeek/OpenAI）
- 带诊断面板：401 / 403 / 重定向死循环全部给出可操作的排错提示

### 🐳 一键部署
- 单镜像 `docker-compose up -d`
- 数据库 + Skill 安装目录均通过 volume 持久化
- 内置 `/api/health` healthcheck

---

## 📂 目录结构

```
EE_AI_Fund_Management/
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py               # 基础配置
│   │   ├── models.py               # ORM
│   │   ├── schemas.py              # Pydantic
│   │   ├── database.py             # SQLAlchemy engine
│   │   ├── scheduler.py            # APScheduler 定时任务
│   │   ├── api/                    # REST 路由
│   │   │   ├── assets.py
│   │   │   ├── quotes.py
│   │   │   ├── advice_api.py       # /api/advice + SSE 流式
│   │   │   ├── chat_api.py         # /api/chat/stream
│   │   │   ├── dca_api.py
│   │   │   ├── settings_api.py     # /api/settings + /profiles
│   │   │   └── skills_api.py
│   │   ├── services/               # 业务逻辑
│   │   │   ├── quotes.py           # 行情抓取
│   │   │   ├── holdings.py         # 持仓计算
│   │   │   ├── snapshot.py         # 估值快照
│   │   │   ├── dca.py              # 定投打分
│   │   │   ├── chat.py
│   │   │   ├── settings_service.py
│   │   │   └── skills_service.py
│   │   └── agent/                  # Hermes-Lite Agent
│   │       ├── hermes.py           # prompt + LLM 调用 + JSON 解析兜底
│   │       ├── analyzer.py         # 并发 / SSE 流式批量分析
│   │       └── profiles.py         # 投资性格 + 报告风格预设
│   └── requirements.txt
├── frontend/                       # React 18 + Vite + TS + Tailwind + ECharts
│   └── src/
│       ├── pages/                  # Dashboard / Assets / AssetDetail / Advice / AIChat / SkillMarket / Settings
│       ├── components/             # 图表 / 模态框 / 各类卡片
│       ├── api/client.ts           # axios + SSE
│       └── main.tsx
├── docker/                         # Dockerfile + Nginx 配置
├── docker-compose.yml
├── start-dev.cmd / .ps1 / .sh      # 本地一键调试脚本
└── README.md
```

---

## 🚀 一键部署到 NAS

### 方式 A：源码构建（适合开发 / 自用调试）

```bash
git clone <repo>
cd EE_AI_Fund_Management

# （可选）把 Cloudflare Access Service Token 放进 .env
# CF_ACCESS_CLIENT_ID=xxx.access
# CF_ACCESS_CLIENT_SECRET=xxxxxxxx
# CF_ACCESS_HOSTS=einsphoton.ren

docker compose up -d
```

打开 `http://<nas-ip>:8888` 即可访问。数据库文件、Skill 安装目录都在 `./data` 和 `./skills_installed`，升级时不会丢。

### 方式 B：Docker Hub 镜像（推荐给绿联 NAS GUI）

绿联 NAS 的 Docker GUI 如果默认只认 `hub.docker.com`，建议把 GitHub Actions 发布到 Docker Hub：

1. 在 Docker Hub 创建仓库：`<your-dockerhub-username>/ee-fund-management`
2. 在 GitHub 仓库 `Settings → Secrets and variables → Actions` 添加：
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`（Docker Hub Access Token）
3. 推送到 `main` 或打 tag（如 `v1.0.0`），Actions 会发布：
   - `docker.io/<your-dockerhub-username>/ee-fund-management:latest`
   - `docker.io/<your-dockerhub-username>/ee-fund-management:<version>`

绿联 Docker GUI 中创建容器时：

| 配置项 | 值 |
| --- | --- |
| 镜像 | `docker.io/<your-dockerhub-username>/ee-fund-management:latest` |
| 端口 | `8888:8000` |
| 数据目录 | `/你的NAS路径/data:/app/data` |
| Skill 目录 | `/你的NAS路径/skills_installed:/app/skills_installed` |
| 环境变量 | `TZ=Asia/Shanghai`，可选填 `CF_ACCESS_*` |

以后在绿联 GUI 里点「更新/拉取最新镜像」并重建容器即可，数据会保留在映射目录里。

> 如果你的绿联 Docker 支持新增镜像仓库，也可以把 `ghcr.io` 加进去后使用 GitHub Container Registry；但为了兼容 GUI，默认推荐 Docker Hub。

### 方式 C：网页内点击更新（Watchtower）

如果希望在本 APP 的「设置 → 在线更新」里点按钮完成更新，使用 Docker Hub 镜像启动，并在 `.env` 中配置：

```bash
EE_FUND_IMAGE=docker.io/<your-dockerhub-username>/ee-fund-management:latest
UPDATE_DOCKERHUB_REPO=<your-dockerhub-username>/ee-fund-management
UPDATE_ENABLE_WEB_TRIGGER=true
UPDATE_WATCHTOWER_TOKEN=<生成一个足够长的随机字符串>
```

然后启动主容器和 Watchtower：

```bash
docker compose --profile update up -d
```

说明：
- 「在线更新」会先查询 Docker Hub 最新 tag，再通过 Watchtower HTTP API 触发拉取镜像和重启容器。
- Watchtower 需要挂载 Docker socket，只建议在可信内网使用；不要把更新接口暴露到公网。
- 如果只想用绿联 Docker GUI 更新，可以不启用 `UPDATE_ENABLE_WEB_TRIGGER` 和 Watchtower。

---


## 🧪 本地调试

### Windows

**推荐方式：双击 `start-dev.cmd`** —— 自动以 `Bypass` 执行策略调用 PowerShell 脚本，不改系统设置。

其他方式：
```powershell
# A. 只对当前进程放开（最安全）
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-dev.ps1

# B. 对当前用户永久放开（一次即可）
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\start-dev.ps1
```

> 首次运行报「禁止运行脚本」是 Windows 默认 `ExecutionPolicy=Restricted` 导致，用上面任一方式即可绕过。

### macOS / Linux

```bash
# A. 加执行权限再跑（推荐）
chmod +x start-dev.sh
./start-dev.sh

# B. 直接用 bash 跑，不需要权限
bash start-dev.sh
```

常见坑：
- `permission denied`：还没 `chmod +x`
- `bad interpreter: /usr/bin/env^M`：脚本是 Windows 行尾，跑 `sed -i '' $'s/\r$//' start-dev.sh`
- `cannot be opened because it is from an unidentified developer`：跑 `xattr -d com.apple.quarantine start-dev.sh`

脚本会自动：
1. 检测 Python 3 / Node.js（缺失给出安装引导）
2. 创建后端虚拟环境并装依赖
3. 启动后端（uvicorn, `:8000`）和前端（vite, `:5173`）
4. 自动打开浏览器

---

## 🤖 大模型配置

进入「设置」页可以配置以下内容。所有模型都走 **OpenAI 兼容协议**，无需改代码。

### 基础参数

| 字段 | 说明 | 示例 |
| --- | --- | --- |
| API Base URL | 以 `/v1` 结尾 | `https://api.deepseek.com/v1` |
| API Key | 本地模型随便填 | `sk-xxxxx` |
| Model | 具体模型名 | `deepseek-chat` / `qwen3:14b` |
| Temperature | 采样温度，结构化输出建议 0.2-0.5 | `0.4` |
| 并发度 | 批量分析时同时跑几个标的 | 云端 3-6，自建 1-2 |
| Max Tokens | 单次输出上限（0=不限） | `800` 够用 |
| Timeout | 单次调用超时（秒） | 本地 Ollama 建议 180 |

点**"测试连接 / 列出可用模型"**会直接去 `/v1/models` 探测，成功后可以点击返回的模型名一键填入。

### 内置预设

页面顶部提供 5 个一键预设：

| 预设 | Base URL | Model |
| --- | --- | --- |
| **DeepSeek** | `https://api.deepseek.com/v1` | `deepseek-chat` |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini` |
| **Ollama（本地）** | `http://<ip>:11434/v1` | `qwen3:14b` |
| **LM Studio（本地）** | `http://<ip>:1234/v1` | `local-model` |
| **oMLX（本地·Apple Silicon）** | `http://127.0.0.1:8080/v1` | `mlx-community/Qwen3-14B-Instruct-4bit` |

> 预设里的模型名只是 **占位示例**，实际请填你本地拉取/加载的最新模型（如 Qwen3.6、Gemma 4、GLM-4.5 等新一代）。点**"测试连接 / 列出可用模型"**后页面会返回你服务里真实可用的模型列表，点一下就能填进去。

### 本地模型推荐（按显存档位，模型名以你本地实际拉取的为准）

本应用对模型的真实诉求：**稳定输出 JSON + 中文金融理解**，不需要超长上下文、不需要 reasoning。按这个目标，各档位推荐思路如下：

| 显存档位 | 推荐方向 | 备注 |
| --- | --- | --- |
| 24 GB（4090 / 3090 / 7900XTX） | **30B 级 MoE**（激活 3B 左右的新一代 Qwen MoE 最优）<br>或同档位 Dense 30B（Qwen / Gemma 最新系列） | MoE 速度接近 7B，质量接近 30B，综合最划算；本地金融中文首选 Qwen 系列 |
| 16 GB（4080 / 4060Ti 16G） | **14B 级 Dense**（Qwen / Gemma 最新一代 14B） | 可开更高并发；Q5_K_M 显存压力小 |
| 12 GB（3060 12G / 4070） | **8B 级 Dense**（Qwen 最新 8B） | JSON 稳定性边缘，建议并发 1 |
| Apple Silicon（M2/M3/M4 统一内存） | **oMLX + 4bit 量化的 14B-30B 系列** | 走 Metal，实测比 llama.cpp 快一截 |

> 📌 模型迭代比 README 快，本文不锁定具体版本号。实操顺序是：
> 1. 先去 Ollama / LM Studio / oMLX / mlx-community 的官方列表，拉最新一代**中文强、有 Instruct 微调**的版本（Qwen3.6 / Gemma 4 / GLM-4.5 等）
> 2. Q4_K_M 起步，够吃得下再换 Q5_K_M / Q6_K
> 3. 本应用 JSON 结构化输出依赖强，**别选 reasoning / thinking 模型**（推理链会吃光 `max_tokens=800` 的预算，JSON 写不完）
> 4. 先用"测试连接"验证模型名和端点，再批量跑

启动本地服务（示例）：

```bash
# Ollama
ollama pull <latest-qwen-or-gemma-tag>
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# oMLX（Apple Silicon 专用，OpenAI 兼容 HTTP 服务）
# 具体命令以 oMLX 版本而定，大体是启动一个 8080 端口的 /v1 兼容服务
omlx serve --model <mlx-community/xxx-4bit> --host 0.0.0.0 --port 8080
```

> 📝 Ollama 默认只监听 `127.0.0.1`，跨机器访问务必设 `OLLAMA_HOST=0.0.0.0:11434` 再重启。

### Cloudflare Access（自建 API 专用）

如果你的自建 LLM 走 Cloudflare Tunnel + Zero Trust：

1. Zero Trust → Access → Applications → 该应用的策略 Action 必须是 **Service Auth**
2. 生成 Service Token，把 Client ID / Secret 填到设置页「Cloudflare Access」折叠区
3. 用"仅对这些域名注入"限制注入范围（**防止把 Token 误发给 DeepSeek/OpenAI**）
4. 也可以通过 `.env` / 环境变量 `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` / `CF_ACCESS_HOSTS` 注入

诊断面板会区分 401 / 403 / 重定向死循环 / 登录页跳转等情况，逐一给出修复建议。

---

## 🪵 运行日志与 NAS 排障

部署到 NAS / Docker 后，应用会同时输出到 `docker logs` 和持久化文件：

- `data/logs/app.log`：全量结构化运行日志（JSON Lines）
- `data/logs/ai.log`：AI Chat、资产分析、OCR、推荐标的、投资经理等 AI 专用日志
- `data/logs/errors.log`：错误级别日志

前端进入「运行日志」页面可以：

1. 查看 `ai.log` / `app.log` 最新内容；
2. 复制最近几百行发给排障助手；
3. 一键导出 `ee-fund-diagnostics.zip`（含脱敏配置和日志文件）。

Docker 环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | 日志级别，排障可临时设 `DEBUG` |
| `LOG_MAX_BYTES` | `10485760` | 单个日志文件最大字节数，默认 10MB |
| `LOG_BACKUP_COUNT` | `5` | 每类日志保留的轮转文件数 |

日志会自动脱敏 `api_key`、`Authorization`、`token`、`secret`、`password` 等字段。

---

## 📊 AI 建议字段说明


每次分析会落库一条 `Advice` 记录，结构如下：

```jsonc
{
  "action": "buy | hold | sell",
  "confidence": 0.0-1.0,
  "summary": "30 字以内一句话结论",
  "score": {
    "technical": 0-100,     // 技术面
    "fundamental": 0-100,   // 基本面
    "sentiment": 0-100,     // 情绪/资金面
    "risk": 0-100           // 风险（越高越危险）
  },
  "fundamentals": "80 字基本面摘要",
  "macro": "80 字宏观因素",
  "micro": "80 字微观信号",
  "pros": ["优势 1", "优势 2"],
  "risks": ["风险 1", "风险 2"],
  "advice": "100 字具体操作建议：仓位 / 节奏 / 止盈止损",
  "time_horizon": "short | mid | long",
  "target_price": 数字或 null,
  "stop_loss": 数字或 null
}
```

这些字段会在「AI 建议」页的富卡片里直接可视化（雷达图 / 优势风险对比 / 价位柱 / 建议文案折叠区）。

---

## 🔌 关键 REST API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/assets` | 资产列表 |
| GET | `/api/assets/summary/all` | 所有持仓汇总（含浮盈浮亏） |
| GET | `/api/quotes/asset/{id}` | 资产行情 + 交易标注 |
| GET | `/api/quotes/asset/{id}/snapshot` | 估值快照 |
| POST | `/api/advice/run/{asset_id}` | 单标的立即分析 |
| POST | `/api/advice/run-all` | 批量分析（同步） |
| POST | `/api/advice/run-all/stream` | **批量分析（SSE 流式进度）** |
| GET | `/api/advice?source=batch\|single` | 列出最近建议 |
| GET | `/api/dca/suggest/{id}` | 定投建议 |
| POST | `/api/chat/stream` | 流式对话 |
| GET | `/api/settings` | 读取全部配置 |
| PUT | `/api/settings/{key}` | 更新某一项配置 |
| POST | `/api/settings/test-ai` | 测试 LLM 连接 + 列出可用模型 |
| GET | `/api/settings/profiles` | 列出投资性格 / 报告风格预设 |
| GET | `/api/skills/marketplace?q=` | SkillHub 搜索 |
| POST | `/api/skills/install` | 安装 Skill |

SSE 事件类型参见 `frontend/src/api/client.ts` 中的 `RunAllEvent`：
`start` → `asset_start` → `log` → `asset_done` / `asset_error` → `done`。

---

## 🛠️ 技术栈

**后端**：Python 3.11+ / FastAPI / SQLAlchemy 2 / SQLite / APScheduler / httpx / OpenAI SDK
**前端**：React 18 / Vite 5 / TypeScript / Tailwind / ECharts / React Query / react-hot-toast
**部署**：Docker 多阶段构建（前端静态产物由 Nginx 托管；后端 uvicorn；通过反向代理统一 8000 端口）

---

## ⚠️ 风险提示

> 本平台仅作为**投研参考工具**，所有 AI 输出**不构成投资建议**。
> 市场有风险，投资需谨慎，所有决策请独立判断、风险自负。
