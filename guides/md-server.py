#!/usr/bin/env python3
"""Lightweight markdown file browser with live-reload via SSE."""
import os, time, hashlib, threading
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
import markdown
import urllib.parse

ROOT = Path(os.environ.get("MD_ROOT", "/root/clawd"))
PORT = int(os.environ.get("MD_PORT", "8787"))
BIND = os.environ.get("MD_BIND", "100.100.62.114")

CSS = """
body { font-family: -apple-system, system-ui, sans-serif; max-width: 800px; margin: 2em auto; padding: 0 1em; color: #222; background: #fafafa; line-height: 1.6; }
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
.edit-btn { position: fixed; top: 1em; right: 1em; background: #0066cc; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 14px; }
textarea { width: 100%; min-height: 60vh; font-family: monospace; font-size: 14px; padding: 1em; border: 1px solid #ccc; border-radius: 6px; }
.save-btn { background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; margin-top: 8px; }
#reload-banner { display:none; position:fixed; top:0; left:0; right:0; background:#28a745; color:white; text-align:center; padding:8px; cursor:pointer; z-index:999; }
"""

LIVE_RELOAD_JS = """
<div id="reload-banner" onclick="location.reload()">File updated — click to reload</div>
<script>
(function() {
  var hash = "HASH_PLACEHOLDER";
  setInterval(function() {
    fetch(location.pathname + "?hash=1").then(r => r.text()).then(h => {
      if (h !== hash) { document.getElementById("reload-banner").style.display = "block"; hash = h; }
    }).catch(() => {});
  }, 2000);
})();
</script>
"""

md_ext = markdown.Markdown(extensions=["fenced_code", "tables", "toc"])

def file_hash(p):
    try:
        return hashlib.md5(p.read_bytes()).hexdigest()
    except:
        return ""

def render_dir(rel_path):
    d = ROOT / rel_path
    parts = ["<div class='breadcrumb'>"]
    cumulative = ""
    parts.append('<a href="/">~/clawd</a> / ')
    for i, seg in enumerate(rel_path.parts):
        cumulative += seg + "/"
        parts.append(f'<a href="/{cumulative}">{seg}</a> / ')
    parts.append("</div><h1>📂 {}</h1><ul class='file-list'>".format(rel_path or "workspace"))
    
    items = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    for item in items:
        if item.name.startswith("."):
            continue
        rp = item.relative_to(ROOT)
        icon = "📂" if item.is_dir() else "📄"
        parts.append(f'<li>{icon} <a href="/{rp}{"/" if item.is_dir() else ""}">{item.name}</a></li>')
    parts.append("</ul>")
    return "\n".join(parts)

def render_md(rel_path):
    p = ROOT / rel_path
    raw = p.read_text(errors="replace")
    md_ext.reset()
    html = md_ext.convert(raw)
    h = file_hash(p)
    
    parts = ["<div class='breadcrumb'>"]
    parts.append('<a href="/">~/clawd</a> / ')
    cumulative = ""
    for seg in rel_path.parent.parts:
        cumulative += seg + "/"
        parts.append(f'<a href="/{cumulative}">{seg}</a> / ')
    parts.append(f"{rel_path.name}</div>")
    parts.append(f'<a class="edit-btn" href="/{rel_path}?edit=1">✏️ Edit</a>')
    parts.append(f"<h1>{rel_path.name}</h1>")
    parts.append(html)
    parts.append(LIVE_RELOAD_JS.replace("HASH_PLACEHOLDER", h))
    return "\n".join(parts)

def render_edit(rel_path):
    p = ROOT / rel_path
    raw = p.read_text(errors="replace")
    escaped = raw.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return f"""
    <div class='breadcrumb'><a href="/">~/clawd</a> / <a href="/{rel_path}">{rel_path.name}</a> / edit</div>
    <h1>✏️ {rel_path.name}</h1>
    <form method="POST" action="/{rel_path}?save=1">
    <textarea name="content">{escaped}</textarea><br>
    <button class="save-btn" type="submit">💾 Save</button>
    <a href="/{rel_path}" style="margin-left:1em;">Cancel</a>
    </form>"""

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path).strip("/")
        qs = urllib.parse.parse_qs(parsed.query)
        
        rel = Path(path) if path else Path()
        full = ROOT / rel
        
        # Security: no path traversal
        try:
            full.resolve().relative_to(ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        
        if not full.exists():
            self.send_error(404)
            return
        
        # Hash check for live reload
        if "hash" in qs and full.is_file():
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(file_hash(full).encode())
            return
        
        if full.is_dir():
            body = render_dir(rel)
        elif full.suffix == ".md":
            if "edit" in qs:
                body = render_edit(rel)
            else:
                body = render_md(rel)
        else:
            # Serve raw file
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(full.read_bytes())
            return
        
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{rel or 'clawd'}</title><style>{CSS}</style></head><body>{body}</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())
    
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path).strip("/")
        rel = Path(path)
        full = ROOT / rel
        
        try:
            full.resolve().relative_to(ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = urllib.parse.parse_qs(body)
        content = params.get("content", [""])[0]
        
        full.write_text(content)
        
        self.send_response(303)
        self.send_header("Location", f"/{rel}")
        self.end_headers()
    
    def log_message(self, fmt, *args):
        pass  # quiet

if __name__ == "__main__":
    server = HTTPServer((BIND, PORT), Handler)
    print(f"Serving {ROOT} at http://{BIND}:{PORT}")
    server.serve_forever()
