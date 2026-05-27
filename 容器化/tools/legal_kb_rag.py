"""
法務稽查官 RAG 工具
═══════════════════════════════════════════════════════════════
使用 ChromaDB + SentenceTransformers (all-MiniLM-L6-v2) 對
knowledge_base/environmental_laws.md 建立語意向量索引，
取代舊版全文回傳 (LegalKBReadTool)，大幅減少 Token 消耗。

啟動時第一次呼叫會自動切塊並建立向量資料庫，
之後直接從磁碟載入，不必重新計算。
向量資料庫儲存位置：knowledge_base/chroma_db/
"""
import os
import threading
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# ─────────────────────────────────────────────
# 路徑常數
# ─────────────────────────────────────────────
_BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KB_FILE   = os.path.join(_BASE_DIR, "knowledge_base", "environmental_laws.md")
_CHROMA_DIR = os.path.join(_BASE_DIR, "knowledge_base", "chroma_db")

_COLLECTION_NAME = "env_laws_v1"
_EMBED_MODEL     = "all-MiniLM-L6-v2"
_CHUNK_SIZE      = 1000
_CHUNK_OVERLAP   = 200
_TOP_K           = 8   # 每次查詢回傳最相關的 N 段法條

# ─────────────────────────────────────────────
# 單例：全域只初始化一次，避免重複載入模型
# ─────────────────────────────────────────────
_collection      = None
_collection_lock = threading.Lock()


def _build_or_load_collection():
    """初始化或從磁碟載入 ChromaDB collection（thread-safe）"""
    global _collection
    if _collection is not None:
        return _collection

    with _collection_lock:
        if _collection is not None:      # double-check
            return _collection

        try:
            import chromadb
            from chromadb.utils.embedding_functions import (
                SentenceTransformerEmbeddingFunction,
            )
            from langchain_text_splitters import RecursiveCharacterTextSplitter
        except ImportError as e:
            raise ImportError(
                f"缺少 RAG 依賴套件：{e}。請執行：\n"
                "pip install chromadb sentence-transformers langchain-text-splitters"
            ) from e

        os.makedirs(_CHROMA_DIR, exist_ok=True)

        ef = SentenceTransformerEmbeddingFunction(model_name=_EMBED_MODEL)
        client     = chromadb.PersistentClient(path=_CHROMA_DIR)
        collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

        # 若向量庫是空的（首次或手動清除），重新索引
        if collection.count() == 0:
            if not os.path.exists(_KB_FILE):
                raise FileNotFoundError(
                    f"找不到法律知識庫：{_KB_FILE}\n"
                    "請確認 knowledge_base/environmental_laws.md 存在。"
                )

            print(f"[LegalRAG] 首次建立向量索引，切塊中（{_KB_FILE}）...")
            with open(_KB_FILE, "r", encoding="utf-8") as f:
                raw_text = f.read()

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=_CHUNK_SIZE,
                chunk_overlap=_CHUNK_OVERLAP,
                separators=["\n\n", "\n", "。", "，", " ", ""],
            )
            chunks = splitter.split_text(raw_text)
            print(f"[LegalRAG] 共切出 {len(chunks)} 個 chunk，正在建立向量...")

            # ChromaDB 建議每批不超過 5461 筆
            batch_size = 500
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                collection.add(
                    documents=batch,
                    ids=[f"chunk_{j}" for j in range(i, i + len(batch))],
                )
            print(f"[LegalRAG] 向量資料庫建立完成，共 {collection.count()} 筆。")
        else:
            print(f"[LegalRAG] 從磁碟載入向量資料庫（{collection.count()} 筆）。")

        _collection = collection
        return _collection


# ─────────────────────────────────────────────
# CrewAI Tool 定義
# ─────────────────────────────────────────────
class LegalRAGInputSchema(BaseModel):
    query: str = Field(
        ...,
        description=(
            "污染情境關鍵字，例如：「重金屬土壤污染、列管場址、非法棄置廢棄物」。"
            "工具會用語意搜尋回傳最相關的法條段落，包含罰鍰金額與刑事門檻。"
        ),
    )


class LegalRAGTool(BaseTool):
    name: str = "LegalRAGTool"
    description: str = (
        "使用語意向量搜尋（RAG）查詢環境法律知識庫，"
        "根據污染情境回傳最相關的法條、行政罰鍰範圍與刑事門檻。"
        "比全文讀取更快、更省 Token。"
        "輸入：污染類型關鍵字（如：重金屬、廢水排放、非法棄置）。"
        "回傳：最相關的 8 段法條原文（含土污法、水污法、廢清法條文）。"
    )
    # 移除 args_schema 限制以完全避開 Pydantic 強制 JSON 字典對應失敗（解決 GGUF 模型直接輸出字串的報錯）
    # args_schema: Type[BaseModel] = LegalRAGInputSchema

    def _run(self, query: str = None, **kwargs) -> str:
        # 1. 處理輸入極限容錯：不論 LLM 傳入 dict、list 還是 raw string，皆能安全解析
        actual_query = ""
        if query:
            actual_query = query
        elif kwargs:
            if "query" in kwargs:
                actual_query = kwargs["query"]
            else:
                # 拼接所有參數值
                actual_query = " ".join(str(v) for v in kwargs.values())
        
        # 2. 如果傳入的 query 是 JSON 字串，進行二次解析
        if isinstance(actual_query, str):
            trimmed = actual_query.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                try:
                    import json
                    parsed = json.loads(trimmed)
                    if isinstance(parsed, dict) and "query" in parsed:
                        actual_query = parsed["query"]
                    elif isinstance(parsed, list) and len(parsed) > 0:
                        if isinstance(parsed[0], dict) and "query" in parsed[0]:
                            actual_query = parsed[0]["query"]
                        else:
                            actual_query = str(parsed[0])
                except Exception:
                    pass

        # 3. 確保 actual_query 有預設值，不為空
        if not actual_query or not isinstance(actual_query, str) or len(actual_query.strip()) == 0:
            actual_query = "重金屬土壤污染、列管場址、非法棄置廢棄物"

        query = actual_query

        try:
            collection = _build_or_load_collection()
        except (ImportError, FileNotFoundError) as e:
            return f"【LegalRAG 初始化失敗】{e}"
        except Exception as e:
            return f"【LegalRAG 載入錯誤】{e}"

        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(_TOP_K, collection.count()),
            )
        except Exception as e:
            return f"【LegalRAG 查詢錯誤】{e}"

        docs = (results.get("documents") or [[]])[0]
        if not docs:
            return (
                f"【LegalRAG 查詢無結果】查詢「{query}」未找到相關法條。"
                "請嘗試不同關鍵字，例如：土壤污染、廢水排放、廢棄物棄置。"
            )

        header = (
            f"【語意搜尋結果】根據查詢「{query}」，"
            f"以下為最相關的 {len(docs)} 段法條（依相關度排序）：\n\n"
        )
        body = "\n\n---\n\n".join(
            f"【第 {i+1} 段】\n{doc}" for i, doc in enumerate(docs)
        )
        return header + body
