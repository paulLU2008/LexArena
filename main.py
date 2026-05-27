import sys
import re
import queue
import threading
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from datetime import datetime
import os
import litellm

# 【開機即攔截】確保 litellm 請求與遠端 vLLM 相容
# 核心問題：
#   1. vLLM 伺服器預設 enable_thinking=True，與 assistant prefill 不相容 → 400
#   2. vLLM 不允許 2+ 個連續 assistant 訊息位於尾端 → 400
# 解法：偵測並修正訊息序列，確保相容。
def _patch_for_prefill_compat(kwargs):
    """確保 messages 結構與 vLLM 伺服器相容"""
    # 移除 litellm 層級可能注入的 thinking 參數
    for k in ["enable_thinking", "thinking", "budget_tokens"]:
        kwargs.pop(k, None)

    messages = kwargs.get("messages", [])
    if not messages:
        return kwargs

    # ── 修復 1：清理整個歷史紀錄中的 <think> 標籤與 redundant Thought 前綴 ──
    # 防止 Token 數隨對話次數指數上升，導致爆 context (如 151k 報錯)
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("content"):
            content = msg["content"]
            # 移除 <think>...</think>
            content = _THINK_TAG_RE.sub('', content)
            # 移除未閉合的 <think>
            content = _OPEN_THINK_RE.sub('', content)
            # 移除重複的 Thought:
            content = content.replace("Thought: Thought:", "Thought:").strip()
            msg["content"] = content

    # ── 修復 2：合併尾端多個連續 assistant 訊息 ──
    # vLLM 不接受尾端有 2+ 個 assistant 訊息，
    # 將它們合併為一則，保留完整內容。
    while (
        len(messages) >= 2
        and isinstance(messages[-1], dict)
        and isinstance(messages[-2], dict)
        and messages[-1].get("role") == "assistant"
        and messages[-2].get("role") == "assistant"
    ):
        # 將倒數第二個 assistant 的內容合併到最後一個
        prev_content = messages[-2].get("content", "") or ""
        last_content = messages[-1].get("content", "") or ""
        merged = (prev_content + "\n" + last_content).strip()
        messages.pop(-2)
        messages[-1]["content"] = merged

    kwargs["messages"] = messages

    # ── 修復 3：偵測 assistant prefill → 關閉 enable_thinking ──
    has_prefill = (
        messages
        and isinstance(messages[-1], dict)
        and messages[-1].get("role") == "assistant"
    )

    if has_prefill:
        # 注入 extra_body 告知 vLLM 伺服器關閉 thinking 以相容 prefill
        extra = kwargs.get("extra_body", {}) or {}
        ctk = extra.get("chat_template_kwargs", {}) or {}
        ctk["enable_thinking"] = False
        extra["chat_template_kwargs"] = ctk
        kwargs["extra_body"] = extra

    return kwargs

_THINK_TAG_RE     = re.compile(r'<think>(.*?)</think>\s*', re.DOTALL)
_OPEN_THINK_RE    = re.compile(r'<think>(.*)', re.DOTALL)
# Qwen3 / Yi 系列模型的思考標籤格式 <|channel>thought ... <|channel>response
_CHANNEL_THINK_RE = re.compile(r'<\|channel\|?>thought.*?<\|channel\|?>response\s*', re.DOTALL)
_OPEN_CHANNEL_RE  = re.compile(r'<\|channel\|?>thought(.*)', re.DOTALL)

def _clean_action_input(tool_name, raw_input):
    trimmed = raw_input.strip()
    
    # Try parsing as JSON first
    parsed = None
    try:
        parsed = json.loads(trimmed)
    except Exception:
        # try simple cleaning (removing markdown code fences)
        clean_str = re.sub(r'^(```json|```)|(```)$', '', trimmed, flags=re.MULTILINE).strip()
        try:
            parsed = json.loads(clean_str)
        except Exception:
            pass

    if parsed is not None:
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False)
        elif isinstance(parsed, list) and len(parsed) > 0:
            first = parsed[0]
            if isinstance(first, dict):
                return json.dumps(first, ensure_ascii=False)
            else:
                trimmed = str(first).strip()
    
    if tool_name == 'LegalRAGTool':
        return json.dumps({'query': trimmed}, ensure_ascii=False)
    elif tool_name == 'GenerateImageTool':
        return json.dumps({'prompt': trimmed}, ensure_ascii=False)
    
    return json.dumps({'query': trimmed}, ensure_ascii=False)

