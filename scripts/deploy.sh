#!/bin/bash
# ============================================================================
# FireworkRouter 一键部署脚本（Docker Compose + PostgreSQL）
#
# 用法：
#   bash scripts/deploy.sh
#   或：curl -fsSL https://raw.githubusercontent.com/d100000/FireworksRouter/main/scripts/deploy.sh | bash
#
# 全程交互式：
#   1) 安装 Docker（如果没装）
#   2) 克隆 / 更新代码到 /opt/FireworksRouter
#   3) 询问初始密码 → 生成 .env（含 PG 密码、加密 key、JWT secret）
#   4) docker compose up -d --build（PostgreSQL + Redis + API）
#   5) 健康检查直到 API 就绪
#   6) 可选启用 Caddy HTTPS（输入域名即可）
# ============================================================================

set -e

REPO="${REPO:-https://github.com/d100000/FireworksRouter.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/FireworksRouter}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }

# ============== 1. 环境检查 ==============
info "检查系统环境..."

# Docker
if ! command -v docker >/dev/null 2>&1; then
    warn "未安装 Docker，正在安装..."
    if [ "$(id -u)" -ne 0 ]; then
        curl -fsSL https://get.docker.com | sudo bash
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        warn "Docker 安装完毕。你可能需要 ${YELLOW}重新登录 SSH${NC} 后再运行此脚本（让 docker group 生效）。"
        warn "或者用 sudo 重新运行：sudo bash $0"
        exit 0
    else
        curl -fsSL https://get.docker.com | bash
    fi
fi

# Compose v2
if ! docker compose version >/dev/null 2>&1; then
    fail "docker compose v2 不可用。请升级到 Docker 20.10+ 或安装 docker-compose-plugin。"
fi

DOCKER_VER=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "?")
COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo "?")
ok "Docker ${DOCKER_VER} + Compose ${COMPOSE_VER}"

# Git
if ! command -v git >/dev/null 2>&1; then
    info "安装 git..."
    sudo apt-get update >/dev/null && sudo apt-get install -y -qq git
fi

# ============== 2. 克隆 / 更新代码 ==============
if [ -d "$INSTALL_DIR/.git" ]; then
    info "代码已存在，拉取最新..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main || warn "git pull 失败，可能有本地未提交修改"
else
    info "克隆代码到 $INSTALL_DIR..."
    sudo mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ ! -w "$(dirname "$INSTALL_DIR")" ]; then
        sudo chown -R "$USER:$USER" "$(dirname "$INSTALL_DIR")" 2>/dev/null || true
    fi
    git clone "$REPO" "$INSTALL_DIR" || fail "git clone 失败"
    cd "$INSTALL_DIR"
fi
ok "代码就绪：$(git log --oneline -1)"

# ============== 3. 生成 .env（如不存在）==============
if [ -f .env ]; then
    warn ".env 已存在，跳过生成（如需重置：mv .env .env.old）"
else
    info "首次部署：需要设置初始管理密码"
    while true; do
        read -p "  请输入管理后台初始登录密码（≥ 8 位）: " -s PASSWORD
        echo
        if [ "${#PASSWORD}" -ge 8 ]; then break; fi
        warn "密码至少 8 位"
    done

    info "生成 .env 文件..."
    # 在临时 venv 跑 bootstrap.py（不污染系统 python）
    VENV_DIR=$(mktemp -d)/fwr-bootstrap
    python3 -m venv "$VENV_DIR" 2>/dev/null || python3.11 -m venv "$VENV_DIR" 2>/dev/null \
        || fail "需要 python3，请先 apt install python3-venv"
    "$VENV_DIR/bin/pip" install -q cryptography passlib bcrypt
    "$VENV_DIR/bin/python" scripts/bootstrap.py "$PASSWORD"
    rm -rf "$VENV_DIR"

    # 可选：HTTPS 配置
    echo
    read -p "  是否启用 HTTPS（Caddy 自动证书）？需要解析了的域名 [y/N]: " enable_https
    if [[ "$enable_https" =~ ^[Yy]$ ]]; then
        read -p "  请输入你的域名（如 fwr.example.com）: " DOMAIN
        read -p "  请输入 ACME 注册邮箱（用于 Let's Encrypt 通知）: " ACME_EMAIL
        echo "DOMAIN=${DOMAIN}" >> .env
        echo "ACME_EMAIL=${ACME_EMAIL}" >> .env
        echo "API_BIND=127.0.0.1" >> .env   # Caddy 反代，API 不必外网
        ENABLE_HTTPS=1
    else
        # 没启用 HTTPS：让 API 直接对外（仅测试用）
        sed -i.bak 's/^API_BIND=.*/API_BIND=0.0.0.0/' .env || true
        rm -f .env.bak
        ENABLE_HTTPS=0
    fi
    ok ".env 生成完毕"
