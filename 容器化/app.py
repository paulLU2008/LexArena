import streamlit as st
import streamlit.components.v1 as components
import httpx
from datetime import datetime
import json
import re
import os
import sys
import base64

# ── 選用套件（未安裝時降級，不中斷應用程式）──
try:
    import folium
    from streamlit_folium import st_folium
    _FOLIUM_OK = True
except ImportError:
    _FOLIUM_OK = False

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
    _GEOPY_OK = True
except ImportError:
    _GEOPY_OK = False

# 將當前路徑加入 sys 讓 Streamlit 能 import database.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from database import get_all_incidents, get_meeting_logs_by_incident
except ImportError:
    def get_all_incidents():
        return []
    def get_meeting_logs_by_incident(incident_id):
        return []

# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────

_TAIWAN_CENTER = (23.5, 121.0)  # 備援：台灣中心點

_SPECIFIC_COORDS = {
    "桃園市觀音區": (25.052805, 121.1530745),
    "彰化縣和美鎮": (24.1060706, 120.5309482),
}

_TAIWAN_COUNTY_COORDS = {
    "基隆市": (25.128, 121.741),
    "臺北市": (25.033, 121.565),
    "台北市": (25.033, 121.565),
    "新北市": (24.964, 121.501),
    "桃園市": (24.993, 121.301),
    "新竹市": (24.814, 120.967),
    "新竹縣": (24.838, 121.018),
    "苗栗縣": (24.560, 120.821),
    "臺中市": (24.148, 120.674),
    "台中市": (24.148, 120.674),
    "彰化縣": (24.052, 120.516),
    "南投縣": (23.906, 120.687),
    "雲林縣": (23.709, 120.431),
    "嘉義市": (23.480, 120.450),
    "嘉義縣": (23.452, 120.256),
    "臺南市": (22.997, 120.212),
    "台南市": (22.997, 120.212),
    "高雄市": (22.627, 120.302),
    "屏東縣": (22.551, 120.548),
    "宜蘭縣": (24.702, 121.738),
    "花蓮縣": (23.987, 121.601),
    "臺東縣": (22.756, 121.150),
    "台東縣": (22.756, 121.150),
    "澎湖縣": (23.571, 119.579),
    "金門縣": (24.448, 118.378),
    "連江縣": (26.160, 119.950)
}

def geocode_location(location_str: str) -> tuple[float, float]:
    """將地名轉為 (lat, lon)，具備離線靜態硬編碼縣市/特定地點地圖對照與 SSL 修復功能"""
    if not location_str:
        return _TAIWAN_CENTER

    # 1. 優先精準硬編碼匹配（範例或預設的熱門地點）
    for key, coords in _SPECIFIC_COORDS.items():
        if key in location_str or location_str in key:
            return coords

    # 2. 第二優先縣市硬編碼匹配（預防網路離線或 Nominatim 連線失敗）
    for county, coords in _TAIWAN_COUNTY_COORDS.items():
        if county in location_str:
            return coords

    if not _GEOPY_OK:
        return _TAIWAN_CENTER

    try:
        # 在 macOS 上配置預設 SSL 上下文以避免證書驗證失敗
        import ssl
        import geopy.geocoders
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            geopy.geocoders.options.default_ssl_context = ctx
        except Exception:
            # 備援：若無 certifi 則使用無驗證的上下文
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            geopy.geocoders.options.default_ssl_context = ctx

        geolocator = Nominatim(user_agent="soil_ai_response_v1", timeout=5)
        result = geolocator.geocode(location_str + ", Taiwan")
        if result:
            return (result.latitude, result.longitude)
        
        # 第二次嘗試：只用地名本身
        result = geolocator.geocode(location_str)
        if result:
            return (result.latitude, result.longitude)
    except (GeocoderTimedOut, GeocoderUnavailable, Exception):
        pass

    # 3. 若皆失敗，嘗試從地名中找縣市名稱以提供縣市級備援定位
    for county, coords in _TAIWAN_COUNTY_COORDS.items():
        if county in location_str:
            return coords

    return _TAIWAN_CENTER