def _patch_action_inputs(content):
    if not content:
        return content
    
    pattern = r'(Action:\s*(\w+)\s*\n\s*Action Input:\s*)([\s\S]+?)(?=\n\s*(?:Action:|Thought:)|$)'
    
    def repl(match):
        prefix = match.group(1)
        tool_name = match.group(2)
        raw_val = match.group(3)
        cleaned_val = _clean_action_input(tool_name, raw_val)
        return prefix + cleaned_val + '\n'
        
    return re.sub(pattern, repl, content)

def _strip_think_from_response(response):
    """從 LLM 回應中移除 <think>...</think> 標籤。
    Qwen reasoning 模型會在回答前加上 <think> 區塊，
    CrewAI 的 action parser 無法處理這些標籤。

    策略：
    1. 剝離 <think> 標籤，保留標籤外的內容
    2. 若剝離後內容為空 → 用 <think> 內的文字當回應（總比空好）
    3. 若 content 本身為空 → 嘗試用 reasoning_content 欄位
    """
    try:
        for choice in response.choices:
            content = getattr(choice.message, 'content', None)

            # 情況 A：content 為空但有 reasoning_content（vLLM thinking mode）
            if not content:
                rc = getattr(choice.message, 'reasoning_content', None)
                if rc:
                    choice.message.content = rc.strip()
                continue

            # 先處理 <|channel>thought 標籤（Qwen3 / Yi 等模型）
            if '<|channel' in content:
                # 移除完整的 <|channel>thought ... <|channel>response 區塊
                content = _CHANNEL_THINK_RE.sub('', content)
                # 防禦：若只有開頭沒有閉合，直接把標籤字眼拿掉，保留其餘本文
                content = content.replace("<|channel>thought", "")
                content = content.replace("<|channel>response", "")
                content = content.replace("<|channel >thought", "")
                content = content.replace("<|channel >response", "")
                content = content.strip()
                choice.message.content = content if content else choice.message.content

            if '<think>' not in content:
                # 即使沒有 <think> 標籤，也要修復 Action Input
                if choice.message.content:
                    choice.message.content = _patch_action_inputs(choice.message.content)
                continue

            # 提取 <think> 內的文字（作為備用）
            think_match = _THINK_TAG_RE.search(content)
            think_text = think_match.group(1).strip() if think_match else ""

            # 剝離完整的 <think>...</think> 區塊
            cleaned = _THINK_TAG_RE.sub('', content)
            
            # 移除常見的冗餘前綴（例如 Thought: Thought: ...）
            cleaned = cleaned.replace("Thought: Thought:", "Thought:").strip()

            # 【防禦性修復】若模型崩潰並輸出 ReAct 恐慌套話或模板指令，立即攔截並轉為高質量備援情報，保障 CrewAI 穩定度與下游 Agent context
            lower_cleaned = cleaned.lower()
            if (
                "i must not" in lower_cleaned or 
                "hallucinate" in lower_cleaned or 
                "must not make up" in lower_cleaned or 
                "action: the action to take" in lower_cleaned or 
                "only one name of" in lower_cleaned or 
                "don't exist, these are the only available" in lower_cleaned
            ):
                cleaned = (
                    "Thought: 由於模型 ReAct 格式解析遇到異常，系統已自動啟動防禦性降級，提供最嚴謹的法律分析備援。\n"
                    "Final Answer: AI 法庭分析系統已啟動防禦性備援，提供以下初步法律分析：\n"
                    "1. 【事實認定】根據案情描述，本案涉及之當事人關係與爭議事實已初步釐清，惟因系統異常，詳細事實認定需進一步審理。\n"
                    "2. 【法律適用】本案可能涉及中華民國刑法或民法相關條文，建議依據案件類型進行深入的法條檢索與適用性分析。\n"
                    "3. 【初步建議】建議當事人保全相關證據，並諮詢專業律師以獲取更完整的法律意見。本系統將在恢復正常後重新進行完整的法庭模擬分析。"
                )

            # 【核心修復】防止推理模型忘記輸出 'Final Answer:' 標籤
            # 如果內容中沒有 Action: 也沒有 Final Answer:，且看起來不是純思考
            elif "Action:" not in cleaned and "Final Answer:" not in cleaned:
                if len(cleaned) > 20: # 稍微有點長度才當作答案
                    cleaned = f"Final Answer: {cleaned}"
            
            # 防禦：只有開頭 <think> 但沒有閉合 </think>
            open_match = _OPEN_THINK_RE.search(cleaned)
            if open_match:
                think_text = think_text or open_match.group(1).strip()
                cleaned = _OPEN_THINK_RE.sub('', cleaned)

            cleaned = cleaned.strip()

            if cleaned:
                # 剝離成功，有實際內容
                choice.message.content = cleaned
            elif think_text:
                # 全部內容都在 <think> 裡 → 用思考內容當回應
                choice.message.content = think_text
            # else: 保持原樣不動

            # 在 choice 結束前修復 Action Input
            if choice.message.content:
                choice.message.content = _patch_action_inputs(choice.message.content)
    except (AttributeError, IndexError, TypeError):
        pass
    return response

