"""Build the browser version of the game into docs/ (served by GitHub Pages).

    pip install pygbag
    python build_web.py

Needs ffmpeg on PATH. The script copies the game into a temporary folder,
converts every .wav/.mp3 to .ogg (browsers play Ogg Vorbis reliably and the
download gets much smaller), runs pygbag on that copy, and puts index.html,
the game archive and the icon into docs/. The desktop game is not changed.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "f1race"
SKIP_DIRS = {"docs", "build", ".git", ".idea", "__pycache__"}
SKIP_FILES = {"build_web.py", "screenshot.png"}


def copy_game(dst: Path):
    for src in ROOT.rglob("*"):
        rel = src.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or rel.name in SKIP_FILES:
            continue
        if src.is_dir():
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() in (".wav", ".mp3"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
                            "-c:a", "libvorbis", "-q:a", "4", str(out.with_suffix(".ogg"))], check=True)
        elif src.suffix.lower() in (".py", ".png", ".jpg", ".ttf", ".otf", ".ogg", ".json", ".txt", ".csv"):
            shutil.copy2(src, out)


def main():
    extra = sys.argv[1:]  # e.g. --template game.tmpl --icon icon.png
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / NAME
        copy_game(game)
        subprocess.run([sys.executable, "-m", "pygbag", "--build", "--title", "F1 Race Road",
                        *extra, str(game)], check=True)
        web = game / "build" / "web"
        docs = ROOT / "docs"
        docs.mkdir(exist_ok=True)
        for f in ("index.html", f"{NAME}.tar.gz", "favicon.png"):
            if (web / f).exists():
                shutil.copy2(web / f, docs / f)
    print("done ->", docs)


if __name__ == "__main__":
    main()
