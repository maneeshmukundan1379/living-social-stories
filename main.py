"""FastAPI backend for Living Social Stories."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from logic import (
    MEDIA_DIR,
    current_audio_path,
    current_scene,
    generate_story_session,
    apply_choice,
    play_complete_plan,
    prewarm_choice_audio,
)

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = APP_DIR / "frontend" / "dist"
RUNTIME_SESSIONS_DIR = APP_DIR / "data" / "runtime_sessions"
RUNTIME_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


class CreateSessionRequest(BaseModel):
    child_name: str = Field(min_length=1)
    age: int = Field(ge=5, le=10)
    event_name: str = Field(min_length=1)
    caregiver_name: str = ""
    suggested_steps: str = ""
    activity_count: int = Field(default=5, ge=1, le=20)


class SelectChoiceRequest(BaseModel):
    choice_index: int = Field(ge=0)


class FlashCard(BaseModel):
    id: str
    index: int
    label: str
    image_url: str | None = None
    selected: bool = False


class SessionResponse(BaseModel):
    session_id: str
    status: str
    child_name: str
    event_name: str
    cards: list[FlashCard]
    selected_cards: list[FlashCard]
    audio_url: str | None = None


app = FastAPI(title="Living Social Stories API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


def _session_path(session_id: str) -> Path:
    return RUNTIME_SESSIONS_DIR / f"{session_id}.json"


def _save_runtime_session(session: dict[str, Any]) -> None:
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("Session has no id.")
    _session_path(session_id).write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_runtime_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def _media_url(path_value: str | None) -> str | None:
    raw = (path_value or "").strip()
    if not raw:
        return None
    try:
        relative = Path(raw).resolve().relative_to(MEDIA_DIR.resolve())
    except Exception:
        return None
    return f"/media/{relative.as_posix()}"


def _build_cards(session: dict[str, Any]) -> tuple[list[FlashCard], list[FlashCard]]:
    scene = current_scene(session) or {}
    choices = list(scene.get("choices") or [])
    image_paths = list(session.get("choice_image_paths") or [])
    selected_ids = list(session.get("selected_choice_ids") or [])
    cards: list[FlashCard] = []
    for idx, choice in enumerate(choices):
        card = FlashCard(
            id=str(choice.get("id") or f"choice_{idx}"),
            index=idx,
            label=str(choice.get("label") or f"Choice {idx + 1}"),
            image_url=_media_url(image_paths[idx] if idx < len(image_paths) else None),
            selected=str(choice.get("id") or f"choice_{idx}") in selected_ids,
        )
        cards.append(card)
    selected_cards = [
        next(card for card in cards if card.id == choice_id)
        for choice_id in selected_ids
        if any(card.id == choice_id for card in cards)
    ]
    return cards, selected_cards


def _session_response(session: dict[str, Any], status: str) -> SessionResponse:
    cards, selected_cards = _build_cards(session)
    return SessionResponse(
        session_id=str(session.get("session_id") or ""),
        status=status,
        child_name=str(session.get("hero_name") or "The Hero"),
        event_name=str(session.get("event_name") or ""),
        cards=cards,
        selected_cards=selected_cards,
        audio_url=_media_url(current_audio_path(session)),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/session", response_model=SessionResponse)
def create_session(payload: CreateSessionRequest) -> SessionResponse:
    try:
        session, status = generate_story_session(
            child_name=payload.child_name,
            age=payload.age,
            event_name=payload.event_name,
            caregiver_name=payload.caregiver_name,
            suggested_steps=payload.suggested_steps,
            activity_count=payload.activity_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_runtime_session(session)
    return _session_response(session, status)


@app.get("/api/session/{session_id}", response_model=SessionResponse)
def read_session(session_id: str) -> SessionResponse:
    session = _load_runtime_session(session_id)
    return _session_response(session, "Session loaded.")


@app.post("/api/session/{session_id}/select", response_model=SessionResponse)
def select_choice(session_id: str, payload: SelectChoiceRequest) -> SessionResponse:
    session = _load_runtime_session(session_id)
    try:
        updated_session, status = apply_choice(session, payload.choice_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_runtime_session(updated_session)
    return _session_response(updated_session, status)


@app.post("/api/session/{session_id}/play-plan", response_model=SessionResponse)
def play_plan(session_id: str) -> SessionResponse:
    session = _load_runtime_session(session_id)
    try:
        updated_session, status = play_complete_plan(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_runtime_session(updated_session)
    return _session_response(updated_session, status)


@app.post("/api/session/{session_id}/prewarm-audio")
def prewarm_audio(session_id: str) -> dict[str, str]:
    session = _load_runtime_session(session_id)
    try:
        updated_session, status = prewarm_choice_audio(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_runtime_session(updated_session)
    return {"status": status}


@app.get("/", response_model=None)
def index():
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {
            "message": "Living Social Stories backend is running.",
            "frontend_dev_hint": "Run the Vite app in ./frontend and open the frontend URL for the tablet UI.",
        }
    )


@app.get("/{full_path:path}", response_model=None)
def spa_fallback(full_path: str):
    if not FRONTEND_DIST_DIR.exists():
        raise HTTPException(status_code=404, detail="Not found.")
    requested = FRONTEND_DIST_DIR / full_path
    if requested.exists() and requested.is_file():
        return FileResponse(requested)
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="Not found.")


def server_host() -> str:
    explicit = os.getenv("HOST", "").strip()
    if explicit:
        return explicit
    # Railway sets PORT; bind broadly in that case. Local dev stays on loopback.
    return "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"


def server_port() -> int:
    return int(os.getenv("PORT", os.getenv("BACKEND_PORT", "8001")))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=server_host(), port=server_port())
