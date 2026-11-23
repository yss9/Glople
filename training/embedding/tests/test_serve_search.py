"""serve_search.py 의 입력 검사·범위 필터·응답 모양·캐시·로그 시험 (모델·색인 없이, 표준 unittest).

실행: cd training/embedding && .venv/bin/python -I -m unittest discover -s tests -v
(top_rows 가 numpy 를 써서 이 폴더 가상환경으로 돌립니다.)
"""
from __future__ import annotations

import io
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import serve_search as ss  # noqa: E402


def parse(**params):
    return ss.parse_search_params(urlencode(params))


def error_fields(query_string: str):
    """틀린 칸 [(field, code)]. 400 이 아니면(BadRequest 가 안 나면) 실패."""
    try:
        ss.parse_search_params(query_string)
    except ss.BadRequest as e:
        return [(x["field"], x["code"]) for x in e.errors]
    raise AssertionError(f"BadRequest 가 나야 합니다: {query_string[:40]}")


ITEMS = [
    {"source": "TOUR_API", "id": "127480", "title": "가거도", "location": "전남광주통합특별시 신안군", "country": "대한민국",
     "scope": "domestic", "category": "섬", "category_path": "자연관광 > 자연경관(하천‧해양) > 섬", "split": "train",
     "description": "설명문 원문은 응답에 나가면 안 됨"},
    {"source": "WIKIDATA", "id": "Q6059", "title": "크르크섬", "location": "크로아티아 프리모레고르스키코타르주",
     "country": "크로아티아", "scope": "overseas", "category": "섬", "category_path": "섬", "split": None},
    {"source": "TOUR_API", "id": "126273", "title": "가계해수욕장", "location": "전남광주통합특별시 진도군",
     "country": "대한민국", "scope": "domestic", "category": "해변. 해수욕장", "category_path": "…", "split": "valid"},
    {"source": "WIKIDATA", "id": "Q12431", "title": "피레네산맥", "location": "안도라", "country": "안도라",
     "scope": "overseas", "category": "산맥", "category_path": "산맥", "split": None},
]


class ParseSearchParamsTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(parse(q="조용한 바닷가 마을"), {"q": "조용한 바닷가 마을", "limit": 12, "scope": "all"})

    def test_limit_and_scope(self):
        self.assertEqual(parse(q="섬", limit="1", scope="domestic")["limit"], 1)
        self.assertEqual(parse(q="섬", limit="30")["limit"], 30)
        self.assertEqual(parse(q="섬", scope=" OverSeas ")["scope"], "overseas")

    def test_query_is_normalized(self):
        self.assertEqual(parse(q="  조용한\n바닷가\t\t마을  ")["q"], "조용한 바닷가 마을")
        nfd = "가거도"  # '가거도' 를 풀어 쓴 자모(NFD)
        self.assertEqual(parse(q=nfd)["q"], "가거도")

    def test_query_length_counts_code_points(self):
        self.assertEqual(len(parse(q="가" * 200)["q"]), 200)
        self.assertEqual(len(parse(q="🌊" * 200)["q"]), 200)
        self.assertEqual(error_fields(urlencode({"q": "가" * 201})), [("q", "too_long")])
        # 정규화 뒤 길이로 셈: 앞뒤 공백은 세지 않음
        self.assertEqual(len(parse(q="  " + "가" * 200 + "  ")["q"]), 200)

    def test_query_required(self):
        self.assertEqual(error_fields(""), [("q", "required")])
        self.assertEqual(error_fields("q="), [("q", "required")])
        self.assertEqual(error_fields(urlencode({"q": "   \n "})), [("q", "required")])
        self.assertEqual(error_fields("limit=3"), [("q", "required")])

    def test_query_control_chars(self):
        self.assertEqual(error_fields("q=" + quote("바다\x00마을")), [("q", "invalid_char")])
        self.assertEqual(parse(q="가족👨‍👩‍👧 여행")["q"], "가족👨‍👩‍👧 여행")  # ZWJ 이모지는 받음

    def test_bad_limit(self):
        for bad in ("0", "31", "-1", "+3", "3.0", "abc", "", " 3", "1e1", "９"):
            with self.subTest(limit=bad):
                fields = error_fields(urlencode({"q": "섬", "limit": bad}))
                self.assertEqual(len(fields), 1)
                self.assertEqual(fields[0][0], "limit")
                self.assertIn(fields[0][1], ("not_integer", "out_of_range"))

    def test_bad_scope(self):
        for bad in ("", "korea", "국내", "all,domestic"):
            with self.subTest(scope=bad):
                self.assertEqual(error_fields(urlencode({"q": "섬", "scope": bad})), [("scope", "unknown_value")])

    def test_all_errors_reported_together(self):
        fields = error_fields(urlencode({"q": "", "limit": "99", "scope": "x"}))
        self.assertEqual(fields, [("q", "required"), ("limit", "out_of_range"), ("scope", "unknown_value")])

    def test_duplicates_rejected_unknown_ignored(self):
        self.assertEqual(error_fields("q=a&q=b"), [("q", "duplicate")])
        self.assertEqual(error_fields("q=a&limit=1&limit=2"), [("limit", "duplicate")])
        self.assertEqual(parse(q="섬", page="3", foo="bar")["q"], "섬")

    def test_unreadable_query_string(self):
        self.assertEqual(error_fields("q=%FF%FE"), [("query", "unreadable")])
        self.assertEqual(error_fields("&".join(f"x{i}=1" for i in range(25)) + "&q=a"), [("query", "unreadable")])

    def test_error_messages_do_not_echo_query(self):
        secret = "아무도 몰랐으면 하는 문장" * 10
        with self.assertRaises(ss.BadRequest) as ctx:
            ss.parse_search_params(urlencode({"q": secret, "limit": "x"}))
        self.assertNotIn("아무도", json.dumps(ctx.exception.errors, ensure_ascii=False))


