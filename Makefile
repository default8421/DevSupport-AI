# @author: liuqinhe
.PHONY: infra-up infra-down infra-status venv test lint init-db seed ingest setup run front health clean

# 后端一律走 venv 里的解释器：本机系统 python 是 3.9，而项目要求 >=3.11，
# 用裸 python 会在 `str | None` 这类语法上直接失败。
PY = .venv/bin/python

# ---------- 基础设施（MySQL / Redis / Milvus）----------
# 本地只起中间件，后端/前端用 make run / make front 原生跑，便于热重载。
# 整套容器化部署走 deploy/docker-compose.yml（+ server overlay），别和这份混用。
DEV_COMPOSE = docker compose -f deploy/docker-compose.dev.yml

infra-up:        ## 启动基础设施
	$(DEV_COMPOSE) up -d

infra-down:      ## 停止基础设施
	$(DEV_COMPOSE) down

infra-status:    ## 查看基础设施状态
	$(DEV_COMPOSE) ps

# ---------- 开发环境 ----------
venv:            ## 建后端虚拟环境并装依赖（需已有 python3.11+）
	cd backend && python3.11 -m venv .venv \
	  && $(PY) -m pip install -q --upgrade pip \
	  && $(PY) -m pip install -q -e . "pytest>=8.3.0" "pytest-asyncio>=0.24.0" "ruff>=0.6.0"

# ---------- 测试 / 静态检查 ----------
test:            ## 跑单元测试
	cd backend && $(PY) -m pytest -q

lint:            ## ruff 静态检查
	cd backend && $(PY) -m ruff check app tests

# ---------- 数据准备（在 backend 下执行）----------
init-db:         ## 建 MySQL 表 + Milvus collection（--recreate 删表重建）
	cd backend && $(PY) -m scripts.init_db --recreate

seed:            ## 灌种子数据（租户/账号/日志/账单）
	cd backend && $(PY) -m scripts.seed_data

ingest:          ## 知识库切片向量化入库
	cd backend && $(PY) -m scripts.ingest_knowledge

setup: init-db seed ingest   ## 一键准备数据（建表 + 种子 + 知识库）

# ---------- 运行 ----------
run:             ## 启动后端 :8000
	cd backend && $(PY) -m uvicorn app.main:app --reload

front:           ## 启动前端 :5173
	cd frontend && npm run dev

health:          ## 健康检查
	curl -s http://localhost:8000/api/health

# ---------- 评估 / 压测 ----------
eval:            ## 跑标准评估集（意图/引用/脱敏等指标）
	cd backend && $(PY) -m eval.run_eval

bench:           ## 并发压测 + 阶段耗时分解 + 缓存优化对比
	cd backend && $(PY) -m benchmark.loadtest

# ---------- 清理 ----------
clean:           ## 停止并清理本地基础设施的容器与数据卷（会删本地数据）
	$(DEV_COMPOSE) down -v
