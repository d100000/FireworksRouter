#!/bin/bash
# FireworkRouter 一键部署脚本（Docker Compose 模式）
# 用法：bash scripts/deploy.sh
# 或：  curl -fsSL https://raw.githubusercontent.com/d100000/FireworksRouter/main/scripts/deploy.sh | bash

set -e

REPO="https://github.com/d100000/FireworksRouter.git"
INSTALL_DIR="${INSTALL_DIR:-/opt/FireworksRouter}"

# ============== 颜色输出 ==============
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()    { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }

# ============== 1. 环境检查 ==============
info "检查系统..."
if ! command -v docker >/dev/null 2>&1; then
    warn "未安装 Docker，正在安装..."
    curl -fsSL https://get.docker.com | sudo bash
    sudo usermod -aG docker "$USER"
    warn "Docker 装好了，但需要重新登录让 docker group 生效。继续前请退出 ssh 重连。"
    exit 0
fi
docker compose version >/dev/null || fail "docker compose 不可用，请升级 Docker 到 v20.10+"
ok "Docker $(docker version --format '{{.Server.Version}}') + Compose $(docker compose version --short)"

# Node.js 检查（构建前端）
if ! command -v node >/dev/null 2>&1; then
    warn "未装 Node.js（构建前端需要 Node 20+）"
    read -p "  是否自动装 Node 20？ [Y/n] " yn
    yn=${yn:-Y}
    if [[ "$yn" =~ ^[Yy]$ ]]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
        sudo apt-get install -y nodejs
    else
        fail "请手动装 Node 20+ 后重试"
    fi
fi
ok "Node $(node -v)"

# ============== 2. 克隆代码 ==============
if [ -d "$INSTALL_DIR/.git" ]; then
    info "代码已存在，更新到最新..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    info "克隆代码到 $INSTALL_DIR..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown -R "$USER:$USER" "$INSTALL_DIR"
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
ok "代码就绪：$(git log --oneline -1)"

# ============== 3. 生成 .env ==============
if [ -f .env ]; then
    warn ".env 已存在，跳过生成（如需重置，请先 rm .env）"
else
    echo
    read -p "  请输入管理后台初始登录密码（至少 8 位）: " -s PASSWORD
    echo
    if [ ${#PASSWORD} -lt 8 ]; then fail "密码至少 8 位"; fi

    read -p "  请输入 PostgreSQL 密码（用于内置 PG）: " -s PG_PASSWORD
    echo
    if [ ${#PG_PASSWORD} -lt 8 ]; then fail "PG 密码至少 8 位"; fi

    info "生成 .env 文件..."
    # 创建一次性 Python venv 跑 bootstrap.py
    python3 -m venv /tmp/fwr-bootstrap-venv
    /tmp/fwr-bootstrap-venv/bin/pip install cryptography passlib bcrypt -q
    /tmp/fwr-bootstrap-venv/bin/python scripts/bootstrap.py "$PASSWORD"
    rm -rf /tmp/fwr-bootstrap-venv

    # 把 DATABASE_URL 改为 PostgreSQL
    sed -i.bak "s|DATABASE_URL=sqlite+aiosqlite:.*|DATABASE_URL=postgresql+asyncpg://fwr:${PG_PASSWORD}@postgres:5432/fwr|" .env
    rm -f .env.bak

    # 同步 PG 密码到 docker-compose.yml
    sed -i.bak "s|POSTGRES_PASSWORD: fwr|POSTGRES_PASSWORD: ${PG_PASSWORD}|" docker-compose.yml
    rm -f docker-compose.yml.bak

    ok ".env 生成完毕"
fi

# ============== 4. 构建前端 ==============
info "安装前端依赖（约 1-2 分钟）..."
cd frontend
npm install --silent
info "构建前端 SPA..."
npm run build
cd ..
ok "前端构建完成（$(du -sh frontend/dist | cut -f1)）"

# ============== 5. 启动 ==============
info "启动 Docker 服务..."
docker compose pull postgres redis
docker compose up -d --build

info "等待 API 启动（约 10-30 秒）..."
for i in {1..30}; do
    if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        ok "API 启动成功！"
        break
    fi
    sleep 1
    [ "$i" = "30" ] && fail "API 启动超时，检查 'docker compose logs api'"
done

# ============== 6. 完成 ==============
echo
echo "═══════════════════════════════════════════════════════════════"
echo
echo "  🎆 FireworkRouter 部署完成！"
echo
echo "  访问后台：  http://$(hostname -I | awk '{print $1}'):8000/"
echo "             或 http://127.0.0.1:8000/"
echo "  初始密码：  你刚才输入的"
echo
echo "  下一步推荐："
echo "    1. 配置 HTTPS（Caddy/Nginx，见 docs/DEPLOYMENT.md 第五节）"
echo "    2. 添加上游 Fireworks Key（菜单「上游 Key 池」）"
echo "    3. 同步价格表（菜单「价格表」→ 从 LiteLLM 同步）"
echo "    4. 启用模型（菜单「模型管理」）"
echo "    5. 颁发下游 API Key（菜单「API Keys」→ 新建）"
echo
echo "  数据备份命令："
echo "    bash $INSTALL_DIR/scripts/backup.sh"
echo
echo "  升级命令："
echo "    cd $INSTALL_DIR && git pull && docker compose up -d --build"
echo
echo "  完整文档：  $INSTALL_DIR/docs/DEPLOYMENT.md"
echo "  GitHub：    https://github.com/d100000/FireworksRouter"
echo
echo "═══════════════════════════════════════════════════════════════"
