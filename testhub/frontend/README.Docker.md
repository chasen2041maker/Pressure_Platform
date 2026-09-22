# TestHub 前端 Docker 部署指南

## 📋 目录

- [快速开始](#快速开始)
- [部署模式](#部署模式)
- [配置说明](#配置说明)
- [常用命令](#常用命令)
- [故障排查](#故障排查)

## 快速开始

### 方式 1: 使用部署脚本（推荐）

```bash
# 赋予执行权限
chmod +x deploy.sh

# 启动生产环境
./deploy.sh start

# 或启动开发环境
./deploy.sh dev

# 或使用交互式菜单
./deploy.sh
```

### 方式 2: 使用 Docker Compose 命令

#### 生产环境

```bash
# 构建并启动
docker compose up -d --build

# 访问应用
open http://localhost
```

#### 开发环境

```bash
# 构建并启动开发环境
docker compose -f docker compose.dev.yml up -d --build

# 访问应用
open http://localhost:3000
```

## 部署模式

### 生产模式

**特点：**
- 多阶段构建，镜像体积小
- 使用 Nginx 提供静态文件服务
- 启用 Gzip 压缩
- 静态资源缓存优化
- 支持 SPA 路由

**端口：** 80

**Dockerfile：** `Dockerfile`

**启动命令：**
```bash
docker compose up -d
```

### 开发模式

**特点：**
- 代码热重载
- 源代码挂载到容器
- 实时编译
- 开发服务器

**端口：** 3000

**Dockerfile：** `Dockerfile.dev`

**启动命令：**
```bash
docker compose -f docker compose.dev.yml up -d
```

## 配置说明

### Nginx 配置

编辑 `nginx.conf` 文件可以自定义 Nginx 配置：

```nginx
server {
    listen 80;
    server_name localhost;
    
    # 前端路由支持
    location / {
        try_files $uri $uri/ /index.html;
    }
    
    # API 代理（如果需要）
    location /api/ {
        proxy_pass http://backend:8000;
    }
}
```

### 环境变量

如需配置环境变量，可以在 `docker compose.yml` 中添加：

```yaml
services:
  frontend:
    environment:
      - VITE_API_BASE_URL=http://localhost:8000
      - VITE_APP_TITLE=TestHub
```

或创建 `.env` 文件：

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_APP_TITLE=TestHub
```

## 常用命令

### 使用部署脚本

```bash
# 构建镜像
./deploy.sh build

# 启动生产环境
./deploy.sh start

# 启动开发环境
./deploy.sh dev

# 停止服务
./deploy.sh stop

# 重启服务
./deploy.sh restart

# 查看日志
./deploy.sh logs

# 查看状态
./deploy.sh status

# 清理资源
./deploy.sh clean
```

### 使用 Docker Compose

#### 生产环境

```bash
# 构建镜像
docker compose build

# 启动服务
docker compose up -d

# 停止服务
docker compose down

# 查看日志
docker compose logs -f

# 查看状态
docker compose ps

# 重启服务
docker compose restart

# 重新构建并启动
docker compose up -d --build
```

#### 开发环境

```bash
# 启动开发环境
docker compose -f docker compose.dev.yml up -d

# 停止开发环境
docker compose -f docker compose.dev.yml down

# 查看开发环境日志
docker compose -f docker compose.dev.yml logs -f
```

### Docker 命令

```bash
# 进入容器
docker exec -it testhub_frontend sh

# 查看容器日志
docker logs -f testhub_frontend

# 查看容器资源使用
docker stats testhub_frontend

# 删除容器
docker rm -f testhub_frontend

# 删除镜像
docker rmi frontend_frontend
```

## 文件结构

```
frontend/
├── Dockerfile              # 生产环境 Dockerfile
├── Dockerfile.dev          # 开发环境 Dockerfile
├── docker compose.yml      # 生产环境配置
├── docker compose.dev.yml  # 开发环境配置
├── nginx.conf              # Nginx 配置文件
├── .dockerignore           # Docker 忽略文件
├── deploy.sh               # 部署脚本
├── README.Docker.md        # 本文档
├── package.json
├── vite.config.js
└── src/
    └── ...
```

## 构建优化

### 多阶段构建

生产环境使用多阶段构建，减小镜像体积：

```dockerfile
# 阶段1: 构建
FROM node:18-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# 阶段2: 运行
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
```

### 缓存优化

利用 Docker 层缓存，先复制 `package.json`，再复制源代码：

```dockerfile
COPY package*.json ./
RUN npm install
COPY . .
```

### .dockerignore

使用 `.dockerignore` 排除不必要的文件：

```
node_modules/
dist/
.git/
*.log
```

## 性能优化

### Nginx 配置优化

1. **启用 Gzip 压缩**
```nginx
gzip on;
gzip_types text/plain text/css application/json application/javascript;
```

2. **静态资源缓存**
```nginx
location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

3. **SPA 路由支持**
```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

## 故障排查

### 1. 构建失败

```bash
# 查看构建日志
docker compose build --no-cache

# 检查 Node.js 版本
docker run --rm node:18-alpine node --version

# 清理缓存重新构建
docker compose build --no-cache --pull
```

### 2. 容器无法启动

```bash
# 查看容器日志
docker compose logs frontend

# 检查端口占用
lsof -i :80

# 使用其他端口
docker compose up -d -p 8080:80
```

### 3. 页面无法访问

```bash
# 检查容器状态
docker compose ps

# 检查 Nginx 配置
docker exec testhub_frontend nginx -t

# 查看 Nginx 日志
docker exec testhub_frontend cat /var/log/nginx/error.log
```

### 4. 开发环境代码不更新

```bash
# 确认卷挂载正确
docker compose -f docker compose.dev.yml config

# 重启容器
docker compose -f docker compose.dev.yml restart

# 重新构建
docker compose -f docker compose.dev.yml up -d --build
```

### 5. API 请求失败

检查 Nginx 配置中的代理设置：

```nginx
location /api/ {
    proxy_pass http://backend:8000;  # 确保后端地址正确
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## 生产环境建议

### 1. 使用环境变量

```yaml
services:
  frontend:
    environment:
      - VITE_API_BASE_URL=${API_BASE_URL}
```

### 2. 配置 HTTPS

```nginx
server {
    listen 443 ssl http2;
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
}
```

### 3. 添加健康检查

```yaml
services:
  frontend:
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 4. 资源限制

```yaml
services:
  frontend:
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

### 5. 日志管理

```yaml
services:
  frontend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## 更新部署

```bash
# 1. 拉取最新代码
git pull

# 2. 重新构建镜像
docker compose build

# 3. 重启服务
docker compose up -d

# 或使用脚本
./deploy.sh build
./deploy.sh restart
```

## 清理资源

```bash
# 停止并删除容器
docker compose down

# 删除镜像
docker rmi frontend_frontend

# 清理所有未使用的资源
docker system prune -a

# 使用脚本清理
./deploy.sh clean
```

## 常见问题

### Q: 如何修改端口？

**A**: 编辑 `docker compose.yml`：

```yaml
services:
  frontend:
    ports:
      - "8080:80"  # 主机端口:容器端口
```

### Q: 如何查看构建的镜像大小？

**A**: 
```bash
docker images | grep frontend
```

### Q: 如何进入容器调试？

**A**: 
```bash
docker exec -it testhub_frontend sh
```

### Q: 如何配置反向代理？

**A**: 编辑 `nginx.conf` 添加 proxy_pass 配置。

### Q: 开发环境如何连接后端？

**A**: 在 `vite.config.js` 中配置代理：

```javascript
export default {
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
}
```

## 技术支持

如有问题，请：
1. 查看日志：`docker compose logs -f`
2. 检查配置：`docker compose config`
3. 查看本文档
4. 提交 Issue

## 相关链接

- [Docker 文档](https://docs.docker.com/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
- [Nginx 文档](https://nginx.org/en/docs/)
- [Vite 文档](https://vitejs.dev/)
