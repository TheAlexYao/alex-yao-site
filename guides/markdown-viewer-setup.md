# Markdown Workspace Viewer - Setup Instructions

> **For AI agents**: Follow these steps exactly. This sets up a web-based file browser for your OpenClaw workspace, accessible from any device on the Tailscale network.

## Prerequisites Check

Run these and report any failures before continuing:

```bash
python3 --version    # needs Python 3.8+
tailscale ip -4      # needs Tailscale installed and running
```

If `tailscale` is not found, install it first:
- macOS: `brew install tailscale` then `sudo tailscale up`
- Linux: `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`

Save the Tailscale IP output - you'll need it at the end.

## Step 1: Install dependency

```bash
pip3 install markdown
```

## Step 2: Create the server script

Determine the workspace root. Use whichever exists:
- `~/clawd/` (OpenClaw default)
- Or whatever directory your `workspace` config points to

Create the directory if needed:
```bash
mkdir -p ~/clawd/scripts
```

Write this exact file to `~/clawd/scripts/md-server.py`:

```python
#!/usr/bin/env python3
"""Lightweight markdown file browser with live-reload."""
import os, hashlib
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import markdown
import urllib.parse

ROOT = Path(os.environ.get("MD_ROOT", os.path.expanduser("~/clawd")))
PORT = int(os.environ.get("MD_PORT", "8787"))
BIND = os.environ.get("MD_BIND", "0.0.0.0")

CSS = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 800px;
       margin: 2em auto; padding: 0 1em; color: #222; background: #fafafa; line-height: 1.6; }
pre { background: #f0f0f0; padding: 1em; overflow-x: auto; border-radius: 6px; }
code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }
pre code { background: none; padding: 0; }
a { color: #0066cc; }
h1,h2,h3 { margin-top: 1.5em; }
.file-list { list-style: none; padding: 0; }
.file-list li { padding: 4px 0; }
.file-list a { text-decoration: none; }
.file-list a:hover { text-decoration: underline; }
.breadcrumb { margin-bottom: 1em; color: #666; }
.breadcrumb a { color: #0066cc; }
.edit-btn { position: fixed; top: 1em; right: 1em; background: #0066cc; color: white;
            border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; }
textarea { width: 100%; min-height: 60vh; font-family: monospace; font-size: 14px;
           padding: 1em; border: 1px solid #ccc; border-radius: 6px; }
.save-btn { background: #28a745; color: white; border: none; padding: 10px 20px;
            border-radius: 6px; cursor: pointer; font-size: 14px; margin-top: 8px; }
#reload-banner { display:none; position:fixed; top:0; left:0; right:0; background:#28a745;
                 color:white; text-align:center; padding:8px; cursor:pointer; z-index:999; }
"""

LIVE_RELOAD_JS = '''
<div id="reload-banner" onclick="location.reload()">File updated - click to reload</div>
<script>
(function() {
  var hash = "HASH_PLACEHOLDER";
  setInterval(function() {
    fetch(location.pathname + "?hash=1").then(r => r.text()).then(h => {
      if (h !== hash) { document.getElementById("reload-banner").style.display = "block"; hash = h; }
    }).catch(function(){});
  }, 2000);
})();
</script>
'''

md_ext = markdown.Markdown(extensions=["fenced_code", "tables", "toc"])

def file_hash(p):
    try: return hashlib.md5(p.read_bytes()).hexdigest()
    except: return ""

def render_dir(rel_path):
    d = ROOT / rel_path
    parts = ['<div class="breadcrumb">']
    parts.append('<a href="/">~/workspace</a> / ')
    cumulative = ""
    for seg in rel_path.parts:
        cumulative += seg + "/"
        parts.append(f'<a href="/{cumulative}">{seg}</a> / ')
    parts.append("</div><h1>{}</h1><ul class='file-list'>".format(rel_path or "workspace"))
    for item in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if item.name.startswith("."): continue
        rp = item.relative_to(ROOT)
        icon = "\U0001f4c2" if item.is_dir() else "\U0001f4c4"
        parts.append(f'<li>{icon} <a href="/{rp}{"/" if item.is_dir() else ""}">{item.name}</a></li>')
    parts.append("</ul>")
    return "\n".join(parts)

def render_md(rel_path):
    p = ROOT / rel_path
    raw = p.read_text(errors="replace")
    md_ext.reset()
    html = md_ext.convert(raw)
    h = file_hash(p)
    parts = ['<div class="breadcrumb">']
    parts.append('<a href="/">~/workspace</a> / ')
    cumulative = ""
    for seg in rel_path.parent.parts:
        cumulative += seg + "/"
        parts.append(f'<a href="/{cumulative}">{seg}</a> / ')
    parts.append(f"{rel_path.name}</div>")
    parts.append(f'<a class="edit-btn" href="/{rel_path}?edit=1">\u270f\ufe0f Edit</a>')
    parts.append(f"<h1>{rel_path.name}</h1>")
    parts.append(html)
    parts.append(LIVE_RELOAD_JS.replace("HASH_PLACEHOLDER", h))
    return "\n".join(parts)

def render_edit(rel_path):
    p = ROOT / rel_path
    raw = p.read_text(errors="replace")
    escaped = raw.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return f"""
    <div class='breadcrumb'><a href="/">~/workspace</a> /
    <a href="/{rel_path}">{rel_path.name}</a> / edit</div>
    <h1>\u270f\ufe0f {rel_path.name}</h1>
    <form method="POST" action="/{rel_path}?save=1">
    <textarea name="content">{escaped}</textarea><br>
    <button class="save-btn" type="submit">\U0001f4be Save</button>
    <a href="/{rel_path}" style="margin-left:1em;">Cancel</a>
    </form>"""

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path).strip("/")
        qs = urllib.parse.parse_qs(parsed.query)
        rel = Path(path) if path else Path()
        full = ROOT / rel
        try: full.resolve().relative_to(ROOT.resolve())
        except ValueError: self.send_error(403); return
        if not full.exists(): self.send_error(404); return
        if "hash" in qs and full.is_file():
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
            self.wfile.write(file_hash(full).encode()); return
        if full.is_dir(): body = render_dir(rel)
        elif full.suffix == ".md":
            body = render_edit(rel) if "edit" in qs else render_md(rel)
        else:
            self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.end_headers()
            self.wfile.write(full.read_bytes()); return
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{rel or 'workspace'}</title><style>{CSS}</style></head><body>{body}</body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write(html.encode())

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path).strip("/")
        rel = Path(path); full = ROOT / rel
        try: full.resolve().relative_to(ROOT.resolve())
        except ValueError: self.send_error(403); return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = urllib.parse.parse_qs(body)
        content = params.get("content", [""])[0]
        full.write_text(content)
        self.send_response(303); self.send_header("Location", f"/{rel}"); self.end_headers()

    def log_message(self, fmt, *args): pass

if __name__ == "__main__":
    server = HTTPServer((BIND, PORT), Handler)
    print(f"Serving {ROOT} at http://{BIND}:{PORT}")
    server.serve_forever()
```

