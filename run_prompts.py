"""Send a batch of /chat requests and save the responses to a file.

Reads request files and POSTs each to the backend, then writes a readable
Markdown report plus a raw JSON dump. Accepted inputs (each record is the same
`{"prompt": ..., "language": ...}` shape the API takes; a `"question"` key works
in place of `"prompt"`, so a Q&A JSONL can be replayed directly):

  * a single-object `.json`             -> one request
  * a `.json` array of objects          -> one request per element
  * a `.jsonl` / `.ndjson` file          -> one request per line (a "flurry")

Two ways to run:

  * Argument mode (CLI):
      python run_prompts.py                                   # all examples/*.json
      python run_prompts.py examples/tc_load_capacity.json    # specific files
      python run_prompts.py --url http://host:8079/chat --timeout 120 -o out/
  * IDE mode: just press Run — no arguments needed. It uses the SETTINGS block
      below, so edit those values to point at your endpoint / files.

Precedence: CLI arguments > the SETTINGS block > the CHAT_URL / CHAT_TIMEOUT
environment variables.
"""

import argparse
import datetime
import glob
import json
import os
import sys

import requests

# ---- SETTINGS (edit these to run straight from your IDE) --------------------
CHAT_URL   = os.environ.get("CHAT_URL", "http://localhost:8079/chat")
TIMEOUT    = int(os.environ.get("CHAT_TIMEOUT", "300"))  # cold model swaps can be slow
FILES      = []          # request files to send; [] = every examples/*.json
OUTPUT_DIR = "."         # where results_<timestamp>.md / .json are written
# -----------------------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Send a batch of /chat requests and save a Markdown + JSON report.")
    p.add_argument("files", nargs="*",
                   help="request JSON files (default: the SETTINGS FILES list, or "
                        "every examples/*.json if that is empty)")
    p.add_argument("--url", default=CHAT_URL,
                   help=f"backend /chat endpoint (default: {CHAT_URL})")
    p.add_argument("--timeout", type=int, default=TIMEOUT,
                   help=f"per-request timeout in seconds (default: {TIMEOUT})")
    p.add_argument("-o", "--output-dir", default=OUTPUT_DIR,
                   help="directory for the results files (default: current dir)")
    return p.parse_args(argv)


def load_requests(files):
    files = files or sorted(glob.glob("examples/*.json") + glob.glob("examples/*.jsonl"))
    out = []
    for f in files:
        if f.lower().endswith((".jsonl", ".ndjson")):
            # One JSON object per line -> one request each.
            with open(f, "r", encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    line = line.strip()
                    if line:
                        out.append((f"{f}#{i}", json.loads(line)))
        else:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):          # a JSON array of requests
                for i, payload in enumerate(data, 1):
                    out.append((f"{f}#{i}", payload))
            else:
                out.append((f, data))
    return out


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    reqs = load_requests(args.files or FILES)
    if not reqs:
        print("No request files found (examples/*.json).")
        return

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = os.path.join(args.output_dir, f"results_{ts}.md")
    json_path = os.path.join(args.output_dir, f"results_{ts}.json")
    raw = []

    with open(md_path, "w", encoding="utf-8") as out:
        out.write(f"# Chat results — {ts}\n\nEndpoint: `{args.url}`\n")
        for fname, payload in reqs:
            prompt = payload.get("prompt") or payload.get("question") or ""
            print(f"-> {fname}: {prompt[:60]}")
            out.write(f"\n---\n\n## {fname}\n\n")
            out.write(f"**Prompt:** {prompt}\n\n")
            out.write(f"**Language:** {payload.get('language', '(default)')}\n\n")
            try:
                body = {"prompt": prompt, "language": payload.get("language")}
                resp = requests.post(args.url, json=body, timeout=args.timeout)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                out.write(f"**ERROR:** {e}\n")
                print(f"   ERROR: {e}")
                raw.append({"file": fname, "request": payload, "error": str(e)})
                continue

            tools = [t.get("tool") for t in data.get("trace", [])]
            out.write(f"**Tools called:** {', '.join(tools) if tools else '(none)'}\n\n")
            out.write(f"**Response:**\n\n{data.get('response', '')}\n\n")

            srcs = data.get("sources", [])
            out.write(f"**Sources ({len(srcs)}):**\n")
            if srcs:
                for s in srcs:
                    bits = [f"{k}={s[k]}" for k in
                            ("file_name", "page_number", "product_type",
                             "language_code", "web_path") if s.get(k) is not None]
                    out.write(f"- {' · '.join(bits) if bits else json.dumps(s, ensure_ascii=False)}\n")
            else:
                out.write("(none)\n")
            out.write("\n")
            raw.append({"file": fname, "request": payload, "response": data})

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(raw, jf, ensure_ascii=False, indent=2)

    print(f"\nSaved -> {md_path}")
    print(f"Saved -> {json_path}")


if __name__ == "__main__":
    main()
