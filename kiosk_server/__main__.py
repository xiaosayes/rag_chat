"""`python -m kiosk_server --host 0.0.0.0 --port 7861`（web-004）。"""
from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import KioskConfig
from .services import apply_kiosk_llm_caps


def main() -> None:
    parser = argparse.ArgumentParser(description="数字人一体机薄层 API")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    cfg = KioskConfig.from_env()
    apply_kiosk_llm_caps()   # web-041：回答限长 320 tokens（须在任何 pipeline 加载之前）
    uvicorn.run(
        create_app(cfg),
        host=args.host or cfg.host,
        port=args.port or cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
