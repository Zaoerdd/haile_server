# Docker 部署说明

## 1. 准备环境变量
复制一份环境变量文件：

```bash
cp .env.example .env
```

按需修改 `.env`，至少建议改掉：

- `FLASK_SECRET_KEY`
- `HAILE_TOKEN`
- `SSL_VERIFY`
- `PORT`

## 2. 准备运行时数据文件

`machines.json` 存放「收藏的机组」，属于**运行时数据，不进版本库**（已在 `.gitignore` 里）。首次部署需要从模板复制一份：

```bash
cp machines.example.json machines.json
```

> 注意：如果跳过这一步，Docker 会把 `./haile_server/machines.json` 这个挂载路径**创建成目录**，应用读取时该文件不可用（收藏列表为空）。

之后在页面里「收藏」机组，服务端会直接写入这个文件，无需手工编辑。

## 3. 构建并启动

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

## 4. 访问
浏览器打开：

```text
http://服务器IP:5000
```

## 5. 说明
- 容器内默认使用 Gunicorn 监听 `0.0.0.0:5000`
- `machines.json` 以**可写**方式挂载（页面收藏会写回该文件），不是只读
- `machines.json` 未从模板初始化时，`/api/config` 返回的收藏列表为空，不会报错
- 如果你的环境需要严格校验证书，把 `.env` 中的 `SSL_VERIFY` 改成 `true`
- Web 页面会从 `.env` 自动读取 `HAILE_TOKEN`，不再要求用户手动输入 token
