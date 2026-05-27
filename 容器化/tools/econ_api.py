import os
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type, ClassVar, List

class EconomicAPIInputSchema(BaseModel):
    location: str = Field(..., description="要查詢工廠登記資料的行政區域或地點字串，例如：觀音區、彰化縣。")

class EconomicAPI_Tool(BaseTool):
    name: str = "EconomicAPI_Tool"
    description: str = "呼叫環境部 EMS_S_01 開放資料 API，查詢指定地點附近已被環保列管的事業單位（含工廠登記證、行業別、毒化物/土壤列管狀態）。"
    # args_schema: Type[BaseModel] = EconomicAPIInputSchema

    # 高風險行業關鍵字
    HIGH_RISK_KEYWORDS: ClassVar[List[str]] = ["電鍍", "化學", "化工", "金屬表面處理", "酸洗", "染整", "塑膠",
                          "農藥", "有機溶劑", "廢棄物", "印刷電路", "半導體", "石化"]

    def _run(self, location: str = None, **kwargs) -> str:
        # 1. 處理輸入極限容錯：不論 LLM 傳入 dict、list 還是 raw string，皆能安全解析
        actual_loc = ""
        if location:
            actual_loc = location
        elif kwargs:
            if "location" in kwargs:
                actual_loc = kwargs["location"]
            elif "query" in kwargs:
                actual_loc = kwargs["query"]
            else:
                actual_loc = " ".join(str(v) for v in kwargs.values())
        
        # 2. 如果傳入的 location 是 JSON 字串，進行二次解析
        if isinstance(actual_loc, str):
            trimmed = actual_loc.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                try:
                    import json
                    parsed = json.loads(trimmed)
                    if isinstance(parsed, dict):
                        actual_loc = parsed.get("location") or parsed.get("query") or str(list(parsed.values())[0])
                    elif isinstance(parsed, list) and len(parsed) > 0:
                        if isinstance(parsed[0], dict):
                            actual_loc = parsed[0].get("location") or parsed[0].get("query") or str(list(parsed[0].values())[0])
                        else:
                            actual_loc = str(parsed[0])
                except Exception:
                    pass

        # 3. 確保 actual_loc 有預設值，不為空
        if not actual_loc or not isinstance(actual_loc, str) or len(actual_loc.strip()) == 0:
            actual_loc = "桃園市觀音區"

        location = actual_loc

        api_url = os.getenv("ECON_API_URL", "")
        api_key = os.getenv("ECON_API_KEY", "")

        if not api_url or not api_key:
            return (
                "【系統降級通知】未設定工廠查詢 API 金鑰 (ECON_API_KEY)，無法進行線上工廠查詢。"
                f" 建議以備援假設處理：假定『{location}』方圓 5 公里內存在高風險化學工廠，"
                "需成立專案稽查小組前往追查。"
            )

        try:
            with httpx.Client(timeout=15.0, verify=False) as client:
                params = {
                    "format": "json",
                    "offset": "0",
                    "limit": "1000",
                    "api_key": api_key,
                }
                response = client.get(api_url, params=params)
                response.raise_for_status()

                raw = response.json()
                records = raw if isinstance(raw, list) else raw.get("records", [])

                if not records:
                    return f"【工廠查詢結果】API 回傳無資料，可能為系統維護中。建議以備援假設處理。"

                # 篩選出符合 location 關鍵字的事業
                matched = []
                for rec in records:
                    county = rec.get("county", "") or ""
                    township = rec.get("township", "") or ""
                    addr = rec.get("facilityaddress", "") or ""
                    full_text = f"{county}{township}{addr}"

                    if any(kw in full_text for kw in [location] + location.replace("市", " ").replace("縣", " ").replace("區", " ").split()):
                        matched.append(rec)

                if not matched:
                    return f"【工廠查詢結果】在『{location}』附近未發現環保列管事業單位（共比對 {len(records)} 筆全國資料）。"

                # 從結果中篩選高風險產業 + 土壤/毒化物列管
                risky = []
                for rec in matched:
                    industry = rec.get("industryname", "") or ""
                    name = rec.get("facilityname", "") or ""
                    is_toxic = rec.get("istoxic", "0") == "1"
                    is_soil = rec.get("issoil", "0") == "1"

                    if is_toxic or is_soil or any(kw in f"{industry}{name}" for kw in self.HIGH_RISK_KEYWORDS):
                        risky.append(rec)

                summary = [f"【工廠查詢結果】在『{location}』附近共掃描到 {len(matched)} 家環保列管事業："]

                if risky:
                    summary.append(f"  ⚠️ 其中 {len(risky)} 家屬於高風險/已列管污染事業：")
                    for i, r in enumerate(risky[:6]):
                        name = r.get("facilityname", "未知")
                        addr = r.get("facilityaddress", "未知")
                        industry = r.get("industryname", "未知")
                        flags = []
                        if r.get("istoxic") == "1": flags.append("毒化物")
                        if r.get("issoil") == "1": flags.append("土壤")
                        if r.get("iswaste") == "1": flags.append("廢棄物")
                        flag_str = "、".join(flags) if flags else "一般"
                        summary.append(f"    {i+1}. {name}｜行業：{industry}｜列管：{flag_str}｜地址：{addr[:30]}")
                    if len(risky) > 6:
                        summary.append(f"    ...及其他 {len(risky)-6} 家。")
                else:
                    summary.append("  未發現明顯的高風險化學/電鍍/金屬製造業者，但建議仍需現場稽查確認。")

                return "\n".join(summary)

        except Exception as e:
            return (
                f"【工廠查詢降級】{e}。"
                f" 系統備援假設：『{location}』方圓 5 公里內可能存在中小型化學製品工廠，"
                "建議成立專案稽查小組前往追查。"
            )
