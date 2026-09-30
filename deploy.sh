#!/bin/bash
# 一键部署脚本：Milvus 向量库 + 报告生成服务（Worker + Gateway）
#
#   ./deploy.sh                  初始化并启动全部服务（幂等，可重复执行）
#   ./deploy.sh --build-vectors  用随包 Markdown 切片灌入 Milvus 向量
#   ./deploy.sh --rebuild        不使用缓存重新构建镜像后启动
#   ./deploy.sh --status         查看服务状态与健康检查结果
#   ./deploy.sh --logs           跟踪查看应用日志
#   ./deploy.sh --stop           停止全部服务（保留数据卷）
#
# 部署完成后访问：http://<服务器IP>:8011

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

UI_PORT=8011          # 报告生成界面 / 全部业务接口（compose.yaml -> worker）
GATEWAY_PORT=8012     # 对外 API 网关（compose.yaml -> gateway）
MILVUS_WAIT=240       # Milvus 健康等待秒数
APP_WAIT=240          # 应用健康等待秒数

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
step()  { echo -e "${BLUE}[STEP]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

dc() {
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

# ---------------------------------------------------------------- 前置检查
check_prereqs() {
    command -v docker >/dev/null 2>&1 || error "未检测到 docker，请先安装 Docker Engine"
    docker info >/dev/null 2>&1 || error "docker 守护进程未运行或当前用户无权限（尝试 sudo 或将用户加入 docker 组）"
    if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
        error "未检测到 docker compose，请安装 Docker Compose 插件"
    fi
    command -v curl >/dev/null 2>&1 || warn "未检测到 curl，将跳过健康检查（建议安装：apt-get install -y curl）"
    info "Docker 环境检查通过"
}

check_ports() {
    local occupied=0
    for port in "$UI_PORT" "$GATEWAY_PORT"; do
        if command -v ss >/dev/null 2>&1; then
            if ss -ltn 2>/dev/null | grep -q ":${port}[[:space:]]"; then
                occupied=1; warn "端口 ${port} 已被监听"
            fi
        elif command -v netstat >/dev/null 2>&1; then
            if netstat -ltn 2>/dev/null | grep -q ":${port}[[:space:]]"; then
                occupied=1; warn "端口 ${port} 已被监听"
            fi
        fi
    done
    if [ "$occupied" -eq 1 ]; then
        # 已被本项目容器占用属正常情况（重复执行部署脚本）
        if docker ps --format '{{.Ports}}' | grep -qE ":(${UI_PORT}|${GATEWAY_PORT})->"; then
            info "端口由本项目已有容器占用，继续执行"
        else
            error "端口 ${UI_PORT}/${GATEWAY_PORT} 被其它进程占用，请修改 compose.yaml 中的 ports 映射后重试"
        fi
    fi
}

init_env() {
    if [ ! -f .env ]; then
        [ -f .env.example ] || error ".env.example 不存在，无法生成 .env"
        cp .env.example .env
        info "已从 .env.example 生成 .env"
    else
        info ".env 已存在，沿用现有配置"
    fi

    local placeholders=""
    for pair in "CHAT_API_KEY:chat-service-api-key" \
                "EMBED_API_KEY:embedding-service-api-key" \
                "SILICONFLOW_API_KEY:rerank-service-api-key" \
                "API_SECRET_KEY:gateway-bearer-token"; do
        key="${pair%%:*}"; sample="${pair##*:}"
        if grep -q "^${key}=${sample}\$" .env 2>/dev/null; then
            placeholders="${placeholders} ${key}"
        fi
    done

    if [ -n "$placeholders" ]; then
        warn ".env 中以下配置仍为示例占位值:${placeholders}"
        warn "请编辑 .env 填入真实值（CHAT_* 为报告生成大模型，EMBED_*/SILICONFLOW_* 为向量与重排服务）"
        if [ -t 0 ]; then
            read -r -p "现在编辑 .env 吗？[y/N] " answer
            if [ "${answer:-n}" = "y" ] || [ "${answer:-n}" = "Y" ]; then
                ${EDITOR:-vi} .env
            fi
            read -r -p "占位值未修改可能导致检索/生成失败，是否继续部署？[Y/n] " cont
            if [ "${cont:-Y}" = "n" ] || [ "${cont:-Y}" = "N" ]; then
                error "已中止部署，请修改 .env 后重新执行 ./deploy.sh"
            fi
        fi
    else
        info ".env 配置检查通过"
    fi
}

init_dirs() {
    # 必须与 compose.yaml 的 volumes 一一对应：宿主机目录若不存在，
    # Docker 会以 root 创建它，导致容器内 appuser(uid 1000) 无写权限。
    mkdir -p app/data_pipeline/xlsx_slices \
             app/data_pipeline/report_template \
             app/data_pipeline/output_markdown \
             data \
             app/memory/data
    if [ "$(id -u)" -eq 0 ]; then
        chown -R 1000:1000 app/data_pipeline data app/memory/data 2>/dev/null || true
    fi
    info "数据目录已就绪"
}

# ---------------------------------------------------------------- 向量库构建
# Milvus 的数据存放在 Docker 卷中，不随项目文件夹一起打包/迁移。
# 换新服务器或重建卷后，需要用已打包的 Markdown 切片重新灌入向量。
build_vectors() {
    check_prereqs
    step "检查 Milvus 是否就绪..."
    if command -v curl >/dev/null 2>&1 && ! curl -fsS "http://127.0.0.1:9091/healthz" >/dev/null 2>&1; then
        error "Milvus 未就绪，请先执行 ./deploy.sh 启动服务"
    fi

    local md_count
    md_count="$(find app/data_pipeline/output_markdown -name '*.md' -type f 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${md_count:-0}" -eq 0 ]; then
        error "未找到 Markdown 切片 (app/data_pipeline/output_markdown)，无法构建向量库。请在界面 Knowledge Base 页上传 Excel 构建知识库。"
    fi
    info "发现 ${md_count} 个 Markdown 切片，开始向量化..."

    dc run --rm worker python app/data_pipeline/build_vector_db.py
    info "向量库构建完成"
}

# ---------------------------------------------------------------- 启动 Milvus
start_milvus() {
    step "启动 Milvus 向量库（etcd / minio / milvus / attu）..."
    dc -f compose.milvus.yaml up -d

    if ! command -v curl >/dev/null 2>&1; then return 0; fi

    step "等待 Milvus 就绪（最多 ${MILVUS_WAIT}s）..."
    local waited=0
    while [ "$waited" -lt "$MILVUS_WAIT" ]; do
        if curl -fsS "http://127.0.0.1:9091/healthz" >/dev/null 2>&1; then
            info "Milvus 已就绪"
            return 0
        fi
        sleep 5; waited=$((waited + 5))
        printf '.'
    done
    echo ""
    warn "Milvus 在 ${MILVUS_WAIT}s 内未通过健康检查，请执行：dc -f compose.milvus.yaml logs milvus"
    return 1
}

# ---------------------------------------------------------------- 启动应用
start_app() {
    local build_flag="$1"
    step "构建应用镜像..."
    if [ "$build_flag" = "--no-cache" ]; then
        dc build --no-cache
    else
        dc build
    fi

    step "启动 Worker 与 Gateway..."
    dc up -d

    command -v curl >/dev/null 2>&1 || { warn "未安装 curl，跳过健康检查"; return 0; }

    step "等待报告生成服务就绪（最多 ${APP_WAIT}s）..."
    local waited=0
    while [ "$waited" -lt "$APP_WAIT" ]; do
        if curl -fsS "http://127.0.0.1:${UI_PORT}/health" >/dev/null 2>&1; then
            info "Worker 健康检查通过 (http://127.0.0.1:${UI_PORT}/health)"
            break
        fi
        sleep 5; waited=$((waited + 5))
        printf '.'
    done
    echo ""
    if [ "$waited" -ge "$APP_WAIT" ]; then
        warn "Worker 未在 ${APP_WAIT}s 内就绪，最近日志如下："
        dc logs --tail=50 worker || true
        return 1
    fi

    # Gateway 依赖 worker healthy 后才启动，给几秒重试窗口避免误报
    local gw_ok=0
    for _ in 1 2 3 4 5 6; do
        if curl -fsS -o /dev/null "http://127.0.0.1:${GATEWAY_PORT}/docs" 2>/dev/null; then
            gw_ok=1; break
        fi
        sleep 3
    done
    if [ "$gw_ok" -eq 1 ]; then
        info "Gateway 已启动 (http://127.0.0.1:${GATEWAY_PORT})"
    else
        warn "Gateway 未响应，如不使用对外 API 可忽略；排查：dc logs gateway"
    fi
}

# ---------------------------------------------------------------- 结果输出
detect_ip() {
    local ip=""
    if command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    if [ -z "$ip" ] && command -v ip >/dev/null 2>&1; then
        ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -n1 || true)"
    fi
    echo "${ip:-127.0.0.1}"
}

