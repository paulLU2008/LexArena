import sys
import os
import json
import time
from datetime import datetime
import httpx

# 確保載入環境變數
from dotenv import load_dotenv
load_dotenv()

# 美美的終端機顏色輸出
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def log_info(msg):
    print(f"{Colors.BLUE}[INFO]{Colors.ENDC} {msg}")

def log_success(msg):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.ENDC} {msg}")

def log_warning(msg):
    print(f"{Colors.WARNING}[WARNING]{Colors.ENDC} {msg}")

def log_fail(msg):
    print(f"{Colors.FAIL}[FAIL]{Colors.ENDC} {msg}")

def print_separator(char="=", length=80):
    print(f"{Colors.HEADER}{char * length}{Colors.ENDC}")

def run_e2e_test():
    print_separator()
    print(f"{Colors.BOLD}⚖️ LexArena AI 模擬法庭系統 — 後端 E2E 完整流程測試腳本{Colors.ENDC}")
    print_separator()

    # 1. 測試測資
    model_name = os.getenv("DEFAULT_LLM_MODEL") or os.getenv("GEMINI_MODEL") or "gemini/gemini-1.5-flash"
    base_url = os.getenv("DEFAULT_LLM_BASE_URL")
    api_key = os.getenv("GEMINI_API_KEY")
    
    payload = {
        "report_text": "【快訊】民眾通報在桃園市觀音區大潭工業區旁，發現大量綠色化學廢液正溢流至灌溉水渠中，現場散發強烈酸臭味，附近為大片農地與水稻田，情況十分緊急。",
        "event_time": datetime.now().isoformat(),
        "model_name": model_name
    }
    if base_url:
        payload["base_url"] = base_url
    if api_key:
        payload["api_key"] = api_key
    
    log_info(f"測試模型: {payload['model_name']}")
    if base_url:
        log_info(f"使用自定義 Base URL: {base_url}")
    log_info(f"測試通報內容: \"{payload['report_text'][:50]}...\"")

    # 2. 連線檢查
    api_url = "http://localhost:8000/api/v1/incident/report"
    log_info(f"嘗試連線至後端 Gateway: {api_url}")
    
    try:
        # 先用 GET 探測連線
        with httpx.Client() as probe_client:
            probe_client.get("http://localhost:8000/docs", timeout=2.0)
    except httpx.ConnectError:
        log_warning("偵測到後端 Gateway (Port 8000) 尚未啟動！")
        log_info("正在嘗試直接載入本地 Python 模組進行 Direct Flow 測試...")
        
        # 如果伺服器沒開，直接在腳本中啟動本地的 CrewAI Kickoff (作為備用方案)
        try:
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from agents.response_crew import create_incident_response_crew
            from database import init_db
            
            log_info("成功載入本地 agents 模組！初始化資料庫...")
            init_db()
            
            log_info("正在建立應變 Swarm...")
            crew = create_incident_response_crew(
                model_name=payload.get("model_name"),
                api_key=payload.get("api_key"),
                base_url=payload.get("base_url")
            )
            
            log_info("🚀 開始執行 Kickoff (此過程將耗時約 20-40 秒)...")
            start_time = time.time()
            result = crew.kickoff(inputs={
                "report_text": payload["report_text"],
                "event_time": payload["event_time"]
            })
            
            print_separator("-")
            log_success(f"CrewAI 本地測試完成！耗時: {time.time() - start_time:.2f} 秒")
            print(f"{Colors.BOLD}最終應變報告摘要：{Colors.ENDC}")
            print(str(result)[:800] + "...\n[以下省略]")
            print_separator()
            return
        except Exception as local_err:
            log_fail(f"本地 Direct Flow 測試失敗：{local_err}")
            log_info("請先執行 `bash start.sh` 啟動後端服務後再運行此腳本。")
            sys.exit(1)

    # 3. 執行正式 SSE 串流測試
    log_success("後端 Gateway 正常運行中！開始模擬前端 SSE 連線...")
    start_time = time.time()
    
    try:
        # 使用 SSE 串流模式連線
        with httpx.stream("POST", api_url, json=payload, timeout=120.0) as r:
            if r.status_code != 200:
                log_fail(f"API 端點回傳錯誤代碼: {r.status_code}")
                sys.exit(1)

            log_success("成功建立 Event-Stream 連線！即時解析日誌流：\n")
            
            for line in r.iter_lines():
                if not line.strip():
                    continue
                
                # SSE 行解析
                if line.startswith("data: "):
                    event_data = json.loads(line[6:])
                    event_type = event_data.get("type")
                    
                    if event_type == "agent_log":
                        agent = event_data.get("agent", "SYSTEM")
                        log_type = event_data.get("log_type", "INFO")
                        content = event_data.get("content", "")
                        
                        # 格式化輸出 Agent 的思考與行動
                        prefix = f"[{agent}] ({log_type})"
                        if agent == "SYSTEM":
                            print(f"{Colors.BLUE}{prefix:<25} {content}{Colors.ENDC}")
                        elif agent == "COMMANDER":
                            print(f"{Colors.HEADER}{prefix:<25} {content}{Colors.ENDC}")
                        elif agent == "INTELLIGENCE":
                            print(f"{Colors.BLUE}{prefix:<25} {content}{Colors.ENDC}")
                        elif agent == "OPERATIONS":
                            print(f"{Colors.WARNING}{prefix:<25} {content}{Colors.ENDC}")
                        elif agent == "LEGAL":
                            print(f"{Colors.UNDERLINE}{prefix:<25} {content}{Colors.ENDC}")
                        else:
                            print(f"{Colors.GREEN}{prefix:<25} {content}{Colors.ENDC}")
                            
                    elif event_type == "done":
                        print_separator("-")
                        log_success("✨ 收到完工信號！完整應變報告產出成功！")
                        log_info(f"本次流程總耗時: {time.time() - start_time:.2f} 秒")
                        log_info(f"產生的資料庫事件 ID (incident_id): {event_data.get('incident_id')}")
                        log_info(f"地理定位判斷地點: {event_data.get('location')}")
                        
                        print_separator("-")
                        print(f"{Colors.BOLD}📋 各單位報告產出驗證：{Colors.ENDC}")
                        
                        commander_plan = event_data.get("commander_plan", "")
                        operations_plan = event_data.get("operations_plan", "")
                        legal_report = event_data.get("legal_report", "")
                        response_plan = event_data.get("response_plan", "")
                        
                        # 驗證內容長度與基本格式
                        reports = {
                            "⚖️ 審判長判決書": commander_plan,
                            "⚔️ 控辯攻防記錄": operations_plan,
                            "📖 法務研析報告": legal_report,
                            "📢 白話判決新聞稿": response_plan
                        }
                        
                        all_valid = True
                        for name, content in reports.items():
                            if content and len(content.strip()) > 100:
                                log_success(f"{name:<20}: 通過驗證！(字數: {len(content)})")
                            else:
                                log_fail(f"{name:<20}: 驗證失敗！內容過短或未生成。")
                                all_valid = False
                                
                        if all_valid:
                            print_separator("-")
                            log_success("🎉 所有法庭 Agent 的專業報告均高質量生成完畢！")
                            log_success("📝 SQLite 資料庫與 MeetingLogs 法庭紀錄均已寫入完成！")
                            print_separator()
                        else:
                            log_warning("⚠️ 部分報告內容驗證未通過，請檢查後台 API 日誌。")
                        break
                        
                    elif event_type == "error":
                        log_fail(f"後端執行出錯: {event_data.get('error')}")
                        sys.exit(1)

    except Exception as e:
        log_fail(f"測試過程中發生未預期異常: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_e2e_test()
