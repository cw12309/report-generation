#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

check_docker() {
    if ! command -v docker &> /dev/null; then
        error "docker 未安装，请先安装 Docker"
    fi
    if ! docker info &> /dev/null; then
        error "docker 未启动，请先启动 Docker 服务"
    fi
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        error "docker-compose 未安装，请先安装 docker-compose"
    fi
}

dc() {
    if docker compose version &> /dev/null 2>&1; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

init_env() {
    if [ ! -f .env ]; then
        if [ -f .env.example ]; then
            cp .env.example .env
            info "已从 .env.example 创建 .env，请根据实际环境修改配置"
        else
            error ".env.example 不存在，无法创建 .env"
        fi
    else
        info ".env 已存在"
    fi
}

init_dirs() {
    mkdir -p app/data_pipeline
    mkdir -p app/data_pipeline/xlsx_slices
    mkdir -p app/report_template
    mkdir -p data
    mkdir -p app/memory/data
    chown -R 1000:1000 app/data_pipeline app/report_template data app/memory/data
    info "数据目录已就绪"
}

build_and_start() {
    info "构建镜像..."
    dc build
    info "启动服务..."
    dc up -d
    info "服务已启动"
    echo ""
    dc ps
    echo ""
    info "访问地址: http://localhost:8000"
}

stop_service() {
    info "停止服务..."
    dc down
    info "服务已停止"
}

show_logs() {
    dc logs -f --tail=100
}

show_status() {
    dc ps
    echo ""
    dc logs --tail=20
}

rebuild() {
    info "重新构建并启动..."
    dc down
    dc build --no-cache
    dc up -d
    info "重建完成"
    echo ""
    dc ps
}

case "${1:-start}" in
    start)
        check_docker
        init_env
        init_dirs
        build_and_start
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        init_dirs
        build_and_start
        ;;
    rebuild)
        check_docker
        rebuild
        ;;
    logs)
        show_logs
        ;;
    status)
        show_status
        ;;
    init)
        check_docker
        init_env
        init_dirs
        info "初始化完成，运行 ./start.sh start 启动服务"
        ;;
    *)
        echo "用法: ./start.sh {start|stop|restart|rebuild|logs|status|init}"
        echo ""
        echo "  start    - 初始化并启动服务（默认）"
        echo "  stop     - 停止服务"
        echo "  restart  - 重启服务"
        echo "  rebuild  - 无缓存重新构建并启动"
        echo "  logs     - 查看实时日志"
        echo "  status   - 查看服务状态"
        echo "  init     - 仅初始化（不启动）"
        ;;
esac