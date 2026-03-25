运行方式：
1. 进入目录：cd server_haile_optimized
2. 安装依赖：pip install flask requests
3. 在 `.env` 中配置 `HAILE_TOKEN=你的_token`
4. 启动：python app.py

默认仅监听 127.0.0.1:5000。
如需允许局域网访问，可设置环境变量：ALLOW_REMOTE=true
如需开启证书校验，可设置：SSL_VERIFY=true

说明：
- 页面不再需要手动输入 Token，服务端会从 `.env` 读取 `HAILE_TOKEN`
- 如果 `HAILE_TOKEN` 缺失、无效或暂时无法校验，页面会弹出提醒并禁用核心操作按钮
- 修改 `.env` 后刷新页面即可重新校验最新的 `HAILE_TOKEN`
