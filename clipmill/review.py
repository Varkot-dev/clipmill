"""A small review page: watch each clip on your phone, fix the title, approve or reject.

Run `clipmill review`, then open the printed address on any device on the same Wi-Fi.
"""

from __future__ import annotations

import html
import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import DB

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>clipmill review</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#111;color:#eee}}
header{{padding:14px 16px;position:sticky;top:0;background:#111;border-bottom:1px solid #333}}
.card{{max-width:420px;margin:16px auto;padding:12px;background:#1c1c1c;border-radius:12px}}
video{{width:100%;border-radius:8px;background:#000}}
input{{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border-radius:8px;border:1px solid #444;background:#000;color:#fff}}
.meta{{font-size:13px;color:#aaa;margin:8px 0}}
.row{{display:flex;gap:8px;margin-top:10px}}
button{{flex:1;padding:12px;font-size:16px;border:0;border-radius:8px;color:#fff}}
.ok{{background:#1f8f4e}}.no{{background:#8f1f1f}}
</style></head><body>
<header><b>{count} clips to review</b></header>
{cards}
<script>
async function decide(id, status) {{
  const title = document.getElementById('t' + id).value;
  await fetch('/decide', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{id, status, title}})}});
  document.getElementById('c' + id).remove();
}}
</script></body></html>"""

CARD = """<div class="card" id="c{id}">
<video src="/media/{id}" controls playsinline preload="metadata"></video>
<div class="meta">{streamer} . score {score} . {category}{focus}<br>{reason}</div>
<input id="t{id}" value="{title}">
<div class="row"><button class="no" onclick="decide({id},'rejected')">Reject</button>
<button class="ok" onclick="decide({id},'approved')">Approve</button></div></div>"""


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def make_handler(db: DB):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                rows = db.clips("pending")
                cards = []
                for c in rows:
                    focus = json.loads(c["focus_hits"] or "[]")
                    cards.append(CARD.format(
                        id=c["id"], streamer=html.escape(c["streamer"]), score=c["score"],
                        category=html.escape(c["category"] or ""),
                        focus=f" . focus: {html.escape(', '.join(focus))}" if focus else "",
                        reason=html.escape(c["reason"] or ""), title=html.escape(c["title"] or "", quote=True)))
                body = PAGE.format(count=len(rows), cards="\n".join(cards)).encode()
                self._send(200, body, "text/html; charset=utf-8")
            elif path.startswith("/media/"):
                clip = db.clip(int(path.rsplit("/", 1)[1]))
                if not clip or not Path(clip["path"]).exists():
                    return self._send(404, b"not found", "text/plain")
                self._send_file(Path(clip["path"]))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/decide":
                return self._send(404, b"not found", "text/plain")
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if data.get("status") not in ("approved", "rejected"):
                return self._send(400, b"bad status", "text/plain")
            db.update_clip(int(data["id"]), status=data["status"], title=str(data.get("title", ""))[:100])
            self._send(200, b"ok", "text/plain")

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path):
            # Phones (Safari especially) need byte-range support to play video.
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "video/mp4"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else max(0, size - int(b))
                end = int(b) if a and b else size - 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return Handler


def serve(db: DB, port: int = 8787):
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(db))
    print(f"Review page: http://localhost:{port}  (phone on same Wi-Fi: http://{_lan_ip()}:{port})")
    server.serve_forever()
