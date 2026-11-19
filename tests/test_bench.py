import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import bench  # noqa: E402
import mock_server  # noqa: E402


class BenchTests(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "server", None):
            self.server.shutdown()
            self.server.server_close()

    def start(self, **kw):
        self.server, base = mock_server.start(**kw)
        return base

    def test_run_counts_tokens_and_corruption(self):
        base = self.start(pieces=["ok ", "\ufffd", "\ufffd", "!"])
        r = bench.run(base, "m", "hi", 16)
        self.assertEqual(r["completion_tokens"], 4)
        self.assertEqual(r["prompt_tokens"], 100)
        self.assertEqual(r["fffd"], 2)
        self.assertEqual(r["tail"], "ok \ufffd\ufffd!")
        req = self.server.requests[0]["body"]
        self.assertTrue(req["stream"])
        self.assertEqual(req["model"], "m")
        self.assertEqual(req["max_tokens"], 16)
        self.assertIsNone(self.server.requests[0]["auth"])

    def test_trailing_slash_in_base_url(self):
        base = self.start()
        r = bench.run(base + "/", "m", "hi", 16)
        self.assertEqual(r["completion_tokens"], 4)

    def test_cli_writes_result_file_with_api_key(self):
        base = self.start()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            env = dict(os.environ, BENCH_TEST_KEY="secret-token")
            p = subprocess.run(
                [sys.executable, os.path.join(ROOT, "bench.py"), base, "m", "mock", out,
                 "--api-key-env", "BENCH_TEST_KEY", "--timeout", "30"],
                env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(out, encoding="utf-8") as f:
                res = json.load(f)
        self.assertEqual(res["label"], "mock")
        self.assertEqual(sorted(res["cases"]), ["arith", "chinese", "code", "long_prefill"])
        self.assertEqual(res["concurrency2"]["fffd"], 0)
        self.assertEqual(len(res["concurrency2"]["per_stream_decode"]), 2)
        # warm-up + 4 cases + 2 concurrent streams
        self.assertEqual(len(self.server.requests), 7)
        self.assertTrue(all(r["auth"] == "Bearer secret-token" for r in self.server.requests))
        long_prompt = self.server.requests[4]["body"]["messages"][0]["content"]
        self.assertIn("function run()", long_prompt)

    def test_http_error_exits_nonzero(self):
        base = self.start(fail_status=500)
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run(
                [sys.executable, os.path.join(ROOT, "bench.py"), base, "m", "x", os.path.join(tmp, "o.json")],
                capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("HTTP 500", p.stderr)

    def test_unreachable_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run(
                [sys.executable, os.path.join(ROOT, "bench.py"), "http://127.0.0.1:1", "m", "x",
                 os.path.join(tmp, "o.json"), "--timeout", "5"],
                capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("cannot reach", p.stderr)

    def test_api_key_env_unset_is_an_error(self):
        env = {k: v for k, v in os.environ.items() if k != "BENCH_MISSING_KEY"}
        p = subprocess.run([sys.executable, os.path.join(ROOT, "bench.py"), "http://127.0.0.1:1", "m", "x", "o.json",
                            "--api-key-env", "BENCH_MISSING_KEY"], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("BENCH_MISSING_KEY", p.stderr)

    def test_no_auth_header_by_default(self):
        base = self.start()
        env = dict(os.environ, OPENAI_API_KEY="should-not-be-sent")
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.run([sys.executable, os.path.join(ROOT, "bench.py"), base, "m", "x", os.path.join(tmp, "o.json")],
                               env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(all(r["auth"] is None for r in self.server.requests))

    def test_help(self):
        p = subprocess.run([sys.executable, os.path.join(ROOT, "bench.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("base_url", p.stdout)


if __name__ == "__main__":
    unittest.main()
