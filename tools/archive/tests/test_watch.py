"""조건부 요청(304)과 보안 장치 리디렉션(302 → 오류 페이지) 처리 점검. 로컬 가짜 서버로 돌립니다."""
import sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ptimes_archive as pa

ETAG = '"10c2c-65dedfc6abf3e"'
LAST = "Fri, 16 Oct 2026 04:56:23 GMT"
seen = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_HEAD(self):     # 실제 사이트처럼 HEAD는 오류 페이지로
        self.send_response(302); self.send_header("Location", "http://se-cu.com/ndsoft/error.html"); self.end_headers()
    def do_GET(self):
        seen.append((self.path, self.headers.get("User-Agent"), self.headers.get("If-None-Match")))
        if self.path.startswith("/blocked"):
            self.send_response(302); self.send_header("Location", "http://se-cu.com/ndsoft/error.html"); self.end_headers(); return
        if self.headers.get("If-None-Match") == ETAG:
            self.send_response(304); self.end_headers(); return
        body = b"<rss/>"
        self.send_response(200); self.send_header("ETag", ETAG); self.send_header("Last-Modified", LAST)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        pa.BASE = f"http://127.0.0.1:{cls.server.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_conditional_get(self):
        f, state = pa.Fetcher(0), {}
        self.assertTrue(pa.rss_changed(f, state))          # 처음: 200, 값 저장
        self.assertEqual(state["etag"], ETAG)
        self.assertFalse(pa.rss_changed(f, state))         # 다음: 304
        self.assertEqual(seen[-1][2], ETAG)
        self.assertTrue(seen[-1][1].startswith("PostechTimesArchive/"))

    def test_redirect_is_blocked(self):
        with self.assertRaises(pa.Blocked):
            pa.Fetcher(0).get("/blocked")

if __name__ == "__main__":
    unittest.main()


class WatchStateTests(unittest.TestCase):
    """갱신이 막히면 피드 값을 되돌려, 다음 확인에서 다시 갱신을 시도하는지."""
    def test_blocked_update_keeps_old_validators(self):
        import tempfile, argparse
        old_data, old_changed, old_update = pa.DATA, pa.rss_changed, pa.cmd_update
        try:
            pa.DATA = Path(tempfile.mkdtemp())
            def changed(fetcher, state):
                state["etag"], state["lastModified"] = '"new"', "new"
                return True
            def update(args):
                raise pa.Blocked("막힘")
            pa.rss_changed, pa.cmd_update = changed, update
            pa.save_json(pa.DATA / ".watch_state.json", {"etag": '"old"', "lastModified": "old"})
            pa.cmd_watch(argparse.Namespace(delay=0, max_pages=1, once=True, interval=10))
            state = pa.load_json(pa.DATA / ".watch_state.json", {})
            self.assertEqual(state["etag"], '"old"')
            self.assertIn("blockedUntil", state)
            # 차단 대기 중에는 요청하지 않음
            pa.rss_changed = lambda f, s: self.fail("차단 대기 중에 요청함")
            pa.cmd_watch(argparse.Namespace(delay=0, max_pages=1, once=True, interval=10))
        finally:
            pa.DATA, pa.rss_changed, pa.cmd_update = old_data, old_changed, old_update
