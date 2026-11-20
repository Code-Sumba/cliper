"""Readiness check: what works, what is missing, and exactly what to set.

    python -m cliper.doctor

Run it before every real run. It never contacts a platform or spends quota -
it only checks that tools are installed and credentials are present.
"""
from __future__ import annotations

import shutil
import sys

from .config import auto_publish_enabled, env, load_creators
from .platforms import all_publishers
from .storage import get_storage

OK, WARN, BAD = "  ok  ", " warn ", " MISS "


def line(state: str, what: str, detail: str = "") -> None:
    print(f"[{state}] {what}" + (f"  -  {detail}" if detail else ""))


def check_tools() -> int:
    print("\nTOOLS")
    bad = 0
    for tool, why in (
        ("ffmpeg", "cutting and rendering"),
        ("ffprobe", "reading clip dimensions"),
        ("yt-dlp", "downloading segments, audio and YouTube chat"),
    ):
        if shutil.which(tool):
            line(OK, tool, why)
        else:
            line(BAD, tool, f"needed for {why}")
            bad += 1
    if shutil.which("TwitchDownloaderCLI"):
        line(OK, "TwitchDownloaderCLI", "Twitch chat download")
    else:
        line(WARN, "TwitchDownloaderCLI", "Twitch creators will be skipped")
    return bad


def check_python() -> int:
    print("\nPYTHON PACKAGES")
    bad = 0
    for mod, why, hard in (
        ("yaml", "reading creators.yml", True),
        ("requests", "API calls", True),
        ("numpy", "volume curve and framing", True),
        ("cv2", "face detection for the stack layout", False),
        ("PIL", "title cards", False),
        ("faster_whisper", "captions", False),
        ("boto3", "uploading clips to a bucket", False),
        ("googleapiclient", "YouTube upload", False),
    ):
        try:
            __import__(mod)
            line(OK, mod, why)
        except ImportError:
            line(BAD if hard else WARN, mod, why)
            bad += hard
    return bad


def check_face_model() -> None:
    print("\nFACE MODEL")
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
        from vendor.sc.common import MODELS
        model = MODELS / "face_yunet.onnx"
        if model.exists() and model.stat().st_size > 100_000:
            line(OK, "face_yunet.onnx", f"{model.stat().st_size // 1024} KB")
        elif model.exists():
            line(WARN, "face_yunet.onnx",
                 "file is tiny - probably a Git LFS pointer, re-download from "
                 "media.githubusercontent.com")
        else:
            line(WARN, "face_yunet.onnx",
                 "absent; clips fall back to the blur layout (still works)")
    except Exception as exc:
        line(WARN, "vendored framing engine", str(exc)[:80])


def check_storage() -> None:
    print("\nSTORAGE")
    s = get_storage()
    ok, why = s.configured()
    line(OK if ok else WARN, f"backend: {s.name}", why or "ready")
    if not ok:
        line(WARN, "consequence", "Instagram and other URL-fetching platforms will be skipped")


def check_platforms() -> None:
    print("\nPLATFORMS")
    any_ready = False
    for name, pub in all_publishers().items():
        ok, why = pub.configured()
        any_ready = any_ready or ok
        line(OK if ok else WARN, f"{name:<10} {pub.label}", why or "ready")
    if not any_ready:
        line(WARN, "nothing can publish yet", "add credentials to repo secrets")


def check_sources() -> None:
    print("\nSTREAM SOURCES")
    line(OK if env("TWITCH_CLIENT_ID") and env("TWITCH_CLIENT_SECRET") else WARN,
         "Twitch", "live detection and VOD listing")
    line(OK if env("YOUTUBE_API_KEY") else WARN,
         "YouTube API key", "live detection (chat replay needs no key)")


def check_creators() -> None:
    print("\nCREATORS")
    creators = load_creators()
    if not creators:
        line(BAD, "creators.yml", "no creators configured")
        return
    for c in creators:
        state = OK if c.may_auto_publish else WARN
        plats = ", ".join(c.platforms) if c.platforms else "all enabled"
        line(state, f"{c.handle:<14} permission={c.permission}",
             f"{plats}; {c.post_per_week}/week")


def main() -> int:
    print("=" * 62)
    print("  Cliper readiness check")
    print("=" * 62)
    bad = check_tools() + check_python()
    check_face_model()
    check_sources()
    check_storage()
    check_platforms()
    check_creators()

    print("\nSAFETY")
    line(WARN if auto_publish_enabled() else OK,
         f"AUTO_PUBLISH = {auto_publish_enabled()}",
         "clips post with no human check" if auto_publish_enabled()
         else "clips wait for your approval")

    print("\n" + "=" * 62)
    if bad:
        print(f"  {bad} blocking problem(s). Fix those first.")
    else:
        print("  No blocking problems. Anything marked 'warn' just means that")
        print("  feature stays off until you add its credentials.")
    print("=" * 62)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
