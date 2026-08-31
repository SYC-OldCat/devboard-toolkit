# DevBoard Toolkit — Web 版

网页版开发板工具箱, 在服务器上运行, 多人通过浏览器使用。

## 目录结构

```
web/
├── backend/               # FastAPI 后端
│   ├── app/
│   │   ├── main.py        # 入口
│   │   ├── config_loader.py
│   │   ├── core/          # 核心组件
│   │   │   ├── logbus.py      # 日志总线 (WebSocket 推送)
│   │   │   ├── board_pool.py  # 全局板池管理
│   │   │   └── user_data.py   # 用户数据隔离
│   │   └── api/           # REST API 路由
│   │       ├── auth.py       # 注册/登录/JWT
│   │       ├── config.py     # 配置读写
│   │       ├── boards.py     # 板池查询/检测/锁定
│   │       ├── replay.py     # 自动回灌
│   │       ├── dataproc.py   # 数据处理
│   │       ├── jenkins.py    # Jenkins 编译
│   │       └── pipeline.py   # 组合流水线
│   ├── data/users/        # 用户数据 (运行时生成)
│   └── requirements.txt
├── frontend/              # Vue 3 前端
│   ├── src/
│   │   ├── views/         # 页面
│   │   │   ├── Login.vue     # 登录/注册
│   │   │   ├── Layout.vue    # 主框架
│   │   │   ├── Replay.vue    # 自动回灌
│   │   │   ├── DataProc.vue  # 数据处理
│   │   │   ├── Jenkins.vue   # Jenkins编译 (P4)
│   │   │   ├── Pipeline.vue  # 组合流水线 (P5)
│   │   │   └── Settings.vue  # 配置管理
│   │   ├── router/
│   │   ├── stores/
│   │   └── utils/
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## 部署 (Linux 服务器)

### 1. 后端

```bash
cd web/backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 前端

```bash
cd web/frontend
npm install
npm run build          # 产物在 frontend/dist/
```

### 3. nginx (生产)

```nginx
server {
    listen 80;
    server_name _;

    location / {
        root /path/to/web/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    location /api {
        proxy_pass http://127.0.0.1:8000;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 开发模式

```bash
# 终端1: 后端
cd web/backend && uvicorn app.main:app --reload --port 8000

# 终端2: 前端 (自动代理 /api 到 8000)
cd web/frontend && npm run dev
# 浏览器打开 http://localhost:3000
```

## 功能状态

| 功能 | 状态 |
|---|---|
| 用户认证 (注册/登录/JWT) | ✅ P1 |
| 用户配置隔离 | ✅ P1 |
| 板池共享管理 | ✅ P1 |
| WebSocket 实时日志 | ✅ P1 |
| 自动回灌 | 🔨 P2 (骨架已搭,待接入 batch_replay) |
| 数据处理 | 🔨 P3 (骨架已搭,待接入 pipeline) |
| Jenkins 编译 | 🔨 P4 (骨架已搭) |
| 组合流水线 | 🔨 P5 (骨架已搭) |
| 设置页 | ✅ (JSON 编辑) |
