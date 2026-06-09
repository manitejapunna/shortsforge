"""Preview web app — localhost-only FastAPI server for reviewing clips before publishing."""

from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, JSONResponse

logger = structlog.get_logger(__name__)

_BIND_HOST = "127.0.0.1"
_PORT = 7878
_IDLE_TIMEOUT_S = 1800  # 30 minutes

app = FastAPI(title="ShortsForge Preview", docs_url=None, redoc_url=None)
_last_activity = time.time()
_workspace_file = Path.home() / ".shortsforge" / "workspace.json"


def _get_workspace() -> dict:
    if _workspace_file.exists():
        try:
            return json.loads(_workspace_file.read_text())
        except Exception:
            pass
    return {}


def _touch():
    global _last_activity
    _last_activity = time.time()


@app.get("/healthz")
async def healthz() -> JSONResponse:
    _touch()
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    _touch()
    workspace = _get_workspace()
    clips_html = ""
    for clip_id, entry in workspace.items():
        path = entry.get("path", "")
        clips_html += f"""
        <div class="clip-card">
          <div class="clip-id">{clip_id[:10]}…</div>
          <div class="clip-path">{Path(path).name}</div>
          <a href="/clip/{clip_id}" class="preview-btn">Preview</a>
        </div>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ShortsForge Preview</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; padding: 2rem; }}
    h1 {{ color: #f80; }}
    .clip-card {{ background: #222; border-radius: 8px; padding: 1rem; margin: 0.5rem 0;
                  display: flex; align-items: center; gap: 1rem; }}
    .clip-id {{ font-family: monospace; color: #aaa; }}
    .preview-btn {{ background: #f80; color: #000; padding: 0.4rem 1rem;
                    border-radius: 4px; text-decoration: none; font-weight: bold; }}
    .preview-btn:hover {{ background: #fa0; }}
  </style>
</head>
<body>
  <h1>🎬 ShortsForge Preview</h1>
  <p>{len(workspace)} clip(s) in workspace</p>
  {clips_html or "<p>No clips yet. Use <code>repurpose</code> or <code>ingest_video</code> first.</p>"}
</body>
</html>""")


@app.get("/clip/{clip_id}", response_class=HTMLResponse)
async def preview_clip(clip_id: str) -> HTMLResponse:
    _touch()
    workspace = _get_workspace()
    entry = workspace.get(clip_id)
    if not entry:
        return HTMLResponse("<h1>Clip not found</h1>", status_code=404)

    path = entry.get("path", "")
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Preview — {clip_id[:10]}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #111; color: #eee; padding: 2rem; }}
    h2 {{ color: #f80; }}
    video {{ border-radius: 12px; max-height: 80vh; }}
    .meta {{ background: #222; padding: 1rem; border-radius: 8px; margin-top: 1rem; font-family: monospace; }}
    a {{ color: #f80; }}
  </style>
</head>
<body>
  <a href="/">← Back to clips</a>
  <h2>Clip: {clip_id}</h2>
  <video controls src="/media/{clip_id}" style="display:block;margin:1rem auto;"></video>
  <div class="meta">
    <div><strong>Path:</strong> {path}</div>
    <div><strong>Parent:</strong> {entry.get("parent", "—")}</div>
  </div>
</body>
</html>""")


@app.get("/media/{clip_id}")
async def serve_media(clip_id: str) -> Response:
    _touch()
    workspace = _get_workspace()
    entry = workspace.get(clip_id)
    if not entry:
        return Response(status_code=404)

    path = Path(entry.get("path", ""))
    if not path.exists():
        return Response(status_code=404)

    # Validate path is a video file (basic check)
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return Response(status_code=403)

    content = path.read_bytes()
    return Response(
        content=content,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"},
    )


def run_preview_server() -> None:
    """Start the preview server. Binds to 127.0.0.1 only."""
    config = uvicorn.Config(
        app=app,
        host=_BIND_HOST,
        port=_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    # Idle shutdown
    def _idle_check():
        import threading
        def check():
            while True:
                time.sleep(60)
                if time.time() - _last_activity > _IDLE_TIMEOUT_S:
                    logger.info("preview.idle_shutdown")
                    server.should_exit = True
                    return
        t = threading.Thread(target=check, daemon=True)
        t.start()

    _idle_check()
    logger.info("preview.starting", host=_BIND_HOST, port=_PORT)
    server.run()


if __name__ == "__main__":
    run_preview_server()
