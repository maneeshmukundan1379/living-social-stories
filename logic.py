"""
Core story generation and play loop for Living Social Stories.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
try:
    from google import genai
except Exception:  # pragma: no cover - optional dependency at runtime
    genai = None
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

load_dotenv(override=True)

GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    os.getenv("OPENAI_MODEL", "gemini-2.0-flash"),
)
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "shimmer")
OPENAI_TTS_TIMEOUT_SECONDS = max(8.0, float(os.getenv("OPENAI_TTS_TIMEOUT_SECONDS", "25")))
TTS_STYLE_VERSION = "v4-openai-mp3"
SYSTEM_TTS_VOICE = os.getenv("SYSTEM_TTS_VOICE", "Samantha")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1").strip() or "us-central1"
GOOGLE_IMAGEN_MODEL = os.getenv("GOOGLE_IMAGEN_MODEL", "imagen-4.0-generate-001")
IMAGEN_TIMEOUT_SECONDS = max(5, int(os.getenv("IMAGEN_TIMEOUT_SECONDS", "20")))
GEMINI_TIMEOUT_SECONDS = max(10.0, float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30")))
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
SESSIONS_DIR = DATA_DIR / "sessions"
for _path in (DATA_DIR, MEDIA_DIR, SESSIONS_DIR):
    _path.mkdir(parents=True, exist_ok=True)


class StoryChoice(BaseModel):
    id: str
    label: str
    visual_prompt: str
    audio_text: str = ""
    is_safe: bool
    coach_line: str
    safe_outcome: str
    risky_outcome: str


class StoryScene(BaseModel):
    id: str
    title: str
    subject: str
    narration: str
    goal: str
    visual_prompt: str
    choices: list[StoryChoice] = Field(default_factory=list, min_length=1, max_length=20)


class StoryPackage(BaseModel):
    story_title: str
    hero_name: str
    event_name: str
    calm_opening: str
    scenes: list[StoryScene] = Field(default_factory=list, min_length=1, max_length=4)


def _gemini_api_key() -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("Set GEMINI_API_KEY or GOOGLE_API_KEY to generate Living Stories with Gemini.")
    return api_key


def _create_client() -> OpenAI:
    return OpenAI(api_key=_gemini_api_key(), base_url=GEMINI_BASE_URL, timeout=GEMINI_TIMEOUT_SECONDS)


def _openai_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _env_flag(name: str) -> bool | None:
    value = os.getenv(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _vertex_credentials_available() -> bool:
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    return bool(creds_path and Path(creds_path).is_file())


def _has_google_api_key() -> bool:
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def _vertex_imagen_available() -> bool:
    if genai is None or not GOOGLE_CLOUD_PROJECT:
        return False
    if _env_flag("ENABLE_IMAGEN") is False:
        return False
    return _vertex_credentials_available()


def _google_imagen_available() -> bool:
    if _env_flag("DISABLE_IMAGEN") is True:
        return False
    if genai is None:
        return False
    if _has_google_api_key():
        return True
    return _vertex_imagen_available()


def _create_vertex_imagen_client():
    if not _vertex_imagen_available():
        raise ValueError("Vertex Imagen is not configured.")
    return genai.Client(vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)


def _create_api_key_imagen_client():
    if not _has_google_api_key():
        raise ValueError("Google API key is not configured.")
    return genai.Client(api_key=_gemini_api_key())


def _write_generated_image(image_obj: Any, out_path: Path) -> bool:
    try:
        if hasattr(image_obj, "save"):
            image_obj.save(out_path)
            return out_path.exists() and out_path.stat().st_size > 0
        image_bytes = getattr(image_obj, "image_bytes", None)
        if image_bytes:
            out_path.write_bytes(image_bytes)
            return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False
    return False


def _generate_image_with_imagen(prompt: str, out_path: Path, *, aspect_ratio: str = "1:1") -> bool:
    if not _google_imagen_available():
        return False

    def _generate_with_client(client: Any) -> bool:
        response = client.models.generate_images(
            model=GOOGLE_IMAGEN_MODEL,
            prompt=prompt,
            config=genai.types.GenerateImagesConfig(number_of_images=1, aspect_ratio=aspect_ratio),
        )
        images = list(getattr(response, "generated_images", []) or [])
        if not images:
            return False
        return _write_generated_image(getattr(images[0], "image", None), out_path)

    def _run() -> bool:
        if _has_google_api_key():
            try:
                if _generate_with_client(_create_api_key_imagen_client()):
                    return True
            except Exception:
                pass
        if _vertex_imagen_available():
            try:
                return _generate_with_client(_create_vertex_imagen_client())
            except Exception:
                return False
        return False

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            return bool(future.result(timeout=IMAGEN_TIMEOUT_SECONDS))
        except Exception:
            return False


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower())
    return clean.strip("-") or "story"


def _story_media_dir(session_id: str) -> Path:
    path = MEDIA_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _image_seed(*parts: str) -> tuple[int, int, int]:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return (40 + int(digest[0:2], 16) % 150, 60 + int(digest[2:4], 16) % 130, 120 + int(digest[4:6], 16) % 120)


def _contains_any(text: str, words: list[str]) -> bool:
    hay = (text or "").lower()
    return any(word in hay for word in words)


def _draw_sun(draw: ImageDraw.ImageDraw) -> None:
    draw.ellipse((48, 48, 136, 136), fill=(251, 191, 36), outline=(245, 158, 11), width=4)
    for x1, y1, x2, y2 in [
        (92, 18, 92, 44),
        (92, 140, 92, 166),
        (22, 92, 48, 92),
        (140, 92, 166, 92),
        (38, 38, 56, 56),
        (128, 128, 146, 146),
        (128, 56, 146, 38),
        (38, 146, 56, 128),
    ]:
        draw.line((x1, y1, x2, y2), fill=(245, 158, 11), width=4)


def _draw_hero(draw: ImageDraw.ImageDraw, *, x: int, y: int, shirt: tuple[int, int, int]) -> None:
    draw.ellipse((x - 34, y - 120, x + 34, y - 52), fill=(254, 215, 170), outline=(15, 23, 42), width=3)
    draw.rounded_rectangle((x - 44, y - 52, x + 44, y + 68), radius=28, fill=shirt, outline=(15, 23, 42), width=3)
    draw.line((x - 20, y + 68, x - 34, y + 148), fill=(15, 23, 42), width=6)
    draw.line((x + 20, y + 68, x + 34, y + 148), fill=(15, 23, 42), width=6)
    draw.line((x - 44, y - 10, x - 96, y + 32), fill=(15, 23, 42), width=6)
    draw.line((x + 44, y - 10, x + 96, y + 20), fill=(15, 23, 42), width=6)


def _draw_grownup(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.ellipse((x - 30, y - 118, x + 30, y - 58), fill=(254, 215, 170), outline=(15, 23, 42), width=3)
    draw.rounded_rectangle((x - 40, y - 58, x + 40, y + 78), radius=26, fill=(16, 185, 129), outline=(15, 23, 42), width=3)
    draw.line((x - 16, y + 78, x - 24, y + 156), fill=(15, 23, 42), width=6)
    draw.line((x + 16, y + 78, x + 24, y + 156), fill=(15, 23, 42), width=6)
    draw.line((x - 40, y - 6, x - 80, y + 28), fill=(15, 23, 42), width=6)
    draw.line((x + 40, y - 6, x + 66, y + 26), fill=(15, 23, 42), width=6)


def _draw_balloons(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    colors = [(244, 114, 182), (96, 165, 250), (251, 191, 36)]
    offsets = [(-24, 0), (18, -8), (0, 28)]
    for (dx, dy), color in zip(offsets, colors):
        draw.ellipse((x + dx - 22, y + dy - 28, x + dx + 22, y + dy + 28), fill=color, outline=(15, 23, 42), width=2)
        draw.line((x + dx, y + dy + 28, x - 8, y + 88), fill=(15, 23, 42), width=2)


def _draw_bubbles(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    for radius, dx, dy in [(24, 0, 0), (18, 42, -26), (16, -34, 18), (12, 58, 28), (10, -56, -18)]:
        draw.ellipse((x + dx - radius, y + dy - radius, x + dx + radius, y + dy + radius), outline=(96, 165, 250), width=4)


def _draw_swing(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.line((x - 58, y + 84, x - 26, y - 76), fill=(71, 85, 105), width=8)
    draw.line((x + 58, y + 84, x + 26, y - 76), fill=(71, 85, 105), width=8)
    draw.line((x - 78, y - 76, x + 78, y - 76), fill=(71, 85, 105), width=8)
    draw.line((x - 20, y - 76, x - 20, y + 6), fill=(51, 65, 85), width=4)
    draw.line((x + 20, y - 76, x + 20, y + 6), fill=(51, 65, 85), width=4)
    draw.rounded_rectangle((x - 32, y + 6, x + 32, y + 20), radius=8, fill=(251, 191, 36), outline=(15, 23, 42), width=2)


def _draw_bench(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.rounded_rectangle((x - 58, y - 20, x + 58, y + 10), radius=8, fill=(180, 83, 9), outline=(15, 23, 42), width=3)
    draw.rounded_rectangle((x - 52, y - 52, x + 52, y - 24), radius=8, fill=(180, 83, 9), outline=(15, 23, 42), width=3)
    draw.line((x - 40, y + 10, x - 46, y + 52), fill=(15, 23, 42), width=5)
    draw.line((x + 40, y + 10, x + 46, y + 52), fill=(15, 23, 42), width=5)


def _draw_headphones(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.arc((x - 48, y - 34, x + 48, y + 54), start=180, end=360, fill=(37, 99, 235), width=7)
    draw.rounded_rectangle((x - 54, y - 6, x - 26, y + 44), radius=8, fill=(37, 99, 235), outline=(15, 23, 42), width=2)
    draw.rounded_rectangle((x + 26, y - 6, x + 54, y + 44), radius=8, fill=(37, 99, 235), outline=(15, 23, 42), width=2)


def _draw_music_notes(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    for dx, dy in [(0, 0), (48, -18)]:
        draw.line((x + dx, y + dy, x + dx, y + dy + 48), fill=(168, 85, 247), width=4)
        draw.line((x + dx, y + dy, x + dx + 28, y + dy - 8), fill=(168, 85, 247), width=4)
        draw.ellipse((x + dx - 14, y + dy + 40, x + dx + 14, y + dy + 62), fill=(168, 85, 247))


def _draw_snack(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.rounded_rectangle((x - 28, y - 34, x + 28, y + 34), radius=14, fill=(245, 158, 11), outline=(15, 23, 42), width=3)
    draw.rectangle((x - 10, y - 54, x + 10, y - 34), fill=(254, 240, 138), outline=(15, 23, 42), width=2)


def _draw_crowd(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    for dx in (-56, 0, 56):
        draw.ellipse((x + dx - 20, y - 54, x + dx + 20, y - 14), fill=(254, 215, 170), outline=(15, 23, 42), width=2)
        draw.rounded_rectangle((x + dx - 26, y - 14, x + dx + 26, y + 42), radius=14, fill=(147, 197, 253), outline=(15, 23, 42), width=2)


def _draw_tree(draw: ImageDraw.ImageDraw, *, x: int, y: int) -> None:
    draw.rounded_rectangle((x - 12, y - 18, x + 12, y + 76), radius=8, fill=(120, 53, 15))
    draw.ellipse((x - 64, y - 92, x + 64, y + 18), fill=(34, 197, 94), outline=(21, 128, 61), width=3)


def _draw_event_scene(draw: ImageDraw.ImageDraw, text: str) -> None:
    if _contains_any(text, ["park", "playground"]):
        _draw_tree(draw, x=116, y=408)
        _draw_tree(draw, x=654, y=420)
    if _contains_any(text, ["party", "birthday"]):
        _draw_balloons(draw, x=620, y=176)
    if _contains_any(text, ["dentist", "tooth"]):
        draw.rounded_rectangle((522, 462, 676, 556), radius=28, fill=(191, 219, 254), outline=(15, 23, 42), width=3)
        draw.ellipse((560, 404, 638, 484), fill=(255, 255, 255), outline=(15, 23, 42), width=3)


def _draw_choice_activity(draw: ImageDraw.ImageDraw, text: str) -> None:
    if _contains_any(text, ["swing"]):
        _draw_swing(draw, x=614, y=306)
    if _contains_any(text, ["bubble"]):
        _draw_bubbles(draw, x=606, y=248)
    if _contains_any(text, ["hold hands", "grown-up", "go to", "with a grown-up"]):
        _draw_grownup(draw, x=602, y=500)
    if _contains_any(text, ["headphone"]):
        _draw_headphones(draw, x=254, y=334)
    if _contains_any(text, ["break", "rest", "sit", "bench"]):
        _draw_bench(draw, x=612, y=574)
    if _contains_any(text, ["music"]):
        _draw_music_notes(draw, x=566, y=182)
    if _contains_any(text, ["snack", "eat"]):
        _draw_snack(draw, x=604, y=564)
    if _contains_any(text, ["crowd", "people", "wait"]):
        _draw_crowd(draw, x=602, y=548)


def _create_fallback_scene_image(session: dict[str, Any], scene: dict[str, Any], idx: int, out_path: Path) -> str:
    hero = str(session.get("hero_name") or "The Hero")
    event_name = str(session.get("event_name") or "the event")
    bg = _image_seed(hero, event_name, str(idx))
    img = Image.new("RGB", (1024, 1024), color=bg)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((40, 40, 984, 984), radius=40, fill=(248, 250, 252), outline=(15, 23, 42), width=4)
    draw.rounded_rectangle((80, 90, 944, 300), radius=32, fill=(15, 23, 42))
    draw.text((110, 120), f"Scene {idx + 1}", fill=(191, 219, 254), font=_font(28))
    draw.text((110, 165), str(scene.get("title") or "Scene"), fill=(255, 255, 255), font=_font(50))
    draw.rounded_rectangle((110, 350, 914, 610), radius=30, fill=(219, 234, 254))
    body = "\n".join(textwrap.wrap(str(scene.get("subject") or ""), width=28))
    draw.multiline_text((145, 390), body, fill=(30, 41, 59), font=_font(34), spacing=10)
    prompt_text = str(scene.get("visual_prompt") or "")
    prompt_lines = "\n".join(textwrap.wrap(prompt_text[:380], width=42))
    draw.rounded_rectangle((110, 660, 914, 900), radius=28, fill=(241, 245, 249))
    draw.text((145, 690), f"{hero} is the hero.", fill=(29, 78, 216), font=_font(30))
    draw.multiline_text((145, 735), prompt_lines, fill=(51, 65, 85), font=_font(24), spacing=8)
    img.save(out_path)
    return str(out_path)


def _create_fallback_choice_image(session: dict[str, Any], choice: dict[str, Any], idx: int, out_path: Path) -> str:
    hero = str(session.get("hero_name") or "The Hero")
    event_name = str(session.get("event_name") or "the event")
    bg = _image_seed(hero, event_name, str(idx), str(choice.get("label") or "choice"))
    img = Image.new("RGB", (768, 768), color=bg)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 24, 744, 744), radius=44, fill=(239, 246, 255), outline=(15, 23, 42), width=4)
    draw.rectangle((36, 36, 732, 360), fill=(191, 219, 254))
    draw.rectangle((36, 360, 732, 732), fill=(187, 247, 208))
    _draw_sun(draw)
    _draw_event_scene(draw, f"{event_name} {choice.get('visual_prompt', '')}")
    _draw_hero(draw, x=254, y=506, shirt=(59, 130, 246))
    _draw_choice_activity(draw, f"{choice.get('label', '')} {choice.get('visual_prompt', '')} {event_name}")
    if _contains_any(f"{choice.get('label', '')} {choice.get('visual_prompt', '')}", ["hold hands", "grown-up", "help"]):
        draw.line((348, 496, 520, 492), fill=(15, 23, 42), width=6)
    if _contains_any(f"{choice.get('label', '')} {choice.get('visual_prompt', '')}", ["headphone"]):
        _draw_headphones(draw, x=254, y=332)
    img.save(out_path)
    return str(out_path)


def _generate_scene_image(session: dict[str, Any], scene: dict[str, Any], idx: int) -> str:
    media_dir = _story_media_dir(str(session.get("session_id") or "session"))
    out_path = media_dir / f"scene_{idx + 1}.png"
    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)

    hero = str(session.get("hero_name") or "The Hero")
    age = int(session.get("age") or 7)
    prompt = (
        f"{scene.get('visual_prompt', '')}. Create a warm, child-safe 3D comic panel. "
        f"Keep the same child hero named {hero}, age {age}, consistent across all panels. "
        "No text overlays, no scary imagery, bright friendly colors, expressive but calm faces."
    )
    if _generate_image_with_imagen(prompt, out_path):
        return str(out_path)

    return _create_fallback_scene_image(session, scene, idx, out_path)


def _generate_choice_image(session: dict[str, Any], choice: dict[str, Any], idx: int) -> str:
    media_dir = _story_media_dir(str(session.get("session_id") or "session"))
    out_path = media_dir / f"choice_{idx + 1}.png"
    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)
    hero = str(session.get("hero_name") or "The Hero")
    age = int(session.get("age") or 7)
    prompt = (
        f"{choice.get('visual_prompt', '')}. Create one polished, child-safe 3D storybook illustration. "
        f"Show the same child hero named {hero}, age {age}. "
        "Make it look like a real illustrated scene, not an icon, sticker, collage, placeholder, or abstract shape. "
        "No text overlays. Soft friendly expressions. Bright warm colors. Literal environment."
    )
    if _generate_image_with_imagen(prompt, out_path):
        return str(out_path)
    return _create_fallback_choice_image(session, choice, idx, out_path)


def _narration_text(session: dict[str, Any]) -> str:
    if not session:
        return ""
    if session.get("completed"):
        return (
            f"{session.get('hero_name', 'The hero')} finished the story. "
            f"{session.get('last_feedback', 'These are the activities that may happen at the event.')}"
        )
    scene = current_scene(session)
    if not scene:
        return ""
    feedback = str(session.get("last_feedback") or "").strip()
    parts = [
        f"{scene.get('title', 'Scene')}.",
        str(scene.get("narration") or ""),
        f"Goal: {scene.get('goal', '')}.",
    ]
    if feedback:
        parts.append(feedback)
    return " ".join(part for part in parts if part).strip()


def _choice_audio_text(choice: dict[str, Any], event_name: str, hero_name: str) -> str:
    text = str(choice.get("audio_text") or "").strip()
    child_name = (hero_name or "My friend").strip()
    if text:
        return text.replace(" can ", " will ")
    label = str(choice.get("label") or "do the next activity").strip().lower()
    return f"{child_name} will {label} at {event_name}."


def _tts_name_hint(name: str) -> str:
    clean = " ".join((name or "").strip().split())
    if not clean:
        return "The child"
    known = {
        "vihaan": "Vee-haan",
    }
    return known.get(clean.lower(), clean)


def _audio_file_candidates(session: dict[str, Any], stem: str, audio_text: str) -> tuple[Path, Path]:
    media_dir = _story_media_dir(str(session.get("session_id") or "session"))
    audio_key = hashlib.sha256(
        f"{OPENAI_TTS_MODEL}|{OPENAI_TTS_VOICE}|{TTS_STYLE_VERSION}|{audio_text}".encode("utf-8")
    ).hexdigest()[:12]
    mp3_path = media_dir / f"{stem}_{audio_key}.mp3"
    local_path = media_dir / f"{stem}_{audio_key}.wav"
    return mp3_path, local_path


def _choice_audio_file_candidates(session: dict[str, Any], choice_slot: int, audio_text: str) -> tuple[Path, Path]:
    return _audio_file_candidates(session, f"choice_{choice_slot + 1}", audio_text)


def _write_openai_speech_response(response: Any, out_path: Path) -> bool:
    try:
        if hasattr(response, "stream_to_file"):
            response.stream_to_file(str(out_path))
        elif hasattr(response, "write_to_file"):
            response.write_to_file(str(out_path))
        elif hasattr(response, "read"):
            out_path.write_bytes(response.read())
        elif hasattr(response, "content"):
            out_path.write_bytes(response.content)
        else:
            return False
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False


def _generate_audio_with_openai(text: str, out_path: Path) -> bool:
    openai_key = _openai_api_key()
    if not openai_key or not text:
        return False

    name_match = re.match(r"\s*([A-Za-z][A-Za-z' -]{0,40})\s+(?:will|can)\b", text)
    spoken_name = _tts_name_hint(name_match.group(1)) if name_match else "the child"
    instructions = (
        "Speak in very cheerful, pleasant, natural US English for a young child. "
        "Use clear American pronunciation, with a bright, smiling, upbeat, playful tone. "
        "Sound warm, sweet, and animated like a happy storyteller, while staying easy to understand. "
        "Pause gently between phrases and avoid sounding robotic. "
        f"If the child's name appears, pronounce it carefully. A pronunciation guide for the child's name is: {spoken_name}."
    )
    models_to_try = [OPENAI_TTS_MODEL]
    if OPENAI_TTS_MODEL != "tts-1":
        models_to_try.append("tts-1")

    for model_name in models_to_try:
        instruction_attempts = [True, False] if model_name == OPENAI_TTS_MODEL else [False]
        for use_instructions in instruction_attempts:
            try:
                client = OpenAI(api_key=openai_key, timeout=OPENAI_TTS_TIMEOUT_SECONDS)
                kwargs: dict[str, Any] = {
                    "model": model_name,
                    "voice": OPENAI_TTS_VOICE,
                    "input": text,
                    "speed": 0.92,
                }
                if use_instructions and model_name == OPENAI_TTS_MODEL:
                    kwargs["instructions"] = instructions
                response = client.audio.speech.create(**kwargs)
                if _write_openai_speech_response(response, out_path):
                    return True
            except Exception:
                continue
    return False


def _convert_to_browser_wav(source: Path, dest: Path) -> bool:
    if not source.exists() or source.stat().st_size == 0:
        return False
    if source.suffix.lower() == ".wav" and source.resolve() == dest.resolve():
        return True
    if sys.platform != "darwin":
        if source.suffix.lower() == ".wav":
            dest.write_bytes(source.read_bytes())
            return dest.exists() and dest.stat().st_size > 0
        return False
    try:
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(source), str(dest)],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        return False


def _generate_audio_with_system_say(text: str, out_path: Path) -> bool:
    if not text or sys.platform != "darwin":
        return False
    temp_aiff = out_path.with_suffix(".tmp.aiff")
    try:
        subprocess.run(
            ["say", "-v", SYSTEM_TTS_VOICE, "-r", "190", "-o", str(temp_aiff), text],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if not temp_aiff.exists() or temp_aiff.stat().st_size == 0:
            return False
        wav_path = out_path if out_path.suffix.lower() == ".wav" else out_path.with_suffix(".wav")
        return _convert_to_browser_wav(temp_aiff, wav_path)
    except Exception:
        return False
    finally:
        temp_aiff.unlink(missing_ok=True)


def _generate_audio_with_pyttsx3(text: str, out_path: Path) -> bool:
    if not text:
        return False
    try:
        script = """
