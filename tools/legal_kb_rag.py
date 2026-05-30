"""
中華民國核心法規 RAG 工具
═══════════════════════════════════════════════════════════════
使用 ChromaDB + SentenceTransformers (all-MiniLM-L6-v2) 對
knowledge_base 目錄下的中華民國憲法、刑法、民法進行語意向量索引。

具備「多庫聯檢」功能：當 AI 輸入案情關鍵字時，跨憲法、民法、刑法資料庫進行
語意檢索，自動依據 Cosine 距離合併排序，回傳最相關的前 8 大法條區塊。
"""
import os
import threading
import re
import json
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# ─────────────────────────────────────────────
# 路徑與常數設定
# ─────────────────────────────────────────────
_BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KB_DIR    = os.path.join(_BASE_DIR, "knowledge_base")
_CHROMA_DIR = os.path.join(_KB_DIR, "chroma_db")

_EMBED_MODEL   = "all-MiniLM-L6-v2"
_CHUNK_SIZE    = 1000
_CHUNK_OVERLAP = 200
_TOP_K         = 8   # 跨庫聯檢回傳最相關的 N 段法條

# 定義法律檔案與其對應的 ChromaDB Collection 名稱
_LAW_FILES = {
    "中華民國憲法.md": "roc_const",
    "中華民國刑法.md": "roc_criminal",
    "民法.md": "roc_civil"
}

# ─────────────────────────────────────────────
# 單例：全域初始化鎖與集合緩衝
# ─────────────────────────────────────────────
_collections = {}
_init_lock   = threading.Lock()


def _build_or_load_all_collections():
    """自動掃描並建立所有核心法規的向量索引（Thread-safe）"""
    global _collections
    
    with _init_lock:
        if len(_collections) == len(_LAW_FILES):
            return _collections

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
        client = chromadb.PersistentClient(path=_CHROMA_DIR)

        for filename, col_name in _LAW_FILES.items():
            filepath = os.path.join(_KB_DIR, filename)
            
            # 建立或載入 Collection
            col = client.get_or_create_collection(
                name=col_name,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )

            # 若 Collection 為空，進行文字切分與向量建立
            if col.count() == 0:
                if not os.path.exists(filepath):
                    print(f"[LegalRAG] 警告：找不到法律檔案 {filepath}，跳過此法規索引。")
                    continue

                print(f"[LegalRAG] 首次建立【{filename}】向量索引...")
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_text = f.read()

                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=_CHUNK_SIZE,
                    chunk_overlap=_CHUNK_OVERLAP,
                    separators=["\n\n", "\n", "。", "，", " ", ""],
                )
                chunks = splitter.split_text(raw_text)
                print(f"[LegalRAG] 共切出 {len(chunks)} 個 chunk，正在建立向量...")

                # 批次寫入
                batch_size = 500
                for i in range(0, len(chunks), batch_size):
                    batch = chunks[i : i + batch_size]
                    col.add(
                        documents=batch,
                        ids=[f"{col_name}_chunk_{j}" for j in range(i, i + len(batch))],
                    )
                print(f"[LegalRAG] 【{filename}】向量庫建立完成，共 {col.count()} 筆。")
            else:
                print(f"[LegalRAG] 載入【{filename}】向量庫（{col.count()} 筆）。")

            _collections[col_name] = col

        return _collections


# ─────────────────────────────────────────────
# CrewAI Tool 定義
# ─────────────────────────────────────────────
class LegalRAGTool(BaseTool):
    name: str = "LegalRAGTool"
    description: str = (
        "使用語意向量搜尋（RAG）查詢中華民國核心法規資料庫（憲法、刑法、民法）。"
        "根據案情關鍵字或起訴事實，跨法規庫檢索最相關的條文與法律效果。"
        "輸入：起訴罪名、爭點或案情描述（例如：共同正犯、損害賠償、不當得利、竊盜）。"
        "回傳：最相關的 8 段法條原文與法律效果（自動依相關度混合排序）。"
    )

    def _run(self, query: str = None, **kwargs) -> str:
        # 使用共用容錯解析工具
        from tools._utils import parse_tool_input
        query = parse_tool_input(
            query, kwargs, key_name="query",
            default="共同正犯、民事損害賠償、不當得利、竊盜罪",
        )

        # 2. 建立或載入 collections
        try:
            collections = _build_or_load_all_collections()
        except Exception as e:
            return f"【LegalRAG 初始化失敗】{e}"

        if not collections:
            return "【LegalRAG 錯誤】未成功載入任何法規資料庫，請確認 knowledge_base 目錄下有憲法、刑法或民法檔案。"

        # 3. 跨庫聯檢與結果排序
        all_hits = []
        
        # 法規檔案對照中文名，用於回傳結果標記
        law_names = {
            "roc_const": "中華民國憲法",
            "roc_criminal": "中華民國刑法",
            "roc_civil": "民法"
        }

        for col_name, col in collections.items():
            try:
                # 每個資料庫查詢 top K 個
                results = col.query(
                    query_texts=[query],
                    n_results=min(_TOP_K, col.count()),
                )
                
                docs = (results.get("documents") or [[]])[0]
                distances = (results.get("distances") or [[]])[0]
                
                for doc, dist in zip(docs, distances):
                    all_hits.append({
                        "law": law_names.get(col_name, col_name),
                        "doc": doc,
                        "distance": dist
                    })
            except Exception as e:
                print(f"[LegalRAG] 查詢 {col_name} 失敗: {e}")

        # 依據 distance 升序排序（Cosine 距離越小越相似）
        all_hits.sort(key=lambda x: x["distance"])
        top_hits = all_hits[:_TOP_K]

        if not top_hits:
            return (
                f"【LegalRAG 查詢無結果】在核心法規中未找到與「{query}」相關的條文。"
                "請嘗試不同的法律關鍵字（如：契約、侵權行為、竊盜、傷害、阻卻違法）。"
            )

        header = (
            f"【⚖️ 核心法規跨庫檢索結果】根據案情關鍵字「{query}」，"
            f"以下為最相關的 {len(top_hits)} 段法條條文（已依關聯度綜合排序）：\n\n"
        )
        
        body = []
        for i, hit in enumerate(top_hits):
            body_str = (
                f"【第 {i+1} 段】來源：🔹《{hit['law']}》 (相關度距離: {hit['distance']:.4f})\n"
                f"{hit['doc']}"
            )
            body.append(body_str)

        return header + "\n\n---\n\n".join(body)
