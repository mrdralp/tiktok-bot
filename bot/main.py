"""Täglicher Lauf: nimmt die nächsten N unbenutzten Themen, rendert sie und
schreibt eine Queue-Datei, aus der die Metricool-Planung die Posts erstellt."""
import datetime as dt
import json
import os
import sys
from pathlib import Path

from render import render

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "data" / "topics.json"
STATE = ROOT / "data" / "state.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "mrdralp/tiktok-bot")
PER_DAY = int(os.environ.get("VIDEOS_PER_DAY", "2"))
SLOTS = os.environ.get("POST_TIMES", "12:30,19:00").split(",")


def main():
    fake = "--fake" in sys.argv
    topics = json.loads(TOPICS.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text()) if STATE.exists() else {"done": []}
    todo = [t for t in topics if t["id"] not in state["done"]][:PER_DAY]
    if not todo:
        print("Keine neuen Themen mehr übrig!")
        sys.exit(1)

    day = os.environ.get("RUN_DATE") or dt.date.today().isoformat()
    tag = f"videos-{day}"
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    queue = {"date": day, "tag": tag, "posts": []}
    for n, t in enumerate(todo):
        name = f"{day}-{n + 1}-{t['id']}.mp4"
        dur = render(t, out_dir / name, ROOT / "work" / t["id"], fake=fake)
        tags = " ".join("#" + h.lstrip("#") for h in t["hashtags"])
        queue["posts"].append({
            "id": t["id"],
            "time": SLOTS[n % len(SLOTS)],
            "file": name,
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
            "duration": round(dur, 1),
            "title": t["title"],
            "caption": f"{t['caption']} {tags}".strip(),
            "scheduled": False,
        })
        state["done"].append(t["id"])
        print(f"Fertig: {name} ({dur:.1f}s)")

    qdir = ROOT / "queue"
    qdir.mkdir(exist_ok=True)
    (qdir / f"{day}.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    state["remaining"] = len([t for t in topics if t["id"] not in state["done"]])
    STATE.write_text(json.dumps(state, indent=2))
    print("Verbleibende Themen:", state["remaining"])


if __name__ == "__main__":
    main()