class ScopeAndRankingTest(unittest.TestCase):
    def test_item_scope(self):
        self.assertEqual(ss.item_scope(ITEMS[0]), "domestic")
        self.assertEqual(ss.item_scope(ITEMS[1]), "overseas")
        # scope 칸이 없는 (labeled) 색인: 서버 /api/search 와 같은 기준
        self.assertEqual(ss.item_scope({"source": "TOUR_API"}), "domestic")
        self.assertEqual(ss.item_scope({"source": None, "country": "대한민국"}), "domestic")
        self.assertEqual(ss.item_scope({"source": None, "country": "일본"}), "overseas")
        self.assertEqual(ss.item_scope({"id": "1"}), "overseas")

    def test_scope_rows(self):
        self.assertEqual(ss.scope_rows(ITEMS), {"all": [0, 1, 2, 3], "domestic": [0, 2], "overseas": [1, 3]})
        self.assertEqual(ss.scope_rows([]), {"all": [], "domestic": [], "overseas": []})

    def test_top_rows_orders_and_filters(self):
        scores = [0.5, 0.9, 0.7, 0.1]
        self.assertEqual([r for r, _ in ss.top_rows(scores, [0, 1, 2, 3], 3)], [1, 2, 0])
        self.assertEqual([r for r, _ in ss.top_rows(scores, [0, 2], 12)], [2, 0])      # 국내만, limit 가 더 커도 됨
        self.assertEqual([r for r, _ in ss.top_rows(scores, [1, 3], 1)], [1])
        self.assertEqual(ss.top_rows(scores, [], 5), [])
        hits = ss.top_rows(scores, [0, 1], 2)
        self.assertAlmostEqual(hits[0][1], 0.9, places=5)
        self.assertIsInstance(hits[0][0], int)
        self.assertIsInstance(hits[0][1], float)

    def test_top_rows_ties_keep_row_order(self):
        # 같은 점수면 rows 에 준 순서. scope_rows 는 행 번호 오름차순(= DB 추출 순서)
        self.assertEqual([r for r, _ in ss.top_rows([0.3, 0.8, 0.8, 0.8], [0, 1, 2, 3], 4)], [1, 2, 3, 0])
        self.assertEqual([r for r, _ in ss.top_rows([0.3, 0.8, 0.8, 0.8], [3, 2, 1, 0], 4)], [3, 2, 1, 0])


class ResponseShapeTest(unittest.TestCase):
    def test_item_view_is_minimal(self):
        view = ss.item_view(ITEMS[0], 0.873456)
        self.assertEqual(view, {"source": "TOUR_API", "id": "127480", "name": "가거도",
                                "location": "전남광주통합특별시 신안군", "category": "섬", "score": 0.8735})
        self.assertNotIn("description", view)
        self.assertNotIn("split", view)

    def test_search_response(self):
        params = {"q": "섬", "limit": 2, "scope": "overseas"}
        body = ss.search_response(params, [(1, 0.81), (3, 0.79)], ITEMS)
        self.assertEqual(set(body), {"scope", "limit", "count", "items"})
        self.assertEqual((body["scope"], body["limit"], body["count"]), ("overseas", 2, 2))
        self.assertEqual([x["id"] for x in body["items"]], ["Q6059", "Q12431"])
        self.assertNotIn("섬", json.dumps({k: v for k, v in body.items() if k != "items"}, ensure_ascii=False))
        json.dumps(body)  # JSON 으로 바뀌어야 함 (numpy 값 없음)

    def test_error_body(self):
        body = ss.error_body(400, "BAD_REQUEST", "검색 값이 올바르지 않습니다.", ss.SEARCH_PATH,
                             [{"field": "q", "code": "required", "message": "…"}])
        self.assertEqual(set(body), {"status", "error", "message", "path", "errors"})
        self.assertNotIn("errors", ss.error_body(404, "NOT_FOUND", "없는 주소입니다.", "/x"))


