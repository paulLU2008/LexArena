"""
農業部水土保持署 土石流警戒 API 工具
═══════════════════════════════════════════════════════════════
呼叫農業部開放資料 (data.moa.gov.tw) 的兩支端點：

1. GetDebrisVillInfo  — 土石流潛勢溪流資料（警戒值 + 偵測站）
2. GetCustomerDebrisAlertInfo — 即時土石流紅/黃色警戒狀態

這兩組資料對土壤污染應變至關重要：
  - 了解事故周遭是否有潛勢溪流（污染物擴散路徑）
  - 掌握是否已發布警戒（影響緊急疏散決策）

API 說明：
  base URL : https://data.moa.gov.tw
  端點 1   : /DebrisAlertServices/GetDebrisVillInfo/
  端點 2   : /DebrisAlertServices/GetCustomerDebrisAlertInfo/
  認證     : 無需 API Key（公開資料）
"""
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

_MOA_BASE     = "https://data.moa.gov.tw"
_ENDPOINT_STREAM = "/Service/OpenData/FromM/GetDebrisVillInfo.aspx"
_ENDPOINT_ALERT  = "/Service/OpenData/FromM/GetCustomerDebrisAlertInfo.aspx"

# 縣市別名對照（支援簡稱）
_COUNTY_ALIAS = {
    "台北": "臺北市", "台中": "臺中市", "台南": "臺南市", "台東": "臺東縣",
    "臺北": "臺北市", "臺中": "臺中市", "臺南": "臺南市", "臺東": "臺東縣",
}


class AgricultureAPIInputSchema(BaseModel):
    location: str = Field(
        ...,
        description="要查詢土石流警戒狀況的行政區域，例如：彰化縣、桃園市觀音區。",
    )


