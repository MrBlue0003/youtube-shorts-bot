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
import logging
import os
import random
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

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


def get_music_track() -> Path | None:
    """Return a random music file from music/, or None if folder is empty."""
    music_files = list(config.MUSIC_DIR.glob("*.mp3")) + list(config.MUSIC_DIR.glob("*.wav")) + list(config.MUSIC_DIR.glob("*.m4a"))
    if not music_files:
        logger.warning("No music files found in music/ — exporting without audio.")
        return None
    return random.choice(music_files)


def scale_filter(width: int = config.VIDEO_WIDTH, height: int = config.VIDEO_HEIGHT) -> str:
    """ffmpeg filtergraph to scale + pad to target resolution, centered."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={config.VIDEO_FPS}"
    )


# ── Core assembly ──────────────────────────────────────────────────────────────

def assemble(
    clip_paths: list[Path] | None = None,
    output_name: str | None = None,
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
                "-preset", "fast",
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
                    f"afade=t=in:st=0:d={fade_duration},"
                    f"afade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
                )
            else:
                audio_filter = (
                    f"atrim=duration={total_duration},"
                    f"afade=t=in:st=0:d={fade_duration},"
                    f"afade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
                )

            video_filter = (
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
            )

            run([
                FFMPEG, "-y",
                "-i", str(concat_out),
                "-i", str(music_path),
                "-filter_complex",
                f"[0:v]{video_filter}[v];[1:a]{audio_filter}[a]",
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", "slow",
                "-crf", "22",
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
            # No music — just apply video fades
            video_filter = (
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={total_duration - fade_duration}:d={fade_duration}"
            )
            run([
                FFMPEG, "-y",
                "-i", str(concat_out),
                "-vf", video_filter,
                "-c:v", "libx264",
                "-preset", "slow",
                "-crf", "22",
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
            "-c:v", "libx264", "-b:v", f"{video_bitrate}k",
            "-pass", "1", "-passlogfile", str(passlog),
            "-an", "-f", "null", "/dev/null",
        ])
        # Pass 2
        run([
            FFMPEG, "-y", "-i", str(src),
            "-c:v", "libx264", "-b:v", f"{video_bitrate}k",
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
