import os
import unittest
from unittest import mock
import sys

# 模擬外部依賴，確保測試在未安裝 crewai 的環境下也能運行
mock_crewai = mock.MagicMock()
sys.modules["crewai"] = mock_crewai
sys.modules["tools.weather_api"] = mock.MagicMock()
sys.modules["tools.image_tool"] = mock.MagicMock()
sys.modules["tools.agri_api"] = mock.MagicMock()
sys.modules["tools.econ_api"] = mock.MagicMock()
sys.modules["tools.legal_kb_rag"] = mock.MagicMock()

from agents.response_crew import create_incident_response_crew

class TestLLMConfig(unittest.TestCase):
    def test_llm_parameter_override(self):
        """驗證：傳入的參數應該蓋過環境變數"""
        custom_model = "openai/gpt-4o"
        custom_key = "sk-test-key"
        custom_url = "https://api.openai.com/v1"
        
        try:
            crew = create_incident_response_crew(
                model_name=custom_model,
                api_key=custom_key,
                base_url=custom_url
            )
            # 檢查 Mock 對象的調用參數
            # 取得 create_incident_response_crew 內部創件的 LLM 實例
            # 由於我們 Mock 了 LLM，我們檢查它被呼叫時的參數
            args, kwargs = mock_crewai.LLM.call_args
            self.assertEqual(kwargs.get("model"), custom_model)
            self.assertEqual(kwargs.get("api_key"), custom_key)
            self.assertEqual(kwargs.get("base_url"), custom_url)
        except TypeError as e:
            print(f"\n[EXPECTED FAILURE] Caught expected TypeError: {e}")
            raise e

    def test_llm_env_fallback(self):
        """驗證：沒傳參數時，應該讀取環境變數 DEFAULT_LLM_*"""
        env_vars = {
            "DEFAULT_LLM_MODEL": "openai/deepseek-chat",
            "DEFAULT_LLM_API_KEY": "sk-deepseek-key",
            "DEFAULT_LLM_BASE_URL": "https://api.deepseek.com"
        }
        
        with mock.patch.dict(os.environ, env_vars):
            create_incident_response_crew()
            args, kwargs = mock_crewai.LLM.call_args
            self.assertEqual(kwargs.get("model"), "openai/deepseek-chat")
            self.assertEqual(kwargs.get("api_key"), "sk-deepseek-key")
            self.assertEqual(kwargs.get("base_url"), "https://api.deepseek.com")

    def test_llm_legacy_compatibility(self):
        """驗證：當舊的 GEMINI_API_KEY 存在且沒其他設定時，仍能運作"""
        # 清除可能影響測試的環境變數
        for k in ["DEFAULT_LLM_MODEL", "DEFAULT_LLM_API_KEY", "DEFAULT_LLM_BASE_URL"]:
            os.environ.pop(k, None)
            
        env_vars = {
            "GEMINI_API_KEY": "legacy-gemini-key"
        }
        
        with mock.patch.dict(os.environ, env_vars):
            create_incident_response_crew()
            args, kwargs = mock_crewai.LLM.call_args
            # 應使用預設模型，但金鑰為 legacy
            self.assertEqual(kwargs.get("api_key"), "legacy-gemini-key")
            self.assertIn("gemini", kwargs.get("model", "").lower())

if __name__ == "__main__":
    unittest.main()
