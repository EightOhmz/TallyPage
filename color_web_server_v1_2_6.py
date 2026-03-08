import json
import socket
import threading
import os
import time
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote
from typing import Optional

import qrcode

VERSION = "1.2.6"

HOST = "0.0.0.0"
PORT = 9876
VIEWER_TIMEOUT_SECONDS = 10

# ============================================================
# LAN IP detection
# ============================================================

def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ============================================================
# Named colors
# ============================================================

NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#00ff00",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ff69b4",
    "hotpink": "#ff69b4",
    "gray": "#808080",
    "grey": "#808080",
    "amber": "#ffbf00",
    "uv": "#7f00ff",
}

# ============================================================
# Server state
# ============================================================

state = {
    "color": "#000000",
    "command_history": [],
    "command_colors": [],  # newest appended at end
    "seq": 0,
    "started_at": time.time(),
    "last_command_at": None,
}

viewers = {}
viewer_lock = threading.Lock()

# ============================================================
# Helpers
# ============================================================

def get_viewer_url():
    return f"http://{get_lan_ip()}:{PORT}/view"


def parse_color(token: str) -> Optional[str]:
    if not token:
        return None

    t = token.strip().lower()
    t = t.replace("%23", "#")

    if t in NAMED_COLORS:
        return NAMED_COLORS[t]

    if t.startswith("#"):
        t = t[1:]

    if len(t) == 3 and all(c in "0123456789abcdef" for c in t):
        t = "".join(c * 2 for c in t)

    if len(t) == 6 and all(c in "0123456789abcdef" for c in t):
        return f"#{t}"

    return None


def add_command_history(entry: str, color: Optional[str] = None):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {entry}"
    print(line, flush=True)

    state["command_history"].append(line)
    if len(state["command_history"]) > 300:
        state["command_history"] = state["command_history"][-300:]

    if color is not None:
        state["command_colors"].append({
            "time": ts,
            "color": color,
            "label": entry,
        })
        if len(state["command_colors"]) > 50:
            state["command_colors"] = state["command_colors"][-50:]


def format_age(seconds: float) -> str:
    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds}s"

    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {sec}s"


def register_viewer(ip: str, ua: str, path: str):
    now = time.time()
    key = f"{ip}|{ua}"

    with viewer_lock:
        existing = viewers.get(key, {})
        viewers[key] = {
            "ip": ip,
            "ua": ua,
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
            "poll_count": existing.get("poll_count", 0) + 1,
            "last_path": path,
        }


def get_viewer_snapshot():
    now = time.time()
    rows = []

    with viewer_lock:
        for v in viewers.values():
            age = now - v["last_seen"]
            rows.append({
                "ip": v["ip"],
                "age": age,
                "online": age <= VIEWER_TIMEOUT_SECONDS,
                "poll_count": v["poll_count"],
                "last_path": v["last_path"],
            })

    rows.sort(key=lambda x: x["age"])
    return rows


