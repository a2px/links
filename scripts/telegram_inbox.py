"""Read new Telegram messages to the bot and add (or remove) the links in them.

Only the owner's chat (TELEGRAM_OWNER_ID) is listened to. Messages:
  https://example.com            add it, sorted automatically
  fonts https://example.com      add it to an existing folder (Fonts)
  #new name https://example.com  add it to a new folder called "New name"
  any other note + link          the note helps the automatic sorting
  remove https://example.com     take it off the board
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board  # noqa: E402

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
STATE_FILE = board.ROOT / "state.json"
PAGE_URL = os.environ.get("PAGE_URL", "")
URL_RE = re.compile(r"(https?://[^\s<>\"']+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/[^\s<>\"']*)?)", re.I)


def api(method, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["result"]


def reply(chat_id, text):
    try:
        api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
    except Exception as e:
        print(f"reply failed: {e}")


def urls_in(message):
    text = message.get("text") or message.get("caption") or ""
    found = []
    for ent in (message.get("entities") or []) + (message.get("caption_entities") or []):
        if ent["type"] == "text_link":
            found.append(ent["url"])
        elif ent["type"] == "url":
            found.append(text[ent["offset"] : ent["offset"] + ent["length"]])
    if not found:
        found = [m.group(0) for m in URL_RE.finditer(text)]
    rest = text
    for u in found:
        rest = rest.replace(u, " ")
    return list(dict.fromkeys(u.rstrip(".,);]") for u in found)), " ".join(rest.split())


def pick_folder(words):
    """A folder named outright: '#new folder', or words matching an existing folder ('font' -> Fonts)."""
    if words.startswith("#"):
        name = words[1:].strip()
        return name[:1].upper() + name[1:] if name else None
    folders = set(board.DEFAULT_FOLDERS) | {l["folder"] for l in board.load_links()}
    w = words.lower().rstrip("s")
    return next((f for f in folders if f.lower().rstrip("s") == w), None) if w else None


def handle(message):
    chat_id = message["chat"]["id"]
    if not OWNER or str(chat_id) != OWNER:
        print(f"ignoring message from chat {chat_id}")
        return False
    urls, words = urls_in(message)
    if not urls:
        reply(chat_id, "Send me a link and I'll add it to your board.")
        return False
    changed = False
    if words.lower() in ("remove", "delete"):
        for u in urls:
            gone = board.remove_link(u)
            changed |= bool(gone)
            reply(chat_id, f"Removed {gone[0]['name']}." if gone else f"{u} isn't on the board.")
        return changed
    folder = pick_folder(words)
    for u in urls:
        try:
            rec, created = board.add_link(u, folder=folder, hint=words or None)
        except Exception as e:
            print(f"could not add {u}: {e}")
            reply(chat_id, f"Couldn't open {u}, so it wasn't added. Check the link and send it again.")
            continue
        changed = True
        if created:
            reply(chat_id, f"Added {rec['name']} to {rec['folder']}. {PAGE_URL}".strip())
        elif folder:
            reply(chat_id, f"Moved {rec['name']} to {rec['folder']}.")
        else:
            reply(chat_id, f"{rec['name']} is already in {rec['folder']}.")
    return changed


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"offset": 0}
    updates = api("getUpdates", offset=state["offset"], timeout=0, allowed_updates=["message", "channel_post"])
    changed = False
    for u in updates:
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post")
        if msg:
            changed |= handle(msg)
    if updates:
        STATE_FILE.write_text(json.dumps(state) + "\n")
    print(f"{len(updates)} new messages, board changed: {changed}")


if __name__ == "__main__":
    main()
