运行方式：
1. 进入目录：cd server_haile_optimized
2. 安装依赖：pip install flask requests
3. 启动：python app.py

默认仅监听 127.0.0.1:5000。
如需允许局域网访问，可设置环境变量：ALLOW_REMOTE=true
如需开启证书校验，可设置：SSL_VERIFY=true
