# DevSupport AI

面向 API 开放平台的多 Agent 智能客服系统。由 [liuqinhe](https://github.com/liuqinhe) 独立实现并维护本仓库中的业务与工程代码。

服务对象是对外提供 API 的平台（数据查询、资质核验、风控评分等），它们的客户是企业开发者。开发者在接入和调用 API 时会持续遇到鉴权失败、签名错误、限流、回调失败、账单异常、数据质量等问题。DevSupport AI 通过多 Agent 协同，把 API 文档、调用日志、错误码、套餐账单、工单系统连接起来：**能理解开发者的技术问题、能查文档、能查日志、能解释账单、能安全脱敏，搞不定时带着完整诊断证据自动建单转人工。**

底层用 LangGraph 编排出一条 `意图路由 → 专业 Agent 并行 → 工单 → 汇总 → 安全审查` 的管线；专业 Agent（文档问答 / API 诊断 / 账单）并行执行，安全审查固定串在最后保证脱敏不被绕过。

## 这个系统能做什么

登录后在「智能助手」对话页可以直接试以下四个核心场景：

| 场景 | 示例提问 | 系统表现 |
| --- | --- | --- |
| 鉴权 401 诊断 | `我调用实名认证接口一直返回 401` | 追问 request_id → 查日志 + Key 状态 → 检索 401 文档 → 给出结论/证据/修复步骤，密钥自动脱敏 |
| 429 限流（复合问题） | `今天下午很多 429，是不是你们服务挂了？` | 诊断 / 账单 / 文档三个 Agent 并行 → 判断超 QPS → 给限速、退避、升级建议 |
| 文档问答（纯 RAG） | `签名算法怎么生成？` | 混合检索 + Rerank → 返回步骤与要点 + 文档引用 |
| 账单解释 + 高风险兜底 | `这个月费用为什么涨这么多？` | 查本月/上月用量与费用构成 → 解释原因；要求降套餐时转人工 |

内部侧的「工作台」可看工单、AI 诊断面板和 React Flow 链路可视化；「运营指标」页可看 AI 解决率、意图分布、按租户 Token 成本，并一键运行评估集。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11 + FastAPI + LangGraph |
| LLM / Embedding / Rerank | 通义 DashScope（OpenAI 兼容）：qwen-turbo / qwen-plus、text-embedding-v3、gte-rerank-v2 |
| 向量库 | Milvus 2.4 ｜ 关系库：MySQL 8 ｜ 缓存/会话：Redis 7 |
| 前端 | React 18 + Vite + Ant Design 6 + Ant Design X + react-markdown |

## 系统架构

三层：**交互层**（FastAPI，REST + SSE 流式 + 鉴权/租户隔离）→ **Agent 编排层**（LangGraph Supervisor 编排多 Agent）→ **基础能力层**（RAG / 工具 / 记忆 / 安全脱敏与兜底 / 缓存与模型分层 / 可观测）。

## 目录结构

```
backend/
  app/         业务代码（agents 编排 / api 路由 / rag / tools / memory / guardrail / llm）
  scripts/     建表、种子数据、知识库入库
  eval/        评估集      benchmark/  压测
frontend/      前端应用（React + Vite）
data/knowledge/ 知识库原始 markdown
deploy/
  docker-compose.dev.yml     本地开发：只起 MySQL / Redis / Milvus
  docker-compose.yml         整套容器化编排（含 api / web）
  docker-compose.server.yml  服务器 overlay：内存上限 + 日志轮转
Makefile       常用命令入口
```

## 环境要求

跑起来之前，本机需要准备：

- **Docker + Docker Compose v2**（起 MySQL / Redis / Milvus）
- **Python 3.11+**
- **Node.js 18+**（前端）
- **一个 DashScope API Key**：到[阿里云百炼控制台](https://bailian.console.aliyun.com/)开通并创建 API Key（大模型、Embedding、Rerank 都走它，调用按量计费）。

## 快速开始

### 0. 装依赖 + 配 .env

```bash
# 后端：建虚拟环境、装依赖、复制配置
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # 打开 .env，把 DASHSCOPE_API_KEY 填上

# 前端依赖
cd ../frontend && npm install
cd ..
```

> 后续的 `make setup / run / eval` 等命令都依赖后端虚拟环境，请在**已执行 `source backend/.venv/bin/activate` 的同一个终端**里运行。

### 1. 起基础设施

```bash
make infra-up                 # 启动 MySQL:3307 / Redis:6380 / Milvus:19531
make infra-status             # 反复看，直到各服务 health 显示 healthy 再下一步
```

> ⚠️ **Milvus 首次启动较慢（约 1~2 分钟）**。一定要等 `make infra-status` 里 Milvus 变成 `healthy` 再执行下一步，否则建向量库 collection 会失败。

### 2. 准备数据（建表 + 种子数据 + 知识库入库）

```bash
make setup                    # = init-db + seed + ingest，一条命令跑完
```

> **从旧版本升级**（知识库租户隔离改造之后）：`knowledge_document` 多了几列、Milvus collection 多了 `tenant_id` 等标量字段。全新环境走 `make setup` 即可；已有数据的环境要先补列再重新摄取，见 [`data/README.md`](data/README.md#升级注意租户隔离改造)。

### 3. 启动服务

```bash
make run                      # 后端 :8000（另开一个终端起前端）
make front                    # 前端 :5173
make health                   # 健康检查，返回 {"status":"ok"} 即正常
```

浏览器打开 **http://localhost:5173** 登录即可体验。

**预置账号**（密码统一 `password123`，仅本地示例，部署必须改掉）：

| 账号 | 角色 | 用途 |
| --- | --- | --- |
| `dev_acme` / `admin_acme` | 客户（开发者 / 管理员） | 客户侧：对话、查工单 |
| `support1` | 技术支持 | 内部侧：工作台、链路、接管回复 |
| `admin` | 管理员 | 运营指标页 |

## 端口一览

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:5173 |
| 后端 API | http://localhost:8000 （文档 `/docs`） |
| MySQL | localhost:3307 |
| Redis | localhost:6380 |
| Milvus | localhost:19531 |
| MinIO 控制台 | http://localhost:9003 |

> 宿主机端口已刻意错开常用端口，避免和本机其它服务冲突。

## 评估与压测

```bash
make eval     # 跑标准评估集，输出意图/引用/脱敏等指标
make bench    # 并发压测 + 阶段耗时分解 + 缓存优化前后对比
```

## 常见问题

- **`make setup` 报 Milvus 连接失败**：基础设施还没就绪，等 `make infra-status` 里 Milvus `healthy` 后重试。
- **后端启动报数据库/Redis 连不上**：确认 `make infra-up` 已起、`.env` 里的端口与 `deploy/docker-compose.dev.yml` 一致（3307 / 6380 / 19531）。
- **对话报 LLM 调用失败**：检查 `.env` 的 `DASHSCOPE_API_KEY` 是否已填、账户是否有额度。
- **端口被占用**：改 `deploy/docker-compose.dev.yml` 的宿主机端口映射，并同步改 `.env`。
- **想重置数据**：`make clean` 停容器并删数据卷，再从第 1 步重来。

所有命令都收在 `Makefile` 里，`make` 不带参数可查看完整列表。

## 容器化部署

整套服务（MySQL / Redis / Milvus + 后端 + 前端）用 `deploy/` 下的编排启动：

```bash
cd deploy
cp .env.example .env          # 本地；服务器用 .env.server.example
docker compose up -d --build
```

服务器上叠加 overlay 以限制内存并收紧端口绑定：

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d --build
```

`CONTEXT_PATH` 控制部署在反代下的路径前缀（默认 `/devsupport`）。它在构建期写进前端产物，改动后必须重建 `web` 镜像。真实密钥只写服务器上的 `deploy/.env`，仓库里只有 `.env.example` 与 `.env.server.example`（其中的口令仅供本地演示，部署必须改掉）。