# 查询 Milvus 中已有向量条数。Milvus 数据存放在 Docker 卷里，不随项目文件夹
# 打包迁移，因此 data/sessions.db 存在不代表向量库有数据，必须实际探测。
milvus_entity_count() {
    local py='
from app.config import get_collection_name, get_db_path, get_milvus_token
from pymilvus import MilvusClient
try:
    c = MilvusClient(uri=get_db_path(), token=get_milvus_token() or None)
    name = get_collection_name()
    if not c.has_collection(name):
        print("MISSING")
    else:
        c.flush(name)
        print(c.get_collection_stats(name).get("row_count", 0))
except Exception as e:
    print("ERROR", e)
'
    dc exec -T worker python -c "$py" 2>/dev/null | tail -n1 || echo "UNKNOWN"
}

print_summary() {
    local host_ip; host_ip="$(detect_ip)"
    echo ""
    dc ps || true
    echo ""
    dc -f compose.milvus.yaml ps || true
    echo ""
    echo -e "${GREEN}==================== 部署完成 ====================${NC}"
    echo -e "  报告生成界面 : ${BLUE}http://${host_ip}:${UI_PORT}${NC}"
    echo -e "  本机访问     : http://127.0.0.1:${UI_PORT}"
    echo -e "  API 网关     : http://${host_ip}:${GATEWAY_PORT}  (Bearer: .env 中 API_SECRET_KEY)"
    echo -e "  Attu 向量库  : http://127.0.0.1:3000  (仅本机，Milvus 地址 milvus:19530)"
    echo -e "${GREEN}==================================================${NC}"
    echo ""
    local count; count="$(milvus_entity_count)"
    case "$count" in
        ''|*[!0-9]*)
            warn "无法探测向量库状态（${count:-UNKNOWN}），请执行 ./deploy.sh --status 查看服务是否就绪。"
            ;;
        0)
            warn "向量库为空：Milvus 数据存放在 Docker 卷中，不会随项目文件夹一起打包迁移。"
            if [ "$(find app/data_pipeline/output_markdown -name '*.md' -type f 2>/dev/null | head -n1)" != "" ]; then
                warn "已检测到随包带来的 Markdown 切片，执行下面命令即可灌入向量（无需重新上传 Excel）："
                echo -e "    ${BLUE}./deploy.sh --build-vectors${NC}"
            else
                warn "请在报告生成界面 -> Knowledge Base 页上传报告 Excel 并构建知识库。"
            fi
            ;;
        *)
            info "向量库已就绪：${count} 条向量。"
            ;;
    esac
    echo ""
    echo "常用命令："
    echo "  构建向量库 : ./deploy.sh --build-vectors"
    echo "  查看状态   : ./deploy.sh --status"
    echo "  查看日志   : ./deploy.sh --logs"
    echo "  停止服务   : ./deploy.sh --stop"
    echo "  重新构建   : ./deploy.sh --rebuild"
    echo ""
    echo "若浏览器无法访问，请放行服务器防火墙/安全组的 ${UI_PORT} 端口（TCP）。"
}

