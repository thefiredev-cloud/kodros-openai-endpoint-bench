#!/usr/bin/env python3
"""Benchmark an OpenAI-compatible streaming chat endpoint (stdlib only).

Measures time to first token (TTFT), prefill and decode tokens per second,
two-stream concurrency, and U+FFFD corruption in the streamed text.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# One fixed Python snippet, repeated to build a long-context prompt of
# LONG_CTX_CHARS characters. The prompt is the same on
# every run, so prefill numbers are comparable between endpoints.
FILLER_UNIT = '''
def run(items, limit=10):
    """Return the number of items kept after filtering and de-duplication."""
    seen = set()
    kept = []
    for item in items:
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(item)
        if len(kept) >= limit:
            break
    return len(kept)
'''
LONG_CTX_CHARS = 32000
LONG_CTX = ("Reference document follows.\n" + FILLER_UNIT * (LONG_CTX_CHARS // len(FILLER_UNIT) + 1))[:LONG_CTX_CHARS]

CASES = [
    ("arith", "What is 17*23? Think briefly, then give the final number.", 2048),
    ("code", "Write an iterative Python function fib(n) returning the nth Fibonacci number. Keep reasoning short.", 2048),
    ("chinese", "用三句话解释什么是量化(quantization)在大模型推理中的作用。", 1024),
    ("long_prefill", LONG_CTX + "\n\nQuestion: what does the function run() return? Answer in one sentence.", 512),
]
CONCURRENT_CASE = "code"
CONCURRENT_MAX_TOKENS = 1024
CONCURRENT_STREAMS = 2


def run(base, model, prompt, max_tokens, api_key=None, timeout=900):
    """Stream one chat completion and return its timing and corruption stats."""
    body = json.dumps({
        "model": model, "stream": True, "max_tokens": max_tokens,
        "temperature": 1.0, "top_p": 0.95, "seed": 7,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(base.rstrip("/") + "/v1/chat/completions", body, headers)
    t0 = time.time()
    t_first = None
    text = []
    usage = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            ev = json.loads(data)
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                piece = (d.get("content") or "") + (d.get("reasoning_content") or d.get("reasoning") or "")
                if piece:
                    if t_first is None:
                        t_first = time.time()
                    text.append(piece)
    t_end = time.time()
    out = "".join(text)
    ct = (usage or {}).get("completion_tokens") or 0
    pt = (usage or {}).get("prompt_tokens") or 0
    ttft = (t_first or t_end) - t0
    decode = ct / (t_end - t_first) if t_first and t_end > t_first and ct else 0.0
    return {"prompt_tokens": pt, "completion_tokens": ct, "ttft_s": round(ttft, 2),
            "prefill_tok_s": round(pt / ttft, 1) if ttft > 0 else 0,
            "decode_tok_s": round(decode, 2), "wall_s": round(t_end - t0, 2),
            "fffd": out.count("\ufffd"), "tail": out[-300:]}


def benchmark(base, model, label, api_key=None, timeout=900, log=print):
    """Run all cases plus the concurrency test and return the result dict."""
    results = {"label": label, "model": model, "base": base, "cases": {}}
    run(base, model, "Say OK.", 16, api_key, timeout)  # warm-up
    for name, prompt, max_tokens in CASES:
        results["cases"][name] = run(base, model, prompt, max_tokens, api_key, timeout)
        log(label, name, {k: v for k, v in results["cases"][name].items() if k != "tail"})

    # Concurrent decode streams -> aggregate throughput.
    prompt = next(p for n, p, _ in CASES if n == CONCURRENT_CASE)
    conc = [None] * CONCURRENT_STREAMS
    errors = []

    def worker(i):
        try:
            conc[i] = run(base, model, prompt, CONCURRENT_MAX_TOKENS, api_key, timeout)
        except Exception as exc:  # reported after the join
            errors.append(exc)

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENT_STREAMS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    wall = time.time() - t0
    results["concurrency2"] = {
        "wall_s": round(wall, 2),
        "aggregate_tok_s": round(sum(c["completion_tokens"] for c in conc) / wall, 2),
        "per_stream_decode": [c["decode_tok_s"] for c in conc],
        "fffd": sum(c["fffd"] for c in conc),
    }
    log(label, "concurrency2", results["concurrency2"])
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Benchmark an OpenAI-compatible streaming chat endpoint: TTFT, prefill and "
                    "decode tok/s, two-stream concurrency, and U+FFFD corruption count.")
    ap.add_argument("base_url", help="server root, for example http://127.0.0.1:8000 (the script appends /v1/chat/completions)")
    ap.add_argument("model", help="model name sent in each request")
    ap.add_argument("label", help="free-text label stored in the result file")
    ap.add_argument("out_json", help="path of the JSON result file")
    ap.add_argument("--timeout", type=float, default=900.0, help="per-request timeout in seconds (default 900)")
    ap.add_argument("--api-key-env", default=None, metavar="NAME",
                    help="name of an environment variable holding a bearer token to send as the "
                         "Authorization header (default: send no Authorization header)")
    args = ap.parse_args(argv)

    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    if args.api_key_env and not api_key:
        ap.error("environment variable %s is not set or empty" % args.api_key_env)
    try:
        results = benchmark(args.base_url, args.model, args.label, api_key, args.timeout,
                            log=lambda *a: print(*a, flush=True))
    except urllib.error.HTTPError as exc:
        print("request failed: HTTP %s %s from %s" % (exc.code, exc.reason, args.base_url), file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print("request failed: cannot reach %s (%s)" % (args.base_url, exc), file=sys.stderr)
        return 1
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
