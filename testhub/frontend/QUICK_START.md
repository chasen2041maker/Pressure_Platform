# TestHub 前端 Docker 快速开始

## 🚀 5分钟快速部署

### 生产环境

```bash
cd frontend
./deploy.sh start
```

访问：http://localhost

### 开发环境

```bash
cd frontend
./deploy.sh dev
```

访问：http://localhost:3000

## 📝 详细步骤

### 1. 进入前端目录

```bash
cd frontend
```

### 2. 选择部署模式

#### 生产模式（推荐用于部署）

```bash
# 方式1: 使用脚本
./deploy.sh start

# 方式2: 使用 docker compose
docker compose up -d --build
```

**特点：**
- ✅ 优化的生产构建
- ✅ Nginx 静态文件服务
- ✅ Gzip 压缩
- ✅ 资源缓存
- 🌐 端口：80

#### 开发模式（推荐用于开发）

```bash
# 方式1: 使用脚本
./deploy.sh dev

# 方式2: 使用 docker compose
docker compose -f docker compose.dev.yml up -d --build
```

**特点：**
- ✅ 代码热重载
- ✅ 实时编译
- ✅ 开发工具
- 🌐 端口：3000

### 3. 验证部署

```bash
# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f

# 或使用脚本
./deploy.sh status
./deploy.sh logs
```

### 4. 访问应用

- **生产环境**: http://localhost
- **开发环境**: http://localhost:3000

## 🛠️ 常用操作

### 停止服务

```bash
./deploy.sh stop
# 或
docker compose down
```

### 重启服务

```bash
./deploy.sh restart
# 或
docker compose restart
```

### 查看日志

```bash
./deploy.sh logs
# 或
docker compose logs -f
```

### 重新构建

```bash
./deploy.sh build
# 或
docker compose build --no-cache
```

### 清理资源

```bash
./deploy.sh clean
# 或
docker compose down -v --rmi local
```

## 🔧 配置修改

### 修改端口

编辑 `docker compose.yml`：

```yaml
services:
  frontend:
    ports:
      - "8080:80"  # 改为 8080 端口
```

### 配置 API 地址

编辑 `nginx.conf`：

```nginx
location /api/ {
    proxy_pass http://your-backend:8000;
}
```

### 添加环境变量

编辑 `docker compose.yml`：

```yaml
services:
  frontend:
    environment:
      - VITE_API_BASE_URL=http://localhost:8000
```

## ❓ 常见问题

### 端口被占用

```bash
# 查看端口占用
lsof -i :80

# 修改端口或停止占用进程
```

### 构建失败

```bash
# 清理缓存重新构建
docker compose build --no-cache --pull
```

### 页面无法访问

```bash
# 检查容器状态
docker compose ps

# 查看日志
docker compose logs frontend
```

### 代码修改不生效（开发模式）

```bash
# 重启容器
docker compose -f docker compose.dev.yml restart
```

## 📚 更多信息

详细文档请查看：[README.Docker.md](./README.Docker.md)

## 🎯 下一步

1. ✅ 部署成功后，配置后端 API 地址
2. ✅ 根据需要修改 Nginx 配置
3. ✅ 配置 HTTPS（生产环境）
4. ✅ 设置日志管理
5. ✅ 配置监控和告警

## 💡 提示

- 生产环境建议使用 `./deploy.sh start`
- 开发环境建议使用 `./deploy.sh dev`
- 使用 `./deploy.sh` 可以打开交互式菜单
- 所有操作都可以通过脚本或 docker compose 命令完成

祝你使用愉快！🎉
