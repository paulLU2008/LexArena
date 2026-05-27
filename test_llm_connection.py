import os
import requests
import json
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# 加載環境變數
load_dotenv()

MODEL = os.getenv("DEFAULT_LLM_MODEL", "qwen-35b")
API_KEY = os.getenv("DEFAULT_LLM_API_KEY", "anywords")
BASE_URL = os.getenv("DEFAULT_LLM_BASE_URL", "https://solar-courier-ftp-storm.trycloudflare.com/v1")

print(f"--- 測試資訊 ---")
print(f"Model: {MODEL}")
print(f"Base URL: {BASE_URL}")
print(f"----------------\n")

def test_raw_requests():
    print("[測試 1] 使用原生 Requests 連線...")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "你好，請簡單自我介紹。"}],
        "temperature": 0.3
    }
    try:
        response = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            print("✅ 原生請求成功！")
            print(f"回覆內容: {response.json()['choices'][0]['message']['content'][:50]}...")
        else:
            print(f"❌ 原生請求失敗！狀態碼: {response.status_code}")
            print(f"錯誤訊息: {response.text}")
    except Exception as e:
        print(f"❌ 原生請求發生意外錯誤: {e}")

def test_langchain_openai():
    print("\n[測試 2] 使用 LangChain ChatOpenAI 連線...")
    try:
        llm = ChatOpenAI(
            model=MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0.3
        )
        response = llm.invoke("你好，請說出一種顏色。")
        print("✅ LangChain 請求成功！")
        print(f"回覆內容: {response.content}")
    except Exception as e:
        print(f"❌ LangChain 請求失敗！")
        print(f"錯誤日誌: {e}")

if __name__ == "__main__":
    test_raw_requests()
    test_langchain_openai()
