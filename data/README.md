# DevSupport AI 知识库原始资料

> 维护：liuqinhe

本目录存放系统的**知识库原始资料**——即 RAG（检索增强问答）所检索的源文档。这些资料既被后端用于切片、向量化、检索，也可供运营人员维护、用户直接查阅。

## 两类文档

| 目录 | 归属 | 可见范围 | 谁维护 | 进仓库 |
| --- | --- | --- | --- | --- |
| `knowledge/` | 平台内置 | 全部租户（切片 `tenant_id` 为空串） | 运营手工维护 | 是 |
| `uploads/` | 租户上传 | 仅所属租户（切片 `tenant_id` 为该租户） | 用户经 `/api/documents` 上传 | 否（已 gitignore） |

> **不要把用户文档放进 `knowledge/`。** 该目录下的 `*.md` 会被 `ingest_builtin` 当作**全局**文档摄取，对所有租户可见——那等于绕过租户隔离。用户文档一律经上传接口进入 `uploads/{tenant_id}/`。

## 资料清单（`knowledge/`）

| 文件 | 分类 | 主要内容 |
| --- | --- | --- |
| `01-接入指南.md` | 接入 | 注册实名、创建应用、获取 API Key、调用流程、接入检查清单 |
| `02-鉴权与签名.md` | 鉴权 | 鉴权要素、签名算法五步、401 与 SIGN_INVALID 排查 |
| `03-错误码手册.md` | 错误码 | 全部错误码的含义 / 原因 / 处理步骤（与数据库 error_code 表一致） |
| `04-Webhook回调.md` | 回调 | 回调机制、重试策略、验签、收不到回调的排查 |
| `05-限流与重试.md` | 限流 | QPS 与配额、429 处理、指数退避、限流 vs 故障判断 |
| `06-套餐计费规则.md` | 计费 | 套餐档位、计费方式、账单上涨原因、用量查询 |
| `07-数据质量说明.md` | 数据质量 | 数据来源与更新、返回为空/不一致的处理 |
| `08-FAQ.md` | FAQ | 高频问题速查 |

## 格式与维护说明

- 所有资料均为 **Markdown（`.md`）** 格式，纯文本、可版本管理、便于运营直接编辑。
- 文件名前缀数字用于排序；前缀映射到分类（见上表），ingest 时自动识别。
- 错误码手册中的章节标题（如 `AUTH_KEY_EXPIRED（HTTP 401）`）会被解析为 `error_code` 标量字段，支持错误码精确检索。
- **更新资料后需重新索引**：在 `backend/` 下执行 `python -m scripts.ingest_knowledge` 重新切片入库。

## 检索链路

```
data/knowledge/*.md（全局）  data/uploads/{tenant}/*（租户私有）
  → 切片(按章节, 400-600字)         → 解析(md/txt/pdf) → 脱敏 → 切片
  → DashScope text-embedding-v3 向量化
  → 写入 Milvus（含 doc_title/section/category/error_code + tenant_id/doc_id/source 标量）
  → 在线：向量检索 + BM25 混合（两侧均按 tenant_id 过滤）
        → RRF → gte-rerank 精排 → 上下文压缩（上传片段标注来源）→ 带引用生成
```

## 升级注意（租户隔离改造）

Milvus 的 `knowledge_chunk` 新增了 `tenant_id` / `doc_id` / `source` 三个标量字段。**已有环境必须重建 collection 并重新摄取**，否则检索会因缺字段而失败：

```bash
cd backend
.venv/bin/python -m scripts.migrate_doc_columns   # 给 MySQL 补列（幂等）
.venv/bin/python -m scripts.ingest_knowledge      # 重建 collection + 重新摄取
```

`create_all` 不会给已存在的表补列，所以 MySQL 那步必须单独跑。`ingest_knowledge` 会在重建后自动把已发布的用户上传文档重新摄取回来。
