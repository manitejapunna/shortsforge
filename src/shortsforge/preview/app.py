"""ShortsForge web app — full content-generation studio (Story, Script, Repurpose).

Bound to 127.0.0.1 only. Auto-shutdown after 30 min idle.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import structlog
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from ulid import ULID

from shortsforge.preview.jobs import manager as job_manager
from shortsforge.security.paths import runtime_workspace_file

logger = structlog.get_logger(__name__)

# Load .env values when running the local preview app.
load_dotenv()

_BIND_HOST = "127.0.0.1"
_PORT = 7878
_IDLE_TIMEOUT_S = 1800
_WORKSPACE_FILE = runtime_workspace_file()

_TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="ShortsForge Studio", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_last_activity = time.time()


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def _load_workspace() -> dict[str, dict]:
    if _WORKSPACE_FILE.exists():
        try:
            return json.loads(_WORKSPACE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_workspace(data: dict[str, dict]) -> None:
    _WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WORKSPACE_FILE.write_text(json.dumps(data, indent=2))


def _register_clip(clip_id: str, path: Path, parent: str | None = None, **extra) -> None:
    ws = _load_workspace()
    ws[clip_id] = {"path": str(path), "parent": parent, **extra}
    _save_workspace(ws)


def _touch():
    global _last_activity
    _last_activity = time.time()


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    _touch()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "/",
            "clips": _load_workspace(),
            "jobs": [j.to_status() for j in job_manager.list_recent(5)],
        },
    )


@app.get("/story", response_class=HTMLResponse)
async def story_page(request: Request):
    _touch()
    return templates.TemplateResponse(request, "story.html", {"active": "/story"})


@app.get("/script", response_class=HTMLResponse)
async def script_page(request: Request):
    _touch()
    return templates.TemplateResponse(request, "script.html", {"active": "/script"})


@app.get("/repurpose", response_class=HTMLResponse)
async def repurpose_page(request: Request):
    _touch()
    return templates.TemplateResponse(request, "repurpose.html", {"active": "/repurpose"})


@app.get("/clips", response_class=HTMLResponse)
async def clips_page(request: Request):
    _touch()
    return templates.TemplateResponse(
        request,
        "clips.html",
        {"active": "/clips", "clips": _load_workspace()},
    )


@app.get("/clip/{clip_id}", response_class=HTMLResponse)
async def clip_detail(request: Request, clip_id: str):
    _touch()
    entry = _load_workspace().get(clip_id)
    if not entry:
        raise HTTPException(404, "Clip not found")
    return templates.TemplateResponse(
        request,
        "clip_detail.html",
        {"active": "/clips", "clip_id": clip_id, "entry": entry},
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    _touch()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "active": "/jobs",
            "jobs": [j.to_status() for j in job_manager.list_recent(50)],
        },
    )


# ---------------------------------------------------------------------------
# Routes — actions (HTMX fragments)
# ---------------------------------------------------------------------------

@app.post("/story/generate", response_class=HTMLResponse)
async def story_generate(
    prompt: str = Form(...),
    audience: str = Form("general"),
    length_seconds: int = Form(30),
    tone: str = Form("uplifting"),
    kb_id: str = Form(""),
):
    _touch()
    try:
        from shortsforge.pipeline.story import generate_story
        story = await generate_story(
            prompt,
            audience=audience,
            length_seconds=length_seconds,
            tone=tone,  # type: ignore[arg-type]
            kb_id=kb_id or None,
        )
    except Exception as exc:
        if _is_missing_llm_credentials(exc):
            logger.warning("story_generate.llm_credentials_missing")
            return HTMLResponse(_llm_credentials_missing_card("Story Studio"))
        logger.exception("story_generate.error")
        return HTMLResponse(_error_card(str(exc)))

    scenes_html = ""
    for i, scene in enumerate(story.scenes, 1):
        scenes_html += f"""
        <div class="bg-slate-800/50 rounded-lg p-3 mb-2">
          <div class="flex justify-between items-start mb-1">
            <span class="text-xs font-semibold text-purple-300">Scene {i} · {_escape(scene.mood_tag)}</span>
            <span class="text-xs text-slate-500">{scene.duration_s:.1f}s</span>
          </div>
          <div class="text-sm text-slate-200 mb-1">{_escape(scene.voiceover_text)}</div>
          <div class="text-xs text-slate-400 italic">🎨 {_escape(scene.image_prompt[:120])}</div>
        </div>
        """

    citations_html = ""
    if story.citations:
        items = "".join(f"<li>📎 {_escape(c)}</li>" for c in story.citations[:5])
        citations_html = f"""
        <div class="mt-4 pt-4 border-t border-slate-800">
          <div class="text-xs font-semibold text-purple-300 mb-2">📚 Sources (Foundry IQ)</div>
          <ul class="text-xs text-slate-400 space-y-1">{items}</ul>
        </div>
        """

    return HTMLResponse(f"""
    <div>
      <h2 class="text-xl font-bold mb-1">{_escape(story.title)}</h2>
      <p class="text-sm text-slate-400 italic mb-4">{_escape(story.logline)}</p>
      <div class="text-xs text-slate-500 mb-4">{story.total_duration_s:.0f}s · {len(story.scenes)} scenes</div>
      {scenes_html}
      {citations_html}
      <form hx-post="/story/render" hx-target="#story-output" hx-swap="innerHTML" class="mt-4">
        <input type="hidden" name="story_json" value='{_escape(story.model_dump_json())}' />
        <button class="bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded-lg text-sm font-semibold">
          🎬 Render to Video
        </button>
      </form>
    </div>
    """)


@app.post("/story/render", response_class=HTMLResponse)
async def story_render(story_json: str = Form(...)):
    _touch()
    try:
        from shortsforge.pipeline.render import render_storyboard
        from shortsforge.pipeline.storyboard import storyboard
        from shortsforge.pipeline.story import Story
        from shortsforge.security.paths import safe_output_path

        story = Story.model_validate_json(story_json)

        def render_factory(job):
            async def coro(job_arg=job):
                job_arg.update(progress=0.1, message="Building storyboard…")
                scenes = storyboard(story)
                job_arg.update(progress=0.3, message=f"Rendering {len(scenes)} scenes…")
                clip_id = str(ULID())
                dst = safe_output_path(f"{clip_id}.mp4", studio="story")
                # render_storyboard is sync; run in executor
                loop = asyncio.get_event_loop()
                out = await loop.run_in_executor(None, render_storyboard, scenes, dst)
                job_arg.update(progress=0.95, message="Registering clip…")
                _register_clip(clip_id, out, source="story")
                return {"clip_id": clip_id, "path": str(out)}
            return coro(job)

        job = job_manager.submit(render_factory)
        return HTMLResponse(_render_job_card(job.job_id))
    except Exception as exc:
        logger.exception("story_render.error")
        return HTMLResponse(_error_card(str(exc)))


@app.post("/script/generate", response_class=HTMLResponse)
async def script_generate(
    logline: str = Form(...),
    genre: str = Form("drama"),
    characters: str = Form("Narrator"),
    format: str = Form("voiceover"),
    kb_id: str = Form(""),
):
    _touch()
    try:
        from shortsforge.pipeline.script import generate_script
        chars = [c.strip() for c in characters.split(",") if c.strip()]
        script = await generate_script(
            logline,
            genre=genre,
            characters=chars,
            format=format,  # type: ignore[arg-type]
            kb_id=kb_id or None,
        )
    except Exception as exc:
        if _is_missing_llm_credentials(exc):
            logger.warning("script_generate.llm_credentials_missing")
            return HTMLResponse(_llm_credentials_missing_card("Script Studio"))
        logger.exception("script_generate.error")
        return HTMLResponse(_error_card(str(exc)))

    lines_html = ""
    for line in script.lines:
        if line.type == "slugline":
            lines_html += f'<div class="font-bold text-blue-300 uppercase mt-3">{_escape(line.text)}</div>'
        elif line.type == "character":
            lines_html += f'<div class="font-semibold text-orange-300 text-center mt-3 uppercase">{_escape(line.text)}</div>'
        elif line.type == "parenthetical":
            lines_html += f'<div class="text-center text-slate-500 italic">({_escape(line.text)})</div>'
        elif line.type == "dialogue":
            lines_html += f'<div class="text-center text-slate-200 mx-12">{_escape(line.text)}</div>'
        elif line.type in ("action", "voiceover"):
            speaker = f"[{_escape(line.speaker)}] " if line.speaker else ""
            lines_html += f'<div class="text-slate-300 my-2">{speaker}{_escape(line.text)}</div>'
        elif line.type == "transition":
            lines_html += f'<div class="text-right text-blue-300 font-semibold uppercase mt-3">{_escape(line.text)}:</div>'

    citations_html = ""
    if script.citations:
        items = "".join(f"<li>📎 {_escape(c)}</li>" for c in script.citations[:5])
        citations_html = f"""
        <div class="mt-4 pt-4 border-t border-slate-800">
          <div class="text-xs font-semibold text-blue-300 mb-2">📚 Sources (Foundry IQ)</div>
          <ul class="text-xs text-slate-400 space-y-1">{items}</ul>
        </div>
        """

    return HTMLResponse(f"""
    <div>
      <h2 class="text-xl font-bold mb-1">{_escape(script.title)}</h2>
      <div class="text-xs text-slate-500 mb-4">{_escape(script.genre)} · {script.format} · {len(script.lines)} lines</div>
      <div class="font-mono text-sm space-y-1 max-h-[500px] overflow-y-auto pr-2">
        {lines_html}
      </div>
      {citations_html}
    </div>
    """)


@app.post("/repurpose/start", response_class=HTMLResponse)
async def repurpose_start(
    source_path: str = Form(...),
    niche: str = Form(...),
    count: int = Form(3),
    caption_preset: str = Form("bold-pop"),
    add_broll: bool = Form(False),
    kb_id: str = Form(""),
):
    _touch()

    if not _llm_credentials_configured():
        logger.warning("repurpose_start.llm_credentials_missing")
        return HTMLResponse(_llm_credentials_missing_card("Repurpose Studio"))

    def factory(job):
        async def coro(job_arg=job):
            from shortsforge.pipeline.repurpose import repurpose
            job_arg.update(progress=0.05, message=f"Starting repurpose of {Path(source_path).name}…")
            results = await repurpose(
                source_path,
                niche=niche,
                count=count,
                caption_preset=caption_preset,
                add_broll=add_broll,
                kb_id=kb_id or None,
            )
            for r in results:
                _register_clip(r.clip_id, r.path, parent=source_path, title=r.title)
            return {
                "clips": [
                    {"clip_id": r.clip_id, "title": r.title,
                     "retention": r.predicted_retention,
                     "citations": len(r.citations)}
                    for r in results
                ]
            }
        return coro(job)

    job = job_manager.submit(factory)
    return HTMLResponse(_render_job_card(job.job_id))


@app.get("/jobs/{job_id}/status", response_class=HTMLResponse)
async def job_status(job_id: str):
        job = job_manager.get(job_id)
        if not job:
                return HTMLResponse("<div class='text-red-300'>Job not found</div>")

        if job.state == "done":
                clips_html = ""
                if job.result and "clips" in job.result:
                        for c in job.result["clips"]:
                                clips_html += f"""
                                <a href="/clip/{c['clip_id']}" class="block bg-slate-800/50 hover:bg-slate-800 rounded-lg p-3 mb-2">
                                    <div class="flex justify-between">
                                        <div class="text-sm font-semibold">{_escape(c.get('title', c['clip_id'][:12]))}</div>
                                        <div class="text-xs text-green-300">{(c.get('retention', 0)*100):.0f}% retention</div>
                                    </div>
                                    <div class="text-xs text-slate-500 mt-1">📎 {c.get('citations', 0)} citations</div>
                                </a>
                                """
                elif job.result and "clip_id" in job.result:
                        cid = job.result["clip_id"]
                        clips_html = f"""
                        <a href="/clip/{cid}" class="block bg-slate-800/50 hover:bg-slate-800 rounded-lg p-3">
                            <div class="text-sm font-semibold">✓ Rendered</div>
                            <div class="text-xs text-orange-300 font-mono">{cid[:16]}…</div>
                        </a>
                        """
                return HTMLResponse(f"""
                <div hx-trigger="none">
                    <div class="text-green-300 mb-2">✓ Done</div>
                    {clips_html}
                </div>
                """)

        if job.state == "error":
                return HTMLResponse(f"""
                <div hx-trigger="none">
                    <div class="text-red-300 bg-red-500/10 p-3 rounded-lg">
                        <strong>✗ Error:</strong> {_escape(job.error or 'unknown')}
                    </div>
                </div>
                """)

        if job.state == "cancelled":
                return HTMLResponse(f"""
                <div hx-trigger="none">
                    <div class="text-amber-300 bg-amber-500/10 p-3 rounded-lg">
                        <strong>⏹ Cancelled:</strong> {_escape(job.error or 'Cancelled by user')}
                    </div>
                </div>
                """)

        pct = int(job.progress * 100)
        log_html = "".join(f"<div>{_escape(l)}</div>" for l in job.log[-6:])
        return HTMLResponse(f"""
        <div hx-get="/jobs/{job_id}/status" hx-trigger="every 1s" hx-swap="outerHTML">
            <div class="flex items-center justify-between gap-3 mb-2">
                <div class="text-blue-300 text-sm">{_escape(job.message or 'Working…')}</div>
                <button
                    hx-post="/jobs/{job_id}/cancel"
                    hx-target="closest div"
                    hx-swap="outerHTML"
                    class="text-xs bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 px-2.5 py-1 rounded border border-amber-500/30"
                >
                    ⏹ Stop job
                </button>
            </div>
            <div class="bg-slate-800 rounded-full h-2 overflow-hidden mb-3">
                <div class="bg-blue-400 h-full transition-all" style="width: {pct}%"></div>
            </div>
            <div class="text-xs font-mono text-slate-500 space-y-0.5 max-h-32 overflow-y-auto">
                {log_html}
            </div>
        </div>
        """)


@app.post("/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job(job_id: str):
    _touch()
    job = job_manager.get(job_id)
    if not job:
        return HTMLResponse("<div class='text-red-300'>Job not found</div>")

    job_manager.cancel(job_id, reason="Cancelled by user")
    return await job_status(job_id)


@app.post("/clip/{clip_id}/publish", response_class=HTMLResponse)
async def publish_clip(
    clip_id: str,
    title: str = Form(...),
    description: str = Form(""),
    visibility: str = Form("unlisted"),
):
    _touch()
    try:
        from shortsforge.publishing.youtube import publish_youtube
        consent_token = None
        if visibility == "public":
            import hashlib
            consent_token = hashlib.sha256(f"{clip_id}{title}".encode()).hexdigest()

        result = await publish_youtube(
            clip_id=clip_id, title=title, description=description,
            visibility=visibility,  # type: ignore[arg-type]
            consent_token=consent_token,
        )
        return HTMLResponse(f"""
        <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-3 text-sm">
          <div class="text-green-300 font-semibold">✓ Published</div>
          <a href="{result.url}" target="_blank" class="text-orange-300 text-xs break-all">{result.url}</a>
        </div>
        """)
    except Exception as exc:
        return HTMLResponse(f"""
        <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm">
          <div class="text-red-300"><strong>✗ Failed:</strong> {_escape(str(exc))}</div>
        </div>
        """)


@app.get("/media/{clip_id}")
async def serve_media(clip_id: str) -> Response:
    _touch()
    entry = _load_workspace().get(clip_id)
    if not entry:
        return Response(status_code=404)
    path = Path(entry.get("path", ""))
    if not path.exists() or path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        return Response(status_code=403)
    return Response(content=path.read_bytes(), media_type="video/mp4",
                    headers={"Accept-Ranges": "bytes"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    import html
    return html.escape(str(s))


def _error_card(msg: str) -> str:
    return f"<div class='text-red-300 bg-red-500/10 p-4 rounded-lg'><strong>Error:</strong> {_escape(msg)}</div>"


def _is_missing_llm_credentials(exc: Exception) -> bool:
    msg = str(exc)
    return (
        "No LLM credentials found" in msg
        and "OPENAI_API_KEY" in msg
        and "AZURE_OPENAI_ENDPOINT" in msg
        and "AZURE_OPENAI_KEY" in msg
    )


def _llm_credentials_configured() -> bool:
    import os

    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    has_azure = bool(os.getenv("AZURE_OPENAI_ENDPOINT")) and bool(os.getenv("AZURE_OPENAI_KEY"))
    return has_openai or has_azure


def _llm_credentials_missing_card(studio_name: str) -> str:
    return (
        "<div class='text-amber-200 bg-amber-500/10 border border-amber-500/30 p-4 rounded-lg'>"
        f"<div class='font-semibold mb-1'>LLM setup required for {studio_name}</div>"
        "<div class='text-sm text-amber-100/90'>"
        "Set either <code>OPENAI_API_KEY</code> or "
        "<code>AZURE_OPENAI_ENDPOINT</code> + <code>AZURE_OPENAI_KEY</code> in your .env, "
        "then restart the preview app."
        "</div>"
        "</div>"
    )


def _render_job_card(job_id: str) -> str:
    return f"""
    <div class="space-y-3">
      <div class="text-xs font-mono text-slate-500">Job {job_id}</div>
      <div hx-get="/jobs/{job_id}/status" hx-trigger="load, every 1s" hx-swap="outerHTML">
        <div class="text-blue-300">Starting…</div>
      </div>
    </div>
    """


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------

def run_preview_server(*, open_browser: bool = True) -> None:
    """Start the app. Binds to 127.0.0.1 only; idle-shutdown after 30 min."""
    if _BIND_HOST not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Refusing to bind to non-loopback host: {_BIND_HOST}")

    config = uvicorn.Config(app=app, host=_BIND_HOST, port=_PORT, log_level="warning")
    server = uvicorn.Server(config)

    def _idle_check():
        import threading
        def check():
            while True:
                time.sleep(60)
                if time.time() - _last_activity > _IDLE_TIMEOUT_S:
                    logger.info("preview.idle_shutdown")
                    server.should_exit = True
                    return
        threading.Thread(target=check, daemon=True).start()

    if open_browser:
        import threading
        import webbrowser
        def _open():
            time.sleep(1.0)
            webbrowser.open(f"http://{_BIND_HOST}:{_PORT}")
        threading.Thread(target=_open, daemon=True).start()

    _idle_check()
    logger.info("preview.starting", host=_BIND_HOST, port=_PORT)
    server.run()


if __name__ == "__main__":
    run_preview_server()