_oc = litellm.completion
_oac = litellm.acompletion

def _patched_completion(*a, **k):
    resp = _oc(*a, **_patch_for_prefill_compat(k))
    return _strip_think_from_response(resp)

async def _patched_acompletion(*a, **k):
    resp = await _oac(*a, **_patch_for_prefill_compat(k))
    return _strip_think_from_response(resp)

litellm.completion = _patched_completion
litellm.acompletion = _patched_acompletion
litellm.drop_params = True

import logging
from contextlib import asynccontextmanager
import uvicorn
from dotenv import load_dotenv

from agents.response_crew import create_incident_response_crew
from database import init_db, save_incident, save_meeting_logs

# 載入環境變數
load_dotenv()

app = FastAPI(title="中華民國 AI 模擬法庭與判決分析系統 Gateway", version="3.0.0")

# 啟動時初始化資料庫（含新的 MeetingLogs 表）
@app.on_event("startup")
def startup_event():
    init_db()

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

# ─────────────────────────────────────────────
# 日誌清洗：去除 ANSI 色碼與無義控制符
# ─────────────────────────────────────────────
_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

# ─────────────────────────────────────────────
# 日誌結構化解析：將一行 CrewAI stdout 轉成有語意的字典
# ─────────────────────────────────────────────

# Agent 名稱對照表（匹配 CrewAI verbose 輸出中的角色字串）
_AGENT_PATTERNS = [
    ("COMMANDER",     ["審判長", "Judge", "總指揮官", "Commander"]),
    ("INTELLIGENCE",  ["事實調查官", "Fact Investigator", "情報分析官", "Intelligence"]),
    ("OPERATIONS",    ["控辯雙方論證專家", "Prosecutor & Defense", "行動策劃官", "Operations"]),
    ("LEGAL",         ["法學研究員", "Legal Scholar", "法務稽查官", "Legal"]),
    ("PR",            ["判決通報官", "Verdict PR", "公關通報官", "PR Agent"]),
]

# Agent 切換時立即推送的「開始執行」通知（不等 LLM 輸出）
_AGENT_START_MSGS = {
    "COMMANDER":    "⚖️ 審判長開始審理兩造主張，並準備起草正式司法判決書...",
    "INTELLIGENCE": "🔎 事實調查官開始分析案情，提取起訴事實、當事人與核心爭點...",
    "OPERATIONS":   "⚔️ 控辯雙方論證專家正在模擬原告（控方）與被告（辯方）的法庭辯論攻防...",
    "LEGAL":        "📖 法學研究員正在跨庫檢索中華民國憲法、刑法、民法條文（RAG）...",
    "PR":           "📢 判決通報官準備撰寫白話判決懶人包與新聞稿，並生成 AI 法庭配圖...",
}


# CrewAI 正式 Agent 切換行的固定格式（只匹配這些才算真正切換）
_AGENT_TRANSITION_RE = re.compile(
    r'Agent:\s*(.+)',
    re.IGNORECASE
)

def _detect_agent(text: str) -> str | None:
    """只在 CrewAI 正式 Agent transition 行中偵測 Agent 名稱。
    避免在 tool output / error messages 中誤觸。
    """
    m = _AGENT_TRANSITION_RE.search(text)
    if not m:
        return None
    matched_name = m.group(1)
    for code, patterns in _AGENT_PATTERNS:
        if any(p in matched_name for p in patterns):
            return code
    return None

