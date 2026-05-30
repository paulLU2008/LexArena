"""
日誌結構化解析模組 (Log Parser)
═══════════════════════════════════════════════════════════════
將 CrewAI 的 stdout 輸出轉換為有語意的結構化日誌，包括：
1. ANSI 色碼清除
2. Agent 切換偵測
3. 日誌類型分類（THOUGHT / ACTION / RESULT / SYSTEM / SKIP）
4. 氣泡文字清理

從 main.py 提取出來以降低單一檔案的複雜度。
"""
import re

# ─────────────────────────────────────────────
# ANSI 清洗
# ─────────────────────────────────────────────
_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


# ─────────────────────────────────────────────
# Agent 名稱對照表（匹配 CrewAI verbose 輸出中的角色字串）
# ─────────────────────────────────────────────
AGENT_PATTERNS = [
    ("COMMANDER",     ["審判長", "Judge", "總指揮官", "Commander"]),
    ("INTELLIGENCE",  ["事實調查官", "Fact Investigator", "情報分析官", "Intelligence"]),
    ("OPERATIONS",    ["控辯雙方論證專家", "Prosecutor & Defense", "行動策劃官", "Operations"]),
    ("LEGAL",         ["法學研究員", "Legal Scholar", "法務稽查官", "Legal"]),
    ("PR",            ["判決通報官", "Verdict PR", "公關通報官", "PR Agent"]),
]

# Agent 切換時立即推送的「開始執行」通知
AGENT_START_MSGS = {
    "COMMANDER":    "⚖️ 審判長開始審理兩造主張，並準備起草正式司法判決書...",
    "INTELLIGENCE": "🔎 事實調查官開始分析案情，提取起訴事實、當事人與核心爭點...",
    "OPERATIONS":   "⚔️ 控辯雙方論證專家正在模擬原告（控方）與被告（辯方）的法庭辯論攻防...",
    "LEGAL":        "📖 法學研究員正在跨庫檢索中華民國憲法、刑法、民法條文（RAG）...",
    "PR":           "📢 判決通報官準備撰寫白話判決懶人包與新聞稿，並生成 AI 法庭配圖...",
}

# CrewAI 正式 Agent 切換行的固定格式
_AGENT_TRANSITION_RE = re.compile(
    r'Agent:\s*(.+)',
    re.IGNORECASE
)


def detect_agent(text: str) -> str | None:
    """只在 CrewAI 正式 Agent transition 行中偵測 Agent 名稱。"""
    m = _AGENT_TRANSITION_RE.search(text)
    if not m:
        return None
    matched_name = m.group(1)
    for code, patterns in AGENT_PATTERNS:
        if any(p in matched_name for p in patterns):
            return code
    return None


def classify_log(text: str) -> str:
    """將一行 CrewAI stdout 分類為 THOUGHT / ACTION / RESULT / SYSTEM / SKIP"""
    t = text.strip()
    if not t or len(t) < 5:
        return "SKIP"
    # CrewAI verbose 的分隔行與無意義行
    if set(t) <= set("═─=- \n") or t.startswith("====") or t.startswith("────"):
        return "SKIP"
    # CrewAI 系統噪音行
    _SKIP_PREFIXES = (
        "## Agent", "Working Agent:", "# Agent:", "# Task:",
        "Task completed", "> Entering", "Retrying", "[1m",
        "╭─", "╰─", "│", "├", "└", "✅",
        "Agent stopped", "Assigned to:", "Status:", "Crew:",
    )
    if t.startswith(_SKIP_PREFIXES):
        return "SKIP"
    if "Crew:" in t or "Executing Task" in t or "Task execution started" in t:
        return "SKIP"
    # 過濾 LLM 思考標籤漏出
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


# 移除氣泡顯示文字中的 CrewAI 前綴
_BUBBLE_PREFIX_RE = re.compile(
    r'^(Thought|Action|Final Answer|Observation|Tool Input|Action Input|THOUGHT|ACTION)\s*:\s*',
    re.IGNORECASE
)

def clean_bubble_text(text: str) -> str:
    """去掉 CrewAI 前綴，回傳乾淨可讀的氣泡文字"""
    return _BUBBLE_PREFIX_RE.sub('', text).strip()