import pyttsx3
engine = pyttsx3.init()
try:
    engine.setProperty("rate", 115)
except Exception:
    pass
engine.save_to_file(TEXT, OUT_PATH)
engine.runAndWait()
"""
        subprocess.run(
            [sys.executable, "-c", f"TEXT = {text!r}\nOUT_PATH = {str(out_path)!r}\n{script}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception:
        return False


def ensure_session_media(session: dict[str, Any]) -> dict[str, Any]:
    if not session:
        return session
    scene = current_scene(session)
    choice_paths: list[str] = []
    if scene:
        choices = list(scene.get("choices") or [])
        if _google_imagen_available():
            with ThreadPoolExecutor(max_workers=min(4, len(choices) or 1)) as executor:
                futures = [executor.submit(_generate_choice_image, session, choice, idx) for idx, choice in enumerate(choices)]
                for future in futures:
                    choice_paths.append(future.result())
        else:
            for idx, choice in enumerate(choices):
                choice_paths.append(_generate_choice_image(session, choice, idx))
    session["choice_image_paths"] = choice_paths
    session["current_audio_path"] = ""
    return session


def _split_items(text: str) -> list[str]:
    parts = [item.strip() for item in re.split(r"[,;\n]+", text or "") if item.strip()]
    return parts[:12]


def _title_text(text: str) -> str:
    clean = (text or "").strip()
    return clean[:1].upper() + clean[1:] if clean else clean


def _sentence_case(text: str) -> str:
    clean = (text or "").strip()
    return clean[:1].lower() + clean[1:] if clean else clean


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = " ".join((item or "").strip().split())
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _fallback_activity_labels(
    *,
    event_name: str,
    caregiver_name: str,
    suggested_steps: list[str],
) -> list[str]:
    event_lower = event_name.lower()
    caregiver = caregiver_name or "caregiver"

    labels: list[str] = []
    labels.extend(suggested_steps)

    if any(word in event_lower for word in ["doctor", "clinic", "checkup", "hospital", "shot", "vaccine"]):
        labels.extend(
            [
                f"Walk into the clinic with {caregiver}",
                "Check in at the front desk",
                "Sit in the waiting room",
                "Listen to what the doctor says",
                "Ask what happens next",
                "Choose a sticker before leaving",
            ]
        )
    elif any(word in event_lower for word in ["dentist", "teeth", "tooth"]):
        labels.extend(
            [
                f"Walk into the dentist office with {caregiver}",
                "Check in at the front desk",
                "Sit in the waiting room",
                "Open wide for the dentist",
                "Ask what tool is being used",
                "Pick a reward before leaving",
            ]
        )
    elif any(word in event_lower for word in ["birthday", "party", "celebration"]):
        labels.extend(
            [
                f"Walk into the party with {caregiver}",
                "See the decorations and people",
                "Say hello to the host or birthday child",
                "Watch the first game or activity",
                "Join a party game or play activity",
                "Sing happy birthday",
                "Eat birthday cake",
                "Watch the birthday child open presents",
                "Take a return gift",
                "Say goodbye before leaving",
            ]
        )
    elif any(word in event_lower for word in ["park", "playground"]):
        labels.extend(
            [
                f"Walk into the park with {caregiver}",
                "Look at the playground first",
                "Choose one thing to try",
                "Take turns on the equipment",
                "Try another playground activity",
                f"Walk back to the car with {caregiver}",
            ]
        )
    elif any(word in event_lower for word in ["school", "class", "classroom"]):
        labels.extend(
            [
                f"Walk into school with {caregiver}",
                "Hang up backpack",
                "Look at the classroom plan",
                "Sit in the first activity",
                "Listen to the teacher",
                "Get ready to go home",
            ]
        )
    else:
        labels.extend(
            [
                f"Go into {event_name} with {caregiver}",
                f"Check what happens first at {event_name}",
                f"Go to the next step at {event_name}",
                f"Do one activity at {event_name}",
                f"Finish the last step at {event_name}",
                f"Finish {event_name} and leave with {caregiver}",
            ]
        )

    return _dedupe_keep_order(labels)


def _ordered_steps_for_count(steps: list[str], activity_count: int) -> list[str]:
    if activity_count <= 0:
        return []
    if len(steps) <= activity_count:
        return steps
    if activity_count == 1:
        return [steps[0]]

    # Keep the start and end, then evenly sample the middle so the child
    # sees the overall event flow instead of only the earliest moments.
    last_index = len(steps) - 1
    selected_indices = {0, last_index}
    slots = activity_count - 2
    if slots > 0:
        for idx in range(1, slots + 1):
            position = round(idx * last_index / (slots + 1))
            selected_indices.add(min(last_index - 1, max(1, position)))
    ordered_indices = sorted(selected_indices)
    return [steps[idx] for idx in ordered_indices[:activity_count]]


def _choice_rank_for_event(label: str, event_name: str, suggested_steps: list[str]) -> tuple[int, int]:
    normalized_label = " ".join((label or "").strip().lower().split())
    for idx, step in enumerate(suggested_steps):
        normalized_step = " ".join((step or "").strip().lower().split())
        if normalized_step and (
            normalized_step in normalized_label or normalized_label in normalized_step
        ):
            return (0, idx)

    event_lower = (event_name or "").lower()
    if any(word in event_lower for word in ["birthday", "party", "celebration"]):
        party_order = [
            ["walk into", "arrive", "enter"],
            ["decorations", "people", "room"],
            ["hello", "host", "birthday child", "greet"],
            ["watch the first game", "watch the first activity"],
            ["game", "play activity", "play"],
            ["sing", "happy birthday", "song"],
            ["cake", "eat"],
            ["present", "return gift", "gift"],
            ["goodbye", "leave", "go home"],
        ]
        for idx, keywords in enumerate(party_order):
            if any(keyword in normalized_label for keyword in keywords):
                return (1, idx)
    return (2, 999)


def _normalize_choice_ids(story: StoryPackage) -> StoryPackage:
    if not story.scenes:
        return story
    normalized_scenes: list[StoryScene] = []
    for scene in story.scenes:
        normalized_choices: list[StoryChoice] = []
        seen: set[str] = set()
        for idx, choice in enumerate(scene.choices):
            choice_id = str(choice.id or "").strip() or f"choice_{idx + 1}"
            if choice_id in seen:
                choice_id = f"choice_{idx + 1}"
            seen.add(choice_id)
            normalized_choices.append(choice.model_copy(update={"id": choice_id}))
        normalized_scenes.append(scene.model_copy(update={"choices": normalized_choices}))
    return story.model_copy(update={"scenes": normalized_scenes})


def _normalize_story_choice_order(story: StoryPackage, event_name: str, suggested_steps: list[str]) -> StoryPackage:
    if not story.scenes:
        return story
    scene = story.scenes[0]
    ordered_choices = sorted(
        scene.choices,
        key=lambda choice: _choice_rank_for_event(choice.label, event_name, suggested_steps),
    )
    story.scenes[0] = scene.model_copy(update={"choices": ordered_choices})
    return story


def _label_to_audio_text(label: str, hero_name: str) -> str:
    hero = hero_name or "The child"
    lower = _sentence_case(label)
    return f"{hero} will {lower}."


def _simple_choice_story(
    *,
    child_name: str,
    age: int,
    event_name: str,
    caregiver_name: str,
    suggested_steps: str,
    activity_count: int,
) -> StoryPackage:
    hero = child_name or "The Hero"
    caregiver = caregiver_name.strip() or "caregiver"
    suggested_step_list = _split_items(suggested_steps)
    planned_steps = _fallback_activity_labels(
        event_name=event_name,
        caregiver_name=caregiver,
        suggested_steps=suggested_step_list,
    )
    if suggested_step_list:
        planned_steps = planned_steps[: max(1, min(activity_count, len(planned_steps)))]
    else:
        planned_steps = _ordered_steps_for_count(planned_steps, activity_count)

    choices: list[StoryChoice] = []
    for idx, label in enumerate(planned_steps):
        choices.append(
            StoryChoice(
                id=f"choice_{idx + 1}",
                label=_title_text(label),
                visual_prompt=(
                    f"3D child-safe illustration of {hero}, age {age}, during {event_name}, showing this step: {label}. "
                    f"Show what this step looks like in the real event with {caregiver} nearby when appropriate. "
                    "No text overlays. Real illustrated scene, warm colors, literal child-friendly setting."
                ),
                audio_text=_label_to_audio_text(label, hero),
                is_safe=True,
                coach_line=f"{_title_text(label)} is one activity that may happen during {event_name}.",
                safe_outcome=f"{hero} sees this activity as part of {event_name}.",
                risky_outcome="",
            )
        )

    scene = StoryScene(
        id="scene_1",
        title=f"What {hero} may see at {event_name}",
        subject=f"These picture cards show activities that may happen during {event_name}.",
        narration=(
            f"{hero} is going to {event_name}. "
            "The picture cards show what will happen first, next, and later at the event."
        ),
        goal=f"Learn what activities to expect during {event_name}.",
        visual_prompt=(
            f"3D comic scene, child hero named {hero}, age {age}, preparing for {event_name}, "
            "warm colors, real event details, direct literal expressions, child-friendly setting"
        ),
        choices=choices,
    )
    return StoryPackage(
        story_title=f"{hero}'s Living Story for {event_name}",
        hero_name=hero,
        event_name=event_name,
        calm_opening=(
            f"This story helps {hero} understand what activities may happen during {event_name}. "
            "The child will look at the picture cards and learn what to expect."
        ),
        scenes=[scene],
    )


def _extract_json_object(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Empty model response.")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError("Model did not return valid JSON.")


def _narrator_story(
    *,
    child_name: str,
    age: int,
    event_name: str,
    caregiver_name: str,
    suggested_steps: str,
    activity_count: int,
) -> StoryPackage:
    client = _create_client()
    system = """You are The Narrator for a touch-first social planning app for autistic children ages 5-10.

