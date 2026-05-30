"""
Tool 共用工具函數
═══════════════════════════════════════════════════════════════
提供所有 CrewAI Tool 共用的輸入解析與容錯邏輯，
避免每個 Tool 中都重複相同的 ~30 行 JSON 容錯程式碼。
"""
import json
from typing import Any


def parse_tool_input(
    positional_arg: Any,
    kwargs: dict,
    key_name: str,
    default: str = "",
) -> str:
    """
    統一解析 CrewAI Tool 的輸入參數。

    CrewAI 的 LLM 有時會傳入：
    - 正常的字串
    - 包裹在 JSON dict 中的字串 (如 '{"query": "xxx"}')
    - 包裹在 JSON list 中的字串 (如 '[{"query": "xxx"}]')
    - kwargs 中的各種欄位名稱

    此函數統一處理所有情況，回傳乾淨的字串。

    Args:
        positional_arg: Tool._run() 的第一個位置參數值
        kwargs: Tool._run() 的 **kwargs
        key_name: 期望的 JSON key 名稱（如 "query", "location", "prompt"）
        default: 當所有解析都失敗時的預設值

    Returns:
        解析後的乾淨字串
    """
    # Step 1: 從 positional_arg 或 kwargs 中取得原始值
    raw_value = ""
    if positional_arg:
        raw_value = positional_arg
    elif kwargs:
        if key_name in kwargs:
            raw_value = kwargs[key_name]
        elif "query" in kwargs:
            raw_value = kwargs["query"]
        else:
            raw_value = " ".join(str(v) for v in kwargs.values())

    # Step 2: 如果是 JSON 字串，進行二次解析
    if isinstance(raw_value, str):
        trimmed = raw_value.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or \
           (trimmed.startswith("[") and trimmed.endswith("]")):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict):
                    raw_value = (
                        parsed.get(key_name)
                        or parsed.get("query")
                        or str(list(parsed.values())[0])
                    )
                elif isinstance(parsed, list) and len(parsed) > 0:
                    if isinstance(parsed[0], dict):
                        raw_value = (
                            parsed[0].get(key_name)
                            or parsed[0].get("query")
                            or str(list(parsed[0].values())[0])
                        )
                    else:
                        raw_value = str(parsed[0])
            except Exception:
                pass

    # Step 3: 確保回傳值不為空
    if not raw_value or not isinstance(raw_value, str) or len(raw_value.strip()) == 0:
        return default

    return raw_value.strip()
