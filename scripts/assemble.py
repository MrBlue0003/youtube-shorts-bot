"""
assemble.py — Concatenate clips, add music, and export a YouTube Short via ffmpeg.

Pipeline:
  1. Collect clips from videos/ (or accept an explicit list)
  2. Scale each to 1080×1920, normalize fps
  3. Concatenate with ffmpeg concat demuxer
  4. Pick a random music track from music/, loop it if needed
  5. Apply 1-second fade-in and fade-out on both video and audio
  6. Export H.264 MP4 optimized for YouTube Shorts (≤60 MB)
"""

import glob
import json
import logging
import os
import platform
import random
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# ── Per-animal emoji ──────────────────────────────────────────────────────────
_ANIMAL_EMOJI = {
    "cat": "🐱", "dog": "🐶", "capybara": "🦫", "panda": "🐼",
    "bunny": "🐰", "fox": "🦊", "bear": "🐻", "penguin": "🐧",
    "koala": "🐨", "frog": "🐸", "duck": "🦆", "chick": "🐣", "lamb": "🐑",
}

# ── Hook text per action (shown top of screen for first 2.5s) ─────────────────
_ACTION_HOOKS = {
    "cooking":             "When your {animal} decides to cook",
    "dancing":             "This {animal} can't stop dancing",
    "cozy_sleep":          "The coziest {animal} you'll see today",
    "little_treat":        "A little treat for this {animal}",
    "exaggerated_reaction":"This {animal}'s reaction though...",
    "birthday":            "Happy birthday little {animal}!",
    "cozy":                "The most cozy {animal} on the internet",
    "eating":              "This {animal} really loves food",
    "playing":             "Playtime for this adorable {animal}",
    "gardening":           "This {animal} has a green thumb",
    "reading":             "Bookworm {animal} spotted",
    "yoga":                "Zen {animal} unlocked",
    "baking":              "Master baker {animal} at work",
    "painting":            "Artist {animal} creating magic",
    "stargazing":          "This {animal} loves the night sky",
}
_DEFAULT_HOOK = "Meet today's cutest {animal}"


def _detect_font() -> str:
    """Return an ffmpeg-safe font path for the current OS."""
    if platform.system() == "Windows":
        return "C\\:/Windows/Fonts/arialbd.ttf"
    candidates = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


_FONT = _detect_font()


def _esc(s: str) -> str:
    """Escape text for ffmpeg drawtext."""
    return (
        s.replace("\\", "\\\\")
         .replace("'",  "\u2019")
         .replace('"',  "\u201c")
         .replace(":",  "\\:")
         .replace("[",  "\\[")
         .replace("]",  "\\]")
         .replace(",",  " ")
         .replace(";",  " ")
         .replace("%",  "%%")
         .replace("$",  "\\$")
         .replace("\n", " ")
    )

# ── Helpers ────────────────────────────────────────────────────────────────────

FFMPEG  = config.FFMPEG_BIN
FFPROBE = config.FFPROBE_BIN


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    logger.debug("ffmpeg: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        logger.error(result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode})")
    return result


# fast preset + crf 26: better quality, ~30% smaller than veryfast/crf22
_ENCODE_FLAGS = ["-threads", "2", "-preset", "fast", "-bufsize", "4M"]


def get_duration(path: Path) -> float:
    """Return video duration in seconds using ffprobe."""
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=duration",
        "-of", "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        cmd2 = [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        return float(result2.stdout.strip() or "0")


USED_MUSIC_FILE = config.LOGS_DIR / "used_music.json"


def _load_used_tracks() -> list[str]:
    if USED_MUSIC_FILE.exists():
        with open(USED_MUSIC_FILE) as f:
            return json.load(f).get("used", [])
    return []


def _save_used_track(name: str, total: int) -> None:
    used = _load_used_tracks()
    used.append(name)
    # Pastreaza doar ultimele (total - 1) track-uri folosite
    # Astfel mereu exista cel putin unul disponibil
    used = used[-(max(total - 1, 1)):]
    with open(USED_MUSIC_FILE, "w") as f:
        json.dump({"used": used}, f, indent=2)


def get_music_track() -> Path | None:
    """Return a music file that hasn't been used recently, rotating through all tracks."""
    music_files = (
        list(config.MUSIC_DIR.glob("*.mp3")) +
        list(config.MUSIC_DIR.glob("*.wav")) +
        list(config.MUSIC_DIR.glob("*.m4a"))
    )
    if not music_files:
        logger.warning("No music files found in music/ — exporting without audio.")
        return None

    used = _load_used_tracks()
    used_names = set(used)

    # Alege din track-urile nefolosite inca
    available = [f for f in music_files if f.name not in used_names]

    # Daca toate au fost folosite, reseteaza si ia din toate
    if not available:
        logger.info("Toate track-urile au fost folosite — resetez rotatia.")
        available = music_files

    chosen = random.choice(available)
    _save_used_track(chosen.name, len(music_files))
    logger.info(f"Music track ales: {chosen.name} ({len(available)} disponibile din {len(music_files)})")
    return chosen


