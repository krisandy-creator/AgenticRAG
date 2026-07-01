import os
import sys
from pathlib import Path

import uvicorn


def main():
    repo_root = Path(__file__).resolve().parents[1]
    backend_root = repo_root / "src" / "backend"
    sys.path.insert(0, str(backend_root))

    host = os.getenv("PADDLEOCR_HOST", "127.0.0.1")
    port = int(os.getenv("PADDLEOCR_PORT", "8791"))
    uvicorn.run(
        "agentchat.infrastructure.ocr.paddleocr_service:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
