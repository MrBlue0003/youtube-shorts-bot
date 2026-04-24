"""
video_generate.py — AI-agnostic video generation interface for the YouTube Shorts bot.

Current backend: Runway Gen-4.5  (scripts/runway_generate.py)

── HOW TO SWAP AI BACKEND ──────────────────────────────────────────────────────
To switch from Runway to a different AI provider (Kling, Pika, Sora, etc.):

  1. Create  scripts/<provider>_generate.py
  2. Implement the same public interface:

       def generate_clips(
           prompt_entry: dict | None = None,
           clip_count: int = ...,
           duration: int = ...,
       ) -> list[Path]:
           ...

       def pick_prompt() -> dict:
           ...   # must return a prompt dict with keys: id, animal, action, prompt

       def get_total_credits_used() -> int:
           ...   # return 0 if the provider has no credit concept

  3. Update the import block below (one line change):

       from scripts.<provider>_generate import (   # ← swap this
           generate_clips  as _backend_generate,
           pick_prompt,
           get_total_credits_used,
       )

  4. Done — assemble.py, upload.py, main.py are all unchanged.
─────────────────────────────────────────────────────────────────────────────────
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── AI Backend ────────────────────────────────────────────────────────────────
# ↓↓↓  Change this import to swap the video AI provider  ↓↓↓
from scripts.runway_generate import (   # noqa: E402
    generate_clips  as _backend_generate,
    pick_prompt,
    get_total_credits_used,
)
# ↑↑↑  One-line swap point  ↑↑↑


# ── Public interface ──────────────────────────────────────────────────────────

def generate_clips(
    prompt_entry: dict | None = None,
    clip_count: int = 2,
    duration: int = 10,
) -> list[Path]:
    """Generate video clips for a Short.

    Wraps the AI backend with an automatic retry on a fresh prompt if the
    first generation attempt fails entirely.

    Args:
        prompt_entry: Prompt dict from animal_prompts.json.
                      A random prompt is picked if None.
        clip_count:   Number of clips to generate (default: 2).
        duration:     Duration of each clip in seconds (default: 10).

    Returns:
        List of Paths to downloaded video clip files.

    Raises:
        RuntimeError: If both generation attempts fail.
    """
    # Attempt 1 — use the provided (or auto-picked) prompt
    try:
        clips = _backend_generate(
            prompt_entry=prompt_entry,
            clip_count=clip_count,
            duration=duration,
        )
        if clips:
            return clips
        raise RuntimeError("Backend returned empty clip list.")
    except Exception as err1:
        logger.warning(f"Generation attempt 1 failed: {err1}")

    # Attempt 2 — pick a completely fresh prompt and try once more
    logger.info("Retrying generation with a different prompt…")
    try:
        fresh = pick_prompt()
        logger.info(f"Retry prompt: #{fresh['id']} — {fresh['animal']} / {fresh['action']}")
        clips = _backend_generate(
            prompt_entry=fresh,
            clip_count=clip_count,
            duration=duration,
        )
        if clips:
            return clips
        raise RuntimeError("Backend returned empty clip list on retry.")
    except Exception as err2:
        logger.error(f"Generation attempt 2 also failed: {err2}")
        raise RuntimeError(
            f"Both generation attempts failed.\n"
            f"  Attempt 1: {err1}\n"
            f"  Attempt 2: {err2}"
        ) from err2
