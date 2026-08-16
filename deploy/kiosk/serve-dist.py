"""一体机前端静态伺服（web-027）：免装 nginx 的极简静态服务器。

用法：python serve-dist.py [--dir frontend/dist] [--port 8080]
特性：SPA 回退 index.html、index.html no-cache、正确 MIME。
"""
from __future__ import annotations

import argparse
import http.server
import os
from pathlib import Path


class SpaHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):  # noqa: N802（父类命名）
        path = self.translate_path(self.path)
        if not os.path.exists(path) and "." not in os.path.basename(self.path):
            self.path = "/index.html"          # SPA 路由回退
        return super().send_head()

    def end_headers(self):
        if self.path.endswith("index.html") or self.path == "/":
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):        # 静音常规访问日志
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="frontend/dist")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    root = Path(args.dir).resolve()
    if not (root / "index.html").exists():
        raise SystemExit(f"未找到构建产物 {root}/index.html——先运行 npm run build")
    os.chdir(root)
    with http.server.ThreadingHTTPServer(("0.0.0.0", args.port), SpaHandler) as srv:
        print(f"数字人前端已伺服: http://127.0.0.1:{args.port} （目录 {root}）")
        srv.serve_forever()


if __name__ == "__main__":
    main()
