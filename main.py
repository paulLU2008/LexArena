import sys
import re
import queue
import threading
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from pydantic import BaseModel, Field

from datetime import datetime
import os
import litellm

# ─────────────────────────────────────────────
# LLM 補丁：從獨立模組載入並安裝
# ─────────────────────────────────────────────
from llm_patches import install_patches, clean_think_tags
install_patches()

# ─────────────────────────────────────────────
# 日誌解析：從獨立模組載入
# ─────────────────────────────────────────────
from log_parser import (
    strip_ansi, detect_agent, classify_log,
    clean_bubble_text, AGENT_START_MSGS,
)

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv

from agents.response_crew import create_incident_response_crew
from database import init_db, save_incident, save_meeting_logs

# 載入環境變數
load_dotenv()

# 应用生命週期管理
@asynccontextmanager
async def lifespan(app):
    # 啟動時初始化資料庫
    init_db()
    yield

app = FastAPI(
    title="中華民國 AI 模擬法庭與判決分析系統 Gateway",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS 中介層：允許 index.html 從不同 origin 存取 API
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def serve_index():
    """Serve the LexArena landing page"""
    return FileResponse("index.html")

@app.get("/health")
def health_check():
    """Docker healthcheck 端點"""
    return {"status": "ok"}

class IncidentReport(BaseModel):
    report_text: str = Field(..., description="原始通報或新聞文字內容")
    event_time: datetime = Field(..., description="事件通報時間")
    model_name: str | None = Field(None, description="動態指定的 LLM 模型名稱")
    api_key: str | None = Field(None, description="動態指定的 API Key")
    base_url: str | None = Field(None, description="動態指定的 API Base URL")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "report_text": "【起訴事實】被告張三與李四於2026年4月9日深夜，共同攜帶油壓剪，前往桃園市觀音區某住宅行竊。",
                    "event_time": "2026-04-09T18:00:00",
                    "model_name": "gemini/gemini-2.5-flash"
                }
            ]
        }
    }

class ConnectionTestRequest(BaseModel):
    model_name: str | None = Field(None, description="動態指定的 LLM 模型名稱")
    api_key: str | None = Field(None, description="動態指定的 API Key")
    base_url: str | None = Field(None, description="動態指定的 API Base URL")

