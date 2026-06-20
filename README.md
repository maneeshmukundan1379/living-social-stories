# Living Social Stories

`Living Social Stories` is now a tablet-first web app for autistic children ages **5-10**.
It uses:

- a `FastAPI` backend for story/session/media APIs
- a `React + Vite + Tailwind` frontend for the touch-friendly child experience
- the existing Python story and media logic in `logic.py`

## Experience

The app is designed for a calm, touch-first workflow:

1. Parent enters the event details, caregiver, and possible steps that may happen.
2. The app creates sequenced picture flash cards for the event.
3. The child taps large picture cards.
4. Selected cards appear in a bottom `My Plan` strip.
5. Voice plays for the tapped card.

## Files

- `main.py` - FastAPI backend, API routes, media serving, SPA fallback
- `logic.py` - story generation, image generation, TTS, session/media helpers
- `frontend/` - React/Vite tablet-first UI
- `requirements.txt` - Python dependencies

## Setup

### Backend

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Frontend

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories/frontend
npm install
```

## Environment

Use `/Users/maneeshmukundan/projects/agents/2_openai/.env` or a local `.env` with keys like:

```env
GEMINI_API_KEY=your_key_here
# Optional:
# GOOGLE_API_KEY=your_key_here
# GEMINI_MODEL=gemini-2.0-flash
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
#
# Optional for image/audio generation:
# OPENAI_API_KEY=your_openai_key_here
# OPENAI_IMAGE_MODEL=gpt-image-1
# OPENAI_TTS_MODEL=gpt-4o-mini-tts
# OPENAI_TTS_VOICE=alloy
```

If no Gemini key is present, the app still works using the built-in fallback story builder.
If no OpenAI key is present, the app falls back to local images and local text-to-speech where available.

## Run In Development

### Terminal 1: backend

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories
source .venv/bin/activate
python3 main.py
```

The backend starts on `http://127.0.0.1:8001`.

### Terminal 2: frontend

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories/frontend
npm run dev
```

The frontend starts on `http://127.0.0.1:5174`.

## Quick Local Run

If you just want to run the app locally with the already-built frontend:

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories
source .venv/bin/activate
python3 main.py
```

Then open:

```text
http://127.0.0.1:8001
```

## Optional Production Build

```bash
cd /Users/maneeshmukundan/projects/agents/2_openai/living_social_stories/frontend
npm run build
```

After building, the FastAPI backend can serve the compiled frontend from `frontend/dist`.

## Current UX Goals

- large touch targets for tablets/iPads
- picture-first flash cards
- one-page layout with parent setup at the top
- selected cards collected at the bottom
- spoken audio on selection
- calm colors and minimal chrome