Write with a calm, direct, literal tone.
Avoid metaphors, abstract filler, and generic placeholder phrases.
Use practical, contextual activities that fit the exact event.
The child is the hero.
Use the caregiver name exactly as provided by the parent.

Generate exactly one JSON object matching this shape:
{
  "story_title": "string",
  "hero_name": "string",
  "event_name": "string",
  "calm_opening": "string",
  "scenes": [
    {
      "id": "scene_1",
      "title": "string",
      "subject": "string",
      "narration": "string",
      "goal": "string",
      "visual_prompt": "string",
      "choices": [
        {
          "id": "choice_id",
          "label": "string",
          "visual_prompt": "string",
          "audio_text": "string",
          "is_safe": true,
          "coach_line": "string",
          "safe_outcome": "string",
          "risky_outcome": ""
        }
      ]
    }
  ]
}

Requirements:
- Create exactly 1 scene.
- Create exactly the requested number of choices.
- All choices must be real-world activities or moments the child should expect at this specific event.
- Sequence the choices so they feel like a natural event flow from first to later.
- If parent-provided suggested steps are present, preserve their order exactly.
- If you add steps beyond the parent-provided ones, only append them where they naturally belong in the event timeline.
- Labels should be short and specific.
- Focus on expectation-setting, not therapy language, coping strategies, or safe-space coaching unless the parent explicitly asked for that in the steps.
- For party or birthday events, prefer likely party moments in a realistic order such as arriving, seeing decorations, greeting people, games, singing happy birthday, cake, presents, return gift, and leaving.
- audio_text must sound natural in US English, should say the child's name instead of "I", and should use "will" rather than "can".
- visual_prompt should describe a warm 3D illustrated scene with the child as the hero.
"""
    user = {
        "child_name": child_name,
        "age": age,
        "event_name": event_name,
        "caregiver_name": caregiver_name,
        "suggested_steps": suggested_steps,
        "activity_count": activity_count,
    }
    try:
        resp = client.chat.completions.create(
            model=GEMINI_MODEL,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
    except Exception:
        resp = client.chat.completions.create(
            model=GEMINI_MODEL,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        )
    raw = resp.choices[0].message.content or ""
    return StoryPackage(**_extract_json_object(raw))


def _story_to_session(story: StoryPackage) -> dict[str, Any]:
    return {
        **story.model_dump(),
        "session_id": uuid4().hex[:12],
        "current_index": 0,
        "completed": False,
        "last_feedback": "",
        "history": [],
        "rewind_count": 0,
        "selected_choice_ids": [],
        "narrator_name": "The Narrator",
        "illustrator_name": "The Illustrator",
        "consequence_engine_name": "The Consequence Engine",
        "image_paths": [],
        "current_audio_path": "",
        "saved_story_name": "",
        "caregiver_name": "",
    }


def generate_story_session(
    *,
    child_name: str,
    age: int,
    event_name: str,
    caregiver_name: str = "",
    suggested_steps: str = "",
    activity_count: int = 5,
) -> tuple[dict[str, Any], str]:
    if not event_name.strip():
        raise ValueError("Please enter the event you want to practice.")
    if not child_name.strip():
        raise ValueError("Please enter the child's name.")
    if age < 5 or age > 10:
        raise ValueError("This app is designed for ages 5-10.")
    if activity_count < 1 or activity_count > 20:
        raise ValueError("Choose between 1 and 20 activities.")

    child_name = child_name.strip()
    event_name = event_name.strip()
    caregiver_name = caregiver_name.strip() or "caregiver"
    suggested_steps = suggested_steps.strip()
    suggested_step_list = _split_items(suggested_steps)

    try:
        story = _narrator_story(
            child_name=child_name,
            age=age,
            event_name=event_name,
            caregiver_name=caregiver_name,
            suggested_steps=suggested_steps,
            activity_count=activity_count,
        )
    except Exception:
        story = _simple_choice_story(
            child_name=child_name,
            age=age,
            event_name=event_name,
            caregiver_name=caregiver_name,
            suggested_steps=suggested_steps,
            activity_count=activity_count,
        )
    story = _normalize_story_choice_order(story, event_name, suggested_step_list)
    story = _normalize_choice_ids(story)
    status = "Flash cards are ready. The child will tap pictures to hear what may happen at the event."
    session = _story_to_session(story)
    session["age"] = age
    session["caregiver_name"] = caregiver_name
    session = ensure_session_media(session)
    return session, status


def dump_session(session: dict[str, Any]) -> str:
    return json.dumps(session, ensure_ascii=False)


def load_session(payload: str) -> dict[str, Any]:
    raw = (payload or "").strip()
    return json.loads(raw) if raw else {}


def current_scene(session: dict[str, Any]) -> dict[str, Any] | None:
    scenes = session.get("scenes") or []
    idx = int(session.get("current_index") or 0)
    if idx < 0 or idx >= len(scenes):
        return None
    return scenes[idx]


def choice_id(choice: dict[str, Any], idx: int) -> str:
    raw = str(choice.get("id") or "").strip()
    return raw or f"choice_{idx + 1}"


def render_comic_strip(session: dict[str, Any]) -> str:
    scenes = session.get("scenes") or []
    if not scenes:
        return "<div style='padding:16px;border:1px dashed #94a3b8;border-radius:16px;'>Generate a story to see the comic strip.</div>"
    cards: list[str] = []
    for idx, scene in enumerate(scenes, start=1):
        cards.append(
            f"""
            <div style="min-width:240px;max-width:260px;background:linear-gradient(160deg,#0f172a,#1d4ed8);color:white;
                        border-radius:20px;padding:16px;box-shadow:0 12px 30px rgba(15,23,42,0.18);">
              <div style="font-size:12px;opacity:0.85;margin-bottom:8px;">Scene {idx}</div>
              <div style="font-size:18px;font-weight:700;margin-bottom:8px;">{scene.get("title","")}</div>
              <div style="font-size:13px;line-height:1.45;background:rgba(255,255,255,0.12);padding:10px;border-radius:14px;">
                {scene.get("subject","")}
              </div>
              <div style="font-size:12px;line-height:1.5;margin-top:10px;opacity:0.92;">
                {scene.get("visual_prompt","")}
              </div>
            </div>
            """
        )
    return (
        "<div style='display:flex;gap:14px;overflow-x:auto;padding:8px 2px 14px 2px;'>"
        + "".join(cards)
        + "</div>"
    )


def gallery_items(session: dict[str, Any]) -> list[str]:
    paths = session.get("choice_image_paths") or []
    items: list[str] = []
    for path in paths:
        if path:
            items.append(path)
    return items


def selected_gallery_items(session: dict[str, Any]) -> list[str]:
    paths = session.get("choice_image_paths") or []
    scene = current_scene(session) or {}
    choices = scene.get("choices") or []
    selected_ids = list(session.get("selected_choice_ids") or [])
    path_by_id: dict[str, str] = {}
    for idx, choice in enumerate(choices):
        if idx < len(paths) and paths[idx]:
            path_by_id[choice_id(choice, idx)] = paths[idx]
    items: list[str] = []
    for choice_id in selected_ids:
        path = path_by_id.get(str(choice_id))
        if path:
            items.append(path)
    return items


def current_audio_path(session: dict[str, Any]) -> str | None:
    path = str(session.get("current_audio_path") or "").strip()
    return path or None


def render_scene_markdown(session: dict[str, Any]) -> str:
    if not session:
        return "Create flash cards to begin."

    scene = current_scene(session)
    if not scene:
        return "Create flash cards to begin."

    feedback = (session.get("last_feedback") or "").strip()
    feedback_block = f"\n\n### Voice\n{feedback}" if feedback else ""
    return (
        f"## Flash Cards for {session.get('event_name', 'the event')}\n\n"
        "Tap any picture the child wants to do. The voice will only play for the picture that was tapped."
        f"{feedback_block}"
    )


def render_history_markdown(session: dict[str, Any]) -> str:
    scene = current_scene(session) or {}
    choices = scene.get("choices") or []
    selected = set(session.get("selected_choice_ids") or [])
    labels = [
        str(choice.get("label") or "Choice")
        for idx, choice in enumerate(choices)
        if choice_id(choice, idx) in selected
    ]
    if not labels:
        return "No flash cards selected yet."
    return "### My Plan\n\n" + "\n".join(f"- {label}" for label in labels)


def _session_payload_for_save(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "story_title": session.get("story_title", ""),
        "hero_name": session.get("hero_name", ""),
        "event_name": session.get("event_name", ""),
        "session": session,
    }


def list_saved_story_names() -> list[str]:
    names: list[str] = []
    for path in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        names.append(path.stem)
    return names


def save_story_session(session: dict[str, Any], story_name: str) -> tuple[dict[str, Any], str, list[str], str]:
    if not session:
        raise ValueError("Generate a story before saving.")
    base_name = (story_name or session.get("story_title") or "living-story").strip()
    display_name = base_name
    slug = _slugify(base_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    final_name = f"{slug}-{timestamp}"
    path = SESSIONS_DIR / f"{final_name}.json"
    session["saved_story_name"] = display_name
    path.write_text(json.dumps(_session_payload_for_save(session), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names = list_saved_story_names()
    return session, f"Saved story as '{display_name}'.", names, final_name


def load_saved_story(choice: str) -> tuple[dict[str, Any], str]:
    selected = (choice or "").strip()
    if not selected:
        raise ValueError("Choose a saved story to load.")
    path = SESSIONS_DIR / f"{selected}.json"
    if not path.exists():
        raise FileNotFoundError("Saved story file not found.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    session = dict(payload.get("session") or {})
    session = ensure_session_media(session)
    name = str(session.get("saved_story_name") or payload.get("story_title") or selected)
    return session, f"Loaded story '{name}'."


def choice_labels(session: dict[str, Any]) -> list[str]:
    scene = current_scene(session)
    if not scene:
        return []
    return [str(choice.get("label") or "Choice") for choice in scene.get("choices", [])]


def _selected_choices(session: dict[str, Any]) -> list[dict[str, Any]]:
    scene = current_scene(session) or {}
    choices = list(scene.get("choices") or [])
    selected_ids = list(session.get("selected_choice_ids") or [])
    choice_by_id = {choice_id(choice, idx): choice for idx, choice in enumerate(choices)}
    selected: list[dict[str, Any]] = []
    for choice_id in selected_ids:
        choice = choice_by_id.get(str(choice_id))
        if choice:
            selected.append(choice)
    return selected


def _complete_plan_audio_text(session: dict[str, Any]) -> str:
    hero = str(session.get("hero_name") or "The child")
    event_name = str(session.get("event_name") or "the event")
    selected = _selected_choices(session)
    if not selected:
        raise ValueError("Select at least one flash card first.")

    plan_lines: list[str] = []
    for idx, choice in enumerate(selected):
        label = str(choice.get("label") or "").strip()
        if not label:
            continue
        if idx == 0:
            plan_lines.append(f"First, {hero} will {label[:1].lower() + label[1:]}.")
        elif idx == len(selected) - 1:
            plan_lines.append(f"Finally, {hero} will {label[:1].lower() + label[1:]}.")
        else:
            plan_lines.append(f"Then, {hero} will {label[:1].lower() + label[1:]}.")

    return f"Here is the complete plan for {event_name}. " + " ".join(plan_lines)


def _ensure_choice_audio(session: dict[str, Any], choice_slot: int) -> str:
    scene = current_scene(session)
    if not scene:
        raise ValueError("No flash cards found.")

    choices = scene.get("choices") or []
    if choice_slot < 0 or choice_slot >= len(choices):
        raise ValueError("That picture is not available.")

    choice = choices[choice_slot]
    audio_text = _choice_audio_text(
        choice,
        str(session.get("event_name") or "the event"),
        str(session.get("hero_name") or "The Hero"),
    )
    audio_path, alt_audio = _choice_audio_file_candidates(session, choice_slot, audio_text)
    if not (audio_path.exists() and audio_path.stat().st_size > 0):
        if not _generate_audio_with_openai(audio_text, audio_path):
            if _generate_audio_with_system_say(audio_text, alt_audio):
                audio_path = alt_audio
            elif _generate_audio_with_pyttsx3(audio_text, alt_audio):
                audio_path = alt_audio
            else:
                audio_path = alt_audio
    return str(audio_path) if audio_path.exists() else ""


def prewarm_choice_audio(session: dict[str, Any], limit: int = 2) -> tuple[dict[str, Any], str]:
    if not session:
        raise ValueError("Create flash cards first.")

    scene = current_scene(session)
    if not scene:
        raise ValueError("No flash cards found.")

    choices = list(scene.get("choices") or [])
    warmed = 0
    for idx in range(min(limit, len(choices))):
        path = _ensure_choice_audio(session, idx)
        if path:
            warmed += 1
    return session, f"Warmed audio for {warmed} flash cards."


def apply_choice(session: dict[str, Any], choice_slot: int) -> tuple[dict[str, Any], str]:
    if not session:
        raise ValueError("Create flash cards first.")

    scene = current_scene(session)
    if not scene:
        raise ValueError("No flash cards found.")

    choices = scene.get("choices") or []
    if choice_slot < 0 or choice_slot >= len(choices):
        raise ValueError("That picture is not available.")

    choice = choices[choice_slot]
    selected_ids = list(session.get("selected_choice_ids") or [])
    resolved_id = choice_id(choice, choice_slot)
    if resolved_id not in selected_ids:
        selected_ids.append(resolved_id)
    session["selected_choice_ids"] = selected_ids

    session["last_feedback"] = str(choice.get("coach_line") or "")
    history = list(session.get("history") or [])
    history.append(
        {
            "scene_title": scene.get("title", "Flash cards"),
            "choice_label": choice.get("label", "Choice"),
            "result": "Selected for the child's plan.",
        }
    )
    session["history"] = history

    session["current_audio_path"] = _ensure_choice_audio(session, choice_slot)
    return session, f"Added '{choice.get('label', 'choice')}' to the plan."


def play_complete_plan(session: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not session:
        raise ValueError("Create flash cards first.")

    audio_text = _complete_plan_audio_text(session)
    audio_path, alt_audio = _audio_file_candidates(session, "selected_plan", audio_text)
    if not (audio_path.exists() and audio_path.stat().st_size > 0):
        if not _generate_audio_with_openai(audio_text, audio_path):
            if _generate_audio_with_system_say(audio_text, alt_audio):
                audio_path = alt_audio
            elif _generate_audio_with_pyttsx3(audio_text, alt_audio):
                audio_path = alt_audio
            else:
                audio_path = alt_audio
    session["current_audio_path"] = str(audio_path) if audio_path.exists() else ""
    return session, "Playing the complete selected plan."
