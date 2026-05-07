#!/usr/bin/env bash
# EE AI Fund Management - macOS / Linux 本地一键启动脚本
# 用法：
#   chmod +x start-dev.sh && ./start-dev.sh
#   或： bash start-dev.sh

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'

# ---- 平台感知：macOS / Linux / Windows-NTFS-shared ----
OS_NAME="$(uname -s)"
case "$OS_NAME" in
  Darwin)  PLATFORM_TAG="mac"   ;;
  Linux)   PLATFORM_TAG="linux" ;;
  *)       PLATFORM_TAG="posix" ;;
esac

VENV_DIR="backend/.venv-${PLATFORM_TAG}"
NODE_MODULES_DIR="frontend/node_modules"   # node_modules 平台无关，但 NTFS 上仍会变慢

# 检测是否运行在 Windows 共享盘 (NTFS / SMB)
IS_FOREIGN_FS=0
case "$ROOT" in
  /Volumes/*Windows*|/Volumes/*BOOTCAMP*|/Volumes/*[Cc]\ *)
    IS_FOREIGN_FS=1 ;;
esac
if df -T "$ROOT" 2>/dev/null | tail -1 | awk '{print $2}' | grep -qiE 'ntfs|fuseblk|cifs|smbfs'; then
  IS_FOREIGN_FS=1
fi

if [ "$IS_FOREIGN_FS" = "1" ]; then
  echo -e "${YELLOW}⚠️  检测到当前目录在 Windows / 共享磁盘上：${NC}"
  echo -e "    $ROOT"
  echo -e "${YELLOW}   这会导致 venv / npm 安装慢且容易出错。${NC}"
  echo -e "${YELLOW}   强烈建议把项目复制到 Mac 本地磁盘后再跑（推荐 ~/Projects/）：${NC}"
  echo -e "    ${CYAN}mkdir -p ~/Projects${NC}"
  echo -e "    ${CYAN}rsync -av --exclude='backend/.venv*' --exclude='frontend/node_modules' \\${NC}"
  echo -e "    ${CYAN}      \"$ROOT/\" ~/Projects/EE_AI_Fund_Management/${NC}"
  echo -e "    ${CYAN}cd ~/Projects/EE_AI_Fund_Management && ./start-dev.sh${NC}"
  echo
  read -r -p "仍然继续在当前目录运行? [y/N] " ans
  case "$ans" in
    y|Y) ;;
    *)   echo "已取消。"; exit 0 ;;
  esac
fi

echo -e "${CYAN}==> 检查环境（OS=${OS_NAME}, venv=${VENV_DIR}）...${NC}"

# ---- python3 ----
if ! command -v python3 >/dev/null 2>&1; then
  echo -e "${RED}❌ 缺少 python3（需要 3.11+）${NC}"
  if [ "$OS_NAME" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
      echo -e "   建议运行: ${CYAN}brew install python@3.11${NC}"
    else
      echo -e "   建议先装 Homebrew (https://brew.sh) 然后 ${CYAN}brew install python@3.11${NC}"
    fi
  else
    echo -e "   apt:    ${CYAN}sudo apt install python3 python3-venv${NC}"
    echo -e "   或下载: https://www.python.org/downloads/"
  fi
  exit 1
fi

# ---- node ----
if ! command -v node >/dev/null 2>&1; then
  echo -e "${RED}❌ 缺少 node（需要 20+）${NC}"
  if [ "$OS_NAME" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    echo -e "   建议运行: ${CYAN}brew install node${NC}"
  else
    echo -e "   下载: https://nodejs.org/"
  fi
  exit 1
fi

PY_VER="$(python3 -V 2>&1 | awk '{print $2}')"
NODE_VER="$(node -v 2>&1)"
echo -e "    python3 -> $(command -v python3)  ($PY_VER)"
echo -e "    node    -> $(command -v node)     ($NODE_VER)"

# -------- backend venv --------
echo -e "\n${CYAN}==> 准备后端虚拟环境 (${VENV_DIR})...${NC}"
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  rm -rf "$VENV_DIR" 2>/dev/null || true
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r backend/requirements.txt -q
deactivate

# -------- frontend deps --------
echo -e "\n${CYAN}==> 准备前端依赖...${NC}"
if [ ! -d "$NODE_MODULES_DIR" ]; then
  (cd frontend && npm install --no-audit --no-fund)
fi

# -------- run --------
mkdir -p backend/data backend/skills_installed

cleanup() {
  echo -e "\n${CYAN}==> 正在关闭服务...${NC}"
  [ -n "${BACK_PID:-}" ]  && kill "$BACK_PID"  2>/dev/null || true
  [ -n "${FRONT_PID:-}" ] && kill "$FRONT_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

echo -e "\n${GREEN}==> 启动后端 (http://localhost:8000)${NC}"
(
  cd backend
  # shellcheck disable=SC1090
  source "../$VENV_DIR/bin/activate"
  export DATA_DIR="$ROOT/backend/data"
  export SKILLS_DIR="$ROOT/backend/skills_installed"
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
) &
BACK_PID=$!

sleep 2

echo -e "${GREEN}==> 启动前端 (http://localhost:5173)${NC}"
(
  cd frontend
  exec npm run dev
) &
FRONT_PID=$!

sleep 3
if command -v open >/dev/null 2>&1; then
  open "http://localhost:5173"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:5173" >/dev/null 2>&1 || true
fi

echo -e "\n${CYAN}✅ 已启动:\n   后端 -> http://localhost:8000\n   前端 -> http://localhost:5173\n按 Ctrl+C 停止全部服务。${NC}"
wait
