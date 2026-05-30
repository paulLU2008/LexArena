import streamlit as st
import streamlit.components.v1 as components
import httpx
from datetime import datetime
import json
import re
import os
import sys
import base64
import html as html_lib

# 將當前路徑加入 sys 讓 Streamlit 能 import database.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from database import get_all_incidents, get_meeting_logs_by_incident
except ImportError:
    def get_all_incidents(): return []
    def get_meeting_logs_by_incident(incident_id): return []

# ─────────────────────────────────────────────
# 工具與地標常數 (供重播地圖定位管轄權法院)
# ─────────────────────────────────────────────
_TAIWAN_CENTER = (23.5, 121.0)
_TAIWAN_COUNTY_COORDS = {
    "基隆市": (25.128, 121.741), "臺北市": (25.033, 121.565), "台北市": (25.033, 121.565),
    "新北市": (24.964, 121.501), "桃園市": (24.993, 121.301), "新竹市": (24.814, 120.967),
    "新竹縣": (24.838, 121.018), "苗栗縣": (24.560, 120.821), "臺中市": (24.148, 120.674),
    "台中市": (24.148, 120.674), "彰化縣": (24.052, 120.516), "南投縣": (23.906, 120.687),
    "雲林縣": (23.709, 120.431), "嘉義市": (23.480, 120.450), "嘉義縣": (23.452, 120.256),
    "臺南市": (22.997, 120.212), "台南市": (22.997, 120.212), "高雄市": (22.627, 120.302),
    "屏東縣": (22.551, 120.548), "宜蘭縣": (24.702, 121.738), "花蓮縣": (23.987, 121.601),
    "臺東縣": (22.756, 121.150), "台東縣": (22.756, 121.150), "澎湖縣": (23.571, 119.579),
    "金門縣": (24.448, 118.378), "連江縣": (26.160, 119.950)
}