class AgricultureAPI_Tool(BaseTool):
    name: str = "AgricultureAPI_Tool"
    description: str = (
        "呼叫農業部水土保持署開放資料 API (data.moa.gov.tw)，"
        "查詢指定地點的土石流潛勢溪流資料與即時警戒狀態。"
        "回傳：潛勢溪流警戒值、偵測站資訊、紅/黃色警戒發布情況。"
    )
    # args_schema: Type[BaseModel] = AgricultureAPIInputSchema

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

        # 標準化縣市名稱
        normalized = location
        for alias, full in _COUNTY_ALIAS.items():
            if alias in normalized and full not in normalized:
                normalized = normalized.replace(alias, full)

        # 從 location 提取縣市關鍵字
        county_keywords = [
            "臺北市","台北市","新北市","桃園市","臺中市","台中市",
            "臺南市","台南市","高雄市","基隆市","新竹市","新竹縣",
            "苗栗縣","彰化縣","南投縣","雲林縣","嘉義市","嘉義縣",
            "屏東縣","宜蘭縣","花蓮縣","臺東縣","台東縣",
            "澎湖縣","金門縣","連江縣",
        ]
        county_filter = None
        for kw in county_keywords:
            if kw in normalized or kw in location:
                county_filter = kw
                break

        # 鄉鎮關鍵字（去除縣市後綴）
        town_filter = (
            location
            .replace(county_filter or "", "")
            .replace("市", "").replace("縣", "")
            .replace("區", "").replace("鄉", "").replace("鎮", "")
            .strip()
        )

        try:
            # 農業部資料集較大，增加超時時間至 30 秒以確保下載完整
            with httpx.Client(timeout=30.0, verify=False) as client:
                stream_result = self._query_stream(client, county_filter, town_filter, location)
                alert_result  = self._query_alert(client, county_filter, town_filter, location)
        except Exception as e:
            return (
                f"【農業部 API 連線失敗】{e}\n"
                f"備援假設：『{location}』附近可能有土石流潛勢區，"
                "若為山坡地/溪流旁之污染事件，建議立即評估土石流擴散風險並啟動預警機制。"
            )

        output = [f"【農業部水土保持署查詢結果】地點：『{location}』\n"]
        output.append(stream_result)
        output.append("")
        output.append(alert_result)
        return "\n".join(output)

    # ─────────────────────────────────────────
    # 端點 1：土石流潛勢溪流
    # ─────────────────────────────────────────
    def _query_stream(
        self, client: httpx.Client,
        county_filter: str | None,
        town_filter: str,
        location: str,
    ) -> str:
        try:
            resp = client.get(_MOA_BASE + _ENDPOINT_STREAM, follow_redirects=True)
            resp.raise_for_status()
            # 檢查是否為 JSON 格式
            if "application/json" not in resp.headers.get("Content-Type", "").lower():
                return "  ⚠️ 潛勢溪流查詢失敗：伺服器未回傳 JSON 格式（可能是系統維護中）。"
            raw = resp.json()
        except httpx.HTTPError as e:
            return f"  ⚠️ 潛勢溪流查詢失敗 (HTTP {getattr(e.response, 'status_code', 'Err')}): 伺服器忙碌中。"
        except ValueError: # 包含 JSONDecodeError
            return "  ⚠️ 潛勢溪流查詢失敗：回傳資料非有效 JSON 格式，請稍後再試。"
        except Exception as e:
            return f"  ⚠️ 潛勢溪流查詢失敗：{e}"

        records = raw if isinstance(raw, list) else raw.get("Data", raw.get("data", []))
        if not records:
            return "  潛勢溪流資料：API 暫無資料。"

        matched = [
            r for r in records
            if self._match_location(r, county_filter, town_filter, location,
                                    ("County", "Town"))
        ]

        if not matched:
            return (
                f"  潛勢溪流資料：在『{location}』附近未發現列管潛勢溪流"
                f"（全國共 {len(records)} 筆資料）。"
            )

        lines = [f"  ▶ 土石流潛勢溪流：共發現 {len(matched)} 條潛勢溪流"]
        for i, r in enumerate(matched[:6]):
            county = r.get("County", "")
            town   = r.get("Town", "")
            dno    = r.get("Debrisno", "N/A")
            alert  = r.get("AlertValue", "N/A")
            sta1   = r.get("station1StationName", "")
            sta2   = r.get("station2StationName", "")
            stations = "、".join(filter(None, [sta1, sta2])) or "無偵測站"
            lines.append(
                f"    {i+1}. 溪流編號：{dno}｜縣市：{county}{town}"
                f"｜警戒值：{alert}｜偵測站：{stations}"
            )
        if len(matched) > 6:
            lines.append(f"    …及其他 {len(matched)-6} 條。")
        return "\n".join(lines)

    # ─────────────────────────────────────────
    # 端點 2：即時土石流警戒
    # ─────────────────────────────────────────
    def _query_alert(
        self, client: httpx.Client,
        county_filter: str | None,
        town_filter: str,
        location: str,
    ) -> str:
        try:
            resp = client.get(_MOA_BASE + _ENDPOINT_ALERT, follow_redirects=True)
            resp.raise_for_status()
            # 檢查是否為 JSON 格式
            if "application/json" not in resp.headers.get("Content-Type", "").lower():
                return "  ⚠️ 即時警戒查詢失敗：伺服器未回傳 JSON 格式（可能是系統維護中）。"
            raw = resp.json()
        except httpx.HTTPError as e:
            return f"  ⚠️ 即時警戒查詢失敗 (HTTP {getattr(e.response, 'status_code', 'Err')}): 伺服器忙碌中。"
        except ValueError: # 包含 JSONDecodeError
            return "  ⚠️ 即時警戒查詢失敗：回傳資料非有效 JSON 格式，請稍後再試。"
        except Exception as e:
            return f"  ⚠️ 即時警戒查詢失敗：{e}"

        records = raw if isinstance(raw, list) else raw.get("Data", raw.get("data", []))
        if not records:
            return "  即時警戒：目前全國無土石流警戒發布。"

        matched = [
            r for r in records
            if self._match_location(r, county_filter, town_filter, location,
                                    ("county", "town"))
        ]

        if not matched:
            return (
                f"  即時警戒：『{location}』附近目前無土石流警戒"
                f"（全國共 {len(records)} 筆警戒資料）。"
            )

        red_alerts    = [r for r in matched if r.get("alert_date")]
        yellow_alerts = [r for r in matched if r.get("alert_date_yellow") and not r.get("alert_date")]

        lines = [f"  ▶ 即時土石流警戒：在『{location}』附近發現 {len(matched)} 筆警戒"]
        if red_alerts:
            lines.append(f"    🔴 紅色警戒 {len(red_alerts)} 筆：")
            for r in red_alerts[:4]:
                lines.append(
                    f"      • {r.get('county','')}{r.get('town','')}{r.get('vill','')}"
                    f" — 溪流 {r.get('debrisNo','?')}"
                    f"（發布時間：{r.get('alert_date','')}）"
                )
        if yellow_alerts:
            lines.append(f"    🟡 黃色警戒 {len(yellow_alerts)} 筆：")
            for r in yellow_alerts[:4]:
                lines.append(
                    f"      • {r.get('county','')}{r.get('town','')}{r.get('vill','')}"
                    f" — 溪流 {r.get('debrisNo','?')}"
                    f"（發布時間：{r.get('alert_date_yellow','')}）"
                )
        return "\n".join(lines)

    # ─────────────────────────────────────────
    # 通用位置比對
    # ─────────────────────────────────────────
    @staticmethod
    def _match_location(
        record: dict,
        county_filter: str | None,
        town_filter: str,
        location: str,
        keys: tuple[str, str],
    ) -> bool:
        ck, tk = keys
        county_val = str(record.get(ck, "") or "")
        town_val   = str(record.get(tk, "") or "")
        combined   = county_val + town_val

        # 縣市精確比對
        if county_filter and county_filter not in county_val:
            # 嘗試用原始 location 中任何 2 字以上子串比對
            if not any(loc_part in combined for loc_part in location.split() if len(loc_part) >= 2):
                return False

        # 有鄉鎮關鍵字時進一步過濾
        if town_filter and len(town_filter) >= 2:
            return town_filter in town_val or town_filter in county_val

        return bool(county_filter and county_filter in county_val)
