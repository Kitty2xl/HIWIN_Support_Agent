"""Send a batch of /chat requests and save the responses to a file.

Reads request files and POSTs each to the backend, then writes a raw JSON dump
(each record: request, per-request timing, and the full response — answer,
sources, trace, metrics). Accepted inputs (each record is the same
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
import time

import requests

# ---- SETTINGS (edit these to run straight from your IDE) --------------------
CHAT_URL   = os.environ.get("CHAT_URL", "http://localhost:8079/chat")
TIMEOUT    = int(os.environ.get("CHAT_TIMEOUT", "300"))  # cold model loads can be slow
FILES      = []          # request files to send; [] = every examples/*.json
OUTPUT_DIR = "."         # where results_<timestamp>.json is written
# -----------------------------------------------------------------------------


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Send a batch of /chat requests and save a JSON report.")
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
    json_path = os.path.join(args.output_dir, f"results_{ts}.json")
    raw = []

    for fname, payload in reqs:
        prompt = payload.get("prompt") or payload.get("question") or ""
        print(f"-> {fname}: {prompt[:60]}")
        started = time.perf_counter()
        try:
            body = {"prompt": prompt, "language": payload.get("language")}
            resp = requests.post(args.url, json=body, timeout=args.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            elapsed = round(time.perf_counter() - started, 2)
            print(f"   ERROR after {elapsed}s: {e}")
            raw.append({"file": fname, "request": payload,
                        "elapsed_s": elapsed, "error": str(e)})
            continue
        elapsed = round(time.perf_counter() - started, 2)

        m = data.get("metrics") or {}
        tools = [t.get("tool") for t in data.get("trace", [])]
        srcs = data.get("sources", [])
        print(f"   done in {elapsed}s — {len(tools)} tool call(s), {len(srcs)} source(s), "
              f"completeness_calls={m.get('completeness_calls')}")

        raw.append({"file": fname, "request": payload,
                    "elapsed_s": elapsed, "response": data})

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(raw, jf, ensure_ascii=False, indent=2)

    print(f"\nSaved -> {json_path}")


if __name__ == "__main__":
    main()
