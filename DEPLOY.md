# Docker 部署说明

## 1. 准备环境变量
复制一份环境变量文件：

```bash
cp .env.example .env
```

按需修改 `.env`，至少建议改掉：

- `FLASK_SECRET_KEY`
- `SSL_VERIFY`
- `PORT`

## 2. 构建并启动

### 使用 docker compose

```bash
docker compose up -d --build
```

### 或使用 docker build / run

```bash
docker build -t server-haile .
docker run -d \
  --name server-haile \
  --restart unless-stopped \
  --env-file .env \
  -p 5000:5000 \
  server-haile
```

## 3. 访问
浏览器打开：

```text
http://服务器IP:5000
```

## 4. 说明
- 容器内默认使用 Gunicorn 监听 `0.0.0.0:5000`
- `machines.json` 通过 volume 只读挂载，便于后续修改机器配置
- 如果你的环境需要严格校验证书，把 `.env` 中的 `SSL_VERIFY` 改成 `true`
