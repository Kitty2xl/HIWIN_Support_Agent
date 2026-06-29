"""Send a batch of /chat requests and save the responses to a file.

Reads request JSON files (the same `{"prompt": ..., "language": ...}` shape the
API takes), POSTs each to the backend, and writes a readable Markdown report
plus a raw JSON dump.

Usage:
  python run_prompts.py                       # every examples/*.json
  python run_prompts.py examples/tc_load_capacity.json   # only the named files
  set CHAT_URL=http://localhost:8079/chat     # override the endpoint (default below)
"""

import datetime
import glob
import json
import os
import sys

import requests

CHAT_URL = os.environ.get("CHAT_URL", "http://localhost:8079/chat")
TIMEOUT = int(os.environ.get("CHAT_TIMEOUT", "300"))  # cold model swaps can be slow


def load_requests(args):
    files = args if args else sorted(glob.glob("examples/*.json"))
    out = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            out.append((f, json.load(fh)))
    return out


def main():
    reqs = load_requests(sys.argv[1:])
    if not reqs:
        print("No request files found (examples/*.json).")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = f"results_{ts}.md"
    json_path = f"results_{ts}.json"
    raw = []

    with open(md_path, "w", encoding="utf-8") as out:
        out.write(f"# Chat results — {ts}\n\nEndpoint: `{CHAT_URL}`\n")
        for fname, payload in reqs:
            prompt = payload.get("prompt", "")
            print(f"-> {fname}: {prompt[:60]}")
            out.write(f"\n---\n\n## {fname}\n\n")
            out.write(f"**Prompt:** {prompt}\n\n")
            out.write(f"**Language:** {payload.get('language', '(default)')}\n\n")
            try:
                resp = requests.post(CHAT_URL, json=payload, timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                out.write(f"**ERROR:** {e}\n")
                print(f"   ERROR: {e}")
                raw.append({"file": fname, "request": payload, "error": str(e)})
                continue

            tools = [t.get("tool") for t in data.get("trace", [])]
            out.write(f"**Tools called:** {', '.join(tools) if tools else '(none)'}\n\n")
            out.write(f"**Response:**\n\n{data.get('response', '')}\n")
            raw.append({"file": fname, "request": payload, "response": data})

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(raw, jf, ensure_ascii=False, indent=2)

    print(f"\nSaved -> {md_path}")
    print(f"Saved -> {json_path}")


if __name__ == "__main__":
    main()
