#!/bin/bash

# TestHub 前端 Docker 部署脚本
# 用法: ./deploy.sh [build|start|stop|restart|logs|dev]

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# 检查 Docker
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    if ! command -v docker compose &> /dev/null; then
        print_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi
    
    print_info "✓ Docker 和 Docker Compose 已安装"
}

# 构建镜像
build_image() {
    print_step "构建前端生产镜像..."
    docker compose build
    print_info "✓ 镜像构建完成"
}

# 启动生产环境
start_production() {
    check_docker
    
    print_step "启动前端生产环境..."
    docker compose up -d
    
    print_info ""
    print_info "========================================="
    print_info "前端生产环境已启动！"
    print_info "========================================="
    print_info "访问地址: http://localhost"
    print_info "========================================="
}

# 启动开发环境
start_development() {
    check_docker
    
    print_step "启动前端开发环境..."
    docker compose -f docker compose.dev.yml up -d
    
    print_info ""
    print_info "========================================="
    print_info "前端开发环境已启动！"
    print_info "========================================="
    print_info "访问地址: http://localhost:3000"
    print_info "代码热重载已启用"
    print_info "========================================="
}

# 停止服务
stop_services() {
    print_step "停止前端服务..."
    docker compose down
    docker compose -f docker compose.dev.yml down 2>/dev/null || true
    print_info "✓ 服务已停止"
}

# 重启服务
restart_services() {
    print_step "重启前端服务..."
    docker compose restart
    print_info "✓ 服务已重启"
}

# 查看日志
view_logs() {
    print_info "查看服务日志 (Ctrl+C 退出)..."
    docker compose logs -f
}

# 查看状态
check_status() {
    print_info "服务状态:"
    docker compose ps
    docker compose -f docker compose.dev.yml ps 2>/dev/null || true
}

# 清理
clean() {
    print_warn "清理 Docker 资源..."
    docker compose down -v --rmi local
    docker compose -f docker compose.dev.yml down -v --rmi local 2>/dev/null || true
    print_info "✓ 清理完成"
}

# 主菜单
show_menu() {
    echo ""
    echo "========================================="
    echo "   TestHub 前端 Docker 部署工具"
    echo "========================================="
    echo "1. 构建镜像"
    echo "2. 启动生产环境"
    echo "3. 启动开发环境"
    echo "4. 停止服务"
    echo "5. 重启服务"
    echo "6. 查看日志"
    echo "7. 查看状态"
    echo "8. 清理资源"
    echo "0. 退出"
    echo "========================================="
    read -p "请选择操作 [0-8]: " choice
    
    case $choice in
        1) build_image ;;
        2) start_production ;;
        3) start_development ;;
        4) stop_services ;;
        5) restart_services ;;
        6) view_logs ;;
        7) check_status ;;
        8) clean ;;
        0) exit 0 ;;
        *) print_error "无效的选择" ;;
    esac
}

# 主程序
main() {
    if [ $# -eq 0 ]; then
        # 无参数，显示菜单
        while true; do
            show_menu
        done
    else
        # 有参数，执行对应命令
        case $1 in
            build) build_image ;;
            start) start_production ;;
            dev) start_development ;;
            stop) stop_services ;;
            restart) restart_services ;;
            logs) view_logs ;;
            status) check_status ;;
            clean) clean ;;
            *)
                print_error "未知命令: $1"
                echo "用法: $0 [build|start|dev|stop|restart|logs|status|clean]"
                exit 1
                ;;
        esac
    fi
}

main "$@"
