"""Turn a raw landscape segment into a vertical captioned Reel.

Three things happen here, and all three matter for whether the clip survives
platform review as transformed rather than reposted content: the reframe, the
burned captions, and the hook overlay.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

TARGET_W, TARGET_H = 1080, 1920


def probe_dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def transcribe(path: Path, model_size: str = "base") -> list[dict]:
    """Word-level timings via faster-whisper.

    Short segments only, which is why `base` on a CPU runner is fine: a 38
    second clip transcribes in a few seconds. Running this over a 5-hour VOD
    on the same hardware would take hours.
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append({"start": w.start, "end": w.end, "text": w.word.strip()})
    return words


def _ass_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words: list[dict], out_path: Path, words_per_line: int = 3) -> Path:
    """Write an ASS subtitle file: big, centred, a few words at a time.

    ASS rather than SRT because we need control of position and outline, and
    burned-in captions because Reels and Shorts are watched with sound off.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_W}
PlayResY: {TARGET_H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,DejaVu Sans,88,&H00FFFFFF,&H00000000,&H80000000,1,1,6,2,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i in range(0, len(words), words_per_line):
        group = words[i:i + words_per_line]
        if not group:
            continue
        text = " ".join(w["text"] for w in group).replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 0,{_ass_time(group[0]['start'])},{_ass_time(group[-1]['end'])},Pop,,0,0,0,,{text.upper()}"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def render_vertical(
    src: Path,
    dest: Path,
    subtitles: Path | None = None,
    hook_text: str | None = None,
    style: str = "blur",
) -> Path:
    """Reframe to 1080x1920 and burn in the captions.

    style 'blur' keeps the whole frame over a blurred backdrop, which is right
    for gameplay where cropping loses the action. style 'crop' centre-crops,
    which suits a facecam-dominant clip.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if style == "crop":
        # Both dimensions must be given alongside force_original_aspect_ratio,
        # otherwise the scale is ambiguous and the later crop overruns the frame.
        vf = (
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H}"
        )
    else:
        vf = (
            f"split=2[bg][fg];"
            f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},gblur=sigma=28[bgb];"
            f"[fg]scale={TARGET_W}:-2:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    if hook_text:
        safe = hook_text.replace(":", "\\:").replace("'", "")[:60]
        vf += (
            f",drawtext=text='{safe}':{_fontfile()}fontcolor=white:fontsize=72:"
            f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=200"
        )
    if subtitles:
        vf += f",subtitles='{_filter_path(subtitles)}'"

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         "-r", "30", str(dest)],
        check=True, capture_output=True,
    )
    return dest


# --------------------------------------------------------------------------
# Preferred path: the vendored stream-clips framing engine
# --------------------------------------------------------------------------

def _vendor_cfg(overrides: dict | None = None) -> dict:
    """Settings dict in the shape vendor/sc expects."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from vendor.sc.settings import DEFAULTS
        cfg = dict(DEFAULTS)
    except Exception:
        cfg = {}
    cfg.update({
        "width": TARGET_W, "height": TARGET_H,
        "layout": "auto",          # auto picks stack / crop / blur per clip
        "crf": 23, "x264_preset": "veryfast", "audio_kbps": 128,
    })
    cfg.update(overrides or {})
    return cfg


def render_framed(
    src: Path,
    dest: Path,
    subtitles: Path | None = None,
    title: str | None = None,
    cfg_overrides: dict | None = None,
    memory: dict | None = None,
) -> tuple[Path, str]:
    """Render using vendor/sc/frame.py, which picks the layout from the picture.

    Returns (path, look) where look is 'stack', 'crop' or 'blur'. 'stack' is the
    facecam-over-gameplay layout that most clip channels use, and it is the main
    reason to prefer this over render_vertical(): a blurred-pad render of a
    gameplay stream throws away the streamer's face, which is the part people
    actually react to.

    Falls back to render_vertical() if the vendored engine is unavailable (no
    numpy, no face model) so the pipeline never hard-fails on framing.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from vendor.sc import frame as vframe
        from vendor.sc.common import probe
    except Exception as exc:  # pragma: no cover - depends on optional deps
        print(f"[render] vendored framing unavailable ({exc}); using blur fallback")
        return render_vertical(src, dest, subtitles=subtitles, hook_text=title), "blur"

    cfg = _vendor_cfg(cfg_overrides)
    info = probe(src)
    length = float(info.get("duration") or 0) or 60.0

    try:
        graph, look, seam = vframe.plan(src, 0.0, length, info, cfg, memory if memory is not None else {})
    except Exception as exc:
        print(f"[render] frame.plan failed ({exc}); using blur fallback")
        return render_vertical(src, dest, subtitles=subtitles, hook_text=title), "blur"

    dest.parent.mkdir(parents=True, exist_ok=True)
    chain, out_label = graph, "framed"
    inputs = ["-i", str(src)]

    if subtitles:
        chain += f";[framed]subtitles='{_filter_path(subtitles)}'[subbed]"
        out_label = "subbed"

    if title:
        try:
            from vendor.sc import titlecard
            png, _, th = titlecard.render(title, cfg, dest.parent / f"{dest.stem}.title.png")
            y = int(seam - th / 2) if seam else int(TARGET_H * cfg["title_top"])
            inputs += ["-i", str(png)]
            chain += f";[{out_label}][1:v]overlay=(W-w)/2:{max(0, y)}[titled]"
            out_label = "titled"
        except Exception as exc:
            print(f"[render] title bar skipped ({exc})")

    chain += f";[0:a]loudnorm=I={cfg['loudness']}:TP=-1.5:LRA=11,aresample=48000[aud]"

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-nostdin", *inputs,
         "-filter_complex", chain,
         "-map", f"[{out_label}]", "-map", "[aud]",
         "-c:v", "libx264", "-crf", str(cfg["crf"]), "-preset", cfg["x264_preset"],
         "-c:a", "aac", "-b:a", f"{cfg['audio_kbps']}k",
         "-movflags", "+faststart", str(dest)],
        check=True, capture_output=True,
    )
    return dest, look


def _filter_path(p: Path) -> str:
    """ffmpeg filter args treat ':' as a separator, so 'D:/x.ass' must be 'D\:/x.ass'."""
    return p.as_posix().replace(":", "\:")


def _fontfile() -> str:
    """Windows ffmpeg builds have no fontconfig, so drawtext needs an explicit
    font file. Linux runners (fonts-dejavu) find one by themselves."""
    import os
    if os.name == "nt":
        for f in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"):
            if os.path.exists(f):
                return f"fontfile='{_filter_path(Path(f))}':"
    return ""
