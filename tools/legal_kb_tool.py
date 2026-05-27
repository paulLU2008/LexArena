import os
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, ClassVar

# 法律知識庫檔案的絕對路徑（固定指向本專案的 knowledge_base）
_KB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge_base", "environmental_laws.md"
)

class LegalKBReadInputSchema(BaseModel):
    query: str = Field(
        ...,
        description=(
            "查詢關鍵字，例如：污染物類型（如重金屬、廢液、廢棄物）、"
            "情境描述（如土壤污染、非法棄置）、或法律名稱（如土污法、水污法、廢清法）。"
            "工具將返回相關條文及罰鍰金額建議。"
        )
    )

class LegalKBReadTool(BaseTool):
    name: str = "LegalKBReadTool"
    description: str = (
        "讀取環境法律知識庫，根據污染情境查詢適用法條、"
        "行政罰鍰金額範圍、刑事門檻，以及現場蒐證的 SOP 要求。"
        "每次查詢都會返回完整的相關法規內容供法務分析使用。"
    )
    args_schema: Type[BaseModel] = LegalKBReadInputSchema

    # 情境關鍵字 → 適用法律 Section tag 的映射
    _KEYWORD_MAP: ClassVar[dict] = {
        # 土污法關鍵字
        "土壤": "土污法",
        "地下水": "土污法",
        "重金屬": "土污法",
        "列管": "土污法",
        "整治場址": "土污法",
        "控制場址": "土污法",
        "農地": "土污法",
        # 水污法關鍵字
        "廢水": "水污法",
        "污水": "水污法",
        "排放": "水污法",
        "放流水": "水污法",
        "超標": "水污法",
        "廢液": "水污法",
        "直排": "水污法",
        # 廢清法關鍵字
        "廢棄物": "廢清法",
        "棄置": "廢清法",
        "非法傾倒": "廢清法",
        "清除": "廢清法",
        "有害": "廢清法",
        "化學品": "廢清法",
    }

    def _run(self, query: str) -> str:
        # 讀取知識庫
        try:
            with open(_KB_FILE, "r", encoding="utf-8") as f:
                full_content = f.read()
        except FileNotFoundError:
            return f"【法律知識庫錯誤】找不到知識庫檔案：{_KB_FILE}。請確認 knowledge_base/environmental_laws.md 存在。"
        except Exception as e:
            return f"【法律知識庫讀取錯誤】{e}"

        # 一律返回完整知識庫（讓 LLM 自行判斷適用條文）
        # 同時在前面加上查詢摘要，幫助 LLM 聚焦
        detected_laws = set()
        q_lower = query.lower()
        for kw, law in self._KEYWORD_MAP.items():
            if kw in query or kw in q_lower:
                detected_laws.add(law)

        if detected_laws:
            hint = f"【系統偵測】根據查詢內容「{query}」，建議重點關注：{'、'.join(sorted(detected_laws))}。\n\n"
        else:
            hint = f"【系統提示】根據查詢內容「{query}」，以下為完整環境法律知識庫，請自行判斷適用法條。\n\n"

        return hint + "=" * 60 + "\n" + full_content
