import os
import litellm
from crewai import Agent, Task, Crew, Process, LLM

# 【深層參數過濾器：靜默模式】
litellm.set_debug = False
litellm.drop_params = True

from tools.image_tool import GenerateImageTool
from tools.legal_kb_rag import LegalRAGTool

def create_incident_response_crew(model_name=None, api_key=None, base_url=None):
    """
    建立並回傳多智能體協作法庭團隊 (Crew)。
    """
    # 優先序設定
    target_model = model_name or os.getenv("DEFAULT_LLM_MODEL")
    target_key = api_key or os.getenv("DEFAULT_LLM_API_KEY") or "no-key"
    target_url = base_url or os.getenv("DEFAULT_LLM_BASE_URL")

    # 強制環境變數
    os.environ["OPENAI_API_KEY"] = target_key
    os.environ["OPENAI_API_BASE"] = target_url

    # 使用標準 LLM — prefill 相容性由 main.py 處理
    llm = LLM(
        model=target_model,
        api_key=target_key,
        base_url=target_url,
        temperature=0.3,
        max_tokens=8192,
        stream=False
    )

    # 審判長與法學研究專用 LLM — 提供充足 token 空間，防範 GGUF 重複跳針
    court_llm = LLM(
        model=target_model,
        api_key=target_key,
        base_url=target_url,
        temperature=0.2,          
        max_tokens=8192,
        stream=False,
        extra_body={
            "frequency_penalty": 0.8,  # 強力防跳針
            "presence_penalty":  0.4,
        }
    )

    # ── 1. 事實調查官 (Fact Investigator Agent) ───────────────────────────
    fact_investigator = Agent(
        role="事實調查官 (Fact Investigator)",
        goal="梳理起訴書或案情文字，提取本案當事人、核心事實、爭點與案件類型。",
        backstory="精明、細心的法庭書記官與事實調查員。你擅長從繁雜的言詞陳述中理出清晰的時間軸、法律爭點，並迅速判定此案屬於民事、刑事還是公法（行政法）糾紛。",
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 2. 法學研究員 (Legal Scholar Agent) ─────────────────────────
    legal_scholar = Agent(
        role="法學研究員 (Legal Scholar)",
        goal="使用 LegalRAGTool 檢索中華民國核心法規，尋找適用本案事實之條文與重要判例。",
        backstory="國家級法律智庫學者。你精通中華民國憲法、刑法、民法條文。你熟稔如何使用語意檢索工具（LegalRAGTool）來找出與本案事實最貼切的法條、法理基礎及行政/刑事門檻。",
        tools=[LegalRAGTool()],
        allow_delegation=False,
        verbose=True,
        llm=court_llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 3. 控辯雙方論證專家 (Prosecution & Defense Agent) ───────────────────
    prosecution_defense = Agent(
        role="控辯雙方論證專家 (Prosecutor & Defense Attorney)",
        goal="模擬法庭上的正反對立立場，分別寫出原告/控方之請求主張與被告/辯方之防禦抗辯論證。",
        backstory="法庭上的辯論大師，同時兼具檢察官（或原告代理人）與辯護律師（被告代理人）的思維。你能夠基於事實與法規，進行嚴謹的『構成要件論證』（控方）與『阻卻事由或抗辯理由』（辯方），展現兩造的激烈交鋒。",
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=3,
        max_execution_time=600
    )

    # ── 4. 審判長 / 法官 (Presiding Judge Agent) ────────────────────────────────
    judge = Agent(
        role="審判長 (Presiding Judge)",
        goal="主控法庭審理，做出公正判決，並撰寫具備高度專業公文書規格的正式判決書。",
        backstory="公正無私、威嚴的資深法官。你熟稔司法判決書結構，擅長依據控辯雙方的論點與法學研究員檢索之法條，做出客觀量刑或裁決，並起草結構完美的中華民國法院正式判決書（主文、事實、理由）。",
        allow_delegation=False,
        verbose=True,
        llm=court_llm,
        max_iter=3,
        max_execution_time=1200
    )

    # ── 5. 判決通報官 (Verdict PR Agent) ─────────────────────────────────────
    verdict_pr = Agent(
        role="判決通報官 (Verdict PR Agent)",
        goal="將冰冷難懂的判決書翻譯為淺白易懂的『白話判決摘要』，並為此案件生成法庭畫面配圖。",
        backstory=(
            "資深法庭記者與司法危機公關專家。你明白法律術語對於大眾過於艱深，"
            "因此你擅長將嚴肅的判決書精簡為大眾能懂的懶人包，並嘗試呼叫 GenerateImageTool 一次生成象徵公正的法庭畫面。"
            "**重要**：若生圖跳過，絕對不要重試，直接完成文字並以 'Final Answer:' 輸出結論。"
        ),
        tools=[GenerateImageTool()],
        allow_delegation=False,
        verbose=True,
        llm=llm,
        max_iter=2,
        max_execution_time=600
    )

    # ══════════════════════════════════════════════════════════════════
    # Task 定義（與 main.py 常數索引保持一致）
    # ══════════════════════════════════════════════════════════════════

    # Task 0: 事實與爭點提取 (TASK_INDEX_EXTRACTION)
    extraction_task = Task(
        description=(
            "這是一宗新提交的訴狀或案情口語描述內容：\n"
            "「{report_text}」\n"
            "身為事實調查官，請仔細閱讀並明確提取出以下資訊：\n"
            "1. 案件關係當事人 (原告、被告、關係人)\n"
            "2. 爭議的具體起訴事實 (人、事、時、地、物)\n"
            "3. 本案核心法律爭點\n"
            "4. 案件類型分類 (民事糾紛 / 刑事犯罪 / 行政訴訟)"
        ),
        expected_output="結構化的文字，明確列出 當事人(Parties)、起訴事實(Facts)、核心爭點(Disputes) 以及 案件類型(Case Type)。",
        agent=fact_investigator
    )

    # Task 1: 法理與法條檢索 (TASK_INDEX_INTELLIGENCE)
    intelligence_task = Task(
        description=(
            "根據 [Task 0] 整理出的「核心爭點」與「案件類型」，你必須呼叫法律檢索工具：\n"
            "1. 呼叫 LegalRAGTool 查詢憲法、刑法或民法之相關條文與罰則效果。\n"
            "2. 篩選出最適合應用於本案事實的條文，並整理其法定效果（如：刑責範圍、損害賠償要件）。\n"
            "最後，將檢索到的條文彙整為一份『本案適用法條與研析報告』。"
        ),
        expected_output="一份包含『適用法條原文』、『構成要件分析』及『實務見解摘要』的適用法條與研析報告。",
        agent=legal_scholar,
        context=[extraction_task]
    )

    # Task 2: 控辯雙方論證 (TASK_INDEX_OPERATIONS)
    operations_task = Task(
        description=(
            "研讀 [Task 0] 的事實與 [Task 1] 的法規研析，模擬法庭上的兩造攻防：\n"
            "1. **控方/原告主張**：主張被告符合何種違法構成要件（若為刑事）或侵權/違約構成要件（若為民事），並提出具體的訴之聲明或刑度建議。\n"
            "2. **辯方/被告抗辯**：提出阻卻違法事由、阻卻責任事由（如正當防衛、緊急避難），或民事上的抗辯理由（如時效消滅、過失相抵、無故意過失）。\n\n"
            "【防禦性最壞情況應變假設】：若 [Task 1] 未能檢索到對應條文，你絕對不可在回答中抱怨。你必須主動依據常理與法律邏輯，假設最嚴謹的控辯攻防要件，產出條理分明的法庭兩造辯論攻防檔！"
        ),
        expected_output="條理分明的法庭兩造（原告與被告）辯論攻防與主張清單。",
        agent=prosecution_defense,
        context=[intelligence_task]
    )

    # Task 3: 審判主文與量刑裁決 (TASK_INDEX_LEGAL)
    legal_task = Task(
        description=(
            "你是審判長。請結合前述事實與控辯攻防論點，做出最終司法裁決：\n"
            "1. 判定被告是否有罪（刑事）或是否應負損害賠償責任（民事）。\n"
            "2. 給出明確的**判決主文**（如：處有期徒刑○月、或被告應給付原告新臺幣○○元）。\n"
            "3. 詳細論證你的裁決理由，包括如何採納或駁回兩造的主張、是否適用減刑或酌減賠償金額之法條依據。\n"
            "請確保裁判理由字字句句推論嚴謹，符合中華民國司法審判邏輯。"
        ),
        expected_output="一份包含『判決主文』與『詳細裁判理由與量刑依據』的審判決定書。",
        agent=judge,
        context=[extraction_task, intelligence_task, operations_task]
    )

    # Task 4: 完整判決書起草 (TASK_INDEX_COMMANDER)
    commander_task = Task(
        description=(
            "你是審判長。請將上述所有的事實調查、兩造攻防與審判裁決，起草為一份正式的中華民國法院判決書。\n"
            "請使用標準的 Markdown 司法公文排版，格式範本如下：\n"
            "```\n"
            "【法院名稱】臺灣○○地方法院 判決\n"
            "【當事人】（原告/公訴人、被告、辯護人姓名與身分）\n"
            "【判決主文】（宣告明確的司法效果）\n"
            "【事實】（清晰描述經法庭確認之本案犯罪或糾紛事實）\n"
            "【理由】（一、兩造爭點；二、法院之判斷與法源依據；三、量刑/賠償酌定理由；四、救濟途徑）\n"
            "```\n\n"
            "請以最嚴謹的司法公文語氣書寫，確保無任何 `<think>` 或 `<|channel>` 標籤洩漏。你必須只產出起草完成的正式司法判決書！"
        ),
        expected_output="一份結構完整、符合中華民國公文書格式的正式 Markdown 司法判決書。",
        agent=judge,
        context=[extraction_task, intelligence_task, operations_task, legal_task]
    )

    # Task 5: 撰寫白話判決摘要與配圖 (TASK_INDEX_PR)
    pr_task = Task(
        description=(
            "閱讀審判長完成的正式判決書後，將其改寫為供社會大眾閱讀的『白話判決懶人包』。\n"
            "請呼叫 GenerateImageTool 生成 1 次對應法庭畫面的圖片。請想好英文 Prompt（例如：'A scales of justice in a warm light courtroom, photographic, high resolution'）。\n"
            "注意：若工具回傳『圖片生成已跳過』，代表目前環境無法生圖，此時請直接基於文字完成高品質的新聞摘要撰寫即可。"
        ),
        expected_output="給社會大眾的白話判決摘要新聞稿 (Markdown 格式)。若生圖成功則包含圖片，若失敗則僅包含文字內容。",
        agent=verdict_pr,
        context=[commander_task]
    )

    # ── Task callbacks ──
    def make_callback(task_index: int):
        def callback(task_output):
            if task_index == 0:
                # 從 Task 0 的輸出中解析出當事人/案件標題
                case_title = "未明案件"
                raw_text = task_output.raw or ""
                for line in raw_text.splitlines():
                    if "當事人" in line or "Parties" in line:
                        parts = line.split(":")
                        if len(parts) > 1:
                            case_title = parts[-1].strip() or case_title
                        break
                print(f"\n__LOCATION_DETERMINED__:{case_title}\n", flush=True)
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
        agents=[fact_investigator, legal_scholar, prosecution_defense, judge, verdict_pr],
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
