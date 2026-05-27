import os
import shutil
import pathlib

def pack():
    # 1. 定義來源與目標
    src_dir = pathlib.Path(".")
    dest_dir = src_dir / "容器化"
    
    # 如果目標資料夾已存在，先刪除以確保版本最新
    if dest_dir.exists():
        print(f"清理舊的 {dest_dir}...")
        shutil.rmtree(dest_dir)
    
    os.makedirs(dest_dir, exist_ok=True)
    print(f"正在打包最新版本至: {dest_dir}...")

    # 2. 定義排除名單
    exclude_patterns = [
        ".git", ".venv", "venv", "__pycache__", ".env", ".DS_Store",
        ".ipynb_checkpoints", "容器化", "*.db", "*.db-shm", "*.db-wal"
    ]

    def should_exclude(path):
        for pattern in exclude_patterns:
            if pattern.startswith("*."):
                if path.name.endswith(pattern[2:]): return True
            if path.name == pattern: return True
        return False

    # 3. 執行複製
    for item in src_dir.iterdir():
        if should_exclude(item):
            continue
        
        target_path = dest_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target_path, ignore=shutil.ignore_patterns(*exclude_patterns))
            print(f"  [DIR]  {item.name}")
        else:
            shutil.copy2(item, target_path)
            print(f"  [FILE] {item.name}")

    # 4. 生成專屬的 Deployment README
    deployment_readme = """# 土壤污染 AI 應急系統 — Docker 部署包

這是一個已打包完成的獨立部署版本。

## 快速啟動
1. 在本資料夾內建立一個 `.env` 檔案（內容請參考主專案的 README）。
2. Windows 使用者：雙擊 `start.bat`
3. Mac/Linux 使用者：執行 `bash start.sh`

## 預設 Port
- 前端 (Streamlit): http://localhost:8501
- 後端 (FastAPI): http://localhost:8000
"""
    with open(dest_dir / "README_DEPLOYMENT.md", "w", encoding="utf-8") as f:
        f.write(deployment_readme)

    print("\n✅ 打包完成！請至「容器化」資料夾查看。")

if __name__ == "__main__":
    pack()