# ─────────────────────────────────────────────
# 系統日誌設定 (Log Rotation)
# ─────────────────────────────────────────────
logger = logging.getLogger("LexArena")
logger.setLevel(logging.INFO)
if not logger.handlers:
    # 輸出至 terminal，避免被 thread-local stdout 捕捉
    ch = logging.StreamHandler(sys.__stdout__)
    ch.setLevel(logging.INFO)
    
    # 建立 5MB 大小的循環日誌，最多保留 3 份備份
    fh = RotatingFileHandler("backend.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    fh.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    fh.setFormatter(formatter)
    
    logger.addHandler(ch)
    logger.addHandler(fh)

# ─────────────────────────────────────────────
# Thread-local stdout 隔離（防止多請求日誌串台）
# ─────────────────────────────────────────────
_thread_local = threading.local()

class QueueWriter:
    """捕捉 stdout 並存入 Queue 及 thread-local pending_logs（具備行緩衝）"""
    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, raw: str):
        # 取得 thread-local 緩衝區
        buf = getattr(_thread_local, 'line_buffer', "")
        buf += raw
        
        # 即使還沒遇到換行，也提早檢查是否出現 Agent 切換（解決 rich.console 卡換行的延遲問題）
        agent = detect_agent(strip_ansi(buf).strip())
        prev_agent = getattr(_thread_local, 'current_agent', 'SYSTEM')
        if agent and agent != prev_agent:
            _thread_local.current_agent = agent
            last_pushed = getattr(_thread_local, 'last_pushed_agent', None)
            if agent != last_pushed:
                _thread_local.last_pushed_agent = agent
                start_msg = AGENT_START_MSGS.get(agent, f"🚀 {agent} 開始執行任務...")
                self.q.put({
                    "type":     "agent_log",
                    "agent":    agent,
                    "log_type": "THOUGHT",
                    "content":  start_msg,
                })
                # 同步寫入 pending_logs，確保重播資料庫有記錄
                pending = getattr(_thread_local, 'pending_logs', [])
                pending.append({
                    "timestamp":  datetime.utcnow().isoformat(),
                    "agent_name": agent,
                    "log_type":   "THOUGHT",
                    "log_content": start_msg,
                })
                _thread_local.pending_logs = pending
        
        # 處理所有完整的行
        while '\n' in buf:
            line, buf = buf.split('\n', 1)
            self._process_line(line)
            
        _thread_local.line_buffer = buf

    def _process_line(self, raw_line: str):
        clean = strip_ansi(raw_line).strip()
        if not clean:
            return

        if clean.startswith("__TASK_COMPLETE__:"):
            try:
                task_index = int(clean.split(":")[-1])
                self.q.put({"type": "task_complete", "task_index": task_index})
            except Exception:
                pass
            return

        if clean.startswith("__LOCATION_DETERMINED__:"):
            try:
                location = clean.split(":")[-1].strip()
                self.q.put({"type": "location_determined", "location": location})
            except Exception:
                pass
            return

        # 偵測 Agent 切換：只在 CrewAI 正式 transition 行觸發
        agent = detect_agent(clean)
        prev_agent = getattr(_thread_local, 'current_agent', 'SYSTEM')
        if agent and agent != prev_agent:
            _thread_local.current_agent = agent
            # 推送切換通知（不與上一次推送的 agent 重複）
            last_pushed = getattr(_thread_local, 'last_pushed_agent', None)
            if agent != last_pushed:
                _thread_local.last_pushed_agent = agent
                self.q.put({
                    "type":     "agent_log",
                    "agent":    agent,
                    "log_type": "THOUGHT",
                    "content":  AGENT_START_MSGS.get(agent, f"🚀 {agent} 開始執行任務..."),
                })
        elif agent:
            _thread_local.current_agent = agent

        log_type = classify_log(clean)
        if log_type == "SKIP":
            return

        current_agent = getattr(_thread_local, 'current_agent', 'SYSTEM')

        # 儲存到 thread-local 待批次寫入 DB（所有類型都記錄）
        pending = getattr(_thread_local, 'pending_logs', [])
        pending.append({
            "timestamp": datetime.utcnow().isoformat(),
            "agent_name": current_agent,
            "log_type": log_type,
            "log_content": clean[:500],
        })
        _thread_local.pending_logs = pending

        # 只把 THOUGHT / ACTION 推送到 SSE（RESULT/SYSTEM 不推前端，避免氣泡噪音）
        if log_type not in ("THOUGHT", "ACTION"):
            return

        bubble_text = clean_bubble_text(clean)[:500]
        if not bubble_text:
            return

        # 建立結構化訊息
        msg = {
            "type": "agent_log",
            "agent": current_agent,
            "log_type": log_type,
            "content": bubble_text,
        }

        # 推送到 SSE Queue（前端即時渲染氣泡用）
        self.q.put(msg)

    def flush(self):
        sys.__stdout__.flush()

class ThreadLocalStdout:
    """代理物件：依據當前 thread 決定寫入目標"""
    def __init__(self, original):
        self._original = original

    def write(self, msg):
        writer = getattr(_thread_local, 'writer', None)
        if writer is not None:
            writer.write(msg)
        else:
            self._original.write(msg)

    def flush(self):
        writer = getattr(_thread_local, 'writer', None)
        if writer is not None:
            writer.flush()
        else:
            self._original.flush()

    def isatty(self):
        return self._original.isatty()

    def fileno(self):
        return self._original.fileno()