def _make_status_msg(agent: str, content: str, log_type: str) -> str:
    """將 SSE log 轉換為人類可讀的 Agent 即時狀態文字（顯示於氣泡）"""
    c = content.lower()

    if log_type == "ACTION":
        if any(k in c for k in ["weather", "氣象", "cwa", "降雨", "rainfall"]):
            return "🌧️ 呼叫氣象署 API 取得降雨數據..."
        if any(k in c for k in ["agriculture", "agri", "農業", "農地", "ems_s_07"]):
            return "🌾 查詢農業部土壤污染場址資料..."
        if any(k in c for k in ["economic", "econ", "工廠", "ems_s_01", "factory"]):
            return "🏭 查詢環保署列管工廠清單..."
        if any(k in c for k in ["image", "imagen", "圖片", "生圖", "generate"]):
            return "🖼️ 正在生成 AI 現場配圖..."
        if any(k in c for k in ["legal", "rag", "法規", "知識庫", "法條"]):
            return "⚖️ 檢索環境法律法規知識庫..."
        return "⚙️ 執行工具呼叫中..."

    # THOUGHT 類型
    if agent == "COMMANDER":
        if any(k in c for k in ["萃取", "extract", "地點", "location"]):
            return "🔍 萃取事件地點與污染物資訊..."
        if any(k in c for k in ["報告", "report", "匯總", "summary", "synthesize"]):
            return "📋 彙整各方情報，撰寫最終報告..."
        if any(k in c for k in ["final answer", "最終答案"]):
            return "✍️ 輸出最終指揮官報告..."
        return "🎖️ 總指揮官分析研判中..."

    if agent == "INTELLIGENCE":
        if any(k in c for k in ["weather", "氣象", "rain", "降雨", "flood", "洪"]):
            return "🌦️ 彙整氣象擴散情報..."
        if any(k in c for k in ["farm", "農", "crop", "作物", "農地"]):
            return "🌾 分析農業損耗風險..."
        if any(k in c for k in ["factory", "工廠", "chemical", "化學", "電鍍"]):
            return "🏭 追蹤可疑污染源工廠..."
        return "🔍 整合三方跨部會情報..."

    if agent == "OPERATIONS":
        if any(k in c for k in ["flood", "洪", "攔截", "barrier", "堤", "防洪"]):
            return "🚧 規劃防洪攔截工程部署..."
        if any(k in c for k in ["farm", "農地", "封存", "crop", "封閉"]):
            return "🌾 發布農作封存令..."
        if any(k in c for k in ["inspect", "稽查", "工廠", "factory", "查緝"]):
            return "🔎 指派環保稽查大隊出動..."
        if any(k in c for k in ["excavat", "開挖", "洗消", "化學"]):
            return "⛏️ 規劃物理除污處置..."
        return "⚙️ 規劃現場行動方案..."

    if agent == "LEGAL":
        if any(k in c for k in ["legalkbreadtool", "legal_kb", "knowledge", "知識庫", "rag", "法規", "法條"]):
            return "📚 查詢法律知識庫條文..."
        if any(k in c for k in ["採樣", "sop", "蒐證", "sample", "採集"]):
            return "🔬 制定現場採樣 SOP..."
        if any(k in c for k in ["裁處", "罰鍰", "penalty", "fine", "裁金"]):
            return "📝 擬定違規裁處書草案..."
        if any(k in c for k in ["法條", "條文", "article", "law", "土污法", "水污法", "廢清法"]):
            return "⚖️ 檢索適用法規條文..."
        if any(k in c for k in ["final answer", "最終答案"]):
            return "📄 輸出法務稽查報告..."
        return "⚖️ 法律合規分析中..."

    if agent == "PR":
        if any(k in c for k in ["image", "imagen", "圖", "photo", "picture"]):
            return "🖼️ 生成 AI 現場配圖..."
        if any(k in c for k in ["news", "新聞", "稿", "press", "release", "通報"]):
            return "📰 撰寫對外公關新聞稿..."
        return "📢 準備公眾通報內容..."

    return "💡 思考研判中..."


def render_markdown_with_images(md_text):
    """解析 Markdown，將本地配圖用 st.image 渲染（Bug #3 修復）"""
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

def load_svg_base64(path: str) -> str:
    """讀取 SVG 檔並轉成 base64 字串，供 HTML 內嵌使用"""
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""

# ─────────────────────────────────────────────
# AI 戰情會議室 HTML 元件
# ─────────────────────────────────────────────

AGENT_META = {
    "COMMANDER":    {"emoji": "🎖️", "name": "總指揮官",  "color": "#e74c3c"},
    "INTELLIGENCE": {"emoji": "🔍", "name": "情報分析官", "color": "#3498db"},
    "OPERATIONS":   {"emoji": "⚙️", "name": "行動策劃官", "color": "#e67e22"},
    "LEGAL":        {"emoji": "⚖️", "name": "法務稽查官", "color": "#1abc9c"},
    "PR":           {"emoji": "📢", "name": "公關通報官", "color": "#9b59b6"},
    "SYSTEM":       {"emoji": "💻", "name": "系統",      "color": "#95a5a6"},
}

