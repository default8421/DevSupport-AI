# @author: liuqinhe
"""Milvus 向量存储：knowledge_chunk collection 的管理与检索。

字段：
- pk           自增主键
- embedding    FLOAT_VECTOR(dim)，HNSW 索引，COSINE 度量
- content      原文片段
- doc_title    文档标题（引用 + 标量过滤）
- section      章节
- category     文档分类
- error_code   错误码（错误码类问题标量精确匹配）
- version      文档版本
- tenant_id    租户隔离。空串表示全局内置文档，对所有租户可见
- doc_id       关联 MySQL knowledge_document.id，删除文档时按此过滤删切片
- source       builtin / upload，用于在提示词里标注来源可信度
"""

import logging
from functools import lru_cache

from pymilvus import DataType, MilvusClient

from app.config import settings

log = logging.getLogger(__name__)

# 检索与语料读取的输出字段，两处必须一致，否则 BM25 语料会缺租户字段
OUTPUT_FIELDS = [
    "content",
    "doc_title",
    "section",
    "category",
    "error_code",
    "version",
    "tenant_id",
    "doc_id",
    "source",
]

VECTOR_DIM = settings.embedding_dim
COLLECTION = settings.milvus_collection


@lru_cache
def get_client() -> MilvusClient:
    return MilvusClient(uri=settings.milvus_uri)


def _q(value: str) -> str:
    """转义要嵌进过滤表达式的字符串字面量。

    租户 id 来自 JWT、doc_id 由服务端生成，理论上都是干净的，
    但过滤表达式是租户隔离的唯一屏障，不能依赖上游永远干净。
    """
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_filter(*, tenant_id: str, error_code: str | None = None) -> str:
    """拼装 Milvus 过滤表达式——全库唯一的过滤拼装点。

    全局内置文档 tenant_id 为空串，对所有租户可见；租户为空时只匹配全局，
    让"忘记传租户"失败到安全侧而不是放行全部。

    用 in 而不是 `a == x or a == y`：两者 Milvus 都支持，但 in 不需要考虑
    与后续 and 的运算符优先级——拼成 `A or B and C` 少一层括号就是错的。
    """
    scopes = ["", tenant_id] if tenant_id else [""]
    quoted = ", ".join(f'"{_q(s)}"' for s in scopes)
    parts = [f"tenant_id in [{quoted}]"]
    if error_code:
        parts.append(f'error_code == "{_q(error_code)}"')
    return " and ".join(parts)


def ensure_collection(recreate: bool = False) -> None:
    """创建 collection（含 HNSW 索引）。recreate=True 时先删除重建。"""
    client = get_client()
    if recreate and client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)
    if client.has_collection(COLLECTION):
        return

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("pk", DataType.INT64, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=VECTOR_DIM)
    schema.add_field("content", DataType.VARCHAR, max_length=4000)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=256)
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("category", DataType.VARCHAR, max_length=64)
    schema.add_field("error_code", DataType.VARCHAR, max_length=64)
    schema.add_field("version", DataType.VARCHAR, max_length=32)
    # enable_dynamic_field=False，故 insert 的每行都必须带上这三个字段
    schema.add_field("tenant_id", DataType.VARCHAR, max_length=64)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
    schema.add_field("source", DataType.VARCHAR, max_length=16)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    client.create_collection(
        collection_name=COLLECTION, schema=schema, index_params=index_params
    )


def drop_collection() -> None:
    client = get_client()
    if client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)


def insert(rows: list[dict]) -> int:
    """批量插入切片。rows 每项含 embedding/content/doc_title/section/category/error_code/version。"""
    client = get_client()
    res = client.insert(collection_name=COLLECTION, data=rows)
    client.flush(COLLECTION)
    return res.get("insert_count", len(rows))


def count() -> int:
    client = get_client()
    if not client.has_collection(COLLECTION):
        return 0
    client.load_collection(COLLECTION)
    res = client.query(COLLECTION, filter="pk >= 0", output_fields=["count(*)"])
    if res and "count(*)" in res[0]:
        return res[0]["count(*)"]
    return 0


def all_chunks(page_size: int = 1000) -> list[dict]:
    """分页取出全部切片（用于构建 BM25 关键词索引）。

    必须分页：原先的单次 limit=2000 会在语料变多后静默截断，
    超出的切片从关键词索引里无声消失——向量检索还能召回、BM25 不能，
    表现为"有时搜得到有时搜不到"，极难排查。
    """
    client = get_client()
    if not client.has_collection(COLLECTION):
        return []
    client.load_collection(COLLECTION)

    cap = settings.bm25_corpus_max
    rows: list[dict] = []
    offset = 0
    while True:
        page = client.query(
            COLLECTION,
            filter="pk >= 0",
            output_fields=OUTPUT_FIELDS,
            limit=page_size,
            offset=offset,
        )
        rows.extend(page)
        if len(page) < page_size:
            break
        if len(rows) >= cap:
            log.warning(
                "关键词索引语料已达上限 %d，部分切片未纳入；如需全量请调大 bm25_corpus_max",
                cap,
            )
            del rows[cap:]
            break
        offset += page_size
    return rows


def delete_by_doc(doc_id: str) -> int:
    """按 doc_id 删除该文档的全部切片。返回删除数量。"""
    client = get_client()
    if not client.has_collection(COLLECTION):
        return 0
    res = client.delete(COLLECTION, filter=f'doc_id == "{_q(doc_id)}"')
    # 必须 flush：不落盘的删除仍可能被检索命中，等于文档没删掉
    client.flush(COLLECTION)
    if isinstance(res, dict):
        return int(res.get("delete_count", 0))
    return 0


def search(
    query_vector: list[float],
    *,
    tenant_id: str,
    top_k: int = 20,
    error_code: str | None = None,
) -> list[dict]:
    """向量检索，返回片段 + 相似度分数（COSINE，越大越相似）。

    tenant_id 是必填关键字参数、刻意不给默认值：给了默认值，漏传就变成
    静默的跨租户放行；必填则漏传直接 TypeError，在测试里就暴露。

    过滤表达式由本函数内部拼装，不接受调用方传入的 expr——
    每个调用点自己拼就意味着每个调用点都可能忘掉租户条件。
    """
    client = get_client()
    client.load_collection(COLLECTION)
    results = client.search(
        collection_name=COLLECTION,
        data=[query_vector],
        limit=top_k,
        filter=build_filter(tenant_id=tenant_id, error_code=error_code),
        output_fields=OUTPUT_FIELDS,
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
    )
    hits = []
    for hit in results[0]:
        entity = hit.get("entity", {})
        item = {"score": float(hit.get("distance", 0.0))}
        for f in OUTPUT_FIELDS:
            item[f] = entity.get(f, "")
        hits.append(item)
    return hits
