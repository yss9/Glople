"""'문장으로 여행지 찾기' 검색 서버: 파인튜닝한 임베딩 모델 + 서비스 색인(build_index.py --corpus db). 읽기 전용.

    GET /semantic-api/v1/search?q=조용한 바닷가 마을&limit=12&scope=all
    GET /semantic-api/v1/health

- 127.0.0.1 에만 붙습니다(기본 8096). 브라우저는 프런트 개발 서버 프록시(frontend/src/setupProxy.js)로 같은 출처에서 부릅니다.
  CORS 는 켜지 않습니다. 쓰기 API 는 없고, GET 말고는 모두 405 입니다.
- 질의 앞에 색인 사이드카의 접두사(e5 규칙 'query: ')를 붙여 같은 모델로 임베딩하고, 색인 행렬과 내적(= 코사인 유사도)이 큰 순서로 돌려줍니다.
- 응답에는 화면에 필요한 최소 칸(source·id·name·location·category·score)만 넣습니다. 설명문 원문은 색인에도 응답에도 없고,
  화면은 기존 여행지 상세 화면(Spring /api/destinations/lookup 으로 찾은 여행지)으로 보여 줍니다.
- 같은 질의 문장은 임베딩을 캐시합니다(LRU). 범위·개수만 다르면 다시 임베딩하지 않습니다.
- 질의 글자는 로그에 남기지 않습니다. 접근 로그는 '메서드 경로(쿼리 문자열 뺌) 상태 걸린 시간 캐시'만 씁니다.
- 장치는 CPU 만 씁니다(GPU 는 다른 학습이 쓰는 중일 수 있음). 실행은 serve_search.sh 를 보세요.

사용: HF_HUB_OFFLINE=1 .venv/bin/python -I scripts/serve_search.py [--index outputs/index/e5-base-ft-db] [--port 8096]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import traceback
import unicodedata
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import emb_common as ec  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8096
DEFAULT_INDEX = ec.OUTPUTS_DIR / "index" / "e5-base-ft-db"
SEARCH_PATH = "/semantic-api/v1/search"
HEALTH_PATH = "/semantic-api/v1/health"

MAX_QUERY_CHARS = 200
MIN_LIMIT, MAX_LIMIT, DEFAULT_LIMIT = 1, 30, 12
SCOPES = ("all", "domestic", "overseas")
DEFAULT_CACHE_SIZE = 512
SCORE_DIGITS = 4

_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"[0-9]{1,6}")


# ---------------------------------------------------------------------------
# 순수 함수 (tests/test_serve_search.py)
# ---------------------------------------------------------------------------
class BadRequest(Exception):
    """잘못된 검색 값. errors 는 Spring 400 JSON 과 같은 모양의 [{field, code, message}]."""

    def __init__(self, errors: List[dict]):
        super().__init__("검색 값이 올바르지 않습니다.")
        self.errors = errors


def normalize_query(text: str) -> str:
    """NFC 로 맞추고 앞뒤 공백을 떼고, 줄바꿈·탭·연속 공백을 공백 하나로 바꿉니다. 캐시 키도 이 값입니다."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _has_control_char(text: str) -> bool:
    """제어 문자(Cc). 줄바꿈·탭은 normalize_query 가 공백으로 바꾸므로 정규화한 글에서 봅니다.
    이모지 잇기(ZWJ, Cf)는 받습니다."""
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def parse_search_params(query_string: str) -> dict:
    """쿼리 문자열 -> {'q', 'limit', 'scope'}. 틀린 칸이 하나라도 있으면 틀린 칸을 모두 담아 BadRequest.

    - q: 필수. 정규화(normalize_query) 뒤 1~200자(유니코드 코드 포인트). 제어 문자는 받지 않음
    - limit: 1~30 정수, 없으면 12. '3.0'·'+3'·' 3' 처럼 숫자만이 아니면 400
    - scope: all·domestic·overseas, 대소문자·앞뒤 공백 무시(Spring /api/search 와 같음), 없으면 all
    - 같은 칸을 두 번 보내면 400. 모르는 칸은 무시
    """
    try:
        raw = parse_qs(query_string or "", keep_blank_values=True, strict_parsing=False, errors="strict",
                       max_num_fields=20)
    except (UnicodeDecodeError, ValueError):
        raise BadRequest([{"field": "query", "code": "unreadable",
                           "message": "쿼리 문자열을 읽을 수 없습니다(UTF-8 로 인코딩해 주세요)."}])
    errors: List[dict] = []

    def single(field: str) -> Optional[str]:
        values = raw.get(field)
        if values is None:
            return None
        if len(values) > 1:
            errors.append({"field": field, "code": "duplicate", "message": f"{field}는 한 번만 보내 주세요."})
            return None
        return values[0]

    q_raw, limit_raw, scope_raw = single("q"), single("limit"), single("scope")

    q = normalize_query(q_raw) if q_raw is not None else ""
    if not q:
        if not any(e["field"] == "q" for e in errors):
            errors.append({"field": "q", "code": "required", "message": "q(찾을 문장)를 1~200자로 보내 주세요."})
    elif _has_control_char(q):
        errors.append({"field": "q", "code": "invalid_char", "message": "q에 쓸 수 없는 글자(제어 문자)가 있습니다."})
    elif len(q) > MAX_QUERY_CHARS:
        errors.append({"field": "q", "code": "too_long", "message": f"q는 {MAX_QUERY_CHARS}자까지입니다."})

    limit = DEFAULT_LIMIT
    if limit_raw is not None:
        if not _DIGITS.fullmatch(limit_raw):
            errors.append({"field": "limit", "code": "not_integer",
                           "message": f"limit는 {MIN_LIMIT}~{MAX_LIMIT} 사이 정수여야 합니다."})
        elif not MIN_LIMIT <= int(limit_raw) <= MAX_LIMIT:
            errors.append({"field": "limit", "code": "out_of_range",
                           "message": f"limit는 {MIN_LIMIT}~{MAX_LIMIT} 사이 정수여야 합니다."})
        else:
            limit = int(limit_raw)

    scope = "all"
    if scope_raw is not None:
        value = scope_raw.strip().lower()
        if value not in SCOPES:
            errors.append({"field": "scope", "code": "unknown_value",
                           "message": "scope는 all, domestic, overseas 중 하나여야 합니다."})
        else:
            scope = value

    if errors:
        raise BadRequest(errors)
    return {"q": q, "limit": limit, "scope": scope}


