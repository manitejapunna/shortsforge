# ShortsForge Setup Notes

This guide explains how to set up ShortsForge locally and configure required API keys before using the app.

## 1) Prerequisites

- Windows 10/11 (PowerShell)
- Python 3.11+
- Git
- FFmpeg (`ffmpeg` and `ffprobe` available)

Optional but recommended:
- A virtual environment (`.venv`)

## 2) Clone and install dependencies

From your workspace folder:

```powershell
cd C:\Users\punteja\MicrosoftAgentsHackathon\CreativeApps
git clone https://github.com/manitejapunna/shortsforge.git
cd shortsforge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

For development/test dependencies:

```powershell
pip install -e .[dev]
```

## 3) Configure environment variables

Create a `.env` file from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set keys.

### Required for script/story/repurpose quality

Use one of the following LLM setups:

- OpenAI:
  - `OPENAI_API_KEY=...`

- Azure OpenAI:
  - `AZURE_OPENAI_ENDPOINT=...`
  - `AZURE_OPENAI_KEY=...`
  - `AZURE_OPENAI_DEPLOYMENT=...`

If these are missing, some AI features fall back to heuristic behavior or fail (for example script generation).

### Required for Foundry IQ features

- `AZURE_FOUNDRY_ENDPOINT=...`
- `AZURE_FOUNDRY_KEY=...`

### Optional integrations

- ElevenLabs TTS:
  - `ELEVENLABS_API_KEY=...`

- YouTube publish flow:
  - `YOUTUBE_CLIENT_SECRET_PATH=...`

## 4) FFmpeg setup

ShortsForge requires `ffmpeg` and `ffprobe`.

Verify:

```powershell
ffmpeg -version
ffprobe -version
```

If commands are not found, install FFmpeg and add its `bin` folder to your PATH.

## 5) Start the app

```powershell
cd C:\Users\punteja\MicrosoftAgentsHackathon\CreativeApps\shortsforge
.\.venv\Scripts\Activate.ps1
python -m shortsforge.preview.app
```

Open:

- `http://127.0.0.1:7878/`

## 6) Output locations

Generated files are stored under:

- `output/story`
- `output/script`
- `output/repurpose`
- `output/imports` (downloaded source videos from URLs)

Workspace clip metadata is tracked in:

- `output/workspace.json`

## 7) Quick troubleshooting

### "No LLM credentials found"

Set OpenAI or Azure OpenAI variables in `.env`, then restart the app.

### "FFprobe is not installed or not on PATH"

Install FFmpeg and confirm both `ffmpeg` and `ffprobe` work in a new terminal.

### App route keeps loading

- Ensure only one server instance is running on port `7878`
- Restart the app from the `shortsforge` folder
- Hard refresh browser (`Ctrl+F5`)

### Repurpose says done but no clips

Check `output/repurpose` and server logs for clip-level errors. If all clips fail, the job now reports the first failure reason.

## 8) Security note

- Never commit `.env`
- Keep API keys and OAuth secrets out of source control
- Rotate keys if accidentally exposed