def build_war_room_html(
    location: str = "",
    svg_b64: str = "",
    all_done: bool = False,
    agent_statuses: dict = None,
    ticker_text: str = "",
    show_map: bool = True,
) -> str:
    """生成完整的 HTML 戰情會議室"""

    if agent_statuses is None:
        agent_statuses = {k: "idle" for k in AGENT_META}

    # 建立四大 Agent 區塊的 HTML
    def agent_card(code: str) -> str:
        meta = AGENT_META[code]

        # 從 agent_statuses 讀取狀態值
        # "" / "idle" → 待命; "done" → 已完成; 其他字串 → 執行中（氣泡文字）
        status_val = (agent_statuses or {}).get(code, "")
        is_running = bool(status_val) and status_val not in ("idle", "done", "")

        # 狀態樣式
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
            glow         = "0 0 4px #0005"

        # 思考氣泡（對有狀態文字的 agent 顯示，注入 agent 專屬顏色）
        bubble_html = ""
        if is_running and not all_done:
            escaped     = status_val.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            agent_color = meta['color']
            tail_color  = agent_color
            bubble_html = (
                f'<div class="speech-bubble" style="border-color:{agent_color};'
                f' box-shadow:0 0 12px {agent_color}66, 0 4px 16px #00000080;">'
                f'<div class="bubble-text">{escaped[:120]}'
                f'{"..." if len(status_val) > 120 else ""}</div>'
                f'<div class="bubble-tail" style="border-top-color:{tail_color};"></div>'
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

    # 地圖紅點 JS 邏輯
    escaped_loc = location.replace("'", "\\'") if location else ""
    location_js = f"""
    // 儲存縣市座標供 Modal 重用
    var _dotCx = 0, _dotCy = 0, _dotVbW = 1, _dotVbH = 1;

    function positionModalDot() {{
        if (!_dotCx && !_dotCy) return;
        var modalSvg = document.getElementById('taiwan-svg-modal');
        var wrapper  = document.getElementById('modal-map-wrapper');
        var dot      = document.getElementById('map-dot-modal');
        if (!modalSvg || !wrapper || !dot) return;
        var mRect = modalSvg.getBoundingClientRect();
        var wRect = wrapper.getBoundingClientRect();
        dot.style.left = (_dotCx / _dotVbW * mRect.width  + (mRect.left - wRect.left)) + 'px';
        dot.style.top  = (_dotCy / _dotVbH * mRect.height + (mRect.top  - wRect.top )) + 'px';
        dot.style.display = 'block';
    }}

    function openMapModal() {{
        document.getElementById('map-modal').classList.add('show');
        requestAnimationFrame(positionModalDot);
    }}

    (function() {{
        var loc = '{escaped_loc}';
        if (!loc) return;
        var counties = ['臺北市','台北市','新北市','基隆市','桃園市','新竹市','新竹縣',
                        '苗栗縣','臺中市','台中市','彰化縣','南投縣','雲林縣',
                        '嘉義市','嘉義縣','臺南市','台南市','高雄市','屏東縣',
                        '宜蘭縣','花蓮縣','臺東縣','台東縣','澎湖縣','金門縣','連江縣'];
        var matched = counties.find(function(c) {{ return loc.includes(c); }});
        if (!matched) return;
        var markerEl = document.getElementById('loc-' + matched);
        if (!markerEl) return;
        var svgEl = document.getElementById('taiwan-svg');
        var svgRect = svgEl.getBoundingClientRect();
        var cx = parseFloat(markerEl.getAttribute('cx'));
        var cy = parseFloat(markerEl.getAttribute('cy'));
        var vb = svgEl.viewBox.baseVal;
        _dotCx = cx; _dotCy = cy; _dotVbW = vb.width; _dotVbH = vb.height;
        var dot = document.getElementById('map-dot');
        dot.style.left = (cx / vb.width  * svgRect.width  + svgRect.left - document.getElementById('map-wrapper').getBoundingClientRect().left - 7) + 'px';
        dot.style.top  = (cy / vb.height * svgRect.height + svgRect.top  - document.getElementById('map-wrapper').getBoundingClientRect().top  - 7) + 'px';
        dot.style.display = 'block';
        var label = document.getElementById('map-dot-label');
        if (label) label.textContent = matched;
    }})();
    """

    # SVG 地圖 HTML
    if svg_b64:
        map_src = f"data:image/svg+xml;base64,{svg_b64}"
        map_img_html = f'<img id="taiwan-svg" src="{map_src}" style="width:100%;height:100%;object-fit:contain;" />'
    else:
        map_img_html = '<div style="color:#555;text-align:center;padding-top:80px;">🗺️ 地圖載入中...</div>'

    # 預先組裝 show_map 依賴的 HTML 片段（避免 f-string 裡使用反斜線，相容 Python 3.10）
    map_panel_html = (
        '<div class="map-panel">'
        '<div class="map-title">⚠ 災情地圖</div>'
        '<div id="map-wrapper" onclick="openMapModal()">'
        + map_img_html +
        '<div id="map-dot"><span id="map-dot-label"></span></div>'
        '</div>'
        '<div style="font-size:9px;color:#3d6494;text-align:center;">點擊地圖放大 🔍</div>'
        '</div>'
    ) if show_map else ""

    modal_html = (
        '<div id="map-modal" onclick="this.classList.remove(\'show\')">'
        '<div id="modal-map-wrapper">'
        + map_img_html.replace('id="taiwan-svg"', 'id="taiwan-svg-modal"') +
        '<div id="map-dot-modal"></div>'
        '</div></div>'
    ) if show_map else ""

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

  /* ── 左側地圖白板 ── */
  .map-panel {{
    flex: 0 0 220px;
    background: linear-gradient(145deg, #0d1b2a, #162036);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .map-title {{
    font-size: 11px;
    font-weight: 700;
    color: #5c8fd4;
    letter-spacing: 2px;
    text-align: center;
    text-transform: uppercase;
  }}
  #map-wrapper {{
    flex: 1;
    position: relative;
    cursor: zoom-in;
  }}
  #map-wrapper:hover {{ opacity: 0.92; }}

  /* 閃爍紅點 */
  #map-dot {{
    display: none;
    position: absolute;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #e74c3c;
    border: 2px solid #ff6b6b;
    box-shadow: 0 0 10px #e74c3c, 0 0 20px #e74c3c88;
    animation: blink 1s ease-in-out infinite;
    z-index: 10;
  }}
  @keyframes blink {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50%       {{ opacity: 0.3; transform: scale(0.7); }}
  }}
  #map-dot-label {{
    position: absolute;
    top: -18px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 9px;
    color: #ff6b6b;
    white-space: nowrap;
    font-weight: 700;
  }}

  /* 全屏模態 */
  #map-modal {{
    display: none;
    position: fixed;
    top: 0; left: 0;
    width: 100vw; height: 100vh;
    background: rgba(0,0,0,0.85);
    z-index: 9999;
    align-items: center;
    justify-content: center;
    cursor: zoom-out;
  }}
  #map-modal img {{
    max-height: 92vh;
    max-width: 60vw;
    border-radius: 8px;
    border: 1px solid #1e3a5f;
  }}
  #map-modal.show {{ display: flex; }}
  #modal-map-wrapper {{ position: relative; display: inline-block; line-height: 0; }}
  #map-dot-modal {{
    display: none;
    position: absolute;
    width: 18px; height: 18px;
    border-radius: 50%;
    background: #e74c3c;
    border: 2px solid #ff6b6b;
    box-shadow: 0 0 14px #e74c3c, 0 0 28px #e74c3c88;
    animation: blink 1s ease-in-out infinite;
    z-index: 10000;
    transform: translate(-50%, -50%);
    pointer-events: none;
  }}

  /* ── 右側會議桌區 ── */
  .agents-panel {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }}
  .panel-header {{
    font-size: 13px;
    font-weight: 800;
    color: #ffffff;
    letter-spacing: 3px;
    text-transform: uppercase;
    text-align: center;
    background: linear-gradient(90deg, transparent, #2c3e50, transparent);
    padding: 10px 0;
    margin-bottom: 15px;
    border-radius: 4px;
    text-shadow: 0 0 15px rgba(255,255,255,0.4);
  }}

  /* 橢圓會議桌 */
  .table-scene {{
    flex: 1;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .meeting-table {{
    width: 340px;
    height: 180px;
    background: linear-gradient(135deg, #1a2a1a, #243624);
    border: 2px solid #2d5a27;
    border-radius: 50%;
    box-shadow: 0 0 30px #2d5a2740, inset 0 0 20px #00000060;
    position: absolute;
  }}
  .table-label {{
    position: absolute;
    color: #4a7c3f;
    font-size: 10px;
    letter-spacing: 1px;
    opacity: 0.6;
  }}

  /* Agent 卡片定位（5 個角色團繞桌子） */
  .agent-card {{
    position: absolute;
    width: 110px;
    background: linear-gradient(145deg, #12192b, #1a2540);
    border: 2px solid #444;
    border-radius: 12px;
    padding: 8px 6px;
    text-align: center;
    transition: all 0.3s ease;
  }}
  .agent-card.active-card {{
    box-shadow: 0 0 18px currentColor, 0 0 6px currentColor;
    transform: scale(1.06);
  }}

  /* 5 個 Agent 的五角形定位 */
  .agent-card:nth-child(1) {{ top: 10px;    left: 20px; }}      /* CMD  - 左上 */
  .agent-card:nth-child(2) {{ top: 10px;    right: 20px; }}     /* INT  - 右上 */
  .agent-card:nth-child(3) {{ bottom: 10px; left: 20px; }}      /* OPS  - 左下 */
  .agent-card:nth-child(4) {{ bottom: 10px; right: 20px; }}     /* PR   - 右下 */
  .agent-card:nth-child(5) {{ top: 50%;    right: -8px;
                               transform: translateY(-50%); }}  /* LEGAL- 右中 */

  .agent-emoji {{ font-size: 26px; margin-bottom: 4px; }}
  .agent-name  {{ font-size: 11px; font-weight: 700; color: #dde; margin-bottom: 4px; }}
  .agent-status {{ font-size: 10px; color: #888; display: flex; align-items: center; gap: 4px; justify-content: center; }}

  /* 狀態燈 */
  .dot {{
    width: 7px; height: 7px;
    border-radius: 50%;
    display: inline-block;
  }}
  .dot.idle   {{ background: #555; }}
  .dot.active {{ background: #2ecc71; box-shadow: 0 0 6px #2ecc71; animation: pulse 1s infinite; }}
  .dot.done   {{ background: #3498db; }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

  /* 思考氣泡（Speech Bubble） */
  @keyframes bubbleFadeIn {{
    from {{ opacity: 0; transform: translateX(-50%) translateY(6px); }}
    to   {{ opacity: 1; transform: translateX(-50%) translateY(0);   }}
  }}
  .speech-bubble {{
    position: absolute;
    bottom: 110%;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(14, 22, 40, 0.93);
    border: 1px solid #3d6494;
    border-radius: 10px;
    padding: 7px 10px;
    width: 190px;
    z-index: 20;
    backdrop-filter: blur(4px);
    animation: bubbleFadeIn 0.3s ease forwards;
  }}
  .bubble-text {{
    font-size: 10px;
    color: #a8c8f0;
    line-height: 1.5;
    text-align: left;
    word-break: break-word;
  }}
  .bubble-tail {{
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    width: 0; height: 0;
    border-left: 7px solid transparent;
    border-right: 7px solid transparent;
    border-top: 8px solid #3d6494;
  }}

  /* 底部 ticker */
  .log-ticker {{
    background: #070d16;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 10px;
    color: #7fb3d3;
    font-family: 'Courier New', monospace;
    max-height: 48px;
    overflow: hidden;
  }}
</style>
</head>
<body>

<div class="war-room">
  {map_panel_html}

  <!-- 右：Agent 會議區 -->
  <div class="agents-panel">
    <div class="panel-header">🎯 AI 戰情會議室 — Agent Activity Monitor</div>
    <div class="table-scene">
      <div class="meeting-table">
        <div class="table-label" style="top:50%;left:50%;transform:translate(-50%,-50%)">🛡 指揮中心</div>
      </div>
      {agents_html}
    </div>
    <div class="log-ticker" id="log-ticker">
      {('💬 ' + ticker_text[:120]) if ticker_text else '💻 系統就緒，等待事件通報...'}
    </div>
  </div>
</div>

{modal_html}

<script>
  {location_js if show_map else ''}
</script>
</body>
</html>
"""
    return html

# ─────────────────────────────────────────────
# 歷史重播 HTML
# ─────────────────────────────────────────────

def build_replay_html(logs: list, svg_b64: str = "", location: str = "") -> str:
    """重播會議過程：上方戰情會議室動畫 + 下方文字時間軸"""
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

    # 四個 Agent 卡片 HTML（靜態，JS 會動態更新樣式）
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
.prog-fill {{ height:100%; background:#3498db; width:0%; transition:width .4s linear; }}

/* ── 戰情會議室 ── */
.rp-war-room {{ display:flex; gap:14px; padding:12px 14px; height:340px; }}

/* 地圖面板 */
.rp-map-panel {{ flex:0 0 160px; background: rgba(13, 27, 42, 0.8);
                 border:1px solid #3d6494; border-radius:10px; padding:8px;
                 display:flex; flex-direction:column; gap:6px; 
                 backdrop-filter: blur(10px);
                 box-shadow: inset 0 0 20px rgba(0,0,0,0.5); }}
.rp-map-title {{ font-size:10px; font-weight:700; color:#5c8fd4;
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

/* 會議桌 */
.rp-agents-panel {{ flex:1; display:flex; flex-direction:column; gap:8px; }}
.rp-panel-hdr {{ font-size:10px; font-weight:700; color:#5c8fd4;
                 letter-spacing:2px; text-transform:uppercase; text-align:center; }}
.rp-table-scene {{ flex:1; position:relative; display:flex;
                   align-items:center; justify-content:center; }}
.rp-meeting-table {{ width:300px; height:160px;
                     background:linear-gradient(135deg,#1a2a1a,#243624);
                     border:2px solid #2d5a27; border-radius:50%;
                     box-shadow:0 0 28px #2d5a2740,inset 0 0 18px #00000060;
                     position:absolute; }}
.rp-table-lbl {{ position:absolute; color:#4a7c3f; font-size:9px;
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
.rp-dot.active {{ background:#2ecc71; box-shadow:0 0 5px #2ecc71; animation:pulse .9s infinite; }}
.rp-dot.done   {{ background:#3498db; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}

/* 思考氣泡 */
.rp-bubble {{ position:absolute; bottom:calc(100% + 6px); left:50%;
              transform:translateX(-50%); background:#1e2d45;
              border:1px solid #3d6494; border-radius:9px;
              padding:6px 8px; width:150px; z-index:20;
              box-shadow:0 4px 14px #00000080; }}
.rp-bubble-text {{ font-size:9px; color:#a8c8f0; line-height:1.45;
                   text-align:left; word-break:break-all; }}
.rp-bubble-tail {{ position:absolute; top:100%; left:50%; transform:translateX(-50%);
                   width:0; height:0;
                   border-left:6px solid transparent;
                   border-right:6px solid transparent;
                   border-top:7px solid #3d6494; }}

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

<!-- 控制列 -->
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

<!-- 戰情會議室 -->
<div class="rp-war-room">

  <!-- 左：地圖 -->
  <div class="rp-map-panel">
    <div class="rp-map-title">⚠ 災情地圖</div>
    <div id="rp-map-wrapper">
      {map_img_tag}
      <div id="rp-dot"><span id="rp-dot-label"></span></div>
    </div>
  </div>

  <!-- 右：會議桌 -->
  <div class="rp-agents-panel">
    <div class="rp-panel-hdr">🎬 會議過程重播 — Agent Activity Replay</div>
    <div class="rp-table-scene">
      <div class="rp-meeting-table">
        <div class="rp-table-lbl" style="top:50%;left:50%;transform:translate(-50%,-50%)">🛡 指揮中心</div>
      </div>
      {agent_cards_html}
    </div>
  </div>

</div>

<!-- 文字時間軸 -->
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

// 初始化地圖紅點（若已知地點）
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

  // 把之前 active 的改為 done
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
      // 隱藏前一個氣泡
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
  // 重設所有 Agent 狀態
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

# ════════════════════════════════════════════════
# 頁面配置
# ════════════════════════════════════════════════
st.set_page_config(
    page_title="土壤污染 AI 應變指揮中心(數位孿生模擬平台)",
    page_icon="🌱",
    layout="wide"
)

# 讀取台灣地圖 SVG
_SVG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "taiwan_map.svg")
_SVG_B64  = load_svg_base64(_SVG_PATH)

st.title("🌱 土壤污染 AI 應變指揮中心(數位孿生模擬平台)")

# 提前提取戰情室 CSS 樣式，在最頂層一次性注入，防止 Streamlit 刷新機制清空或重複載入 CSS 導致跑版與卡頓
import re
temp_html = build_war_room_html(show_map=False)
style_match = re.search(r'<style>(.*?)</style>', temp_html, re.DOTALL)
if style_match:
    st.html(f"<style>{style_match.group(1)}</style>")

st.sidebar.markdown("""
這是一套多智能體 (Multi-Agent) 數位孿生AI土壤污染應變系統。<br>
當您啟動後，**總指揮官**負責統管全局，**情報官**從文章中提取地緣情報從氣象署、農業部與環保署三大部門尋找相關資訊，**行動官**規劃除污工程與農作封存，**法務稽查官**進行違規裁處，最後由**公關通報官**以 AI 生圖技術輸出即時新聞稿。
""", unsafe_allow_html=True)

with st.sidebar.expander("🧠 大腦模型設定", expanded=False):
    st.info("若未填寫，則使用系統 .env 預設配置")
    custom_model = st.text_input("Model Name", placeholder="例如: openai/gpt-4o")
    custom_key = st.text_input("API Key", type="password", placeholder="填入 API Key")
    custom_url = st.text_input("Base URL", placeholder="例如: https://api.deepseek.com")
    st.caption("支援: Gemini, OpenAI, DeepSeek (需輸入 Base URL), Groq 等 OpenAI 相容接口。")

# ─── Session State 初始化 ───
for key, default in {
    "report_text_input": "",
    "active_agent": "SYSTEM",
    "bubble_content": "",
    "location": "",
    "lat": None,
    "lon": None,
    "agent_statuses": {k: "" for k in AGENT_META},
    "all_done": False,
    "war_room_key": 0,
    "sidebar_logs": [], # 持久化日誌
    "operations_plan": "",
    "legal_report": "",
    "final_pr": "",
    "map_key_idx": 0,
    "map_localized": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─── 側邊欄日誌顯示區 ───
if st.session_state.sidebar_logs:
    with st.sidebar:
        st.markdown("---")
        with st.status("🏗️ AI 團隊執行狀態", expanded=True):
            for log in st.session_state.sidebar_logs:
                st.write(log)

def set_text(text):
    st.session_state.report_text_input = text

# ─── 範例按鈕 ───
cols = st.columns(2)
with cols[0]:
    st.button("📝 帶入範例：口語化民眾通報", on_click=set_text,
              args=("剛才在桃園市觀音區看到一輛化學槽車翻覆，有很多綠色液體流出來，味道很酸很臭，天空下著超級大雨，旁邊還圍了一堆人。",),
              use_container_width=True)
with cols[1]:
    st.button("📰 帶入範例：新聞快訊稿", on_click=set_text,
              args=("【快訊】彰化縣和美鎮發生不明重金屬廢液傾倒事件，疑似附近電鍍工廠非法排放，已造成附近農地嚴重污染...",),
              use_container_width=True)

report_text = st.text_area(
    "請貼上災情新聞報導、民眾通報訊息或稽查報告...",
    key="report_text_input",
    height=180
)

st.markdown("---")

tab1, tab2 = st.tabs(["🚨 即時應變中心", "📂 歷史公關資料庫"])

# ════════════════════════════════════════════════
# TAB 1：即時應變中心
# ════════════════════════════════════════════════
with tab1:
    # 左欄：folium 地圖
    # 右欄：AI 會議室 HTML
    map_col, agents_col = st.columns([1, 2])

    map_placeholder = map_col.empty()

    def render_map():
        st.session_state.map_key_idx = st.session_state.get("map_key_idx", 0) + 1
        with map_placeholder.container():
            _lat = st.session_state.lat
            _lon = st.session_state.lon
            _loc = st.session_state.location or "台灣"
            if _FOLIUM_OK:
                if _lat and _lon:
                    _center, _zoom = (_lat, _lon), 13
                else:
                    _center, _zoom = _TAIWAN_CENTER, 7
                # 換成更清晰且耐看的 CartoDB Positron (淺色版) 或 voyager (彩色版)
                _m = folium.Map(location=_center, zoom_start=_zoom, tiles="CartoDB voyager")
                if _lat and _lon:
                    folium.CircleMarker(
                        location=(_lat, _lon), radius=12, color="#e74c3c",
                        fill=True, fill_color="#e74c3c", fill_opacity=0.85,
                        popup=folium.Popup(_loc, max_width=200), tooltip=_loc,
                    ).add_to(_m)
                    folium.Marker(
                        location=(_lat, _lon),
                        icon=folium.Icon(color="red", icon="exclamation-sign"),
                        popup=_loc,
                    ).add_to(_m)
                st_folium(_m, height=560, use_container_width=True, key=f"incident_map_{st.session_state.map_key_idx}", returned_objects=[])
            else:
                st.info("⚠️ 請安裝 folium 以啟用地圖。")

    render_map()

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
            # 移除 style 標籤與 iframe 標籤，只保留乾淨的 HTML Body（CSS 已在頂層全域注入，防止跑版與卡頓）
            import re
            clean_html = re.sub(r'<style>.*?</style>', '', raw_html, flags=re.DOTALL)
            clean_html = clean_html.replace("<!DOCTYPE html>", "").replace("<html>", "").replace("</html>", "").replace("<head>", "").replace("</head>", "").replace("<body>", "").replace("</body>", "")
            st.html(clean_html)

    render_agents()

    st.markdown("---")

    if st.button("🚀 啟動應變團隊", type="primary", use_container_width=True):
        if not report_text.strip():
            st.warning("請填寫事件內容！")
        else:
            # 重設與初始化
            st.session_state.active_agent    = "SYSTEM"
            st.session_state.bubble_content  = "⚡ 正在啟動 AI 應變團隊..."
            st.session_state.location        = ""
            st.session_state.lat             = None
            st.session_state.lon             = None
            st.session_state.all_done        = False
            st.session_state.agent_statuses  = {k: "" for k in AGENT_META}
            st.session_state.operations_plan = ""
            st.session_state.legal_report    = ""
            st.session_state.final_pr        = ""
            st.session_state.map_key_idx     = 0
            st.session_state.map_localized   = False
            st.session_state.sidebar_logs    = []  # 清除舊日誌
            
            render_agents()
            result_container = st.empty()
            
            # 在側邊欄顯示執行日誌狀態
            with st.sidebar:
                st.markdown("---")
                status_area = st.status("🏗️ AI 團隊執行中...", expanded=True)

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

                            # 解析標準 SSE (data: )
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

                                # 1. 如果該 Agent 的狀態已是 "done"，代表該階段已完工，忽略延遲的舊日誌，防止狀態被回滾復活
                                if st.session_state.agent_statuses.get(agent) == "done" and st.session_state.active_agent != agent:
                                    continue

                                # 2. 將狀態流轉與完成的控制權「完全交由 task_complete 事件」，在此不主動將 prev_agent 標記為 done。
                                #    這能徹底杜絕因異步 stdout 快取與 API 解析延遲導致的 Race Condition。
                                st.session_state.active_agent   = agent
                                st.session_state.bubble_content = content
                                
                                status_label = _make_status_msg(agent, content, log_type)
                                st.session_state.agent_statuses[agent] = status_label
                                
                                # 在側邊欄持久化日誌儲存
                                full_log_msg = f"**{AGENT_META.get(agent, {}).get('name', agent)}**: {status_label}"
                                if not st.session_state.sidebar_logs or st.session_state.sidebar_logs[-1] != full_log_msg:
                                    st.session_state.sidebar_logs.append(full_log_msg)
                                    status_area.write(full_log_msg)

                                # 地理偵測（僅在地圖尚未被定位時進行備援偵測）
                                if not st.session_state.map_localized:
                                    found_county = None
                                    for county in ["臺北市","台北市","新北市","基隆市","桃園市","新竹市","新竹縣",
                                                   "苗栗縣","臺中市","台中市","彰化縣","南投縣","雲林縣",
                                                   "嘉義市","嘉義縣","臺南市","台南市","高雄市","屏東縣",
                                                   "宜蘭縣","花蓮縣","臺東縣","台東縣","澎湖縣","金門縣","連江縣"]:
                                        if county in content or county in report_text:
                                            found_county = county
                                            break
                                    if found_county:
                                        # 嘗試匹配具體的鄉鎮市區，例如「桃園市觀音區」
                                        full_loc = found_county
                                        for text_source in [content, report_text]:
                                            match = re.search(rf"({found_county}[一-龥]{{1,4}}?(?:區|鎮|鄉|市))", text_source)
                                            if match:
                                                full_loc = match.group(1)
                                                break
                                        
                                        st.session_state.location = full_loc
                                        la, lo = geocode_location(full_loc)
                                        st.session_state.lat, st.session_state.lon = la, lo
                                        st.session_state.map_key_idx = st.session_state.get("map_key_idx", 0) + 1
                                        st.session_state.map_localized = True
                                        render_map() # 立即定位地圖，且只觸發一次！

                                render_agents()
                                import time
                                time.sleep(0.01) # 強制讓出 thread 給 Streamlit UI 更新

                            elif msg_type == "location_determined":
                                loc = data.get("location", "")
                                if loc and not st.session_state.map_localized:
                                    st.session_state.location = loc
                                    la, lo = geocode_location(loc)
                                    st.session_state.lat, st.session_state.lon = la, lo
                                    st.session_state.map_key_idx = st.session_state.get("map_key_idx", 0) + 1
                                    st.session_state.map_localized = True
                                    render_map() # 立即定位地圖，且只觸發一次！

                            elif msg_type == "task_complete":
                                task_index = data.get("task_index", -1)
                                if task_index == 0:
                                    st.session_state.agent_statuses["COMMANDER"] = "done"
                                    st.session_state.active_agent = "INTELLIGENCE"
                                    st.session_state.agent_statuses["INTELLIGENCE"] = "🔄 執行中"
                                    st.session_state.bubble_content = "🔍 情報分析官開始收集跨部會情報（氣象署 / 農業部 / 環境部）..."
                                    status_area.write("🎖️ **總指揮官**: ✅ 資訊擷取完成！")
                                elif task_index == 1:
                                    st.session_state.agent_statuses["INTELLIGENCE"] = "done"
                                    st.session_state.active_agent = "OPERATIONS"
                                    st.session_state.agent_statuses["OPERATIONS"] = "🔄 執行中"
                                    st.session_state.bubble_content = "⚙️ 行動策劃官正在規劃現場應變與工程防堵方案..."
                                    status_area.write("🔍 **情報分析官**: ✅ 跨部會情報收集完成！")
                                elif task_index == 2:
                                    st.session_state.agent_statuses["OPERATIONS"] = "done"
                                    st.session_state.active_agent = "LEGAL"
                                    st.session_state.agent_statuses["LEGAL"] = "🔄 執行中"
                                    st.session_state.bubble_content = "⚖️ 法務稽查官正在語意搜尋法律知識庫（RAG）..."
                                    status_area.write("⚙️ **行動策劃官**: ✅ 現場行動方案規劃完成！")
                                elif task_index == 3:
                                    st.session_state.agent_statuses["LEGAL"] = "done"
                                    st.session_state.active_agent = "COMMANDER"
                                    st.session_state.agent_statuses["COMMANDER"] = "🔄 執行中"
                                    st.session_state.bubble_content = "🎖️ 總指揮官正在彙整最終官方應變計畫報告..."
                                    status_area.write("⚖️ **法務稽查官**: ✅ 現場蒐證 SOP 與裁處書草案擬定完成！")
                                elif task_index == 4:
                                    st.session_state.agent_statuses["COMMANDER"] = "done"
                                    st.session_state.active_agent = "PR"
                                    st.session_state.agent_statuses["PR"] = "🔄 執行中"
                                    st.session_state.bubble_content = "📢 公關通報官準備撰寫對外新聞稿並生成 AI 配圖..."
                                    status_area.write("🎖️ **總指揮官**: ✅ 官方最終應變報告彙整完成！")
                                elif task_index == 5:
                                    st.session_state.agent_statuses["PR"] = "done"
                                    status_area.write("📢 **公關通報官**: ✅ 新聞稿撰寫完成！")

                                render_agents()
                                import time
                                time.sleep(0.01)

                            elif msg_type == "done":
                                st.session_state.all_done        = True
                                st.session_state.bubble_content  = "✅ 所有任務完成！"
                                st.session_state.location        = data.get("location", st.session_state.location)
                                st.session_state.operations_plan = data.get("operations_plan", "")
                                st.session_state.legal_report    = data.get("legal_report", "")
                                st.session_state.final_pr        = data.get("response_plan", "")
                                is_fallback = (data.get("data_source") == "FALLBACK")
                                
                                status_area.update(label="✅ 應變計畫制定完成！", state="complete", expanded=False)
                                render_agents()
                                render_map() # <-- 完成後再次確保地圖最新！

                            elif msg_type == "error":
                                st.error(f"❌ 後端發生錯誤: {data.get('error', '')}")
                                status_area.update(label="❌ 執行發生錯誤", state="error")
                                break

                st.success("✅ 應變計畫制定完成！")
                if is_fallback:
                    st.warning("⚠️ 已套用備援設定。")

                # 顯示三個獨立結果區塊
                with result_container.container():
                    st.markdown("---")

                    with st.expander("🛠️ 行動策劃官：現場處置與工程防堵計畫", expanded=True):
                        ops = st.session_state.operations_plan
                        if ops:
                            st.markdown(ops)
                        else:
                            st.info("行動策劃官報告尚未取得。")

                    with st.expander("⚖️ 法務稽查官：違規裁處書草案與蒐證 SOP", expanded=True):
                        legal = st.session_state.legal_report
                        if legal:
                            st.markdown("""
<style>
.legal-doc {
    background: linear-gradient(135deg, #081a10, #0a1520);
    border: 1px solid #1abc9c44;
    border-left: 4px solid #1abc9c;
    border-radius: 8px;
    padding: 20px 24px;
    font-family: '標楷體', Georgia, serif;
    color: #c8e6d4;
    line-height: 1.9;
    white-space: pre-wrap;
}
.legal-doc h3 { color: #1abc9c; letter-spacing: 2px; margin-top: 16px; }
.legal-doc strong { color: #76e4b0; }
</style>
""", unsafe_allow_html=True)
                            st.markdown(f'<div class="legal-doc">{legal}</div>', unsafe_allow_html=True)
                        else:
                            st.info("法務稽查官報告尚未取得。")

                    with st.expander("📢 公關通報官：對外新聞稿與圖文", expanded=True):
                        pr = st.session_state.final_pr
                        if pr:
                            render_markdown_with_images(pr)
                        else:
                            st.info("公關新聞稿尚未取得。")

            except httpx.ConnectError:
                st.error("❌ 無法連線到 FastAPI 後端，請確認已啟動後端伺服器 (start.bat)！")
            except httpx.TimeoutException:
                st.error("⏳ 呼叫逾時：AI 團隊思考時間過長，請再試一次。")
            except Exception as e:
                st.error(f"❌ 發生未預期錯誤：{e}")


# ════════════════════════════════════════════════
# TAB 2：歷史公關資料庫
# ════════════════════════════════════════════════
with tab2:
    st.subheader("📂 歷史公關資料庫")
    st.markdown("這裡紀錄了系統過去處理過的所有即時應變指令與新聞通報稿。")

    if st.button("🔄 重新載入資料庫", key="btn_reload_db"):
        st.rerun()

    try:
        incidents = get_all_incidents()
        if not incidents:
            st.info("目前資料庫中尚無紀錄。")
        else:
            # ── 分頁機制：每頁顯示 10 筆，大幅減少 Streamlit widget tree 元素數量 ──
            PAGE_SIZE = 10
            total_pages = max(1, (len(incidents) + PAGE_SIZE - 1) // PAGE_SIZE)

            if "hist_page" not in st.session_state:
                st.session_state.hist_page = 0

            # 分頁導航列
            nav_cols = st.columns([1, 2, 1])
            with nav_cols[0]:
                if st.button("⬅️ 上一頁", key="hist_prev",
                             disabled=(st.session_state.hist_page <= 0)):
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
                if st.button("下一頁 ➡️", key="hist_next",
                             disabled=(st.session_state.hist_page >= total_pages - 1)):
                    st.session_state.hist_page += 1
                    st.rerun()

            # 取出當前頁的資料
            start = st.session_state.hist_page * PAGE_SIZE
            page_incidents = incidents[start : start + PAGE_SIZE]

            # 注入法務報告 CSS（只注入一次，不在迴圈中重複）
            st.markdown("""
<style>
.legal-doc-hist {
    background:linear-gradient(135deg,#081a10,#0a1520);
    border:1px solid #1abc9c44; border-left:4px solid #1abc9c;
    border-radius:8px; padding:20px 24px;
    font-family:'標楷體',Georgia,serif;
    color:#c8e6d4; line-height:1.9; white-space:pre-wrap;
}
</style>
""", unsafe_allow_html=True)

            for idx, inc in enumerate(page_incidents):
                inc_id = inc.get('id', start + idx)
                label = f"[{inc['timestamp'][:19]}] {inc['location']} — {inc['pollutant']}"
                with st.expander(label, expanded=False, key=f"hist_exp_{inc_id}"):
                    t1, t2, t3, t4 = st.tabs(
                        ["📰 公關新聞稿", "🎖️ 指揮官應變計畫", "⚖️ 法務裁處書", "🎬 重播會議過程"],
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
                            st.info("此事件尚無指揮官報告。")

                    with t3:
                        legal_text = inc.get('legal_report', '')
                        if legal_text:
                            st.html(f'<div class="legal-doc-hist">{legal_text}</div>')
                        else:
                            st.info("此事件尚無法務稽查報告（可能為升級前的舊紀錄）。")

                    with t4:
                        st.markdown("按下 **▶ 開始重播** 即可以 0.5 秒/條的速度重播本次會議過程：")
                        try:
                            meeting_logs = get_meeting_logs_by_incident(inc['id'])
                            if not meeting_logs:
                                st.info("此事件尚無會議日誌（可能為升級前的舊紀錄）。")
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