show_status() {
    dc ps || true
    echo ""
    dc -f compose.milvus.yaml ps || true
    echo ""
    if command -v curl >/dev/null 2>&1; then
        for target in "Worker|http://127.0.0.1:${UI_PORT}/health" "Gateway|http://127.0.0.1:${GATEWAY_PORT}/docs" "Milvus|http://127.0.0.1:9091/healthz"; do
            name="${target%%|*}"; url="${target##*|}"
            if curl -fsS -o /dev/null "$url" 2>/dev/null; then
                info "${name} 正常 (${url})"
            else
                warn "${name} 不可达 (${url})"
            fi
        done
        local count; count="$(milvus_entity_count)"
        case "$count" in
            ''|*[!0-9]*) warn "向量库状态未知（${count:-UNKNOWN}）" ;;
            0)           warn "向量库为空，检索不可用。执行 ./deploy.sh --build-vectors 构建" ;;
            *)           info "向量库 ${count} 条向量" ;;
        esac
    fi
}

stop_all() {
    step "停止应用服务..."
    dc down
    step "停止 Milvus..."
    dc -f compose.milvus.yaml down
    info "已停止（数据卷保留，重新执行 ./deploy.sh 可恢复）"
}

deploy() {
    local build_flag="${1:-}"
    check_prereqs
    check_ports
    init_env
    init_dirs
    start_milvus || warn "Milvus 未就绪，仍继续启动应用（应用会在向量库不可用时降级报错）"
    start_app "$build_flag" || true
    print_summary
}

usage() {
    cat <<'EOF'
用法: ./deploy.sh [--rebuild|--build-vectors|--status|--logs|--stop|--help]

  (无参数)        一键部署：检查环境 -> 生成 .env -> 启动 Milvus -> 构建并启动应用
  --rebuild       无缓存重新构建镜像后启动
  --build-vectors 用随包的 Markdown 切片灌入 Milvus 向量（迁移服务器后必做）
  --status        查看容器状态、各服务健康检查与向量库条数
  --logs          跟踪应用日志
  --stop          停止全部服务（保留数据）
  --help          显示本帮助
EOF
}

case "${1:-}" in
    "")                deploy ;;
    --rebuild|-r)      deploy "--no-cache" ;;
    --build-vectors)   build_vectors ;;
    --status|-s)       show_status ;;
    --logs|-l)         dc logs -f --tail=100 ;;
    --stop)            stop_all ;;
    --help|-h)         usage ;;
    *) error "未知参数: $1（使用 ./deploy.sh --help 查看用法）" ;;
esac
