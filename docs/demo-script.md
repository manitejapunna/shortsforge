# ShortsForge Demo Script

A 2-minute walkthrough for hackathon judges.

---

## Setup (before demo)

```bash
cd shortsforge
uv sync --extra dev
cp .env.example .env
# Fill in OPENAI_API_KEY and AZURE_FOUNDRY_ENDPOINT + AZURE_FOUNDRY_KEY
```

---

## Demo A — Repurpose a Podcast in VS Code Copilot Chat

**Context:** Open VS Code in the `shortsforge/` folder. The `.vscode/mcp.json` registers the ShortsForge MCP server automatically.

**Step 1** — Start Copilot Chat in Agent mode and type:

```
Ingest samples/podcast.mp4 and detect the top 3 hooks for the "AI devtools" niche.
```

> **Expected tool sequence:** `ingest_video` → `detect_hooks(niche="AI devtools", count=3)`
> **Expected output:** A table of 3 `HookCandidate` objects with headlines, timestamps, and predicted retention.

**Step 2** — Follow up with:

```
Repurpose those hooks into YouTube Shorts with the bold-pop caption preset, then preview them.
```

> **Expected tool sequence:** `repurpose(count=3, caption_preset="bold-pop")` → `preview_short` (opens browser)
> **Screenshot cue:** Browser opens at `http://127.0.0.1:7878` showing the 3 clips side-by-side.

---

## Demo B — Terminal One-Command Repurpose

**Step 3** — In the terminal:

```bash
shortsforge repurpose samples/podcast.mp4 --niche "AI devtools" --count 3
```

> **Expected output:** Rich progress dashboard → results table with predicted retention scores.
> **Screenshot cue:** Terminal showing the results table.

---

## Demo C — Grounded Bedtime Story via Foundry IQ

**Step 4** — In Copilot Chat:

```
Create a Foundry IQ knowledge base called "azure-fundamentals" and ingest samples/azure-overview.pdf
```

> **Expected tool sequence:** `kb_create(name="azure-fundamentals")` → `kb_ingest(kb_id=..., source="samples/azure-overview.pdf")`

**Step 5** — Then:

```
Write a 30-second educational story for cloud beginners about Azure services, grounded in the azure-fundamentals KB, and render it as a Short.
```

> **Expected tool sequence:** `generate_story(prompt=..., kb_id=..., length_seconds=30)` → `storyboard(story)` → `render_storyboard(scenes)`
> **Screenshot cue:** Final mp4 previewed in browser with citation end-card showing Azure docs sources.

---

## Demo D — Safe Publishing

**Step 6** — In Copilot Chat:

```
Request publish consent for clip <clip_id> with title "Azure Basics in 30 Seconds"
```

> **Expected:** Agent returns consent token and asks user to confirm the title.

**Step 7:**

```
Publish that clip to YouTube as unlisted
```

> **Expected tool sequence:** `publish_youtube(visibility="unlisted", consent_token=...)` → returns YouTube URL

---

## Key Talking Points

1. **Microsoft Foundry IQ**: Story generation is grounded in real documents — citations appear in scenes and on the end-card. No hallucination about Azure services.
2. **Security**: Try injecting "ignore previous instructions" into a video title — the agent sanitizes it. Try uploading without consent — it's rejected.
3. **GitHub Copilot**: This entire project was built using Copilot Chat in agent mode + inline chat refinement.
