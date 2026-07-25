"""Send a batch of /chat requests and save the responses to a file.

Reads request files and POSTs each to the backend, then writes a raw JSON dump
(each record: request, per-request timing, and the full response — answer,
sources, trace, metrics). Per request it prints the latency broken down by call
kind (agent loop / vision / completeness / embed / rerank), and finishes with a
table of those totals across the whole batch — which is what tells you where a
slow run is actually spending its time. Accepted inputs (each record is the same
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


def fmt_ms(ms):
    """Compact duration for terminal output: '840 ms', '4.2 s', '1m 03.4s'."""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:.0f} ms"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f} s"
    return f"{int(s // 60)}m {s % 60:04.1f}s"


def kind_line(by_kind):
    """One-line per-request breakdown, slowest call kind first."""
    if not by_kind:
        return ""
    return " | ".join(
        f"{k} {v['calls']}x {fmt_ms(v['ms'])}"
        + (f" ERR {v['errors']}" if v.get("errors") else "")
        for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["ms"])
    )


def print_kind_summary(raw):
    """Aggregate the per-call-kind timings across every request in the batch."""
    totals = {}
    for rec in raw:
        by_kind = ((rec.get("response") or {}).get("metrics") or {}).get("by_kind") or {}
        for k, v in by_kind.items():
            t = totals.setdefault(k, {"calls": 0, "ms": 0.0, "errors": 0})
            t["calls"] += v.get("calls") or 0
            t["ms"] += v.get("ms") or 0.0
            t["errors"] += v.get("errors") or 0
    if not totals:
        print("\n(No per-kind metrics returned — is the backend running this build?)")
        return

    print("\n=== Latency by call kind ===")
    print(f"{'kind':<20}{'calls':>7}{'total':>12}{'avg/call':>12}{'errors':>8}")
    for k, t in sorted(totals.items(), key=lambda kv: -kv[1]["ms"]):
        avg = t["ms"] / t["calls"] if t["calls"] else 0
        print(f"{k:<20}{t['calls']:>7}{fmt_ms(t['ms']):>12}"
              f"{fmt_ms(avg):>12}{t['errors']:>8}")
    print("Concurrent calls are summed, so totals can exceed wall-clock time — "
          "compare kinds against each other, not against elapsed.")


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
        print(f"-> {fname}")
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
              f"{m.get('llm_calls')} llm call(s), {m.get('total_tokens') or 0} tok")
        breakdown = kind_line(m.get("by_kind"))
        if breakdown:
            print(f"   {breakdown}")

        raw.append({"file": fname, "request": payload,
                    "elapsed_s": elapsed, "response": data})

    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(raw, jf, ensure_ascii=False, indent=2)

    print_kind_summary(raw)
    print(f"\nSaved -> {json_path}")


if __name__ == "__main__":
    main()
