import os
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# 自訂例外層 - 讓上層邏輯能夠辨識 API 降級狀態
class WeatherAPIDownException(Exception):
    pass

# 備援資料常數
FALLBACK_RESPONSE = (
    "【氣象 API 降級通知】無法連線至中央氣象署，已啟動備援預測資料。"
    " 該區域假定過去 1 小時降雨量為 50 mm（備援預設值），請務必以防洪規格執行應變。"
)

class WeatherAPIInputSchema(BaseModel):
    location: str = Field(..., description="要查詢天氣的行政區域，例如：內湖區")

class WeatherAPI_Tool(BaseTool):
    name: str = "WeatherAPI_Tool"
    description: str = "呼叫中央氣象署 API，傳入行政區，並回傳該區過去 1 小時降雨量。"
    # args_schema: Type[BaseModel] = WeatherAPIInputSchema

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
            actual_loc = "內湖區"

        location = actual_loc

        api_key = os.getenv("CWA_API_KEY")
        if not api_key:
            # Bug 2 修復：CrewAI 會吞掉 Exception，所以不能 raise，
            # 必須回傳 fallback 字串讓 Agent 拿到備援資料繼續運作
            return FALLBACK_RESPONSE

        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"
        params = {
            "Authorization": api_key,
            "format": "JSON"
        }

        try:
            print(f"\n[DEBUG] 正在請求氣象資料: {location}...")
            # 增加超時時間至 15 秒，並允許重試，關閉 SSL 驗證避免憑證問題
            with httpx.Client(timeout=15.0, follow_redirects=True, verify=False) as client:
                response = client.get(url, params=params)
                print(f"[DEBUG] API 回傳狀態碼: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                
                # 氣象局 API 不同端點可能叫 location 或 Station
                locations = data.get("records", {}).get("location", [])
                if not locations:
                    locations = data.get("records", {}).get("Station", [])
                    
                print(f"[DEBUG] 找到 {len(locations)} 個觀測站資料")
                if not locations:
                    print(f"[DEBUG] 找不到測站數據，請檢查 API 金鑰與網路。")

                target_station = None
                best_match_score = 0

                # 嘗試模糊比對：如果第一次沒找到，嘗試精簡地點名稱
                def find_best(loc_query):
                    best_match = None
                    max_score = 0
                    for loc in locations:
                        # 暴力掃描：檢查所有數值，只要包含關鍵字就加分
                        match_found = False
                        for val in loc.values():
                            if val and isinstance(val, str) and loc_query in val:
                                match_found = True
                                break
                        
                        # 同時保留之前的模糊比對分數
                        s_name = loc.get("StationName") or loc.get("locationName") or loc.get("stationName") or ""
                        score = self._match_score(loc_query, s_name)
                        if match_found: score += 50 # 強加權
                        
                        if score > max_score:
                            max_score = score
                            best_match = loc
                    return best_match, max_score

                target_station, score = find_best(location)

                # 如果得分太低，嘗試去掉「市、區、縣、鄉、鎮」再找一次
                if score < 20:
                    clean_loc = location.replace("桃園市", "").replace("區", "").replace("市", "").replace("縣", "").replace("鄉", "").replace("鎮", "")
                    print(f"[DEBUG] 精簡地點為: {clean_loc} 重新搜尋...")
                    target_station, score = find_best(clean_loc)

                if target_station and score > 10:
                    # 嘗試從新的 API 結構 (RainfallElement) 抓取過去 1 小時降雨量
                    rain_element = target_station.get("RainfallElement", {})
                    rain_mm = None
                    
                    past1hr = rain_element.get("Past1hr", {}).get("Precipitation")
                    now_rain = rain_element.get("Now", {}).get("Precipitation")
                    
                    raw_val = past1hr if past1hr is not None else now_rain
                    if raw_val is not None:
                        try:
                            rain_mm = float(raw_val)
                        except (ValueError, TypeError):
                            rain_mm = 0.0
                    
                    # 相容舊版 API 結構 (weatherElement) 的防呆機制
                    if rain_mm is None:
                        weather_elements = target_station.get("weatherElement", [])
                        for elem in weather_elements:
                            if elem.get("elementName") in ["HOUR_1", "NOW"]:
                                try:
                                    rain_mm = float(elem.get("elementValue", "0"))
                                except (ValueError, TypeError):
                                    rain_mm = 0.0
                                break

                    # 獲取測站名稱（檢查多個可能鍵名）
                    st_name = (
                        target_station.get('StationName') or 
                        target_station.get('locationName') or 
                        target_station.get('stationName') or 
                        "未知測站"
                    )

                    if rain_mm is not None:
                        # 特殊值 -99 代表無觀測資料
                        if rain_mm < 0:
                            return f"查詢到 {st_name} 測站，但該站目前回報無有效觀測資料 (值={rain_mm})，建議以備援假設 50mm 處理。"
                        return (
                            f"經查詢氣象署資料，最接近 {location} 的測站為「{st_name}」，"
                            f"過去 1 小時累積降雨量為 {rain_mm} mm。"
                        )
                    else:
                        return f"找到測站「{st_name}」但無降雨量欄位，建議以備援假設 50mm 處理。"
                else:
                    return f"找不到 {location} 附近的自動氣象站資料，建議以備援假設 50mm 處理。"

        except (httpx.RequestError, httpx.HTTPStatusError, Exception) as exc:
            # Bug 2 修復：不拋出 Exception，改為回傳 fallback 字串
            # 讓 CrewAI Agent 能繼續運作，不被框架吞掉
            return f"{FALLBACK_RESPONSE} (原因: {type(exc).__name__}: {exc})"

    @staticmethod
    def _match_score(query: str, station_name: str) -> int:
        """
        Bug 6 修復：改善位置比對邏輯，使用評分制度而非簡單 in 判斷。
        分數越高代表匹配越精準。
        """
        if not query or not station_name:
            return 0

        score = 0
        # 完全一致
        if query == station_name:
            return 100

        # 站名完整包含在查詢中 (如查詢 "台北市內湖區" 包含站名 "內湖")
        if station_name in query:
            score += len(station_name) * 10  # 匹配的字越長越好

        # 查詢包含在站名中
        if query in station_name:
            score += len(query) * 5

        # 逐字比對共同字元數量
        common_chars = sum(1 for c in station_name if c in query)
        score += common_chars

        return score