fi

# ============== 4. 启动 Docker 服务 ==============
info "拉取镜像 + 构建 + 启动..."
COMPOSE_ARGS=""
if grep -q '^DOMAIN=' .env 2>/dev/null && [ -n "$(grep '^DOMAIN=' .env | cut -d= -f2)" ]; then
    COMPOSE_ARGS="--profile https"
    info "检测到 DOMAIN 配置：将启用 Caddy HTTPS"
fi

docker compose ${COMPOSE_ARGS} pull postgres redis 2>/dev/null || true
docker compose ${COMPOSE_ARGS} up -d --build

# 读 .env 里的最终 API_PORT（用户可能在 .env 里改过）
API_PORT_HOST=$(grep '^API_PORT=' .env 2>/dev/null | cut -d= -f2 | head -1)
API_PORT_HOST=${API_PORT_HOST:-8011}
API_BIND_HOST=$(grep '^API_BIND=' .env 2>/dev/null | cut -d= -f2 | head -1)
API_BIND_HOST=${API_BIND_HOST:-127.0.0.1}

# ============== 5. 健康检查 ==============
info "等待 API 启动（最多 90 秒，PG 初次 initdb 耗时较长）..."
HEALTH_OK=0
for i in $(seq 1 90); do
    # 容器内永远是 8011（Dockerfile 固定）
    if docker compose exec -T api curl -fsS http://127.0.0.1:8011/healthz >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    [ $((i % 10)) -eq 0 ] && info "  仍在等待... (${i}/90s)"
    sleep 1
done
if [ "$HEALTH_OK" = "0" ]; then
    warn "API 启动超时，最近 50 行日志："
    docker compose logs --tail 50 api
    fail "请检查上面的错误，或运行 docker compose logs -f api 看完整日志"
fi
ok "API 启动成功 ✓"

# ============== 6. 完成 ==============
HOSTIP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
DOMAIN_LINE=$(grep '^DOMAIN=' .env 2>/dev/null | cut -d= -f2 | head -1)

echo
echo "═══════════════════════════════════════════════════════════════"
echo
echo "  🎆 FireworkRouter 部署完成！"
echo
if [ -n "$DOMAIN_LINE" ]; then
    echo "  🔒 HTTPS 访问： https://${DOMAIN_LINE}/"
    echo "  📋 首次访问前确保 ${DOMAIN_LINE} 的 A 记录指向本机 IP"
    echo "  📋 Caddy 会自动从 Let's Encrypt 申请证书（首次约 30 秒）"
elif [ "$API_BIND_HOST" = "127.0.0.1" ]; then
    echo "  🔒 API 只在本机监听 (127.0.0.1:${API_PORT_HOST})"
    echo "  外网访问请通过反向代理（Caddy / Nginx）；或编辑 .env 设 API_BIND=0.0.0.0"
    echo "  本机访问： http://127.0.0.1:${API_PORT_HOST}/"
else
    echo "  访问： http://${HOSTIP}:${API_PORT_HOST}/   或   http://127.0.0.1:${API_PORT_HOST}/"
    echo "  ⚠️  目前是 HTTP 明文，生产建议加 HTTPS（编辑 .env 加 DOMAIN= 后重跑此脚本）"
fi
echo
echo "  下一步推荐："
echo "    1. 登录修改默认密码（右上角头像 → 修改密码）"
echo "    2. 添加上游 Fireworks Key（菜单「上游 Key 池」）"
echo "    3. 同步价格表（菜单「价格表」→ 从 LiteLLM 同步）"
echo "    4. 启用模型（菜单「模型管理」）"
echo "    5. 颁发下游 API Key（菜单「API Keys」→ 新建）"
echo
echo "  常用命令："
echo "    日志：    docker compose logs -f api"
echo "    重启：    docker compose restart api"
echo "    升级：    cd ${INSTALL_DIR} && git pull && docker compose up -d --build"
echo "    备份：    bash ${INSTALL_DIR}/scripts/backup.sh"
echo "    备份计划任务：sudo crontab -e  添加：0 3 * * * bash ${INSTALL_DIR}/scripts/backup.sh"
echo
echo "  完整文档：${INSTALL_DIR}/docs/DEPLOYMENT.md"
echo "  GitHub：  https://github.com/d100000/FireworksRouter"
echo
echo "═══════════════════════════════════════════════════════════════"