# 全域只替換一次，之後透過 thread-local 切換
_original_stdout = sys.stdout
sys.stdout = ThreadLocalStdout(_original_stdout)

# ─────────────────────────────────────────────
# Task 索引常數（集中管理）
# ─────────────────────────────────────────────
TASK_INDEX_EXTRACTION   = 0
TASK_INDEX_INTELLIGENCE = 1
TASK_INDEX_OPERATIONS   = 2
TASK_INDEX_LEGAL        = 3  # 法務稽查官（新增）
TASK_INDEX_COMMANDER    = 4  # 原為 3
TASK_INDEX_PR           = 5  # 原為 4

def _make_step_callback(q: queue.Queue):
    """
    CrewAI step_callback：每個 Agent 完成一個 action-observation 循環後觸發。
    用於在 LLM 回應後立即更新前端氣泡（補充 stdout 可能延遲的問題）。
    """
    def callback(agent_action):
        agent_code = getattr(_thread_local, 'current_agent', 'SYSTEM')
        if agent_code == 'SYSTEM':
            return

        # 嘗試從 agent_action 中取得有意義的內容
        content = (
            getattr(agent_action, 'tool', None) or
            getattr(agent_action, 'thought', None) or
            getattr(agent_action, 'result', None) or
            str(agent_action)
        )
        content = strip_ansi(str(content)).strip()[:300]
        bubble  = clean_bubble_text(content)[:120]
        if not bubble or len(bubble) < 3:
            return

        q.put({
            "type":     "agent_log",
            "agent":    agent_code,
            "log_type": "ACTION",
            "content":  bubble,
        })
    return callback


def _get_task_output_safe(crew, index: int) -> str:
    """安全地按索引取 Task 輸出，失敗就回傳空字串，並清洗 LLM 思考殘留"""
    try:
        raw = crew.tasks[index].output.raw or ""
    except (IndexError, AttributeError):
        return ""

    # ── 清洗 LLM 殘留雜訊 ──
    import re

    # 1. 移除 <think>...</think> 標籤及其內容
    raw = clean_think_tags(raw)
    # 2. 移除 <|channel|> 等特殊控制 token
    raw = re.sub(r'<\|?[a-zA-Z_]+\|?>', '', raw)

    # 3. 偵測正式文件起始點：截斷前方的 LLM 思考或提示詞殘留（如 "Please ensure all keys..."）
    # 移除過於通用的 '臺灣'，避免誤切截到文件尾端
    doc_markers = ['【法院名稱】', '【判決主文】', '【事實】', '【理由】',
                   '【當事人】', '# 【', '## 【', '---\n#',
                   '**【法院名稱】', '**【判決主文】', '【判決懶人包】']
    earliest_pos = -1
    for marker in doc_markers:
        pos = raw.find(marker)
        # 只檢查前 800 字元，避免匹配到文件尾端導致內容被大量截斷
        if 0 < pos < 800:
            if earliest_pos == -1 or pos < earliest_pos:
                earliest_pos = pos

    if earliest_pos > 0:
        raw = raw[earliest_pos:]

    return raw.strip()

