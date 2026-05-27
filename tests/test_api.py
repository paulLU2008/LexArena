import unittest
from unittest import mock
import sys
import json
from datetime import datetime

# 模擬外部依賴，避免實際執行
sys.modules["crewai"] = mock.MagicMock()
sys.modules["database"] = mock.MagicMock()
# 直接模擬整個模組路徑
mock_crew_module = mock.MagicMock()
sys.modules["agents.response_crew"] = mock_crew_module

from main import app
from fastapi.testclient import TestClient

client = TestClient(app)

class TestMainAPI(unittest.TestCase):
    @mock.patch("main.create_incident_response_crew")
    @mock.patch("main.threading.Thread")
    def test_api_receives_llm_config(self, mock_thread, mock_create_crew):
        """驗證 API 端點是否能接收並處理 LLM 配置"""
        payload = {
            "report_text": "測試通報內容",
            "event_time": datetime.now().isoformat(),
            "model_name": "openai/gpt-4o",
            "api_key": "sk-12345",
            "base_url": "https://custom.api/v1"
        }
        
        # 發送 POST 請求
        response = client.post("/api/v1/incident/report", json=payload)
        
        # 檢查是否調用了 create_incident_response_crew 並帶入正確參數
        mock_create_crew.assert_called_with(
            model_name="openai/gpt-4o",
            api_key="sk-12345",
            base_url="https://custom.api/v1"
        )
        self.assertEqual(response.status_code, 200)

if __name__ == "__main__":
    unittest.main()
