# ⚖️ 中華民國 AI 模擬法庭與判決分析系統 v3.0.0
# ROC AI Mock Court & Judgment Analysis System v3.0.0

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![CrewAI](https://img.shields.io/badge/Framework-CrewAI-orange.svg)](https://www.crewai.com/)
[![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-red.svg)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-green.svg)](https://fastapi.tiangolo.com/)

---

## 📖 專案介紹 | Introduction

### 🇹🇼 中文 (Traditional Chinese)
本專案是一套基於 **Multi-Agent (多智能體)** 協作技術開發的「AI 模擬法庭與判決分析系統」。系統模擬了現代化的法庭審理流程，透過五位具備司法專業背景的 AI 代理人，將複雜或口語化的「糾紛與案情描述」自動轉化為包含：事實調查、法條檢索、控辯攻防、有罪/賠償判定、以及最終判決書與白話摘要產出的全方位司法分析報告。

### 🇺🇸 English
This project is an automated "ROC AI Mock Court & Judgment Analysis System" built on **Multi-Agent** collaborative technology. It simulates a modern judicial trial process. Through five specialized AI legal agents, it automatically transforms vague verbal case descriptions or indictments into comprehensive judicial analyses, including: factual investigation, legal statute retrieval, prosecution/defense argumentation, verdict determination, and the generation of a formal judgment document with a plain-language summary.

---

## 🏗️ 系統架構圖 | System Architecture

```mermaid
graph TD
    subgraph Frontend_Layer [Frontend: Streamlit]
        User[使用者 - Court Interface]
        WarRoom[法庭活動監控 - HTML/JS]
        History[歷史判決資料庫 - 分頁導航]
    end

    subgraph Backend_Gateway [Backend: FastAPI]
        SSE[SSE Streamer - 即時日誌與狀態流傳輸]
        Orchestrator[CrewAI 任務調控]
    end

    subgraph AI_Swarm [ROC Judicial Team]
        I[事實調查官 - Fact Investigator]
        L[法學研究員 - Legal Scholar]
        O[控辯論證專家 - Prosecutor & Defense]
        C[審判長 - Presiding Judge]
        P[判決通報官 - PR Agent]
    end

    subgraph Data_Tools [External Tools & APIs]
        RAG[法律知識庫 - ChromaDB]
        IMG[DALL-E 3 圖像生成]
    end

    User -->|POST Case| SSE
    SSE --> Orchestrator
    Orchestrator --> AI_Swarm
    L -->|Vector Search| RAG
    P -->|Generate| IMG
    AI_Swarm -->|Real-time Logs| SSE
    SSE -->|Update UI| User
```

---

## 🛰️ 邏輯時序圖 | Logic Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    participant U as 報案人/當事人 (User)
    participant B as 後端 (FastAPI/CrewAI)
    participant I as 事實官 (Investigator)
    participant L as 法學官 (Legal)
    participant O as 控辯雙方 (Prosecutor/Defense)
    participant C as 審判長 (Judge)
    participant P as 書記官/公關 (PR)

    U->>B: 輸入糾紛與案情描述
    B->>I: 任務啟動：萃取案件屬性與人事時地物
    I-->>B: 提供結構化案件事實
    B->>L: 任務啟動：跨庫檢索中華民國法律
    L->>O: 提供法規條文與適用解析
    B->>O: 任務啟動：擬定控方指控與辯方阻卻事由
    O->>C: 提供攻防論證報告
    B->>C: 任務啟動：作出司法裁決與量刑/賠償
    C->>P: 交付正式判決結果
    B->>P: 任務啟動：撰寫判決書與白話文懶人包
    P-->>U: 完成！顯示完整法庭審理紀錄
```

---

## 📊 資料流圖 (DFD) | Data Flow Diagram

```mermaid
graph LR
    User([當事人 User]) -->|口語化案情 Raw Text| P1[[案件事實提取 parsing]]
    P1 -->|人事時地物| P2[[法條檢索檢驗 RAG]]
    Chroma[(三大法典 ChromaDB)] <-->|語意搜索與比對| P2
    P2 -->|適用法條| P3[[控辯論證與裁定 Decision]]
    P3 -->|判決結果| P4[[卷宗歸檔 Persistence]]
    P4 --> SQLite[(法院判決資料庫 SQLite)]
```

---

## 🔄 業務流程圖 | Business Process Diagram

```mermaid
flowchart LR
    Start([案件受理]) --> Input[/口語案情輸入/]
    subgraph Diagnosis [偵查與檢索]
        Input --> Detect[人事時地物萃取]
        Detect --> Legal[跨庫法條 RAG 檢索]
    end
    subgraph Decision [審理與判決]
        Legal --> Debate[控辯雙方論證]
        Debate --> Judge[法官心證與裁量]
    end
    subgraph Dissemination [宣判與歸檔]
        Judge --> Summary[判決書草擬]
        Summary --> Media[白話文懶人包輸出]
    end
    Media --> End([案件定讞])
```

---

## ✨ 核心特色 | Key Features

*   **👨‍⚖️ 多智能體司法協作 (Multi-Agent Swarm)**：採用五位專屬司法 AI 角色（調查官、研究員、控辯專家、法官、書記官），模擬真實法庭的合議與攻防邏輯。
*   **📚 跨庫法律 RAG 引擎 (Multi-Collection RAG)**：內建《中華民國憲法》、《中華民國刑法》、《中華民國民法》，系統能依據案件動態切換或跨庫搜尋適用法條。
*   **⚖️ 深度控辯模擬 (Adversarial Reasoning)**：內建防禦性推理機制，自動為被告尋找「阻卻違法事由」或「減輕責任事由」，確保判決不偏頗。
*   **🕹️ 全即時審理面板 (Live Court Room)**：前端採用莊重的法院設計風格，透過 SSE 技術實現各 AI 法官、律師思考過程的毫秒級同步顯示。
*   **🎬 歷史卷宗重播 (Case Replay)**：新增歷史判決記錄還原功能！可選取歷史案件，以 0.5 秒/條的速度重播法庭攻防過程，提供 Log 時間軸以及各 Agent 氣泡思考過程。
*   **⚡ 高性能與防跑版機制**：
    *   **無縫 iframe 獨特鍵值定位**：重播組件使用動態 HTML 註解 Hash 機制，徹底避免 Streamlit widget ID 重複報錯。
    *   **高併發 SQLite WAL 模式**：讀寫分流與超時等待，避免 Web API 與前端元件多線程鎖死。
    *   **思考標籤攔截**：底層強制過濾開源模型的 `<think>` 或 `<|channel|>` 標籤，確保判決書格式純淨。

---

## 📁 目錄架構 | Directory Structure

```text
/
├── app.py                # Streamlit 前端法庭介面 (Port: 8501)
├── main.py               # FastAPI 後端路由與 CrewAI 調度器 (Port: 8000)
├── database.py           # SQLite 判決卷宗與會議日誌持久化 (WAL 併發模式)
├── start.sh              # 一鍵啟動指令碼 (自動管理背景 Port 占用)
├── stop.sh               # 一鍵停止指令碼 (優雅釋放 8000 與 8501 埠口)
├── agents/
│   └── response_crew.py  # 核心 Agent 司法行為與 Task 定義
├── tools/
│   ├── legal_kb_rag.py   # RAG 知識庫管理器 (支援多 Collection)
│   ├── legal_kb_tool.py  # AI Agent 調用之檢索工具
│   └── image_tool.py     # AI 圖片生成工具 (可選)
├── knowledge_base/       # 存放三大法典 Markdown 文本與 ChromaDB 向量索引
├── assets/               # 靜態視覺元件與生成圖片
└── requirements.txt      # Python 相依套件清單
```

---

## 🚀 快速開始 | Quick Start

### 1. 準備 `.env` 文件
在專案根目錄建立 `.env`，填入您的 LLM API 設定。
系統預設支援自定義的高端 LLM (如 vLLM/Ollama) 或一般雲端 API：
```env
# 核心大語言模型 API (必填)
GEMINI_API_KEY="your_api_key_here"

# (選項) 如果您使用自定義的開源 LLM，請填寫以下欄位：
DEFAULT_LLM_MODEL=""
DEFAULT_LLM_BASE_URL=""
```

### 2. 一鍵啟動與停止 (Local)
本專案已配置完整的管理腳本，無需手動管理多個終端機視窗：

*   **啟動系統**：
    ```bash
    bash start.sh
    ```
    啟動後會自動在背景啟動後端與前端，並開啟對應服務：
    *   **前端模擬法庭**: [http://localhost:8501](http://localhost:8501)
    *   **後端 API 文件**: [http://localhost:8000/docs](http://localhost:8000/docs)

*   **停止系統**：
    ```bash
    bash stop.sh
    ```
    此腳本將自動探測並強制終止佔用 Port `8000` 與 `8501` 的程序，確保系統乾淨關閉。

---

## 🏛️ 免責聲明 | Disclaimer
本系統生成的法律建議、判決書草案與攻防論點僅供**學術研究與 AI 邏輯推論技術展示參考**，**絕對不得直接作為正式法律諮詢或法院處分之依據**。如遇真實法律糾紛，請務必尋求合格執業律師之專業協助。

---
*© 2026 ROC AI Judicial Mock Court Team. Developed for Legal Tech Innovation.*