def item_scope(item: dict) -> str:
    """색인 항목의 국내·해외. 사이드카의 scope 칸을 쓰고, 없으면 서버 /api/search 와 같은 기준으로 계산합니다."""
    if item.get("scope") in ("domestic", "overseas"):
        return item["scope"]
    return "domestic" if item.get("source") == "TOUR_API" or item.get("country") == "대한민국" else "overseas"


def scope_rows(items: Sequence[dict]) -> Dict[str, List[int]]:
    """범위마다 색인 행 번호 목록 (행 순서 유지). 서버를 띄울 때 한 번만 계산합니다."""
    rows = {"all": list(range(len(items))), "domestic": [], "overseas": []}
    for i, item in enumerate(items):
        rows[item_scope(item)].append(i)
    return rows


def top_rows(scores, rows: Sequence[int], limit: int) -> List[Tuple[int, float]]:
    """rows 안에서 점수가 큰 순서로 limit 개 (행 번호, 점수). 같은 점수면 rows 에 준 순서
    (scope_rows 는 행 번호 오름차순 = DB 추출 순서)."""
    import numpy as np
    if not rows or limit <= 0:
        return []
    rows_arr = np.asarray(rows, dtype=np.int64)
    sub = np.asarray(scores, dtype=np.float32)[rows_arr]
    order = np.argsort(-sub, kind="stable")[:limit]
    return [(int(rows_arr[j]), float(sub[j])) for j in order]


def item_view(item: dict, score: float) -> dict:
    """응답 한 줄: 화면에 필요한 최소 칸만. 설명문·분할·분류 전체 경로는 내보내지 않습니다."""
    return {
        "source": item.get("source"),
        "id": item.get("id"),
        "name": item.get("title"),
        "location": item.get("location"),
        "category": item.get("category"),
        "score": round(float(score), SCORE_DIGITS),
    }