class QueryCacheTest(unittest.TestCase):
    def test_lru(self):
        cache = ss.QueryCache(2)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)      # a 를 최근으로
        cache.put("c", 3)                         # 가장 오래 안 쓴 b 가 빠짐
        self.assertIsNone(cache.get("b"))
        self.assertEqual((cache.get("a"), cache.get("c"), len(cache)), (1, 3, 2))
        self.assertEqual((cache.hits, cache.misses), (3, 1))

    def test_zero_size_disables(self):
        cache = ss.QueryCache(0)
        cache.put("a", 1)
        self.assertIsNone(cache.get("a"))


class FakeEngine:
    """모델 없이 HTTP 처리만 보려는 가짜 엔진."""

    def __init__(self):
        self.cache = ss.QueryCache(8)
        self.calls = []

    def search(self, params):
        self.calls.append(params)
        cached = self.cache.get(params["q"]) is not None
        self.cache.put(params["q"], True)
        rows = ss.scope_rows(ITEMS)[params["scope"]]
        hits = ss.top_rows([0.9, 0.8, 0.7, 0.6], rows, params["limit"])
        return ss.search_response(params, hits, ITEMS), cached

    def health(self):
        return {"status": "ok", "count": len(ITEMS)}


class HttpHandlerTest(unittest.TestCase):
    """실제 HTTP 서버(임시 포트)에 가짜 엔진을 끼워 상태 코드·JSON·로그를 봅니다."""

    @classmethod
    def setUpClass(cls):
        cls.engine = FakeEngine()
        handler = type("TestHandler", (ss.Handler,), {"engine": cls.engine})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, method="GET"):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            return e.code, dict(e.headers), (json.loads(body) if body else None)

    def test_search_ok_and_cache(self):
        secret = "비밀스러운 바닷가 마을"
        out = io.StringIO()
        with redirect_stdout(out):
            status, headers, body = self.request(ss.SEARCH_PATH + "?" + urlencode({"q": secret, "scope": "domestic",
                                                                                    "limit": 5}))
            self.request(ss.SEARCH_PATH + "?" + urlencode({"q": secret, "scope": "overseas"}))
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), None)   # CORS 없음
        self.assertEqual((body["scope"], body["count"]), ("domestic", 2))
        self.assertEqual([x["source"] for x in body["items"]], ["TOUR_API", "TOUR_API"])
        self.assertEqual(set(body["items"][0]), {"source", "id", "name", "location", "category", "score"})
        log = out.getvalue()
        self.assertIn("GET /semantic-api/v1/search 200", log)
        self.assertIn("캐시 적중", log)                     # 같은 문장 두 번째는 캐시
        self.assertNotIn("비밀", log)
        self.assertNotIn(quote("비밀"), log)
        self.assertNotIn("q=", log)

    def test_bad_request_json(self):
        out = io.StringIO()
        with redirect_stdout(out):
            status, _, body = self.request(ss.SEARCH_PATH + "?" + urlencode({"q": "숨길 문장", "limit": "100"}))
        self.assertEqual(status, 400)
        self.assertEqual((body["status"], body["error"], body["path"]), (400, "BAD_REQUEST", ss.SEARCH_PATH))
        self.assertEqual([(e["field"], e["code"]) for e in body["errors"]], [("limit", "out_of_range")])
        self.assertNotIn("숨길", out.getvalue())
        self.assertNotIn("숨길", json.dumps(body, ensure_ascii=False))

    def test_not_found_and_method_not_allowed(self):
        with redirect_stdout(io.StringIO()):
            status, _, body = self.request("/semantic-api/v1/nope?q=x")
            self.assertEqual((status, body["error"]), (404, "NOT_FOUND"))
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                status, headers, body = self.request(ss.SEARCH_PATH + "?q=x", method=method)
                self.assertEqual((status, body["error"], headers.get("Allow")), (405, "METHOD_NOT_ALLOWED", "GET"))
            status, _, body = self.request(ss.HEALTH_PATH)
        self.assertEqual((status, body["status"]), (200, "ok"))


if __name__ == "__main__":
    unittest.main()