def load_svg_base64(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""

def render_markdown_with_images(md_text):
    if md_text:
        md_text = md_text.replace('\\n', '\n')
    parts = re.split(r'!\[.*?\]\((.*?)\)', md_text)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            if part.strip():
                st.markdown(part)
        else:
            try:
                if os.path.exists(part):
                    st.image(part)
                else:
                    st.warning(f"圖檔不存在: {part}")
            except Exception:
                st.markdown(f"*(無法載入配圖: {part})*")

# ─────────────────────────────────────────────
# AI 模擬法庭 HTML 與 Mermaid 元件
# ─────────────────────────────────────────────

def build_mermaid_relation_html(location: str, pollutant: str) -> str:
    """動態生成案件關係人與爭點 Mermaid 圖表 HTML"""
    case_type = pollutant or "案件"
    case_title = location or "未明案件"
    
    if "刑事" in case_type or "刑" in case_type or "竊盜" in case_title:
        diagram = f"""
graph TD
    classDef court fill:#1a2540,stroke:#d4af37,stroke-width:2px,color:#d4af37;
    classDef pros fill:#301414,stroke:#e74c3c,stroke-width:1px,color:#e74c3c;
    classDef defs fill:#122a2a,stroke:#3498db,stroke-width:1px,color:#3498db;
    classDef law fill:#122a12,stroke:#1abc9c,stroke-width:1px,color:#1abc9c;

    Judge["⚖️ 審判長 (法官)<br>臺灣地方法院刑事庭"]:::court
    Pros["⚔️ 公訴人 (檢察官)<br>代表國家提起公訴"]:::pros
    Def["🛡️ 被告 與 辯護律師<br>進行防禦與無罪抗辯"]:::defs
    RAG["📖 中華民國刑法<br>共同正犯 / 竊盜罪"]:::law
    
    Judge -->|進行審判| Pros
    Judge -->|聽取答辯| Def
    Pros -->|起訴事實| Fact["📋 共同竊盜罪嫌<br>(刑法第321條/第28條)"]
    Def -->|抗辯論點| Arg["🛡️ 無犯罪故意 / 從犯減刑"]
    Fact -.->|檢索適用法條| RAG
    Arg -.->|法律適用性| RAG
        """
    elif "民事" in case_type or "民" in case_type or "賠償" in case_title:
        diagram = f"""
graph TD
    classDef court fill:#1a2540,stroke:#d4af37,stroke-width:2px,color:#d4af37;
    classDef pros fill:#301414,stroke:#e74c3c,stroke-width:1px,color:#e74c3c;
    classDef defs fill:#122a2a,stroke:#3498db,stroke-width:1px,color:#3498db;
    classDef law fill:#122a12,stroke:#1abc9c,stroke-width:1px,color:#1abc9c;

    Judge["⚖️ 審判長 (法官)<br>臺灣地方法院民事庭"]:::court
    Pros["⚔️ 原告 與 訴訟代理人<br>損害賠償請求權"]:::pros
    Def["🛡️ 被告 與 訴訟代理人<br>抗辯與酌減聲明"]:::defs
    RAG["📖 中華民國民法<br>侵權行為/損害賠償"]:::law
    
    Judge -->|民事審判| Pros
    Judge -->|民事審判| Def
    Pros -->|訴之聲明| Fact["📋 請求損害賠償新台幣30萬<br>(民法第184條/195條)"]
    Def -->|抗辯論點| Arg["🛡️ 爭執精神撫慰金過高 / 過失相抵"]
    Fact -.->|法之檢索| RAG
    Arg -.->|法之檢索| RAG
        """
    else:
        diagram = f"""
graph TD
    classDef court fill:#1a2540,stroke:#d4af37,stroke-width:2px,color:#d4af37;
    classDef pros fill:#301414,stroke:#e74c3c,stroke-width:1px,color:#e74c3c;
    classDef defs fill:#122a2a,stroke:#3498db,stroke-width:1px,color:#3498db;
    classDef law fill:#122a12,stroke:#1abc9c,stroke-width:1px,color:#1abc9c;

    Judge["⚖️ 審判長 (法官)"]:::court
    Pros["⚔️ 控方 / 原告"]:::pros
    Def["🛡️ 辯方 / 被告"]:::defs
    RAG["📖 中華民國核心法規"]:::law
    
    Judge --> Pros
    Judge --> Def
    Pros --> Fact["📋 起訴事實與爭點"]
    Def --> Arg["🛡️ 抗辯與訴訟主張"]
    Fact -.-> RAG
    Arg -.-> RAG
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <script type="module">
      import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
      mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});
    </script>
    <style>
      body {{
        background: #0a0f1a;
        margin: 0;
        padding: 5px;
        overflow: hidden;
      }}
      #graph {{
        width: 100%;
        height: 100%;
        display: flex;
        justify-content: center;
        align-items: center;
      }}
    </style>
    </head>
    <body>
      <div id="graph" class="mermaid">
        {diagram}
      </div>
    </body>
    </html>
    """
    return html

AGENT_META = {
    "COMMANDER":    {"emoji": "⚖️", "name": "審判長 (法官)",  "color": "#d4af37"},
    "INTELLIGENCE": {"emoji": "🔎", "name": "事實調查官", "color": "#3498db"},
    "OPERATIONS":   {"emoji": "⚔️", "name": "控辯攻防專家", "color": "#e67e22"},
    "LEGAL":        {"emoji": "📖", "name": "法學研究員", "color": "#1abc9c"},
    "PR":           {"emoji": "📢", "name": "判決通報官", "color": "#9b59b6"},
    "SYSTEM":       {"emoji": "💻", "name": "系統",      "color": "#95a5a6"},
}

def _make_status_msg(agent: str, content: str, log_type: str) -> str:
    c = content.lower()

    if log_type == "ACTION":
        if any(k in c for k in ["rag", "法規", "知識庫", "法條", "legalragtool"]):
            return "📖 正在跨庫檢索憲法、刑法、民法條文（RAG）..."
        if any(k in c for k in ["image", "imagen", "圖片", "生圖", "generate"]):
            return "🖼️ 正在生成 AI 法庭模擬配圖..."
        return "⚙️ 執行法律工具呼叫中..."

    if agent == "COMMANDER":
        if any(k in c for k in ["主文", "判決", "宣告", "sentence"]):
            return "⚖️ 正在撰寫判決主文與量刑..."
        if any(k in c for k in ["起草", "格式", "裁判", "書面"]):
            return "📄 正在起草正式法院判決書（事實與理由）..."
        return "⚖️ 審判長分析研判中..."

    if agent == "INTELLIGENCE":
        if any(k in c for k in ["當事人", "原告", "被告", "parties"]):
            return "🔎 正在分析當事人關係與背景..."
        if any(k in c for k in ["爭點", "起訴事實", "dispute", "fact"]):
            return "📋 梳理本案核心爭點與起訴事實..."
        return "🔎 正在進行案情事實調查與釐清..."

    if agent == "OPERATIONS":
        if any(k in c for k in ["控方", "原告", "檢察", "起訴", "prosecut"]):
            return "⚔️ 正在模擬原告（控方）之起訴與主張要件..."
        if any(k in c for k in ["辯方", "被告", "律師", "抗辯", "defense"]):
            return "🛡️ 正在模擬被告（辯方）之阻卻違法與權利防禦..."
        return "⚔️ 模擬兩造法庭攻防辯論中..."

    if agent == "LEGAL":
        if any(k in c for k in ["檢索", "條文", "查詢", "rag"]):
            return "📚 檢索法學知識庫適用條文..."
        if any(k in c for k in ["刑法", "民法", "憲法"]):
            return "📖 分析關聯法理與實務判例..."
        return "📚 法律適用性研析中..."

    if agent == "PR":
        if any(k in c for k in ["新聞", "白話", "懶人包", "釋出", "pr"]):
            return "📢 撰寫白話判決新聞稿與社會大眾包..."
        if any(k in c for k in ["image", "生圖", "配圖"]):
            return "🖼️ 產生模擬法庭現場示意圖..."
        return "📢 整理社會大眾通報摘要..."

    return "💡 思考研判中..."

# ─────────────────────────────────────────────
# HTML 戰情室/模擬法庭與重播元件 CSS/JS
# ─────────────────────────────────────────────

def build_war_room_html(
    location: str = "",
    svg_b64: str = "",
    all_done: bool = False,
    agent_statuses: dict = None,
    ticker_text: str = "",
    show_map: bool = True,
) -> str:
    if agent_statuses is None:
        agent_statuses = {k: "idle" for k in AGENT_META}

    def agent_card(code: str) -> str:
        meta = AGENT_META[code]
        status_val = (agent_statuses or {}).get(code, "")
        is_running = bool(status_val) and status_val not in ("idle", "done", "")

        if all_done:
            border_color = "#2ecc71"
            status_dot   = '<span class="dot done"></span>'
            status_label = "✅ 完成"
            glow         = "0 0 12px #2ecc7166"
        elif is_running:
            border_color = meta["color"]
            status_dot   = '<span class="dot active"></span>'
            status_label = "🔄 執行中"
            glow         = f"0 0 14px {meta['color']}99"
        elif status_val == "done":
            border_color = "#3498db"
            status_dot   = '<span class="dot done" style="background:#3498db;box-shadow:0 0 6px #3498db;"></span>'
            status_label = "✅ 完成"
            glow         = "0 0 8px #3498db55"
        else:
            border_color = "#444"
            status_dot   = '<span class="dot idle"></span>'
            status_label = "⚪ 待命"
            glow = "0 0 4px #0005"

        bubble_html = ""
        if is_running and not all_done:
            escaped     = status_val.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            bubble_html = (
                f'<div class="speech-bubble" style="border-color:{meta["color"]};'
                f' box-shadow:0 0 12px {meta["color"]}66, 0 4px 16px #00000080;">'
                f'<div class="bubble-text">{escaped[:120]}'
                f'{"..." if len(status_val) > 120 else ""}</div>'
                f'<div class="bubble-tail" style="border-top-color:{meta["color"]};"></div>'
                f'</div>'
            )

        avatar_html = (
            f'<div class="agent-avatar" style="'
            f'background:radial-gradient(circle at 40% 35%,{meta["color"]}44,#0a0f1a);'
            f'border:2px solid {border_color};'
            f'width:52px;height:52px;border-radius:50%;'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:24px;margin:0 auto 6px;'
            f'box-shadow:{glow};transition:box-shadow 0.3s ease;">'
            f'{meta["emoji"]}</div>'
        )

        active_cls = "active-card" if is_running and not all_done else ""
        return (
            f'<div class="agent-card {active_cls}" style="border-color:{border_color}">'
            f'{bubble_html}'
            f'{avatar_html}'
            f'<div class="agent-name">{meta["name"]}</div>'
            f'<div class="agent-status">{status_dot} {status_label}</div>'
            f'</div>'
        )

    agents_html = "".join(agent_card(code) for code in ["COMMANDER", "INTELLIGENCE", "OPERATIONS", "LEGAL", "PR"])

    html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0f1a;
    font-family: 'Segoe UI', -apple-system, sans-serif;
    color: #c9d1d9;
    overflow: hidden;
  }}

  .war-room {{
    display: flex;
    gap: 16px;
    padding: 16px;
    height: 580px;
  }}

  .agents-panel {{
    flex: 1;
    background: rgba(13, 27, 42, 0.4);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    backdrop-filter: blur(10px);
    box-shadow: inset 0 0 24px rgba(0, 0, 0, 0.6);
  }}

  .panel-hdr {{
    font-size: 13px;
    font-weight: 700;
    color: #d4af37;
    text-shadow: 0 0 8px rgba(212, 175, 55, 0.3);
    letter-spacing: 2px;
    text-align: center;
    text-transform: uppercase;
    border-bottom: 1px solid rgba(30, 58, 95, 0.5);
    padding-bottom: 8px;
  }}

  .table-scene {{
    flex: 1;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }}

  .meeting-table {{
    width: 440px;
    height: 220px;
    background: linear-gradient(135deg, #1a2238, #2a3c5a);
    border: 2px solid #d4af37;
    border-radius: 50%;
    box-shadow: 0 0 35px rgba(212, 175, 55, 0.25), inset 0 0 25px rgba(0,0,0,0.8);
    position: absolute;
  }}

  .table-lbl {{
    position: absolute;
    color: #d4af37;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    opacity: 0.8;
  }}

  /* Agent 卡片定位 */
  .agent-card {{
    position: absolute;
    width: 130px;
    background: linear-gradient(145deg, #0e1726, #16223b);
    border: 2px solid #333;
    border-radius: 12px;
    padding: 12px 8px;
    text-align: center;
    box-shadow: 0 4px 10px rgba(0,0,0,0.5);
    transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
  }}
  .agent-card.active-card {{
    transform: scale(1.08);
  }}

  .agent-card:nth-child(2) {{ top: 15px; left: 60px; }}
  .agent-card:nth-child(3) {{ top: 15px; right: 60px; }}
  .agent-card:nth-child(4) {{ bottom: 15px; left: 60px; }}
  .agent-card:nth-child(5) {{ bottom: 15px; right: 60px; }}
  .agent-card:nth-child(6) {{ top: 50%; right: -25px; transform: translateY(-50%); }}

  .agent-name {{
    font-size: 11px;
    font-weight: 700;
    color: #e2e8f0;
    margin-bottom: 4px;
    letter-spacing: 0.5px;
  }}
  .agent-status {{
    font-size: 9px;
    color: #888;
    display: flex;
    align-items: center;
    gap: 4px;
    justify-content: center;
  }}

  /* 呼吸點 */
  .dot {{
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #555;
    display: inline-block;
  }}
  .dot.active {{
    background: #e67e22;
    box-shadow: 0 0 6px #e67e22;
    animation: pulse 1s infinite;
  }}
  .dot.done {{
    background: #2ecc71;
    box-shadow: 0 0 6px #2ecc71;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50%       {{ opacity: 0.2; }}
  }}

  /* 思考氣泡 */
  .speech-bubble {{
    position: absolute;
    bottom: calc(100% + 10px);
    left: 50%;
    transform: translateX(-50%);
    background: #152238;
    border: 1.5px solid #d4af37;
    border-radius: 10px;
    padding: 8px 10px;
    width: 170px;
    z-index: 999;
  }}
  .bubble-text {{
    font-size: 10px;
    color: #f3e5ab;
    line-height: 1.4;
    text-align: left;
  }}
  .bubble-tail {{
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    width: 0; height: 0;
    border-left: 7px solid transparent;
    border-right: 7px solid transparent;
    border-top: 8px solid #d4af37;
  }}

  /* 跑馬燈/Ticker */
  .ticker-wrap {{
    background: #070c14;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 11px;
    color: #8892b0;
    letter-spacing: 0.5px;
    text-align: left;
    height: 38px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .ticker-lbl {{ color: #d4af37; font-weight: 700; margin-right: 6px; }}

</style>
</head>
<body>

<div class="war-room">
  <div class="agents-panel">
    <div class="panel-hdr">🏛️ AI 模擬法庭現場 — Mock Court Room</div>
    <div class="table-scene">
      <div class="meeting-table">
        <div class="table-lbl" style="top:50%;left:50%;transform:translate(-50%,-50%)">🏛️ 模擬法庭</div>
      </div>
      {agents_html}
    </div>
    <div class="ticker-wrap">
      <span class="ticker-lbl">📢 法庭公告:</span>
      <span id="ticker-text">{ticker_text or "法庭準備中，等待審判開始..."}</span>
    </div>
  </div>
</div>

</body>
</html>
"""
    return html

def build_replay_html(logs: list, svg_b64: str = "", location: str = "") -> str:
    """重播會議過程：上方戰情會議室動畫 + 下方文字時間軸"""
    # 限制 log 數量，避免注入過大 HTML 導致前端渲染卡頓
    if len(logs) > 500:
        logs = logs[-500:]
        
    logs_json      = json.dumps(logs, ensure_ascii=False)
    agent_colors   = json.dumps({k: v["color"] for k, v in AGENT_META.items()})
    agent_emojis   = json.dumps({k: v["emoji"] for k, v in AGENT_META.items()})
    agent_names    = json.dumps({k: v["name"]  for k, v in AGENT_META.items()})
    init_location  = location.replace("'", "\\'")

    if svg_b64:
        map_src     = f"data:image/svg+xml;base64,{svg_b64}"
        map_img_tag = f'<img id="rp-svg" src="{map_src}" style="width:100%;height:100%;object-fit:contain;" />'
    else:
        map_img_tag = '<div style="color:#555;text-align:center;padding-top:60px;">🗺️</div>'

    agent_cards_html = ""
    for code in ["COMMANDER", "INTELLIGENCE", "OPERATIONS", "LEGAL", "PR"]:
        meta = AGENT_META[code]
        agent_cards_html += f"""
        <div class="rp-agent-card" id="rp-card-{code}" data-agent="{code}"
             style="border-color:#444">
          <div class="rp-bubble" id="rp-bubble-{code}" style="display:none">
            <div class="rp-bubble-text" id="rp-bubble-text-{code}"></div>
            <div class="rp-bubble-tail"></div>
          </div>
          <div class="rp-avatar" id="rp-avatar-{code}"
               style="background:radial-gradient(circle at 40% 35%,{meta['color']}22,#0a0f1a);
                      border:2px solid #444;">
            {meta['emoji']}
          </div>
          <div class="rp-name">{meta['name']}</div>
          <div class="rp-status" id="rp-status-{code}">
            <span class="rp-dot" id="rp-dot-{code}"></span> <span id="rp-label-{code}">⚪ 待命</span>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:#0a0f1a; color:#c9d1d9;
        font-family:'Segoe UI',-apple-system,sans-serif; overflow-x:hidden; }}

/* ── 控制列 ── */
.ctrl-bar {{ display:flex; gap:8px; padding:10px 14px; align-items:center;
             background:#060b14; border-bottom:1px solid #1e3a5f; flex-wrap:wrap; }}
.ctrl-btn {{ background:#1e3a5f; color:#a8c8f0; border:1px solid #3d6494;
             border-radius:6px; padding:5px 14px; cursor:pointer; font-size:11px; }}
.ctrl-btn:hover {{ background:#2a4f7a; }}
.speed-sel {{ background:#12192b; color:#a8c8f0; border:1px solid #3d6494;
              border-radius:6px; padding:4px 8px; font-size:11px; cursor:pointer; }}
#rp-counter {{ font-size:10px; color:#556; margin-left:auto; }}

/* 進度條 */
.prog-bar {{ height:3px; background:#111820; }}
.prog-fill {{ height:100%; background:#d4af37; width:0%; transition:width .4s linear; }}

/* ── 戰情會議室 ── */
.rp-war-room {{ display:flex; gap:14px; padding:12px 14px; height:340px; }}

/* 管轄法院面板 */
.rp-court-panel {{ flex:0 0 160px; background: rgba(13, 27, 42, 0.8);
                 border:1px solid #d4af37; border-radius:10px; padding:8px;
                 display:flex; flex-direction:column; gap:6px; 
                 backdrop-filter: blur(10px);
                 box-shadow: inset 0 0 20px rgba(0,0,0,0.5); }}
.rp-court-title {{ font-size:10px; font-weight:700; color:#d4af37;
                 letter-spacing:2px; text-align:center; text-transform:uppercase; }}
#rp-map-wrapper {{ flex:1; position:relative; }}
#rp-dot {{ display:none; position:absolute; width:13px; height:13px;
           border-radius:50%; background:#e74c3c; border:2px solid #ff6b6b;
           box-shadow:0 0 10px #e74c3c,0 0 20px #e74c3c88;
           animation:blink 1s ease-in-out infinite; z-index:10; }}
#rp-dot-label {{ position:absolute; top:-17px; left:50%;
                 transform:translateX(-50%); font-size:8px;
                 color:#ff6b6b; white-space:nowrap; font-weight:700; }}
@keyframes blink {{ 0%,100%{{opacity:1;transform:scale(1)}} 50%{{opacity:.3;transform:scale(.7)}} }}

/* 会议桌 */
.rp-agents-panel {{ flex:1; display:flex; flex-direction:column; gap:8px; }}
.rp-panel-hdr {{ font-size:10px; font-weight:700; color:#d4af37;
                 letter-spacing:2px; text-transform:uppercase; text-align:center; }}
.rp-table-scene {{ flex:1; position:relative; display:flex;
                    align-items:center; justify-content:center; }}
.rp-meeting-table {{ width:300px; height:160px;
                     background:linear-gradient(135deg,#1a2238,#2a3c5a);
                     border:2px solid #d4af37; border-radius:50%;
                     box-shadow:0 0 28px rgba(212, 175, 55, 0.2),inset 0 0 18px #00000060;
                     position:absolute; }}
.rp-table-lbl {{ position:absolute; color:#d4af37; font-size:9px;
                  letter-spacing:1px; opacity:.6; }}

.rp-agent-card {{ position:absolute; width:100px;
                   background:linear-gradient(145deg,#12192b,#1a2540);
                   border:2px solid #444; border-radius:10px;
                   padding:8px 6px; text-align:center; transition:all .3s ease; }}
.rp-agent-card.rp-active {{ transform:scale(1.06); }}
.rp-agent-card:nth-child(1) {{ top:10px; left:20px; }}
.rp-agent-card:nth-child(2) {{ top:10px; right:20px; }}
.rp-agent-card:nth-child(3) {{ bottom:10px; left:20px; }}
.rp-agent-card:nth-child(4) {{ bottom:10px; right:20px; }}
.rp-agent-card:nth-child(5) {{ top:50%; right:-5px; transform:translateY(-50%); }}

.rp-avatar {{ width:40px; height:40px; border-radius:50%;
               display:flex; align-items:center; justify-content:center;
               font-size:20px; margin:0 auto 5px; transition:all .3s; }}
.rp-name  {{ font-size:9px; font-weight:700; color:#dde; margin-bottom:3px; }}
.rp-status {{ font-size:8px; color:#888; display:flex; align-items:center;
               gap:3px; justify-content:center; }}
.rp-dot {{ width:6px; height:6px; border-radius:50%; background:#555; display:inline-block; }}
.rp-dot.active {{ background:#e67e22; box-shadow:0 0 5px #e67e22; animation:pulse .9s infinite; }}
.rp-dot.done   {{ background:#2ecc71; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}

/* 思考氣泡 */
.rp-bubble {{ position:absolute; bottom:calc(100% + 6px); left:50%;
               transform:translateX(-50%); background:#1e2d45;
               border:1px solid #d4af37; border-radius:9px;
               padding:6px 8px; width:150px; z-index:20;
               box-shadow:0 4px 14px #00000080; }}
.rp-bubble-text {{ font-size:9px; color:#f3e5ab; line-height:1.45;
                   text-align:left; word-break:break-all; }}
.rp-bubble-tail {{ position:absolute; top:100%; left:50%; transform:translateX(-50%);
                   width:0; height:0;
                   border-left:6px solid transparent;
                   border-right:6px solid transparent;
                   border-top:7px solid #d4af37; }}

/* ── 文字時間軸 ── */
.rp-timeline-wrap {{ padding:0 14px 12px; }}
.tl-hdr {{ font-size:10px; font-weight:700; color:#3d6494;
           letter-spacing:2px; margin-bottom:6px; }}
#rp-timeline {{ max-height:200px; overflow-y:auto; display:flex;
                flex-direction:column; gap:5px; }}
.tl-entry {{ padding:6px 10px; border-radius:7px; font-size:11px;
             background:#12192b; border-left:3px solid #555;
             animation:fadein .25s ease; }}
@keyframes fadein {{ from{{opacity:0;transform:translateX(-8px)}} to{{opacity:1;transform:none}} }}
.tl-agent {{ font-weight:700; font-size:9px; letter-spacing:1px; }}
</style>
</head>
<body>

<div class="ctrl-bar">
  <button class="ctrl-btn" onclick="startReplay()">▶ 開始重播</button>
  <button class="ctrl-btn" onclick="pauseReplay()">⏸ 暫停</button>
  <button class="ctrl-btn" onclick="resetReplay()">↺ 重設</button>
  <label style="font-size:10px;color:#556;">速度
    <select class="speed-sel" id="speed-sel">
      <option value="250">2x</option>
      <option value="500" selected>1x</option>
      <option value="1000">0.5x</option>
    </select>
  </label>
  <span id="rp-counter">0 / 0 條</span>
</div>
<div class="prog-bar"><div class="prog-fill" id="rp-prog"></div></div>

<div class="rp-war-room">

  <!-- 左：轄區 -->
  <div class="rp-court-panel">
    <div class="rp-court-title">🏛️ 管轄法院</div>
    <div id="rp-map-wrapper">
      {map_img_tag}
      <div id="rp-dot"><span id="rp-dot-label"></span></div>
    </div>
  </div>

  <!-- 右：法庭 -->
  <div class="rp-agents-panel">
    <div class="rp-panel-hdr">🎬 審理過程重播 — Agent Activity Replay</div>
    <div class="rp-table-scene">
      <div class="rp-meeting-table">
        <div class="rp-table-lbl" style="top:50%;left:50%;transform:translate(-50%,-50%)">🏛️ 模擬法庭</div>
      </div>
      {agent_cards_html}
    </div>
  </div>

</div>

<div class="rp-timeline-wrap">
  <div class="tl-hdr">📋 LOG TIMELINE</div>
  <div id="rp-timeline"></div>
</div>

<script>
var LOGS   = {logs_json};
var COLORS = {agent_colors};
var EMOJIS = {agent_emojis};
var NAMES  = {agent_names};
var COUNTIES = ['臺北市','台北市','新北市','基隆市','桃園市','新竹市','新竹縣',
                '苗栗縣','臺中市','台中市','彰化縣','南投縣','雲林縣',
                '嘉義市','嘉義縣','臺南市','台南市','高雄市','屏東縣',
                '宜蘭縣','花蓮縣','臺東縣','台東縣','澎湖縣','金門縣','連江縣'];

var idx = 0, timer = null;
var agentState = {{COMMANDER:'idle',INTELLIGENCE:'idle',OPERATIONS:'idle',LEGAL:'idle',PR:'idle'}};
var dotLocated = false;

(function() {{
  var loc = '{init_location}';
  if (!loc) return;
  var matched = COUNTIES.find(function(c) {{ return loc.includes(c); }});
  if (matched) positionDot(matched);
}})();

function positionDot(countyName) {{
  var svgEl = document.getElementById('rp-svg');
  if (!svgEl) return;
  var markerEl = document.getElementById('loc-' + countyName);
  if (!markerEl) return;
  var svgRect = svgEl.getBoundingClientRect();
  var cx = parseFloat(markerEl.getAttribute('cx'));
  var cy = parseFloat(markerEl.getAttribute('cy'));
  var vb = svgEl.viewBox.baseVal;
  var wrapper = document.getElementById('rp-map-wrapper');
  var wRect = wrapper.getBoundingClientRect();
  var dot = document.getElementById('rp-dot');
  dot.style.left = (cx / vb.width  * svgRect.width  + (svgRect.left - wRect.left) - 6) + 'px';
  dot.style.top  = (cy / vb.height * svgRect.height + (svgRect.top  - wRect.top ) - 6) + 'px';
  dot.style.display = 'block';
  var label = document.getElementById('rp-dot-label');
  if (label) label.textContent = countyName;
  dotLocated = true;
}}

function tryExtractLocation(text) {{
  if (dotLocated) return;
  var matched = COUNTIES.find(function(c) {{ return text.includes(c); }});
  if (matched) positionDot(matched);
}}

function updateAgentCard(agent, content) {{
  var AGENT_CODES = ['COMMANDER','INTELLIGENCE','OPERATIONS','LEGAL','PR'];

  AGENT_CODES.forEach(function(a) {{
    if (agentState[a] === 'active') {{
      agentState[a] = 'done';
      var card = document.getElementById('rp-card-' + a);
      var dot  = document.getElementById('rp-dot-' + a);
      var lbl  = document.getElementById('rp-label-' + a);
      var av   = document.getElementById('rp-avatar-' + a);
      if (card) {{ card.style.borderColor='#2ecc71'; card.classList.remove('rp-active'); }}
      if (dot)  {{ dot.className='rp-dot done'; }}
      if (lbl)  {{ lbl.textContent='✅ 完成'; }}
      if (av)   {{ av.style.boxShadow='0 0 6px #2ecc7166'; }}
      var bub = document.getElementById('rp-bubble-' + a);
      if (bub) bub.style.display = 'none';
    }}
  }});

  agentState[agent] = 'active';
  var color = COLORS[agent] || '#888';
  var card  = document.getElementById('rp-card-' + agent);
  var dot   = document.getElementById('rp-dot-' + agent);
  var lbl   = document.getElementById('rp-label-' + agent);
  var av    = document.getElementById('rp-avatar-' + agent);
  var bub   = document.getElementById('rp-bubble-' + agent);
  var bubTx = document.getElementById('rp-bubble-text-' + agent);

  if (card) {{
    card.style.borderColor = color;
    card.style.boxShadow   = '0 0 16px ' + color + '88';
    card.classList.add('rp-active');
  }}
  if (dot) dot.className = 'rp-dot active';
  if (lbl) lbl.textContent = '🔄 執行中';
  if (av)  {{
    av.style.background  = 'radial-gradient(circle at 40% 35%,' + color + '44,#0a0f1a)';
    av.style.borderColor = color;
    av.style.boxShadow   = '0 0 12px ' + color;
  }}
  if (bub && bubTx && content) {{
    bubTx.textContent = content.substring(0, 120) + (content.length > 120 ? '...' : '');
    bub.style.display = 'block';
  }}
}}

function addTimelineEntry(log) {{
  var div = document.createElement('div');
  div.className = 'tl-entry';
  var c     = COLORS[log.agent_name] || '#555';
  var emoji = EMOJIS[log.agent_name] || '💻';
  var name  = NAMES[log.agent_name]  || log.agent_name;
  div.style.borderLeftColor = c;
  div.innerHTML =
    '<span class="tl-agent" style="color:' + c + '">' + emoji + ' ' + name + '</span>' +
    ' <span style="color:#334;font-size:9px;">[' + (log.log_type||'') + ']</span><br>' +
    '<span style="color:#8ba8c8;font-size:10px;">' +
    (log.log_content || '').substring(0, 180) + '</span>';
  var tl = document.getElementById('rp-timeline');
  tl.appendChild(div);
  tl.scrollTop = tl.scrollHeight;
}}

function replayStep(log) {{
  var agent   = log.agent_name;
  var content = log.log_content || '';
  if (agent && agent !== 'SYSTEM') updateAgentCard(agent, content);
  if (!dotLocated) tryExtractLocation(content);
  addTimelineEntry(log);
  idx++;
  document.getElementById('rp-counter').textContent = idx + ' / ' + LOGS.length + ' 條';
  document.getElementById('rp-prog').style.width = (idx / LOGS.length * 100) + '%';
}}

function getSpeed() {{
  return parseInt(document.getElementById('speed-sel').value) || 500;
}}

function startReplay() {{
  if (timer) return;
  timer = setInterval(function() {{
    if (idx >= LOGS.length) {{ clearInterval(timer); timer = null; return; }}
    replayStep(LOGS[idx]);
  }}, getSpeed());
}}

function pauseReplay() {{ clearInterval(timer); timer = null; }}

function resetReplay() {{
  clearInterval(timer); timer = null; idx = 0;
  document.getElementById('rp-timeline').innerHTML = '';
  document.getElementById('rp-prog').style.width = '0%';
  document.getElementById('rp-counter').textContent = '0 / ' + LOGS.length + ' 條';
  ['COMMANDER','INTELLIGENCE','OPERATIONS','LEGAL','PR'].forEach(function(a) {{
    agentState[a] = 'idle';
    var card = document.getElementById('rp-card-' + a);
    var dot  = document.getElementById('rp-dot-' + a);
    var lbl  = document.getElementById('rp-label-' + a);
    var av   = document.getElementById('rp-avatar-' + a);
    var bub  = document.getElementById('rp-bubble-' + a);
    if (card) {{ card.style.borderColor='#444'; card.style.boxShadow=''; card.classList.remove('rp-active'); }}
    if (dot)  {{ dot.className='rp-dot'; }}
    if (lbl)  {{ lbl.textContent='⚪ 待命'; }}
    if (av)   {{ av.style.background=''; av.style.borderColor='#444'; av.style.boxShadow=''; }}
    if (bub)  {{ bub.style.display='none'; }}
  }});
  dotLocated = false;
  document.getElementById('rp-dot').style.display = 'none';
}}

document.getElementById('rp-counter').textContent = '0 / ' + LOGS.length + ' 條';
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
# 頁面配置
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="中華民國 AI 模擬法庭與判決分析系統",
    page_icon="⚖️",
    layout="wide"
)

# 讀取台灣地圖 SVG
_SVG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "taiwan_map.svg")
_SVG_B64  = load_svg_base64(_SVG_PATH)

st.title("⚖️ 中華民國 AI 模擬法庭與判決分析系統")

# 提前提取戰情室 CSS 樣式，在最頂層一次性注入
temp_html = build_war_room_html(show_map=False)
style_match = re.search(r'<style>(.*?)</style>', temp_html, re.DOTALL)
if style_match:
    st.html(f"<style>{style_match.group(1)}</style>")

st.sidebar.markdown("""
這是一套基於多智能體 (Multi-Agent) 協作技術與 RAG 設計的**「中華民國 AI 模擬法庭與判決分析系統」**。<br><br>
當您輸入起訴事實或訴狀後：<br>
- 🔎 **事實調查官**：負責梳理起訴事實、當事人關係與法律爭點。<br>
- 📖 **法學研究員**：語意檢索憲法、刑法、民法向量庫（RAG）。<br>
- ⚔️ **控辯攻防專家**：模擬原告（控方）的構成要件論證與被告（辯方）的防禦抗辯。<br>
- ⚖️ **審判長**：依據司法邏輯與三階論法做出裁決並起草正式司法判決書。<br>
- 📢 **判決通報官**：將深奧的判決書翻譯成社會大眾能讀懂的「白話判決懶人包」並配上 AI 圖示。
""", unsafe_allow_html=True)

with st.sidebar.expander("🧠 大腦模型設定", expanded=False):
    st.info("若未填寫，則使用系統 .env 預設配置")
    custom_model = st.text_input("Model Name", placeholder="例如: openai/gpt-4o")
    custom_key = st.text_input("API Key", type="password", placeholder="填入 API Key")
    custom_url = st.text_input("Base URL", placeholder="例如: https://api.deepseek.com")
    st.caption("支援: Gemini, OpenAI, DeepSeek, Groq 等 OpenAI 相容接口。")

# ─── Session State 初始化 ───
for key, default in {
    "report_text_input": "",
    "active_agent": "SYSTEM",
    "bubble_content": "",
    "location": "",
    "pollutant": "案件",
    "all_done": False,
    "war_room_key": 0,
    "sidebar_logs": [],
    "operations_plan": "",
    "legal_report": "",
    "final_pr": "",
    "map_key_idx": 0,
    "map_localized": False,
    "agent_statuses": {k: "idle" for k in AGENT_META},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─── 側邊欄日誌顯示區 ───
if st.session_state.sidebar_logs:
    with st.sidebar:
        st.markdown("---")
        with st.status("🏛️ AI 模擬法庭審理進度", expanded=True):
            for log in st.session_state.sidebar_logs:
                st.write(log)

def set_text(text):
    st.session_state.report_text_input = text

# ─── 範例按鈕 ───
cols = st.columns(2)
with cols[0]:
    st.button("📝 帶入範例：刑事犯罪案件 (竊盜與共同正犯)", on_click=set_text,
              args=("【起訴事實】被告張三與李四於2026年4月9日深夜，共同攜帶油壓剪，前往桃園市觀音區某住宅鐵窗剪斷後潛入，竊取現金新臺幣10萬元及金飾，得手後搭乘由共犯王五駕駛之接應車輛逃逸。經被害人報警究辦，警方於周邊路口監視器鎖定車牌並將三人逮捕歸案。",),
              use_container_width=True)
with cols[1]:
    st.button("📝 帶入範例：民事侵權行為 (車禍損害賠償)", on_click=set_text,
              args=("【原告主張】原告於2026年4月5日駕駛自用小客車，行經彰化縣和美鎮某路口時，被告超速且闖紅燈，直接撞擊原告車輛側邊，導致原告車輛嚴重毀損（修理費用新臺幣15萬元）、原告本人右腳骨折住院（醫療費5萬元，精神撫慰金10萬元），為此依民法第184條、第191-2條、第195條侵權行為法律關係，請求被告賠償新臺幣30萬元。",),
              use_container_width=True)

report_text = st.text_area(
    "請輸入或貼上民/刑事起訴書、爭點訴狀或案情描述...",
    key="report_text_input",
    height=180
)

st.markdown("---")

tab1, tab2 = st.tabs(["🏛️ 即時模擬法庭", "📂 歷史判決書資料庫"])

# ════════════════════════════════════════════════
# TAB 1：即時模擬法庭
# ════════════════════════════════════════════════
with tab1:
    map_col, agents_col = st.columns([1, 2])
    map_placeholder = map_col.empty()

    def render_relation_graph():
        with map_placeholder.container():
            st.markdown('<div style="text-align:center;font-size:14px;color:#d4af37;font-weight:bold;margin-bottom:8px;">🏛️ 案件關係人與爭點鏈 (Mermaid Graph)</div>', unsafe_allow_html=True)
            html_content = build_mermaid_relation_html(st.session_state.location, st.session_state.pollutant)
            components.html(html_content, height=520, scrolling=False)

    render_relation_graph()

    agents_placeholder = agents_col.empty()

    def render_agents():
        with agents_placeholder.container():
            raw_html = build_war_room_html(
                location="",
                svg_b64="",
                all_done=st.session_state.all_done,
                agent_statuses=st.session_state.agent_statuses,
                ticker_text=st.session_state.bubble_content,
                show_map=False,
            )
            # 移除 style 標籤與 iframe 標籤，只保留乾淨的 HTML Body
            clean_html = re.sub(r'<style>.*?</style>', '', raw_html, flags=re.DOTALL)
            clean_html = clean_html.replace("<!DOCTYPE html>", "").replace("<html>", "").replace("</html>", "").replace("<head>", "").replace("</head>", "").replace("<body>", "").replace("</body>", "")
            st.html(clean_html)

    render_agents()

    st.markdown("---")

    if st.button("⚖️ 啟動 AI 模擬法庭", type="primary", use_container_width=True):
        if not report_text.strip():
            st.warning("請填寫起訴案情內容！")
        else:
            # 重設與初始化
            st.session_state.active_agent    = "SYSTEM"
            st.session_state.bubble_content  = "⚡ 正在啟動 AI 模擬法庭..."
            st.session_state.location        = ""
            st.session_state.pollutant       = "案件"
            st.session_state.all_done        = False
            st.session_state.agent_statuses  = {k: "" for k in AGENT_META}
            st.session_state.operations_plan = ""
            st.session_state.legal_report    = ""
            st.session_state.final_pr        = ""
            st.session_state.map_localized   = False
            st.session_state.sidebar_logs    = []
            
            render_agents()
            result_container = st.empty()
            
            with st.sidebar:
                st.markdown("---")
                status_area = st.status("🏛️ AI 模擬法庭審理中...", expanded=True)

            try:
                api_url = "http://localhost:8000/api/v1/incident/report"
                payload = {
                    "report_text": report_text,
                    "event_time": datetime.now().isoformat(),
                    "model_name": custom_model if custom_model.strip() else None,
                    "api_key": custom_key if custom_key.strip() else None,
                    "base_url": custom_url if custom_url.strip() else None
                }
                is_fallback = False

                with httpx.Client(timeout=300.0) as client:
                    with client.stream("POST", api_url, json=payload) as response:
                        response.raise_for_status()

                        for line in response.iter_lines():
                            if not line.strip():
                                continue

                            clean_line = line[6:] if line.startswith("data: ") else line
                            if line.startswith(":"): continue # keep-alive

                            try:
                                data = json.loads(clean_line)
                            except json.JSONDecodeError:
                                continue

                            msg_type = data.get("type", "")

                            if msg_type == "agent_log":
                                agent    = data.get("agent", "SYSTEM")
                                content  = data.get("content", "")
                                log_type = data.get("log_type", "THOUGHT")

                                if st.session_state.agent_statuses.get(agent) == "done" and st.session_state.active_agent != agent:
                                    continue

                                st.session_state.active_agent   = agent
                                st.session_state.bubble_content = content
                                
                                status_label = _make_status_msg(agent, content, log_type)
                                st.session_state.agent_statuses[agent] = status_label
                                
                                full_log_msg = f"**{AGENT_META.get(agent, {}).get('name', agent)}**: {status_label}"
                                if not st.session_state.sidebar_logs or st.session_state.sidebar_logs[-1] != full_log_msg:
                                    st.session_state.sidebar_logs.append(full_log_msg)
                                    status_area.write(full_log_msg)

                                # 轄區縣市偵測
                                if not st.session_state.map_localized:
                                    found_county = None
                                    for county in _TAIWAN_COUNTY_COORDS.keys():
                                        if county in content or county in report_text:
                                            found_county = county
                                            break
                                    if found_county:
                                        full_loc = found_county
                                        for text_source in [content, report_text]:
                                            match = re.search(rf"({found_county}[一-龥]{{1,4}}?(?:區|鎮|鄉|市))", text_source)
                                            if match:
                                                full_loc = match.group(1)
                                                break
                                        
                                        st.session_state.location = full_loc
                                        st.session_state.map_localized = True
                                        render_relation_graph()

                                render_agents()
                                import time
                                time.sleep(0.01)

                            elif msg_type == "location_determined":
                                loc = data.get("location", "")
                                if loc and not st.session_state.map_localized:
                                    st.session_state.location = loc
                                    st.session_state.map_localized = True
                                    render_relation_graph()

                            elif msg_type == "task_complete":
                                task_index = data.get("task_index", -1)
                                if task_index == 0:
                                    st.session_state.agent_statuses["INTELLIGENCE"] = "done"
                                    st.session_state.active_agent = "LEGAL"
                                    st.session_state.agent_statuses["LEGAL"] = "🔄 執行中"
                                    st.session_state.bubble_content = "📖 法學研究員正在跨庫檢索中華民國憲法、刑法、民法條文（RAG）..."
                                    status_area.write("🔎 **事實調查官**: ✅ 案情事實與爭點梳理完成！")
                                elif task_index == 1:
                                    st.session_state.agent_statuses["LEGAL"] = "done"
                                    st.session_state.active_agent = "OPERATIONS"
                                    st.session_state.agent_statuses["OPERATIONS"] = "🔄 執行中"
                                    st.session_state.bubble_content = "⚔️ 控辯攻防專家正在模擬法庭兩造辯論..."
                                    status_area.write("📖 **法學研究員**: ✅ 適用法條檢索與法理研析完成！")
                                elif task_index == 2:
                                    st.session_state.agent_statuses["OPERATIONS"] = "done"
                                    st.session_state.active_agent = "COMMANDER"
                                    st.session_state.agent_statuses["COMMANDER"] = "🔄 執行中"
                                    st.session_state.bubble_content = "⚖️ 審判長正在審理兩造主張，做出裁判理由與主文..."
                                    status_area.write("⚔️ **控辯攻防專家**: ✅ 法庭兩造辯論模擬完成！")
                                elif task_index == 3:
                                    st.session_state.agent_statuses["COMMANDER"] = "done"
                                    st.session_state.active_agent = "COMMANDER"
                                    st.session_state.agent_statuses["COMMANDER"] = "🔄 執行中"
                                    st.session_state.bubble_content = "📄 審判長正在起草中華民國法院正式判決書..."
                                    status_area.write("⚖️ **審判長**: ✅ 量刑與審判裁決決定完成！")
                                elif task_index == 4:
                                    st.session_state.agent_statuses["COMMANDER"] = "done"
                                    st.session_state.active_agent = "PR"
                                    st.session_state.agent_statuses["PR"] = "🔄 執行中"
                                    st.session_state.bubble_content = "📢 判決通報官準備撰寫白話判決摘要與懶人包，並生成 AI 法庭配圖..."
                                    status_area.write("📄 **審判長**: ✅ 法院正式判決書起草完成！")
                                elif task_index == 5:
                                    st.session_state.agent_statuses["PR"] = "done"
                                    status_area.write("📢 **判決通報官**: ✅ 白話判決新聞稿與配圖完成！")

                                render_agents()
                                import time
                                time.sleep(0.01)

                            elif msg_type == "done":
                                st.session_state.all_done        = True
                                st.session_state.bubble_content  = "⚖️ 法庭審判完成，判決書已送達！"
                                st.session_state.location        = data.get("location", st.session_state.location)
                                # 偵測民刑事案由
                                if "民事" in report_text or "民法" in report_text or "原告" in report_text:
                                    st.session_state.pollutant = "民事糾紛"
                                else:
                                    st.session_state.pollutant = "刑事犯罪"
                                
                                st.session_state.operations_plan = data.get("operations_plan", "")
                                st.session_state.legal_report    = data.get("legal_report", "")
                                st.session_state.final_pr        = data.get("response_plan", "")
                                is_fallback = (data.get("data_source") == "FALLBACK")
                                
                                status_area.update(label="⚖️ 模擬法庭審判完成！", state="complete", expanded=False)
                                render_agents()
                                render_relation_graph()

                            elif msg_type == "error":
                                st.error(f"❌ 後端發生錯誤: {data.get('error', '')}")
                                status_area.update(label="❌ 執行發生錯誤", state="error")
                                break

                st.success("⚖️ 模擬法庭審判與量刑完成！")
                if is_fallback:
                    st.warning("⚠️ 已套用備援設定。")

                with result_container.container():
                    st.markdown("---")

                    with st.expander("⚔️ 控辯兩造：法庭辯論攻防記錄", expanded=True):
                        ops = st.session_state.operations_plan
                        if ops:
                            st.markdown(ops)
                        else:
                            st.info("兩造攻防記錄尚未取得。")

                    with st.expander("⚖️ 審判長：中華民國法院正式判決書 (草案)", expanded=True):
                        legal = st.session_state.legal_report
                        if legal:
                            st.markdown("""
<style>
.legal-doc {
    background: linear-gradient(135deg, #0f172a, #1e293b);
    border: 1px solid #d4af3744;
    border-left: 5px solid #d4af37;
    border-radius: 8px;
    padding: 24px 28px;
    font-family: '標楷體', Georgia, serif;
    color: #f8fafc;
    line-height: 2.0;
    white-space: pre-wrap;
    word-wrap: break-word;
    word-break: break-all;
    box-shadow: 0 4px 14px rgba(0,0,0,0.5);
}
.legal-doc h3 { color: #d4af37; letter-spacing: 2px; margin-top: 18px; border-bottom:1px solid #d4af3722; padding-bottom:6px; }
.legal-doc strong { color: #facc15; }
</style>
""", unsafe_allow_html=True)
                            # XSS 防護：對 LLM 輸出進行 HTML 轉義
                            safe_legal = html_lib.escape(legal)
                            # 保留基本 Markdown 格式標記（已轉義的）轉回 HTML
                            safe_legal = safe_legal.replace('\n', '<br>')
                            st.markdown(f'<div class="legal-doc">{safe_legal}</div>', unsafe_allow_html=True)
                        else:
                            st.info("正式判決書尚未起草。")

                    with st.expander("📢 判決通報官：白話判決新聞懶人包與模擬圖", expanded=True):
                        pr = st.session_state.final_pr
                        if pr:
                            render_markdown_with_images(pr)
                        else:
                            st.info("白話判決懶人包尚未取得。")

            except httpx.ConnectError:
                st.error("❌ 無法連線到 FastAPI 後端，請確認已啟動後端伺服器 (start.sh)！")
            except httpx.TimeoutException:
                st.error("⏳ 呼叫逾時：AI 團隊審理時間過長，請再試一次。")
            except Exception as e:
                st.error(f"❌ 發生未預期錯誤：{e}")

# ════════════════════════════════════════════════
# TAB 2：歷史判決書資料庫與審理重播
# ════════════════════════════════════════════════
with tab2:
    st.subheader("📂 歷史判決書資料庫")
    st.markdown("這裡紀錄了系統過去審理過的所有模擬案件判決書與法庭辯論。")

    if st.button("🔄 重新載入資料庫", key="btn_reload_db"):
        st.rerun()

    try:
        incidents = get_all_incidents()
        if not incidents:
            st.info("目前資料庫中尚無案件紀錄。")
        else:
            PAGE_SIZE = 10
            total_pages = max(1, (len(incidents) + PAGE_SIZE - 1) // PAGE_SIZE)

            if "hist_page" not in st.session_state:
                st.session_state.hist_page = 0

            nav_cols = st.columns([1, 2, 1])
            with nav_cols[0]:
                if st.button("⬅️ 上一頁", key="hist_prev", disabled=(st.session_state.hist_page <= 0)):
                    st.session_state.hist_page -= 1
                    st.rerun()
            with nav_cols[1]:
                st.markdown(
                    f"<div style='text-align:center;padding:6px 0;color:#888;'>"
                    f"第 {st.session_state.hist_page + 1} / {total_pages} 頁 "
                    f"（共 {len(incidents)} 筆紀錄）</div>",
                    unsafe_allow_html=True,
                )
            with nav_cols[2]:
                if st.button("下一頁 ➡️", key="hist_next", disabled=(st.session_state.hist_page >= total_pages - 1)):
                    st.session_state.hist_page += 1
                    st.rerun()

            start = st.session_state.hist_page * PAGE_SIZE
            page_incidents = incidents[start : start + PAGE_SIZE]

            st.markdown("""
<style>
.legal-doc-hist {
    background: linear-gradient(135deg, #0f172a, #1e293b);
    border: 1px solid #d4af3744; border-left: 5px solid #d4af37;
    border-radius: 8px; padding: 24px 28px;
    font-family: '標楷體', Georgia, serif;
    color: #f8fafc; line-height: 2.0; 
    white-space: pre-wrap; word-wrap: break-word; word-break: break-all;
}
</style>
""", unsafe_allow_html=True)

            for idx, inc in enumerate(page_incidents):
                inc_id = inc.get('id', start + idx)
                
                # 相容舊版名稱
                c_title = inc['location'] if inc['location'] != "未明" else "模擬案件"
                c_type = inc['pollutant'] if inc['pollutant'] != "未明" else "司法審判"
                
                label = f"⚖️ [{inc['timestamp'][:19]}] {c_title} — {c_type}"
                with st.expander(label, expanded=False, key=f"hist_exp_{inc_id}"):
                    t1, t2, t3, t4 = st.tabs(
                        ["📢 白話判決新聞稿", "⚖️ 控辯攻防記錄", "📜 正式法院判決書", "🎬 模擬法庭審理重播"],
                        key=f"hist_tabs_{inc_id}",
                    )

                    with t1:
                        pr_text = inc.get('pr_press_release', '')
                        if pr_text:
                            render_markdown_with_images(pr_text)
                        else:
                            st.write("無新聞稿資料")

                    with t2:
                        cmd_text = inc.get('commander_report', '')
                        if cmd_text:
                            st.markdown(cmd_text)
                        else:
                            st.info("此案件尚無控辯攻防記錄。")

                    with t3:
                        legal_text = inc.get('legal_report', '')
                        if legal_text:
                            # XSS 防護：對歷史判決書 LLM 輸出進行 HTML 轉義
                            safe_legal_text = html_lib.escape(legal_text).replace('\n', '<br>')
                            st.html(f'<div class="legal-doc-hist">{safe_legal_text}</div>')
                        else:
                            st.info("此案件尚無正式判決書。")

                    with t4:
                        st.markdown("按下 **▶ 開始重播** 即可以 0.5 秒/條的速度重播本次模擬法庭審理與辯論過程：")
                        try:
                            meeting_logs = get_meeting_logs_by_incident(inc['id'])
                            if not meeting_logs:
                                st.info("此案件尚無審理會議日誌。")
                            else:
                                replay_html = build_replay_html(
                                    logs=meeting_logs,
                                    svg_b64=_SVG_B64,
                                    location=inc.get("location", ""),
                                )
                                components.html(
                                    replay_html + f"\n<!-- id: {inc_id} -->",
                                    height=780,
                                    scrolling=True,
                                )
                        except Exception as e:
                            st.error(f"無法載入日誌：{e}")

    except Exception as e:
        st.error(f"無法讀取資料庫：{e}")