def search_response(params: dict, hits: Sequence[Tuple[int, float]], items: Sequence[dict]) -> dict:
    """검색 응답. 질의 글자는 되돌려 주지 않습니다(화면이 이미 알고 있음)."""
    return {
        "scope": params["scope"],
        "limit": params["limit"],
        "count": len(hits),
        "items": [item_view(items[i], s) for i, s in hits],
    }


def error_body(status: int, error: str, message: str, path: str, errors: Optional[List[dict]] = None) -> dict:
    """Spring 오류 JSON 과 같은 모양 {status, error, message, path, errors?}."""
    body = {"status": status, "error": error, "message": message, "path": path}
    if errors:
        body["errors"] = errors
    return body


class QueryCache:
    """정규화한 질의 문장 -> 질의 벡터. 가장 오래 안 쓴 것부터 버립니다(LRU). 스레드 안전."""

    def __init__(self, max_size: int = DEFAULT_CACHE_SIZE):
        self.max_size = max(0, max_size)
        self._data: "OrderedDict[str, object]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def put(self, key: str, value) -> None:
        if self.max_size == 0:
            return
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# ---------------------------------------------------------------------------
# 검색 엔진 (모델 + 색인)
# ---------------------------------------------------------------------------
class SearchEngine:
    def __init__(self, index_dir: Path, num_threads: int = 2, cache_size: int = DEFAULT_CACHE_SIZE):
        import numpy as np
        import torch
        torch.set_num_threads(num_threads)
        self.index_dir = index_dir
        side = ec.read_json(index_dir / "ids.json")
        self.items: List[dict] = side["items"]
        self.emb = np.load(index_dir / "embeddings.npy")
        if self.emb.shape != (len(self.items), side["dim"]):
            raise SystemExit(f"색인 모양이 사이드카와 다릅니다: {self.emb.shape} vs ({len(self.items)}, {side['dim']})")
        self.query_prefix = side["query_prefix"]
        self.max_query_len = side["max_query_len"]
        model_path = Path(side["model"])
        if not model_path.is_absolute() and (ec.ROOT / model_path).exists():
            model_path = ec.ROOT / model_path      # build_index.py 를 training/embedding 에서 돌려 상대 경로로 저장됨
        settings = ec.model_settings(str(model_path), side.get("base_revision"))
        self.model = ec.load_model(settings, "cpu")
        self.model_name = model_path.parent.name if model_path.name == "best" else model_path.name
        self.rows = scope_rows(self.items)
        self.cache = QueryCache(cache_size)
        self._encode_lock = threading.Lock()   # 모델 하나를 스레드끼리 나눠 쓰므로 임베딩은 한 번에 하나씩
        self.corpus = side.get("corpus", "labeled")

    def query_vector(self, q: str):
        vec = self.cache.get(q)
        if vec is not None:
            return vec, True
        with self._encode_lock:
            vec = ec.encode_texts(self.model, [q], self.query_prefix, self.max_query_len)[0]
        self.cache.put(q, vec)
        return vec, False

    def search(self, params: dict) -> Tuple[dict, bool]:
        qv, cached = self.query_vector(params["q"])
        scores = self.emb @ qv          # 전체 내적·정렬은 1만 곳 x 768차원이라 0.5ms 안팎. 범위는 행 번호로 거름
        hits = top_rows(scores, self.rows[params["scope"]], params["limit"])
        return search_response(params, hits, self.items), cached

    def health(self) -> dict:
        return {"status": "ok", "model": self.model_name, "corpus": self.corpus, "count": len(self.items),
                "domestic": len(self.rows["domestic"]), "overseas": len(self.rows["overseas"]),
                "cached_queries": len(self.cache)}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "GlopleSemanticSearch/1.0"
    protocol_version = "HTTP/1.1"
    engine: SearchEngine = None  # main() 에서 넣음

    def version_string(self):
        return self.server_version  # 파이썬 버전은 알리지 않음

    # --- 로그: 기본 구현은 요청 줄(쿼리 문자열 = 질의 글자 포함)을 찍으므로 모두 바꿉니다.
    def log_request(self, code="-", size="-"):
        pass

    def log_error(self, format, *args):
        pass

    def log_message(self, format, *args):
        pass

    def _path_only(self) -> str:
        try:
            return urlsplit(self.path).path[:200]
        except ValueError:
            return "?"

    def _access_log(self, status: int, started: float, note: str = "") -> None:
        ms = (time.perf_counter() - started) * 1000
        log(f"{self.command} {self._path_only()} {status} {ms:.0f}ms{(' ' + note) if note else ''}")

    def _send_json(self, status: int, body: dict, extra_headers: Optional[Dict[str, str]] = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response_only(status)
        self.send_header("Server", self.version_string())
        self.send_header("Date", self.date_time_string())
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_error(self, code, message=None, explain=None):
        """http.server 가 직접 부르는 오류(요청 줄이 깨짐, 너무 긴 URL 등)도 JSON 으로. 받은 요청 줄은 싣지 않습니다."""
        self.close_connection = True
        phrase = self.responses.get(code, ("Error",))[0]
        try:
            self._send_json(code, error_body(code, phrase.upper().replace(" ", "_"), "요청을 읽을 수 없습니다.",
                                             self._path_only() if hasattr(self, "path") else "?"))
        except Exception:  # noqa: BLE001 - 연결이 이미 끊긴 경우
            pass
        log(f"{getattr(self, 'command', None) or '-'} 요청 오류 {code}")

    def do_GET(self):
        started = time.perf_counter()
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if path == SEARCH_PATH:
                try:
                    params = parse_search_params(parts.query)
                except BadRequest as e:
                    self._send_json(400, error_body(400, "BAD_REQUEST", "검색 값이 올바르지 않습니다.", path, e.errors))
                    self._access_log(400, started, "입력 오류 " + ",".join(sorted({x["field"] for x in e.errors})))
                    return
                body, cached = self.engine.search(params)
                self._send_json(200, body)
                self._access_log(200, started, f"scope={params['scope']} limit={params['limit']} "
                                               f"결과 {body['count']} 캐시 {'적중' if cached else '새로 계산'}")
            elif path == HEALTH_PATH:
                self._send_json(200, self.engine.health())
                self._access_log(200, started)
            else:
                self._send_json(404, error_body(404, "NOT_FOUND", "없는 주소입니다.", path))
                self._access_log(404, started)
        except BrokenPipeError:
            pass
        except Exception:  # noqa: BLE001
            # 예외 메시지·스택에는 질의 글자가 들어가지 않습니다(질의는 지역 변수로만 다룸).
            log("검색 중 오류:\n" + traceback.format_exc(limit=5))
            try:
                self._send_json(500, error_body(500, "INTERNAL_SERVER_ERROR", "검색 중 오류가 났습니다.", path))
            except Exception:  # noqa: BLE001
                pass
            self._access_log(500, started)

    def _not_allowed(self):
        started = time.perf_counter()
        self._send_json(405, error_body(405, "METHOD_NOT_ALLOWED", "이 서버는 읽기(GET)만 받습니다.", self._path_only()),
                        {"Allow": "GET"})
        self._access_log(405, started)

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _not_allowed


def main() -> None:
    ec.setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="build_index.py --corpus db 가 만든 폴더")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--num-threads", type=int, default=2, help="CPU 스레드 (질의 하나 임베딩은 가벼움)")
    parser.add_argument("--cache-size", type=int, default=DEFAULT_CACHE_SIZE)
    args = parser.parse_args()

    index_dir = Path(args.index)
    if not (index_dir / "ids.json").exists():
        raise SystemExit(f"{index_dir}/ids.json 이 없습니다. 먼저 scripts/build_index.py --corpus db 를 실행하세요.")
    t0 = time.time()
    engine = SearchEngine(index_dir, num_threads=args.num_threads, cache_size=args.cache_size)
    ec.encode_texts(engine.model, ["준비"], engine.query_prefix, engine.max_query_len)  # 첫 요청이 느리지 않게 한 번 돌려 둠
    Handler.engine = engine
    info = engine.health()
    log(f"색인 {index_dir.name}: {info['count']}곳 (국내 {info['domestic']}, 해외 {info['overseas']}), "
        f"모델 {info['model']}, CPU {args.num_threads}스레드, 준비 {time.time() - t0:.1f}s")

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    server.daemon_threads = True
    log(f"듣는 중: http://{HOST}:{args.port}{SEARCH_PATH}?q=…  (끄기: Ctrl+C 또는 serve_search.sh stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log("서버를 껐습니다.")


if __name__ == "__main__":
    main()
