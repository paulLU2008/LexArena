import os
import httpx
import base64
import time
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

class GenerateImageInputSchema(BaseModel):
    prompt: str = Field(..., description="場景與畫面的詳細文字描述 (請盡量使用英文以獲得最佳生成效果)")

class GenerateImageTool(BaseTool):
    name: str = "GenerateImageTool"
    description: str = "呼叫 Google Gemini 原生 Imagen API 產生一張對應的災情或應變處理現場圖片。回傳值會是能直接在 Markdown 中顯示的外連圖片標籤檔名。若生成失敗會回傳佔位說明。"
    # args_schema: Type[BaseModel] = GenerateImageInputSchema

    def _run(self, prompt: str = None, **kwargs) -> str:
        # 1. 處理輸入極限容錯：不論 LLM 傳入 dict、list 還是 raw string，皆能安全解析
        actual_prompt = ""
        if prompt:
            actual_prompt = prompt
        elif kwargs:
            if "prompt" in kwargs:
                actual_prompt = kwargs["prompt"]
            elif "query" in kwargs:
                actual_prompt = kwargs["query"]
            else:
                actual_prompt = " ".join(str(v) for v in kwargs.values())
        
        # 2. 如果傳入的 prompt 是 JSON 字串，進行二次解析
        if isinstance(actual_prompt, str):
            trimmed = actual_prompt.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                try:
                    import json
                    parsed = json.loads(trimmed)
                    if isinstance(parsed, dict):
                        actual_prompt = parsed.get("prompt") or parsed.get("query") or str(list(parsed.values())[0])
                    elif isinstance(parsed, list) and len(parsed) > 0:
                        if isinstance(parsed[0], dict):
                            actual_prompt = parsed[0].get("prompt") or parsed[0].get("query") or str(list(parsed[0].values())[0])
                        else:
                            actual_prompt = str(parsed[0])
                except Exception:
                    pass

        # 3. 確保 actual_prompt 有預設值，不為空
        if not actual_prompt or not isinstance(actual_prompt, str) or len(actual_prompt.strip()) == 0:
            actual_prompt = "Soil heavy metal pollution response site"

        prompt = actual_prompt

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "【圖片生成已跳過】缺少 GEMINI_API_KEY，請直接撰寫文字新聞稿。"
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-fast-generate-001:predict?key={api_key}"
        
        # 依照 Gemini Imagen API 規格組裝 Payload
        payload = {
            "instances": [
                {
                    "prompt": prompt
                }
            ],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": "16:9" # 寬螢幕比例較適合新聞
            }
        }
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                
                predictions = data.get("predictions", [])
                if not predictions:
                    return "【圖片生成已跳過】API 未回傳結果，請直接撰寫文字新聞稿。"
                    
                b64_img = predictions[0].get("bytesBase64Encoded")
                if not b64_img:
                    return "【圖片生成已跳過】未取得影像資料，請直接撰寫文字新聞稿。"
                    
                # 確保儲存路徑存在
                os.makedirs("assets/images", exist_ok=True)
                
                # 儲存到本地端
                timestamp = int(time.time() * 1000)
                filename = f"gen_{timestamp}.png"
                filepath = os.path.join("assets/images", filename)
                
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(b64_img))
                    
                # 回傳相對路徑的 Markdown 標籤，讓外層可以渲染
                return f"![新聞配圖](assets/images/{filename})"
                
        except Exception as e:
            # 優雅降級：不讓 Agent 因為圖片失敗而卡住
            return f"【圖片生成已跳過】{e}。請直接撰寫文字新聞稿，不需要再次嘗試生成圖片。"