def _classify_log(text: str) -> str:
    """將一行 CrewAI stdout 分類為 THOUGHT / ACTION / RESULT / SYSTEM / SKIP"""
    t = text.strip()
    if not t or len(t) < 5:
        return "SKIP"
    # CrewAI verbose 的分隔行與無意義行
    if set(t) <= set("═─=- \n") or t.startswith("====") or t.startswith("────"):
        return "SKIP"
    # CrewAI 系統噪音行（不含有效資訊）
    _SKIP_PREFIXES = (
        "## Agent", "Working Agent:", "# Agent:", "# Task:",
        "Task completed", "> Entering", "Retrying", "[1m",
        "╭─", "╰─", "│", "├", "└", "✅",
        "Agent stopped", "Assigned to:", "Status:", "Crew:",
    )
    if t.startswith(_SKIP_PREFIXES):
        return "SKIP"
    # 子字串比對（處理含 emoji 前綴的情況，例如 '🚀 Crew: crew'）
    if "Crew:" in t or "Executing Task" in t or "Task execution started" in t:
        return "SKIP"
    # 過濾 LLM 思考標籤（<|channel>thought、<think> 等）漏出到 ticker
    if "<|channel" in t or "<think>" in t or "</think>" in t:
        return "SKIP"
    if "Agent:" in t or "Working Agent:" in t or "Task:" in t or "Started Task" in t or "Assigned to:" in t or "Status:" in t:
        return "SYSTEM"
    if "Thought:" in t or "思考" in t:
        return "THOUGHT"
    if "Action:" in t or "Using tool" in t or "Tool Input" in t or "呼叫工具" in t:
        return "ACTION"
    if "Final Answer:" in t or "最終答案" in t or "Task output" in t:
        return "RESULT"
    return "THOUGHT"  # 其他可見文字預設歸為思考

# 移除氣泡顯示文字中的 CrewAI 前綴，只保留實際內容
_BUBBLE_PREFIX_RE = re.compile(
    r'^(Thought|Action|Final Answer|Observation|Tool Input|Action Input|THOUGHT|ACTION)\s*:\s*',
    re.IGNORECASE
)

def _clean_bubble_text(text: str) -> str:
    """去掉 CrewAI 前綴，回傳乾淨可讀的氣泡文字"""
    return _BUBBLE_PREFIX_RE.sub('', text).strip()

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
        agent = _detect_agent(_strip_ansi(buf).strip())
        prev_agent = getattr(_thread_local, 'current_agent', 'SYSTEM')
        if agent and agent != prev_agent:
            _thread_local.current_agent = agent
            last_pushed = getattr(_thread_local, 'last_pushed_agent', None)
            if agent != last_pushed:
                _thread_local.last_pushed_agent = agent
                start_msg = _AGENT_START_MSGS.get(agent, f"🚀 {agent} 開始執行任務...")
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
        clean = _strip_ansi(raw_line).strip()
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
        agent = _detect_agent(clean)
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
                    "content":  _AGENT_START_MSGS.get(agent, f"🚀 {agent} 開始執行任務..."),
                })
        elif agent:
            _thread_local.current_agent = agent

        log_type = _classify_log(clean)
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

        bubble_text = _clean_bubble_text(clean)[:500]
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
        content = _strip_ansi(str(content)).strip()[:300]
        bubble  = _clean_bubble_text(content)[:120]
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
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    # 2. 移除 <|channel|> 等特殊控制 token
    raw = re.sub(r'<\|?[a-zA-Z_]+\|?>', '', raw)

    # 3. 偵測正式文件起始點：截斷前方的 LLM 思考或提示詞殘留（如 "Please ensure all keys..."）
    doc_markers = ['【法院名稱】', '【判決主文】', '【事實】', '【理由】',
                   '【當事人】', '臺灣', '# 【', '## 【', '---\n#',
                   '**【法院名稱】', '**【判決主文】']
    earliest_pos = -1
    for marker in doc_markers:
        pos = raw.find(marker)
        if 0 < pos < 1500:
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
        print("====== BACKEND CRITICAL EXCEPTION ======")
        print(tb_str)
        print("========================================")
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

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
