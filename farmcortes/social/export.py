"""
Export packages for Instagram Reels and Kwai.

Creates a .zip with:
- MP4 video (1080x1920, reuses existing file)
- .txt caption with title, description, CTA, hashtags
- .jpg cover (middle frame via ffmpeg)
"""
from __future__ import annotations

import os
import zipfile
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


EXPORT_BASE = Path("data/exports")
EXPORT_BASE.mkdir(parents=True, exist_ok=True)

# Platform limits (recommended)
PLATFORM_LIMITS = {
    "instagram": {"max_duration": 90, "name": "Instagram Reels"},
    "kwai": {"max_duration": 60, "name": "Kwai"},
}


def _get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def _extract_middle_frame(video_path: Path, output_path: Path) -> bool:
    """Extract middle frame as JPG cover using ffmpeg."""
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return False
    middle = duration / 2
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(middle), "-i", str(video_path),
                "-vframes", "1", "-q:v", "2", str(output_path)
            ],
            capture_output=True, timeout=30
        )
        return result.returncode == 0 and output_path.is_file()
    except Exception:
        return False


def _generate_caption(
    title: str,
    description: str,
    cta: str,
    hashtags: list[str],
    platform: str,
    duration: float,
) -> str:
    """Generate caption text file content."""
    lines = []
    lines.append(f"Título: {title}")
    lines.append("")
    lines.append(f"Descrição: {description}")
    lines.append("")
    lines.append(f"CTA: {cta}")
    lines.append("")
    if hashtags:
        lines.append("Hashtags: " + " ".join(f"#{tag.lstrip('#')}" for tag in hashtags))
    lines.append("")
    lines.append(f"Plataforma: {PLATFORM_LIMITS.get(platform, {}).get('name', platform.title())}")
    lines.append(f"Duração do vídeo: {duration:.1f}s")
    
    max_duration = PLATFORM_LIMITS.get(platform, {}).get("max_duration")
    if max_duration and duration > max_duration:
        lines.append(f"⚠️ AVISO: Duração excede o recomendado para {PLATFORM_LIMITS[platform]['name']} ({max_duration}s)")
    
    lines.append("")
    lines.append(f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("Origem: Farmcortes - Coletivo Cortes")
    return "\n".join(lines)


def _default_hashtags(title: str, platform: str) -> list[str]:
    """Generate relevant hashtags based on title/platform."""
    base = ["cortes", "coletivocortes", "viral", "brasil"]
    if platform == "instagram":
        base.extend(["reels", "reelsbrasil", "instareels"])
    elif platform == "kwai":
        base.extend(["kwai", "kwaibr", "viralbr"])
    
    # Extract keywords from title (simple approach)
    words = title.lower().split()
    keywords = [w for w in words if len(w) > 3 and w not in {"para", "com", "que", "uma", "ele", "ela", "isso", "aquilo"}]
    for kw in keywords[:3]:
        if kw not in base:
            base.append(kw)
    return base[:15]  # Limit hashtags


def build_pack(
    video_path: str | Path,
    meta: dict,
    platform: str = "instagram",
) -> str:
    """
    Build export package for Instagram Reels or Kwai.

    Args:
        video_path: Path to source MP4 (should be 1080x1920)
        meta: Dict with keys: title, description, cta (optional), hashtags (optional)
        platform: "instagram" or "kwai"

    Returns:
        Path to generated .zip file
    """
    platform = platform.lower()
    if platform not in PLATFORM_LIMITS:
        raise ValueError(f"Plataforma inválida: {platform}. Use 'instagram' ou 'kwai'")

    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise ValueError("Apenas arquivos MP4 são suportados")

    # Validate video dimensions (warn if not 1080x1920)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            width, height = map(int, result.stdout.strip().split(","))
            if width != 1080 or height != 1920:
                print(f"Aviso: Vídeo é {width}x{height}, recomendado 1080x1920 para {PLATFORM_LIMITS[platform]['name']}")
    except Exception:
        pass

    # Prepare metadata
    title = meta.get("title", "Corte selecionado")
    description = meta.get("description", "")
    cta = meta.get("cta", "Siga para mais cortes!")
    hashtags = meta.get("hashtags", _default_hashtags(title, platform))
    duration = _get_video_duration(video_path)

    # Create export directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:50]
    export_dir = EXPORT_BASE / platform / f"{timestamp}_{safe_title}"
    export_dir.mkdir(parents=True, exist_ok=True)

    # Copy video
    video_dest = export_dir / "video.mp4"
    shutil.copy2(video_path, video_dest)

    # Generate cover
    cover_path = export_dir / "cover.jpg"
    _extract_middle_frame(video_path, cover_path)

    # Generate caption.txt
    caption_content = _generate_caption(title, description, cta, hashtags, platform, duration)
    caption_path = export_dir / "caption.txt"
    caption_path.write_text(caption_content, encoding="utf-8")

    # Create zip
    zip_path = EXPORT_BASE / platform / f"{timestamp}_{safe_title}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(video_dest, "video.mp4")
        if cover_path.is_file():
            zf.write(cover_path, "cover.jpg")
        zf.write(caption_path, "caption.txt")

    # Cleanup temp directory
    shutil.rmtree(export_dir, ignore_errors=True)

    return str(zip_path)


def build_instagram_pack(video_path: str | Path, meta: dict) -> str:
    """Convenience function for Instagram Reels."""
    return build_pack(video_path, meta, "instagram")


def build_kwai_pack(video_path: str | Path, meta: dict) -> str:
    """Convenience function for Kwai."""
    return build_pack(video_path, meta, "kwai")
