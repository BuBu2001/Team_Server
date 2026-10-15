"""Точка входа Team Server: python run_server.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # gitingest (src/)
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from app_platform.main import app

if __name__ == "__main__":
    print("Team Server запущен: http://localhost:8000  (доступ по IP в локальной сети)")
    uvicorn.run(app, host="0.0.0.0", port=8000)
