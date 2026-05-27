import sqlite3
import os
from datetime import datetime

# Bug #5 修復：使用絕對路徑，確保 FastAPI 和 Streamlit 讀寫同一個 .db 檔
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incident_logs.db")

def _get_conn():
    """Bug #1 修復：統一建立安全的 SQLite 連線，支援跨執行緒且加上鎖等待"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # WAL 模式提升多執行緒併發寫入穩定度
    return conn

def init_db():
    """初始化資料庫與資料表"""
    conn = _get_conn()
    cursor = conn.cursor()

    # 主表：事件應變紀錄
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            location TEXT,
            pollutant TEXT,
            commander_report TEXT,
            pr_press_release TEXT,
            legal_report TEXT
        )
    ''')

    # 若是舊版資料庫（缺 legal_report 欄位），自動補欄位（idempotent）
    try:
        cursor.execute('ALTER TABLE Incidents ADD COLUMN legal_report TEXT')
    except Exception:
        pass  # 欄位已存在，忽略

    # 新增：會議日誌逐條紀錄，關聯 Incidents.id
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS MeetingLogs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER,
            timestamp   TEXT,
            agent_name  TEXT,
            log_type    TEXT,
            log_content TEXT
        )
    ''')

    conn.commit()
    conn.close()

def save_incident(
    timestamp: str, location: str, pollutant: str,
    commander_report: str, pr_press_release: str,
    legal_report: str = ""
) -> int:
    """儲存單筆應變與公關稿紀錄，並回傳新建的 incident_id"""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Incidents (timestamp, location, pollutant, commander_report, pr_press_release, legal_report)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (timestamp, location, pollutant, commander_report, pr_press_release, legal_report))
    incident_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return incident_id

def save_meeting_logs(incident_id: int, logs: list):
    """批次儲存一次事件所有的會議日誌到 MeetingLogs"""
    if not logs:
        return
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.executemany('''
        INSERT INTO MeetingLogs (incident_id, timestamp, agent_name, log_type, log_content)
        VALUES (?, ?, ?, ?, ?)
    ''', [
        (incident_id, log["timestamp"], log["agent_name"], log["log_type"], log["log_content"])
        for log in logs
    ])
    conn.commit()
    conn.close()

def get_all_incidents():
    """取得所有歷史紀錄"""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Incidents ORDER BY id DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_meeting_logs_by_incident(incident_id: int):
    """取得指定事件的所有會議日誌，按 id 升冪排序（時序）"""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM MeetingLogs WHERE incident_id = ? ORDER BY id ASC',
        (incident_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
