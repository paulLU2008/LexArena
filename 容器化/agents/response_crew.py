import os
import litellm
from crewai import Agent, Task, Crew, Process, LLM
from langchain_openai import ChatOpenAI

# 【深層參數過濾器：靜默模式】
litellm.set_debug = False
litellm.drop_params = True

from tools.weather_api import WeatherAPI_Tool
from tools.image_tool import GenerateImageTool
from tools.agri_api import AgricultureAPI_Tool
from tools.econ_api import EconomicAPI_Tool
from tools.legal_kb_rag import LegalRAGTool

def create_incident_response_crew(model_name=None, api_key=None, base_url=None):
    """
    建立並回傳多智能體協作團隊 (Crew)。
    """
    # 優先序設定
    target_model = model_name or os.getenv("DEFAULT_LLM_MODEL")
    target_key = api_key or os.getenv("DEFAULT_LLM_API_KEY") or "no-key"
    target_url = base_url or os.getenv("DEFAULT_LLM_BASE_URL")

    # 強制環境變數
    os.environ["OPENAI_API_KEY"] = target_key
    os.environ["OPENAI_API_BASE"] = target_url

    # 使用標準 LLM — prefill 相容性由 main.py 的
    # _patch_for_prefill_compat monkey-patch 統一處理
    # stream=False 確保 main.py 的 _strip_think_from_response 能攔截完整回應
    llm = LLM(
        model=target_model,
        api_key=target_key,
        base_url=target_url,
        temperature=0.3,
        max_tokens=8192,
        stream=False
    )

    # 法務稽查官專用 LLM — 提供充足 token 空間，設置適度 temperature 與高 frequency_penalty 防禦 GGUF 重複跳針
    legal_llm = LLM(
        model=target_model,
        api_key=target_key,
        base_url=target_url,
        temperature=0.3,          # 設置於 0.3 兼顧格式與發散度，防止極低溫死鎖
        max_tokens=8192,
        stream=False,
        extra_body={
            "frequency_penalty": 0.8,  # 強力防跳針，預防 GGUF 的單字/法條重複循環
            "presence_penalty":  0.4,
        }
    )

    # ── 1. 總指揮官 (Commander Agent) ────────────────────────────────
    commander = Agent(
        role="總指揮官 (Commander)",
        goal="主控全局，提取關鍵資訊並統整最終應變計畫。請務必在回答最後使用 'Final Answer:' 標籤給出報告。",
        backstory="果斷、嚴謹的決策者。你深知格式的重要性，輸出最終結果時一定會使用 'Final Answer:' 前綴。",
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 2. 情報分析官 (Intelligence Agent) ───────────────────────────
    intelligence = Agent(
        role="跨部會情報分析官 (Intelligence)",
        goal="收集通報地點的氣象、工廠與農業分佈情報。",
        backstory="熟悉政府 API 接口，擅長快速獲取跨部會數據。",
        tools=[WeatherAPI_Tool(), AgricultureAPI_Tool(), EconomicAPI_Tool()],
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 3. 行動策劃官 (Operations Agent) ─────────────────────────────
    operations = Agent(
        role="行動策劃官 (Operations)",
        goal="給出實體防護方案、農作隔離與稽查追溯策略。",
        backstory="重機具調度與環保執法專家，重視現場應變速度。",
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 4. 法務稽查官 (Legal Agent) — 新成員 ─────────────────────────
    legal_agent = Agent(
        role="資深環境法務稽查官 (Legal)",
        goal="從法律知識庫檢索法條後，親自撰寫高品質、具備法律效力的現場蒐證 SOP 編號指引，以及正式公文書格式的違規裁處書草案，絕不直接吐出原始法條內容。",
        backstory="擁有 20 年豐富經驗的環保局法務主管。你熟稔法規，且非常注重公文書的格式規範。你的最終產出必須是嚴謹的公文本文，而非冷冰冰的原始法條清單。",
        tools=[LegalRAGTool()],
        allow_delegation=False,
        verbose=True,
        llm=legal_llm,
        max_iter=3,
        max_execution_time=1200
    )

    # ── 5. 公關通報官 (PR Agent) ─────────────────────────────────────
    pr_agent = Agent(
        role="公關通報官 (PR Agent)",
        goal="將應變報告轉譯為新聞通報。請務必在最後使用 'Final Answer:' 給出新聞稿內容。",
        backstory=(
            "資深危機溝通專家。你明白在緊急時刻，正確的『文字資訊』比圖片更重要。"
            "你可以嘗試呼叫 GenerateImageTool 一次（請使用英文 Prompt）。"
            "**重要**：若工具跳過，絕對不要重試，直接完成文字並以 'Final Answer:' 輸出結論。"
        ),
        tools=[GenerateImageTool()],
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=2,
        max_execution_time=600
    )

    # ══════════════════════════════════════════════════════════════════
    # Task 定義（Task Index 對應 main.py 常數，請同步更新）
    # ══════════════════════════════════════════════════════════════════

    # Task 0: 資訊擷取
    extraction_task = Task(
        description=(
            "這是一宗剛收到的原始通報/新聞內容：\n"
            "「{report_text}」\n"
            "身為總指揮官，請仔細閱讀並明確提取出以下資訊：\n"
            "1. 發生地點 (盡量精確)\n"
            "2. 疑似污染物類型\n"
            "3. 重要的環境背景 (天氣描述、民眾相關、發生原因等)"
        ),
        expected_output="結構化的文字，明確列出 地點(Location)、污染物(Pollutant) 以及 現場背景(Context)。",
        agent=commander
    )

    # Task 1: 情報分析
    intelligence_task = Task(
        description=(
            "根據 [Task 0] 抓取到的「地點」資訊，你必須依序呼叫三個工具：\n"
            "1. 氣象工具 (WeatherAPI_Tool)：獲取該地點的降雨量與天氣變化。\n"
            "2. 農業部工具 (AgricultureAPI_Tool)：確認周遭是否有敏感農作區及列管污染場址。\n"
            "3. 經濟部工具 (EconomicAPI_Tool)：調查該地點周圍的嫌疑工廠清單與環保列管狀態。\n"
            "最後，將三份報告總結成一份綜合環境情報檔。"
        ),
        expected_output="一份包含『即時天氣擴散條件』、『農業損耗風險評估』及『污染溯源清單』的綜合情報報告。",
        agent=intelligence,
        context=[extraction_task]
    )

    # Task 2: 擬定行動方案
    operations_task = Task(
        description=(
            "研讀 [Task 1] 的綜合情報，依據情報層級給出處置計畫：\n"
            "1. 若有大雨：提出防洪與污染物攔截策略。\n"
            "2. 若影響農地：強制要求並描述預防性農作封存與農業部調閱機制。\n"
            "3. 針對肇事溯源：若有高嫌疑工廠清單，指派稽查大隊前往查緝。\n"
            "結合上述建立物理處理手段(如：怪手開挖、化學洗消)。\n\n"
            "【防禦性最壞情況應變假設】：若 [Task 1] 未能提供完整的氣象、農作或嫌疑工廠數據，你絕對不可在最終回答中抱怨或提到「因無 Task 1 資料所以無法進行」。你必須主動啟動最嚴格的「預防性應變假設」：\n"
            "- 假設現場刻正下著超級大雨，污染隨雨水逕流極易擴散。\n"
            "- 假設事故點方圓 500 公尺內即為高敏感農作區與灌溉系統，必須實施最嚴格的全面隔離封鎖。\n"
            "- 鎖定周邊 5 家高嫌疑電鍍/化工列管工廠（台灣比特實業有限公司、長典生物科技、金亞典科技有限公司、緯紘環保有限公司、富暘重機械有限公司）進行突擊查緝與溯源。\n"
            "在此預防性假設下，依然產出高品質、專業的應變計畫！"
        ),
        expected_output="條理分明的現場應變與跨部會合作計畫。",
        agent=operations,
        context=[intelligence_task]
    )

    # Task 3: 法律合規分析與裁處擬定（新增）
    legal_task = Task(
        description=(
            "你是法務稽查官。請依照以下步驟完成法律分析，並**親自撰寫行政處分公文**，絕對不要只把 RAG 查出的法條清單當作結果交差：\n\n"
            "**步驟一：查詢法律知識庫**\n"
            "呼叫 LegalRAGTool 查詢法條。\n\n"
            "**步驟二：在 Final Answer 中產出正式公文（絕對嚴禁一字不漏地複寫、抄寫或列印原始條文）**\n"
            "你必須親自起草並生成以下兩部分內容，請使用標準 Markdown 公文排版：\n\n"
            "### 1. 【環境監測與現場蒐證 SOP】\n"
            "依據法規授權，為現場應變團隊規劃專業合規的蒐證指引，應包含：\n"
            "  - **一、採樣點位與深度**：寫出針對污染物（如重金屬等）的點位佈設、採樣深度及樣品包裝保存規範。\n"
            "  - **二、物證與影像保全**：現場拍照、錄影規格，及會同受處分關係人見證簽認之要件。\n"
            "  - **三、檢查授權法源**：明確引述土壤及地下水污染整治法或水污染防治法授權進入現場蒐證之條文（如土污法第 7 條）。\n\n"
            "### 2. 【行政處分裁處書（草案）】\n"
            "你必須套用下方的處分書範本，親自填寫違規內容（如：彰化縣和美鎮），**絕對不可以保留任何 "
            "『第XX條』的原始條文大篇幅貼上**。格式如下：\n"
            "```\n"
            "【機關名稱】○○市政府環境保護局 行政處分書\n"
            "【處分對象】（受處分人姓名/工廠名稱、地址）\n"
            "【處分主旨】因違反水污染防治法/土壤及地下水污染整治法，依法處以罰鍰新臺幣○○元整，並限期改善。\n"
            "【違規事實】（請依據 Task 0 與 1 的事實，寫出如某日於某區非法傾倒廢液之具體事實）\n"
            "【處分理由與法令依據】（請用你自己的話，總結寫出違反哪一條，禁止抄寫完整條文本身，只要寫出：『違反水污法第30條，依同法第52條處罰...』即可）\n"
            "【處分內容】處以新臺幣○○元罰鍰，並限期改善。\n"
            "【救濟途徑】如不服本處分，得於收到處分書之次日起 30 日內，向本局提起訴願。\n"
            "```\n\n"
            "【防禦性最壞情況應變假設】：若前置任務未提供具體位置與涉案工廠，你必須假設違規事實為：「於本市轄內特定區域發生非法化學液體傾倒事故，波及灌溉水渠，且肇事車輛或工廠涉嫌違反廢棄物清理與水污法，已由稽查小組立案查辦」。在此假設下，依然嚴謹完整起草上述兩份正式法律公文，嚴禁抱怨缺少前置任務資料，且嚴禁輸出任何無意義重複單字！\n\n"
            "事件時間：{event_time}\n"
            "請以嚴謹的公文書寫，確保無任何 `<think>` 或 `<|channel>` 標籤洩漏。你必須只產出起草完成的這兩份正式法律公文！"
        ),
        expected_output=(
            "包含兩個清晰區塊的法律文件：\n"
            "1.【現場蒐證 SOP】：編號清單，每條附法律依據\n"
            "2.【違規裁處書草案】：正式公文格式，含罰鍰金額建議"
        ),
        agent=legal_agent,
        context=[extraction_task, intelligence_task]
    )

    # Task 4: 匯總最終報告
    commander_task = Task(
        description=(
            "根據上述所有任務的結果（資訊擷取、環境情報、行動方案、法律裁處），"
            "為此次通報事件 (發生時間: {event_time}) 產出最終官方應變計畫報告。\n"
            "報告應具備專業感與指揮官語氣，並在最後摘要引述法務稽查官的裁處建議。\n\n"
            "【防禦性最壞情況應變假設】：即使前置情報或行動方案存在缺失，你也絕對不可輸出抱怨文字。你必須發揮指揮官擔當，整合本案的超級大雨防洪、500公尺農水路預防封鎖與稽查查緝部署，產出最全面的 Markdown 官方應變報告！"
        ),
        expected_output=(
            "一份包含『事件概要』、『環境情報』、『具體處置方案』"
            "與『法律裁處摘要』四個重點章節的最終 Markdown 格式報告。"
        ),
        agent=commander,
        context=[extraction_task, intelligence_task, operations_task, legal_task]
    )

    # Task 5: 撰寫公關新聞稿與配圖
    pr_task = Task(
        description=(
            "閱讀總指揮官完成的官方應變計畫報告後，將其改寫為給社會大眾看的新聞稿。\n"
            "請呼叫 GenerateImageTool 生成 1 次對應畫面的圖片。請想好對應畫面的英文 Prompt。\n"
            "注意：若工具回傳『圖片生成已跳過』，代表目前環境無法生圖，此時請**不要再次重試呼叫生圖工具**，"
            "直接基於現有的文字資訊完成高品質的新聞稿撰寫即可。"
        ),
        expected_output="公關新聞通稿 (Markdown 格式)，內容淺白。若生圖成功則包含圖片，若失敗則僅包含文字內容。",
        agent=pr_agent,
        context=[commander_task]
    )

    # ── Task callbacks ──
    def make_callback(task_index: int):
        def callback(task_output):
            if task_index == 0:
                # 從 Task 0 的結構化輸出中解析出發生地點
                loc_str = "未明"
                raw_text = task_output.raw or ""
                for line in raw_text.splitlines():
                    if "地點" in line or "Location" in line:
                        parts = line.split(":")
                        if len(parts) > 1:
                            loc_str = parts[-1].strip() or loc_str
                        break
                print(f"\n__LOCATION_DETERMINED__:{loc_str}\n", flush=True)
            print(f"\n__TASK_COMPLETE__:{task_index}\n", flush=True)
        return callback

    extraction_task.callback = make_callback(0)
    intelligence_task.callback = make_callback(1)
    operations_task.callback = make_callback(2)
    legal_task.callback = make_callback(3)
    commander_task.callback = make_callback(4)
    pr_task.callback = make_callback(5)

    # ── 封裝為 Crew，循序執行 ─────────────────────────────────────────
    crew = Crew(
        agents=[commander, intelligence, operations, legal_agent, pr_agent],
        tasks=[
            extraction_task,    # index 0
            intelligence_task,  # index 1
            operations_task,    # index 2
            legal_task,         # index 3
            commander_task,     # index 4
            pr_task,            # index 5
        ],
        process=Process.sequential,
        verbose=True,
        full_output=True,
        cache=False
    )

    return crew