def make_qr(data: str) -> bytes:
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def set_color_and_log(color: str):
    state["color"] = color
    state["seq"] += 1
    state["last_command_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    add_command_history(f"APPLY_COLOR {color}", color=color)


# ============================================================
# Viewer page
# ============================================================

HTML_VIEW_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Color Viewer</title>
<style>
html,body{
margin:0;
width:100%;
height:100%;
background:#000;
}
body{
transition:background-color 0.05s linear;
}
</style>
</head>
<body>

<script>
let lastSeq = -1;

async function poll(){
    try{
        const r = await fetch("/state", {cache:"no-store"});
        const data = await r.json();

        if(data.seq !== lastSeq){
            document.body.style.backgroundColor = data.color;
            lastSeq = data.seq;
        }
    }catch(e){}
}

setInterval(poll, 250);
poll();
</script>

</body>
</html>
"""

# ============================================================
# Status page
# ============================================================

HTML_STATUS_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Color Server Status</title>
<style>
body{
font-family:Arial, sans-serif;
background:#111;
color:#eee;
margin:30px;
}
.card{
background:#1a1a1a;
padding:20px;
border-radius:10px;
margin-bottom:20px;
}
.swatch{
display:inline-block;
width:20px;
height:20px;
border:1px solid #555;
margin-left:10px;
vertical-align:middle;
}
.qr img{
width:220px;
height:220px;
}
table{
border-collapse:collapse;
width:100%;
}
td,th{
border:1px solid #333;
padding:6px;
text-align:left;
vertical-align:top;
}
.online{
color:#7CFC98;
}
.offline{
color:#ff9b9b;
}
pre{
white-space:pre-wrap;
word-break:break-word;
margin:0;
}
.swatch-strip{
display:flex;
flex-wrap:wrap;
gap:8px;
margin-top:10px;
}
.swatch-item{
width:30px;
height:30px;
border:1px solid #555;
border-radius:6px;
box-sizing:border-box;
}
.muted{
color:#aaa;
}
</style>
</head>
<body>

<h1>Color Web Server v1.2.6</h1>

<div class="card">
<p><b>Current color:</b>
<span id="colorText"></span>
<span id="swatch" class="swatch"></span>
</p>

<p><b>Uptime:</b> <span id="uptime"></span></p>

<p><b>Viewer URL:</b><br>
<a id="viewerUrl" target="_blank"></a></p>

<p><b>Last command:</b> <span id="lastCommand"></span></p>

<div class="qr">
<p><b>Scan to open viewer:</b></p>
<img src="/qr.png" alt="Viewer QR code">
</div>
</div>

<div class="card">
<h3>Last 10 Color Commands</h3>
<div id="swatchStrip" class="swatch-strip"></div>
<p class="muted">Hover over a swatch to see the exact command.</p>
</div>

<div class="card">
<h3>Viewer Connections</h3>
<table>
<tr>
<th>Status</th>
<th>IP</th>
<th>Seen</th>
<th>Polls</th>
<th>Last Path</th>
</tr>
<tbody id="viewerRows"></tbody>
</table>
</div>

<div class="card">
<h3>Command History</h3>
<pre id="history"></pre>
</div>

<script>
function escapeHtml(value){
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

async function refreshStatus(){
    try{
        const r = await fetch("/status.json", {cache:"no-store"});
        const data = await r.json();

        document.getElementById("colorText").textContent = data.color;
        document.getElementById("swatch").style.background = data.color;
        document.getElementById("uptime").textContent = data.uptime;
        document.getElementById("lastCommand").textContent = data.last_command || "never";

        const link = document.getElementById("viewerUrl");
        link.href = data.viewer_url;
        link.textContent = data.viewer_url;

        let rows = "";
        for (const v of data.viewers) {
            rows += `
<tr>
<td class="${v.online ? 'online' : 'offline'}">${v.online ? 'online' : 'offline'}</td>
<td>${escapeHtml(v.ip)}</td>
<td>${escapeHtml(v.age)}</td>
<td>${v.poll_count}</td>
<td>${escapeHtml(v.last_path)}</td>
</tr>`;
        }

        if (!rows) {
            rows = '<tr><td colspan="5">No viewers seen yet.</td></tr>';
        }

        document.getElementById("viewerRows").innerHTML = rows;
        document.getElementById("history").textContent = data.command_history.join("\\n");

        const strip = document.getElementById("swatchStrip");
        strip.innerHTML = "";

        for (const item of data.recent_colors) {
            const div = document.createElement("div");
            div.className = "swatch-item";
            div.style.background = item.color;
            div.title = `${item.time} - ${item.label}`;
            strip.appendChild(div);
        }

        if (data.recent_colors.length === 0) {
            strip.innerHTML = '<span class="muted">No color commands yet.</span>';
        }

    }catch(e){}
}

setInterval(refreshStatus, 1000);
refreshStatus();
</script>

</body>
</html>
"""

# ============================================================
# HTTP server
# ============================================================

class RobustThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        import sys
        exc_type, exc, tb = sys.exc_info()
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def send_text(self, text: str, code: int = 200):
        body = text.encode("utf-8")

        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        self.wfile.write(body)

    def send_json(self, obj, code: int = 200):
        body = json.dumps(obj).encode("utf-8")

        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        self.wfile.write(body)

    def send_html(self, html: str, code: int = 200):
        body = html.encode("utf-8")

        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        self.wfile.write(body)

    def send_png(self, data: bytes, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        self.wfile.write(data)

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path).strip("/")

            ip = self.client_address[0]
            ua = self.headers.get("User-Agent", "")

            if path == "":
                return self.send_text("Color Server running")

            if path == "view":
                register_viewer(ip, ua, path)
                return self.send_html(HTML_VIEW_PAGE)

            if path == "state":
                register_viewer(ip, ua, path)
                return self.send_json({
                    "color": state["color"],
                    "seq": state["seq"],
                    "history": state["command_history"],
                    "uptime_seconds": time.time() - state["started_at"]
                })

            if path == "qr.png":
                return self.send_png(make_qr(get_viewer_url()))

            if path == "status":
                return self.send_html(HTML_STATUS_PAGE)

            if path == "status.json":
                snapshot = get_viewer_snapshot()

                viewers_out = []
                for v in snapshot:
                    viewers_out.append({
                        "ip": v["ip"],
                        "online": v["online"],
                        "age": format_age(v["age"]),
                        "poll_count": v["poll_count"],
                        "last_path": v["last_path"],
                    })

                return self.send_json({
                    "version": VERSION,
                    "color": state["color"],
                    "uptime": format_age(time.time() - state["started_at"]),
                    "viewer_url": get_viewer_url(),
                    "last_command": state["last_command_at"],
                    "viewers": viewers_out,
                    "command_history": state["command_history"][-50:],
                    "recent_colors": state["command_colors"][-10:]
                })

            if path == "shutdown":
                add_command_history("SERVER_SHUTDOWN")
                self.send_text("Server shutting down")
                threading.Thread(target=lambda: os._exit(0), daemon=True).start()
                return

            color = parse_color(path)
            if color:
                set_color_and_log(color)
                return self.send_text(f"Set color {color}")

            if path.startswith("color/"):
                token = path.split("/", 1)[1]
                color = parse_color(token)

                if color:
                    set_color_and_log(color)
                    return self.send_text(f"Set color {color}")

                return self.send_text("Invalid color", 400)

            return self.send_text("Not found", 404)

        except Exception as e:
            return self.send_text(f"Server error: {e}", 500)


# ============================================================
# Main
# ============================================================

def main():
    ip = get_lan_ip()

    print("")
    print("====================================")
    print(f" Color Web Server v{VERSION}")
    print("====================================")
    print("")
    print("STATUS PAGE:")
    print(f"  http://{ip}:{PORT}/status")
    print("")
    print("VIEWER PAGE:")
    print(f"  http://{ip}:{PORT}/view")
    print("")
    print("COMMAND EXAMPLES:")
    print(f"  http://{ip}:{PORT}/red")
    print(f"  http://{ip}:{PORT}/blue")
    print(f"  http://{ip}:{PORT}/green")
    print(f"  http://{ip}:{PORT}/color/ff00ff")
    print("")
    print("Press CTRL+C to stop the server")
    print("")

    server = RobustThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
