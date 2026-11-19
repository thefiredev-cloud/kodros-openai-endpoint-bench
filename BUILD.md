# Build guide: kodros-openai-endpoint-bench

## You need

- Python 3. Nothing else: the script and its tests use only the standard library.
- An endpoint that serves `/v1/chat/completions` with streaming and returns a `usage` object when `stream_options.include_usage` is set.

## Build

```bash
git clone https://github.com/thefiredev-cloud/kodros-openai-endpoint-bench.git
cd kodros-openai-endpoint-bench
```

There is nothing to compile or install.

## Test

```bash
python3 -m unittest discover -s tests -v
```

On 2026-11-18 this ran 8 tests in about 3 seconds. `tests/mock_server.py` starts a small server on a free localhost port, so the tests make no calls beyond localhost.

## Run

```bash
python3 bench.py http://127.0.0.1:8000 my-model run-a out.json
```

If the endpoint needs a token:

```bash
export BENCH_KEY=your-token
python3 bench.py http://127.0.0.1:8000 my-model run-a out.json --api-key-env BENCH_KEY
```

The script prints one line per case as it finishes and writes the full result to `out.json`. Run it once per endpoint with a different label, then compare the files.

## Read the result

| Field | Meaning |
| --- | --- |
| `ttft_s` | Seconds from request to first token |
| `prefill_tok_s` | Prompt tokens divided by TTFT |
| `decode_tok_s` | Completion tokens divided by time from first token to end |
| `fffd` | Count of U+FFFD characters in the reply |
| `concurrency2.aggregate_tok_s` | Completion tokens of both streams divided by their shared wall time |

## Troubleshooting

- Exit 1 and `cannot reach`: the base URL is wrong or the server is down.
- Exit 1 and `HTTP 401`: pass `--api-key-env`.
- Token counts and rates are 0: the server does not return `usage` in streaming mode.
- Exit 2: a required argument is missing.