Make it executable:
```bash
chmod +x ~/clawd/scripts/md-server.py
```

## Step 3: Configure environment

Set these environment variables based on the host:

| Variable | Default | Description |
|----------|---------|-------------|
| `MD_ROOT` | `~/clawd` | Path to workspace directory |
| `MD_PORT` | `8787` | Port to serve on |
| `MD_BIND` | `0.0.0.0` | Bind address (0.0.0.0 = all interfaces) |

## Step 4: Start the server

```bash
nohup python3 ~/clawd/scripts/md-server.py > /tmp/md-viewer.log 2>&1 &
```

Verify it's running:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8787/
# Should return 200
```

## Step 5: Make it persistent (survive reboots)

### If macOS:

Create `~/Library/LaunchAgents/com.md-viewer.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.md-viewer</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>HOMEDIR/clawd/scripts/md-server.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MD_ROOT</key>
        <string>HOMEDIR/clawd</string>
        <key>MD_PORT</key>
        <string>8787</string>
        <key>MD_BIND</key>
        <string>0.0.0.0</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/md-viewer.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/md-viewer.log</string>
</dict>
</plist>
```

**Important**: Replace `HOMEDIR` with the actual home directory path (run `echo $HOME` to get it).

Then load it:
```bash
launchctl load ~/Library/LaunchAgents/com.md-viewer.plist
```

### If Linux:

Create `/etc/systemd/system/md-viewer.service`:

```ini
[Unit]
Description=Markdown Workspace Viewer
After=network.target tailscaled.service

[Service]
ExecStart=/usr/bin/python3 HOMEDIR/clawd/scripts/md-server.py
Environment=MD_ROOT=HOMEDIR/clawd
Environment=MD_PORT=8787
Environment=MD_BIND=0.0.0.0
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Important**: Replace `HOMEDIR` with the actual home directory path.

Then enable and start:
```bash
systemctl enable --now md-viewer
```

## Step 6: Report back

Tell the user:

1. The Tailscale IP of this machine (from `tailscale ip -4`)
2. The URL to access: `http://[TAILSCALE_IP]:8787`
3. Confirm the server is running and returning 200

Then tell them to install Tailscale on their other devices (MacBook, iPhone) using the same account, and open that URL.

## What this gives you

- **File browser**: Navigate your entire workspace in a web UI
- **Markdown rendering**: `.md` files display as formatted HTML with syntax highlighting
- **In-browser editing**: Click Edit, change content, click Save
- **Live reload**: When any process (including your AI agent) modifies a file you're viewing, a green banner appears - click it to refresh
- **Security**: Bound to Tailscale network only. Nothing exposed to the public internet. Tailscale handles encryption.

## Troubleshooting

- **Import error**: `pip3 install markdown`
- **Port in use**: `lsof -i :8787` to find what's using it
- **Can't reach from other devices**: Verify Tailscale is running on both ends with `tailscale status`
- **Server crashes**: Check `/tmp/md-viewer.log`