def scale_filter(width: int = config.VIDEO_WIDTH, height: int = config.VIDEO_HEIGHT) -> str:
    """ffmpeg filtergraph to scale + pad to target resolution, centered."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={config.VIDEO_FPS}"
    )


# ── Core assembly ──────────────────────────────────────────────────────────────

def _build_overlay(animal: str, action: str, duration: float) -> str:
    """Build minimal overlay filters: hook text (2.5s) + name bar + watermark."""
    emoji    = _ANIMAL_EMOJI.get(animal, "🐾")
    hook_tpl = _ACTION_HOOKS.get(action, _DEFAULT_HOOK)
    hook_txt = _esc(hook_tpl.format(animal=animal.title()))
    name_txt = _esc(f"{emoji} {animal.title()}")
    hook_end = min(2.5, duration * 0.45)  # never more than 45% of video

    return ",".join([
        # ── Subtle bottom gradient for name readability ──────────────────
        "drawbox=x=0:y=1780:w=1080:h=140:color=black@0.45:t=fill",

        # ── Animal name centred in bottom bar ────────────────────────────
        f"drawtext=fontfile='{_FONT}':text='{name_txt}'"
        f":fontsize=52:fontcolor=white:x=(w-text_w)/2:y=1820"
        f":borderw=2:bordercolor=black@0.60",

        # ── Hook text — top of screen, first {hook_end}s only ────────────
        f"drawtext=fontfile='{_FONT}':text='{hook_txt}'"
        f":fontsize=44:fontcolor=white:x=(w-text_w)/2:y=72"
        f":borderw=3:bordercolor=black@0.70"
        f":enable='between(t,0,{hook_end:.2f})'",

        # ── CuteDaily watermark — subtle bottom-right ─────────────────────
        f"drawtext=fontfile='{_FONT}':text='CuteDaily'"
        f":fontsize=22:fontcolor=white@0.28:x=w-text_w-16:y=h-34"
        f":borderw=1:bordercolor=black@0.10",
    ])


def assemble(
    clip_paths: list[Path] | None = None,
    output_name: str | None = None,
    prompt_entry: dict | None = None,
) -> Path:
    """
    Assemble clips into a Short.

    Args:
        clip_paths: Explicit list of clip files. If None, uses all files in videos/.
        output_name: Output filename stem (without extension). Auto-generated if None.

    Returns:
        Path to the finished Short MP4.
    """
    # ── Collect clips ──────────────────────────────────────────────────────────
    if clip_paths is None:
        raw = sorted(config.VIDEOS_DIR.glob("*.mp4"), key=os.path.getmtime)
        clip_paths = raw
    if not clip_paths:
        raise FileNotFoundError("No clip files found. Run runway_generate.py first.")

    logger.info(f"Assembling {len(clip_paths)} clips…")

    # ── Work in a temp directory ───────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # Step 1: Scale each clip individually to target resolution
        scaled_clips: list[Path] = []
        for i, clip in enumerate(clip_paths):
            scaled = tmp / f"scaled_{i:02d}.mp4"
            run([
                FFMPEG, "-y",
                "-i", str(clip),
                "-vf", scale_filter(),
                "-c:v", "libx264",
                *_ENCODE_FLAGS,
                "-crf", "23",
                "-an",   # strip audio from individual clips
                "-movflags", "+faststart",
                str(scaled),
            ])
            scaled_clips.append(scaled)

        # Step 2: Build concat list file
        concat_list = tmp / "concat.txt"
        with open(concat_list, "w") as f:
            for sc in scaled_clips:
                f.write(f"file '{sc}'\n")

        # Step 3: Concatenate
        concat_out = tmp / "concat.mp4"
        run([
            FFMPEG, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(concat_out),
        ])

        total_duration = get_duration(concat_out)
        logger.info(f"Concatenated duration: {total_duration:.1f}s")

        # Build optional overlay (requires prompt_entry)
        animal = (prompt_entry or {}).get("animal", "")
        action = (prompt_entry or {}).get("action", "")
        overlay = _build_overlay(animal, action, total_duration) if animal else ""

        # Clamp to max Short duration
        if total_duration > config.SHORT_MAX_DURATION:
            trim_out = tmp / "trimmed.mp4"
            run([
                FFMPEG, "-y",
                "-i", str(concat_out),
                "-t", str(config.SHORT_MAX_DURATION),
                "-c", "copy",
                str(trim_out),
            ])
            concat_out = trim_out
            total_duration = config.SHORT_MAX_DURATION

        # Step 4: Add music
        music_path = get_music_track()
        fade_duration = 1.0  # seconds for fade in/out

        if output_name is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_name = f"short_{ts}"

        output_path = config.SHORTS_DIR / f"{output_name}.mp4"

        if music_path:
            music_duration = get_duration(music_path)
            # Build audio filter: loop if music shorter than video, then trim
            if music_duration < total_duration:
                loop_times = int(total_duration / music_duration) + 1
                audio_filter = (
                    f"aloop=loop={loop_times}:size=2e+09,"
                    f"atrim=duration={total_duration},"
                    f"loudnorm=I=-14:LRA=11:TP=-1,"
                    f"afade=t=in:st=0:d={fade_duration},"
                    f"afade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
                )
            else:
                audio_filter = (
                    f"atrim=duration={total_duration},"
                    f"loudnorm=I=-14:LRA=11:TP=-1,"
                    f"afade=t=in:st=0:d={fade_duration},"
                    f"afade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
                )

            video_filter = ",".join(filter(None, [
                # Warm kawaii color grade — subtle saturation + slight warmth
                "eq=saturation=1.08:gamma_r=1.03:gamma_g=1.02",
                # Vignette — dark edges pull focus to centre animation
                "vignette=angle=PI/5:mode=forward",
                f"fade=t=in:st=0:d={fade_duration}",
                f"fade=t=out:st={total_duration - fade_duration}:d={fade_duration}",
                overlay,
            ]))

            run([
                FFMPEG, "-y",
                "-i", str(concat_out),
                "-i", str(music_path),
                "-filter_complex",
                f"[0:v]{video_filter}[v];[1:a]{audio_filter}[a]",
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                *_ENCODE_FLAGS,
                "-crf", "26",
                "-profile:v", "high",
                "-level", "4.1",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-movflags", "+faststart",
                "-t", str(total_duration),
                str(output_path),
            ])
        else:
            # No music — just apply video fades + overlay
            video_filter = ",".join(filter(None, [
                "eq=saturation=1.08:gamma_r=1.03:gamma_g=1.02",
                "vignette=angle=PI/5:mode=forward",
                f"fade=t=in:st=0:d={fade_duration}",
                f"fade=t=out:st={total_duration - fade_duration}:d={fade_duration}",
                overlay,
            ]))
            run([
                FFMPEG, "-y",
                "-i", str(concat_out),
                "-vf", video_filter,
                "-c:v", "libx264",
                *_ENCODE_FLAGS,
                "-crf", "26",
                "-profile:v", "high",
                "-level", "4.1",
                "-movflags", "+faststart",
                "-t", str(total_duration),
                str(output_path),
            ])

    # ── Size check ─────────────────────────────────────────────────────────────
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"Output: {output_path.name} ({size_mb:.1f} MB)")

    if size_mb > 60:
        logger.warning(f"File is {size_mb:.1f} MB — recompressing to target ≤60 MB…")
        output_path = _compress_to_target(output_path, target_mb=58)

    return output_path


def _compress_to_target(src: Path, target_mb: float) -> Path:
    """Two-pass compress src to meet target file size."""
    duration = get_duration(src)
    target_bits = target_mb * 1024 * 1024 * 8
    video_bitrate = int((target_bits / duration) * 0.85 / 1000)  # kbps, 85% for video
    audio_bitrate = 96  # kbps
    video_bitrate = max(video_bitrate - audio_bitrate, 500)

    recompressed = src.with_stem(src.stem + "_recompressed")

    with tempfile.TemporaryDirectory() as tmp:
        passlog = Path(tmp) / "ffmpeg2pass"
        # Pass 1
        run([
            FFMPEG, "-y", "-i", str(src),
            "-c:v", "libx264", *_ENCODE_FLAGS, "-b:v", f"{video_bitrate}k",
            "-pass", "1", "-passlogfile", str(passlog),
            "-an", "-f", "null", "/dev/null",
        ])
        # Pass 2
        run([
            FFMPEG, "-y", "-i", str(src),
            "-c:v", "libx264", *_ENCODE_FLAGS, "-b:v", f"{video_bitrate}k",
            "-pass", "2", "-passlogfile", str(passlog),
            "-c:a", "aac", "-b:a", f"{audio_bitrate}k",
            "-movflags", "+faststart",
            str(recompressed),
        ])

    size_mb = recompressed.stat().st_size / (1024 * 1024)
    logger.info(f"Recompressed: {recompressed.name} ({size_mb:.1f} MB)")
    return recompressed


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    result = assemble()
    print(f"\nShort ready: {result}")
