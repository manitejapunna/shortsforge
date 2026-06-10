"""ShortsForge MCP server — exposes pipeline primitives as typed MCP tools."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Literal

import structlog
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator
from ulid import ULID

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Workspace registry
# ---------------------------------------------------------------------------
_WORKSPACE_FILE = Path.home() / ".shortsforge" / "workspace.json"


def _load_workspace() -> dict[str, dict]:
    if _WORKSPACE_FILE.exists():
        try:
            return json.loads(_WORKSPACE_FILE.read_text())
        except json.JSONDecodeError:
            logger.warning("workspace.corrupt", path=str(_WORKSPACE_FILE))
        except Exception:
            logger.exception("workspace.load_error", path=str(_WORKSPACE_FILE))
    return {}


def _save_workspace(data: dict[str, dict]) -> None:
    _WORKSPACE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WORKSPACE_FILE.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(_WORKSPACE_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.warning("workspace.chmod_failed", path=str(_WORKSPACE_FILE))


_workspace: dict[str, dict] = _load_workspace()

# ---------------------------------------------------------------------------
# MCP App
# ---------------------------------------------------------------------------
mcp = FastMCP("shortsforge")


def _ok(**kwargs: Any) -> dict:
    return {"ok": True, **kwargs}


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Arg models
# ---------------------------------------------------------------------------
_ULID_RE = r"^[0-9A-HJKMNP-TV-Z]{26}$"


class IngestArgs(BaseModel):
    source_path: str = Field(max_length=512)


class TranscribeArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)


class CutClipArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    start_s: float = Field(ge=0.0)
    end_s: float = Field(ge=0.0)

    @field_validator("clip_id")
    @classmethod
    def _no_shell_metachar(cls, v: str) -> str:
        forbidden = set("|&;`$<>(){}[]\\!\"'")
        if any(c in forbidden for c in v):
            raise ValueError("clip_id contains forbidden character")
        return v


class ReformatArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    mode: Literal["speaker_track", "smart_crop", "letterbox"] = "letterbox"


class StyleCaptionsArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    preset: Literal["bold-pop", "subtle-bottom", "glow-center", "meme"] = "bold-pop"


class RenderShortArgs(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=20)
    output_name: str = Field(max_length=128, pattern=r"^[a-zA-Z0-9_\-]+$")


class KbCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class KbIngestArgs(BaseModel):
    kb_id: str = Field(max_length=256)
    source: str = Field(max_length=512)


class KbQueryArgs(BaseModel):
    kb_id: str = Field(max_length=256)
    question: str = Field(min_length=1, max_length=1024)
    top_k: int = Field(default=8, ge=1, le=20)


class GenerateStoryArgs(BaseModel):
    prompt: str = Field(min_length=1, max_length=2048)
    audience: str = Field(max_length=128)
    length_seconds: int = Field(ge=15, le=90)
    tone: Literal["soothing", "punchy", "mysterious", "uplifting", "educational"]
    kb_id: str | None = Field(default=None, max_length=256)


class GenerateScriptArgs(BaseModel):
    logline: str = Field(min_length=1, max_length=1024)
    genre: str = Field(max_length=128)
    characters: list[str] = Field(min_length=1, max_length=10)
    format: Literal["screenplay", "dialogue", "voiceover"]
    kb_id: str | None = Field(default=None, max_length=256)


class DetectHooksArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    niche: str = Field(max_length=128)
    count: int = Field(default=3, ge=1, le=10)


class RepurposeArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    niche: str = Field(max_length=128)
    count: int = Field(default=3, ge=1, le=10)
    caption_preset: Literal["bold-pop", "subtle-bottom", "glow-center", "meme"] = (
        "bold-pop"
    )
    add_broll: bool = True
    kb_id: str | None = Field(default=None, max_length=256)


class PublishConsentArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    title: str = Field(min_length=1, max_length=100)


class PublishYouTubeArgs(BaseModel):
    clip_id: str = Field(pattern=_ULID_RE)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    visibility: Literal["private", "unlisted", "public"] = "unlisted"
    consent_token: str | None = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


@mcp.tool(
    description="Ingest a local video/audio file and register it as a clip. side_effect=write"
)
async def ingest_video(source_path: str) -> dict:
    try:
        args = IngestArgs(source_path=source_path)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    try:
        from shortsforge.pipeline.ingest import ingest

        clip_id = str(ULID())
        transcript = ingest(args.source_path)
        _workspace[clip_id] = {
            "path": args.source_path,
            "transcript": transcript.model_dump(),
        }
        _save_workspace(_workspace)
        return _ok(clip_id=clip_id, duration_s=transcript.duration_s)
    except Exception as exc:
        logger.exception("ingest_video.error")
        return _err("INGEST_ERROR", str(exc))


@mcp.tool(
    description="Transcribe an already-ingested clip. Returns word-level timestamps."
)
async def transcribe(clip_id: str) -> dict:
    try:
        args = TranscribeArgs(clip_id=clip_id)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found in workspace")

    return _ok(transcript=entry.get("transcript", {}))


@mcp.tool(
    description="Cut a clip to a time range. Returns a new clip_id. side_effect=write"
)
async def cut_clip(clip_id: str, start_s: float, end_s: float) -> dict:
    try:
        args = CutClipArgs(clip_id=clip_id, start_s=start_s, end_s=end_s)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.pipeline.edit import cut
        from shortsforge.security.paths import safe_output_path

        new_id = str(ULID())
        dst = safe_output_path(f"{new_id}.mp4")
        out = cut(entry["path"], args.start_s, args.end_s, dst)
        _workspace[new_id] = {"path": str(out), "parent": clip_id}
        _save_workspace(_workspace)
        return _ok(clip_id=new_id, path=str(out))
    except Exception as exc:
        logger.exception("cut_clip.error")
        return _err("CUT_ERROR", str(exc))


@mcp.tool(description="Reformat a clip to 1080x1920 vertical. side_effect=render")
async def reformat_vertical(clip_id: str, mode: str = "letterbox") -> dict:
    try:
        args = ReformatArgs(clip_id=clip_id, mode=mode)  # type: ignore[arg-type]
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.pipeline.edit import reformat_to_vertical
        from shortsforge.security.paths import safe_output_path

        new_id = str(ULID())
        dst = safe_output_path(f"{new_id}_vertical.mp4")
        out = reformat_to_vertical(entry["path"], dst, args.mode)
        _workspace[new_id] = {"path": str(out), "parent": clip_id}
        _save_workspace(_workspace)
        return _ok(clip_id=new_id, path=str(out))
    except Exception as exc:
        logger.exception("reformat_vertical.error")
        return _err("REFORMAT_ERROR", str(exc))


@mcp.tool(description="Apply animated caption preset to a clip. side_effect=render")
async def style_captions(clip_id: str, preset: str = "bold-pop") -> dict:
    try:
        args = StyleCaptionsArgs(clip_id=clip_id, preset=preset)  # type: ignore[arg-type]
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.pipeline.captions import render_captions_over, style_preset
        from shortsforge.pipeline.ingest import Word
        from shortsforge.security.paths import safe_output_path

        transcript_data = entry.get("transcript", {})
        words: list[Word] = []
        for seg in transcript_data.get("segments", []):
            for w in seg.get("words", []):
                words.append(Word(**w))

        style = style_preset(args.preset)
        new_id = str(ULID())
        dst = safe_output_path(f"{new_id}_captioned.mp4")
        out = render_captions_over(entry["path"], words, style, dst)
        _workspace[new_id] = {"path": str(out), "parent": clip_id}
        _save_workspace(_workspace)
        return _ok(clip_id=new_id, path=str(out))
    except Exception as exc:
        logger.exception("style_captions.error")
        return _err("CAPTIONS_ERROR", str(exc))


@mcp.tool(
    description="Render clips into a YouTube-Shorts-ready mp4. side_effect=render"
)
async def render_short(clip_ids: list[str], output_name: str) -> dict:
    try:
        args = RenderShortArgs(clip_ids=clip_ids, output_name=output_name)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    clips = []
    for cid in args.clip_ids:
        entry = _workspace.get(cid)
        if not entry:
            return _err("NOT_FOUND", f"Clip {cid!r} not found")
        clips.append(entry["path"])

    try:
        from shortsforge.pipeline.render import ClipRef, Timeline
        from shortsforge.pipeline.render import render_short as _render
        from shortsforge.security.paths import safe_output_path

        timeline = Timeline(clips=[ClipRef(path=p) for p in clips])
        dst = safe_output_path(f"{args.output_name}.mp4")
        out = _render(timeline, dst)
        out_id = str(ULID())
        _workspace[out_id] = {"path": str(out)}
        _save_workspace(_workspace)
        return _ok(clip_id=out_id, path=str(out))
    except Exception as exc:
        logger.exception("render_short.error")
        return _err("RENDER_ERROR", str(exc))


@mcp.tool(description="Preview a clip in the browser. side_effect=open_browser")
async def preview_short(clip_id: str) -> dict:
    entry = _workspace.get(clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")
    try:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:7878/clip/{clip_id}")
        return _ok(clip_id=clip_id, url=f"http://127.0.0.1:7878/clip/{clip_id}")
    except Exception as exc:
        return _err("PREVIEW_ERROR", str(exc))


@mcp.tool(description="List all clips in the workspace.")
async def list_clips() -> dict:
    return _ok(
        clips=[{"clip_id": k, "path": v.get("path")} for k, v in _workspace.items()]
    )


@mcp.tool(description="Get details of a specific clip.")
async def get_clip(clip_id: str) -> dict:
    entry = _workspace.get(clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")
    return _ok(clip_id=clip_id, **entry)


# Knowledge base tools
@mcp.tool(description="Create a Foundry IQ knowledge base.")
async def kb_create(name: str) -> dict:
    try:
        args = KbCreateArgs(name=name)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))
    try:
        from shortsforge.providers.foundry_iq import FoundryIQ

        fiq = FoundryIQ.from_env()
        kb_id = await fiq.kb_create(args.name)
        await fiq.close()
        return _ok(kb_id=kb_id)
    except Exception as exc:
        return _err("KB_ERROR", str(exc))


@mcp.tool(description="Ingest a document or URL into a Foundry IQ knowledge base.")
async def kb_ingest(kb_id: str, source: str) -> dict:
    try:
        args = KbIngestArgs(kb_id=kb_id, source=source)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))
    try:
        from shortsforge.providers.foundry_iq import FoundryIQ

        fiq = FoundryIQ.from_env()
        job_id = await fiq.kb_ingest(args.kb_id, args.source)
        await fiq.close()
        return _ok(job_id=job_id)
    except Exception as exc:
        return _err("KB_INGEST_ERROR", str(exc))


@mcp.tool(
    description="Query a Foundry IQ knowledge base. Returns grounded answer + citations."
)
async def kb_query(kb_id: str, question: str, top_k: int = 8) -> dict:
    try:
        args = KbQueryArgs(kb_id=kb_id, question=question, top_k=top_k)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))
    try:
        from shortsforge.providers.foundry_iq import FoundryIQ

        fiq = FoundryIQ.from_env()
        result = await fiq.kb_query(args.kb_id, args.question, top_k=args.top_k)
        await fiq.close()
        return _ok(
            answer=result.answer,
            citations=[c.model_dump() for c in result.citations],
            confidence=result.confidence,
        )
    except Exception as exc:
        return _err("KB_QUERY_ERROR", str(exc))


@mcp.tool(
    description="Generate a structured short-form story, optionally grounded via Foundry IQ."
)
async def generate_story(
    prompt: str,
    audience: str,
    length_seconds: int,
    tone: str,
    kb_id: str | None = None,
) -> dict:
    try:
        args = GenerateStoryArgs(
            prompt=prompt,
            audience=audience,
            length_seconds=length_seconds,
            tone=tone,  # type: ignore[arg-type]
            kb_id=kb_id,
        )
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))
    try:
        from shortsforge.pipeline.story import generate_story as _gen

        story = await _gen(
            args.prompt,
            audience=args.audience,
            length_seconds=args.length_seconds,
            tone=args.tone,
            kb_id=args.kb_id,
        )
        return _ok(story=story.model_dump())
    except Exception as exc:
        return _err("STORY_ERROR", str(exc))


@mcp.tool(description="Generate a short-form script (screenplay/dialogue/voiceover).")
async def generate_script(
    logline: str,
    genre: str,
    characters: list[str],
    format: str,
    kb_id: str | None = None,
) -> dict:
    try:
        args = GenerateScriptArgs(
            logline=logline,
            genre=genre,
            characters=characters,
            format=format,  # type: ignore[arg-type]
            kb_id=kb_id,
        )
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))
    try:
        from shortsforge.pipeline.script import generate_script as _gen

        script = await _gen(
            args.logline,
            genre=args.genre,
            characters=args.characters,
            format=args.format,
            kb_id=args.kb_id,
        )
        return _ok(script=script.model_dump())
    except Exception as exc:
        return _err("SCRIPT_ERROR", str(exc))


@mcp.tool(description="Detect the best hook candidates from a clip for a given niche.")
async def detect_hooks(clip_id: str, niche: str, count: int = 3) -> dict:
    try:
        args = DetectHooksArgs(clip_id=clip_id, niche=niche, count=count)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.pipeline.hooks import detect_hooks as _detect
        from shortsforge.pipeline.ingest import Transcript

        transcript = Transcript(**entry["transcript"])
        hooks = await _detect(
            transcript,
            Path(entry["path"]),
            niche=args.niche,
            count=args.count,
        )
        return _ok(hooks=[h.model_dump() for h in hooks])
    except Exception as exc:
        return _err("HOOKS_ERROR", str(exc))


@mcp.tool(
    description="One-shot repurpose: detect hooks + cut + reformat + captions + render. side_effect=render+network"
)
async def repurpose(
    clip_id: str,
    niche: str,
    count: int = 3,
    caption_preset: str = "bold-pop",
    add_broll: bool = True,
    kb_id: str | None = None,
) -> dict:
    try:
        args = RepurposeArgs(
            clip_id=clip_id,
            niche=niche,
            count=count,
            caption_preset=caption_preset,
            add_broll=add_broll,
            kb_id=kb_id,
        )
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.pipeline.repurpose import repurpose as _repurpose

        results = await _repurpose(
            Path(entry["path"]),
            niche=args.niche,
            count=args.count,
            caption_preset=args.caption_preset,
            add_broll=args.add_broll,
            kb_id=args.kb_id,
        )
        # Register all results in workspace
        for r in results:
            _workspace[r.clip_id] = {"path": str(r.path), "parent": clip_id}
        _save_workspace(_workspace)
        return _ok(results=[r.model_dump() for r in results])
    except Exception as exc:
        logger.exception("repurpose.error")
        return _err("REPURPOSE_ERROR", str(exc))


@mcp.tool(
    description="Request a publish consent token. Must be called before publish_youtube with visibility=public."
)
async def request_publish_consent(clip_id: str, title: str) -> dict:
    import hashlib

    try:
        args = PublishConsentArgs(clip_id=clip_id, title=title)
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    token = hashlib.sha256(f"{args.clip_id}{args.title}".encode()).hexdigest()
    return _ok(
        consent_token=token,
        message=(
            f"Consent token for publishing '{args.title}' (clip {args.clip_id}). "
            "Pass this token to publish_youtube. This authorises one public upload of this title."
        ),
    )


@mcp.tool(
    description=(
        "Publish a clip to YouTube. visibility=public requires a consent_token "
        "from request_publish_consent. side_effect=network+publish"
    )
)
async def publish_youtube(
    clip_id: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    visibility: str = "unlisted",
    consent_token: str | None = None,
) -> dict:
    try:
        args = PublishYouTubeArgs(
            clip_id=clip_id,
            title=title,
            description=description,
            tags=tags or [],
            visibility=visibility,  # type: ignore[arg-type]
            consent_token=consent_token,
        )
    except Exception as exc:
        return _err("VALIDATION_ERROR", str(exc))

    entry = _workspace.get(args.clip_id)
    if not entry:
        return _err("NOT_FOUND", f"Clip {clip_id!r} not found")

    try:
        from shortsforge.publishing.youtube import publish_youtube as _publish

        result = await _publish(
            clip_id=args.clip_id,
            title=args.title,
            description=args.description,
            tags=args.tags,
            visibility=args.visibility,
            consent_token=args.consent_token,
        )
        return _ok(**result.model_dump())
    except Exception as exc:
        logger.exception("publish_youtube.error")
        return _err("PUBLISH_ERROR", str(exc))


def main() -> None:
    """Entry point for the MCP server."""
    from shortsforge.security.secrets import configure_logging

    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
