"""
Runner script for AI VMS Task Generator.
Automatically loads APP_HOST and APP_PORT (8086) from .env / app.config.

Usage:
    python run.py
"""

import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    host = settings.APP_HOST
    port = settings.APP_PORT
    print(f"\n=======================================================")
    print(f" 🚀 AI VMS Task Generator is running!")
    print(f" 👉 Web UI:     http://localhost:{port}/")
    print(f" 👉 Localhost:  http://127.0.0.1:{port}/")
    print(f" 👉 API Docs:   http://localhost:{port}/docs")
    print(f"=======================================================\n")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=True
    )