# ─────────────────────────────────────────────
# 後台執行緒：跑 Crew 並把結果推回 Queue
# ─────────────────────────────────────────────
def run_crew_in_background(crew, inputs, q: queue.Queue, fallback_check_fn):
    writer = QueueWriter(q)
    _thread_local.writer = writer
    _thread_local.current_agent = "SYSTEM"
    _thread_local.pending_logs = []

    try:
        result = crew.kickoff(inputs=inputs)
        result_str = str(result)
        is_fallback = fallback_check_fn(result_str)

        extraction_out  = _get_task_output_safe(crew, TASK_INDEX_EXTRACTION)
        operations_out  = _get_task_output_safe(crew, TASK_INDEX_OPERATIONS)
        legal_out       = _get_task_output_safe(crew, TASK_INDEX_LEGAL)
        commander_out   = _get_task_output_safe(crew, TASK_INDEX_COMMANDER) or result_str
        pr_out          = _get_task_output_safe(crew, TASK_INDEX_PR)        or result_str

        # 解析當事人與案件類型（相容舊版地點與污染物）
        loc_str = "未明案件"
        pol_str = "未明類型"
        for line in extraction_out.splitlines():
            if any(k in line for k in ["當事人", "Parties", "地點", "Location"]):
                parts = line.split(":")
                if len(parts) > 1:
                    loc_str = parts[-1].strip() or loc_str
            elif any(k in line for k in ["案件類型", "Case Type", "污染物", "Pollutant"]):
                parts = line.split(":")
                if len(parts) > 1:
                    pol_str = parts[-1].strip() or pol_str

        # 儲存事件主紀錄，並取得 incident_id
        incident_id = save_incident(
            inputs.get("event_time", ""),
            loc_str, pol_str,
            commander_out, pr_out,
            legal_out
        )

        # 批次儲存會議日誌到 MeetingLogs
        pending_logs = getattr(_thread_local, 'pending_logs', [])
        save_meeting_logs(incident_id, pending_logs)

        q.put({
            "type": "done",
            "status": "success" if not is_fallback else "partial_success_with_fallback",
            "data_source": "FALLBACK" if is_fallback else "LIVE_API",
            "incident_id": incident_id,
            "location": loc_str,
            "commander_plan": commander_out,
            "operations_plan": operations_out,
            "legal_report": legal_out,
            "response_plan": pr_out
        })

    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        logger.error("====== BACKEND CRITICAL EXCEPTION ======\n%s\n========================================", tb_str)
        q.put({"type": "error", "error": f"{str(e)}\n{tb_str}"})
    finally:
        _thread_local.writer = None
        _thread_local.pending_logs = []

# ─────────────────────────────────────────────
# API 端點：SSE 串流
# ─────────────────────────────────────────────
@app.post("/api/v1/incident/report")
def handle_incident_report(report: IncidentReport):

    def event_generator():
        q = queue.Queue()
        crew = create_incident_response_crew(
            model_name=report.model_name,
            api_key=report.api_key,
            base_url=report.base_url
        )
        # 掛載 step_callback：每個 Agent action 完成後即時更新前端氣泡
        crew.step_callback = _make_step_callback(q)
        inputs = {
            "report_text": report.report_text,
            "event_time": report.event_time.isoformat()
        }

        def fallback_check(result_str):
            return "備援" in result_str or "API_DOWN" in result_str

        thread = threading.Thread(
            target=run_crew_in_background,
            args=(crew, inputs, q, fallback_check)
        )
        thread.start()

        # 初始系統訊息
        yield f"data: {json.dumps({'type': 'agent_log', 'agent': 'SYSTEM', 'log_type': 'SYSTEM', 'content': '⚡ 系統已建立後台任務，AI 團隊開始啟動...'}, ensure_ascii=False)}\n\n"

        while True:
            try:
                # 使用 timeout 避免無限等待，並定時發送 keep-alive 如果需要
                item = q.get(timeout=60)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item["type"] in ("done", "error"):
                    break
            except queue.Empty:
                # 發送一個空的注釋作為 keep-alive，防止連線斷開
                yield ": keep-alive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/v1/test_connection")
def test_connection(req: ConnectionTestRequest):
    try:
        kwargs = {
            "messages": [{"role": "user", "content": "Ping. Please reply 'Pong' only."}],
            "max_tokens": 10,
        }
        
        # 預設 fallback
        model = req.model_name.strip() if req.model_name else "gemini/gemini-2.5-flash"
        kwargs["model"] = model
        
        if req.api_key:
            kwargs["api_key"] = req.api_key.strip()
            
        if req.base_url:
            kwargs["api_base"] = req.base_url.strip()

        response = litellm.completion(**kwargs)
        
        return {"status": "success", "message": "連線成功！模型回應正常。"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
