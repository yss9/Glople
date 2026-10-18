"""Spring REST 클라이언트(design.md 8.1·10.1). 읽기 도구 어댑터가 이것만 통해 Glople 백엔드를 부른다.

- base_url은 설정값 하나(기본 http://127.0.0.1:8080, GLOPLE_SPRING_BASE_URL). **모델 출력으로 URL을 만들지 않는다**:
  경로는 아래 ENDPOINTS 허용 목록의 상수뿐이고, 모델 값은 httpx가 인코딩하는 query·JSON 본문으로만 들어간다.
- 공개 경로에는 토큰을 붙이지 않는다(최소 권한). auth를 넘기면 오히려 ValueError로 막는다. 로그인 경로(my-info,
  M3의 travel-plans)는 BearerToken이 있어야 하고, 헤더는 그 요청에만 붙인다(클라이언트 기본 헤더에는 없음).
- (M3) 경로 변수는 `/api/travel-plans/{id}`의 id 하나뿐이고 양의 정수만 받는다(call(path_params={"id": 3})). 모델 출력이
  아니라 저장소가 받은 일정 id다. accept에 4xx 상태를 주면 예외 대신 SpringResponse(status, 본문)로 돌려준다(일정 저장소가
  400 검증 JSON·401 만료·403을 직접 나누려고 씀). 5xx·연결 오류는 그대로 SpringError.
- timeout 연결 2초·읽기 5초, GET만 연결 오류·5xx에서 1회 재시도(POST는 재시도 없음), 응답 2MB 상한,
  리다이렉트를 따라가지 않음(토큰이 다른 곳으로 새지 않게), 환경 변수의 프록시를 쓰지 않음(trust_env=False).
- 오류는 SpringError(code)로 올린다. code는 envelope.ErrorCode와 같다(timeout·upstream_unavailable·bad_request).
  메시지에는 경로 이름·상태 코드만 넣고 URL·query·본문은 넣지 않는다(사용자 발화·토큰이 로그에 남지 않게).
"""
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from glople_agent.context import BearerToken

Access = Literal["public", "user"]
Method = Literal["GET", "POST", "DELETE"]
SpringErrorCode = Literal["upstream_unavailable", "timeout", "bad_request"]

CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
GET_RETRIES = 1
USER_AGENT = "glople-agent/0.0.1"


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: Method
    path: str          # "{id}"가 있으면 경로 변수(양의 정수만)
    access: Access

    @property
    def templated(self) -> bool:
        return "{id}" in self.path

    def matches(self, path: str) -> bool:
        if not self.templated:
            return path == self.path
        head, tail = self.path.split("{id}", 1)
        return re.fullmatch(re.escape(head) + _ID_PATTERN + re.escape(tail), path) is not None

    def render(self, path_params: Mapping[str, Any] | None) -> str:
        if not self.templated:
            if path_params:
                raise ValueError(f"{self.name}에는 경로 변수가 없습니다")
            return self.path
        value = (path_params or {}).get("id")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or set(path_params or {}) != {"id"}:
            raise ValueError(f"{self.name}의 경로 변수는 양의 정수 id 하나입니다")
        return self.path.replace("{id}", str(value))


_ID_PATTERN = r"[1-9][0-9]{0,17}"


# 허용 목록. 공개 경로는 SecurityConfig.java:84-117의 permitAll과 같다(검색·추천·후기·대행자 공개 프로필).
ENDPOINTS: dict[str, Endpoint] = {e.name: e for e in (
    Endpoint("keyword_search", "POST", "/api/search", "public"),                         # recommend_by_keywords
    Endpoint("info_recommend", "POST", "/api/info-recommend", "public"),                 # 유사 여행자 루트
    Endpoint("post_search", "GET", "/api/posts/search", "public"),                       # search_reviews
    Endpoint("agents_by_hashtag", "GET", "/api/users/search-agents-by-hashtag", "public"),  # search_gloplers
    Endpoint("agents", "GET", "/api/users/agents", "public"),                            # 글로플러 국가·전체 대체
    # M6 S1 여행지 읽기(공개, docs/travel-api.md). {id}는 lookup 응답의 id(모델 출력이 아님)
    Endpoint("destinations", "GET", "/api/destinations", "public"),                      # search_destinations
    Endpoint("destination_lookup", "GET", "/api/destinations/lookup", "public"),         # get_destination_detail 이름 찾기
    Endpoint("destination_get", "GET", "/api/destinations/{id}", "public"),              # 상세(없는 id는 404 JSON)
    Endpoint("my_info", "GET", "/api/users/my-info", "user"),                            # M5 신원 확인(로그인)
    # M3 S2 일정(로그인, 주인 = 토큰 사용자). plan/spring_store.py가 쓴다
    Endpoint("travel_plan_create", "POST", "/api/travel-plans", "user"),                 # 201 새로 / 200 같은 키
    Endpoint("travel_plan_list", "GET", "/api/travel-plans", "user"),                    # 내 일정 목록
    Endpoint("travel_plan_get", "GET", "/api/travel-plans/{id}", "user"),                # 본인 것(남의 것·없는 것 403)
    Endpoint("travel_plan_delete", "DELETE", "/api/travel-plans/{id}", "user"),
)}
_BY_ROUTE = {(e.method, e.path): e for e in ENDPOINTS.values() if not e.templated}
_TEMPLATED = [e for e in ENDPOINTS.values() if e.templated]


class SpringError(Exception):
    """Spring 호출 실패. code는 도구 오류 봉투의 error 값으로 그대로 쓰인다(envelope.classify_error)."""

    def __init__(self, code: SpringErrorCode, *, endpoint: str = "", status: int | None = None,
                 reason: str = "") -> None:
        detail = ", ".join(x for x in (endpoint, f"HTTP {status}" if status else "", reason) if x)
        super().__init__(f"Spring {code}" + (f" ({detail})" if detail else ""))
        self.code: SpringErrorCode = code
        self.endpoint = endpoint
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class SpringResponse:
    status: int
    data: Any = None          # 204·빈 본문이면 None

    @property
    def empty(self) -> bool:
        return self.status == 204 or self.data in (None, [], {})


def check_base_url(base_url: str) -> str:
    """http(s)://host[:port]만 받는다(경로·query·사용자 정보 없음)."""
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Spring base_url은 http(s)://호스트[:포트] 꼴이어야 합니다: {base_url!r}")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("Spring base_url에는 경로·query·사용자 정보를 넣지 않습니다")
    return f"{parts.scheme}://{parts.netloc}"


class SpringClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None,
                 connect_timeout: float = CONNECT_TIMEOUT, read_timeout: float = READ_TIMEOUT,
                 max_bytes: int = MAX_RESPONSE_BYTES, get_retries: int = GET_RETRIES) -> None:
        self.base_url = check_base_url(base_url)
        self.max_bytes = max_bytes
        self.get_retries = get_retries
        self._http = httpx.Client(
            base_url=self.base_url, transport=transport, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},   # Authorization 기본값 없음
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SpringClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def endpoint(method: str, path: str) -> Endpoint:
        ep = _BY_ROUTE.get((method.upper(), path))
        if ep is None:
            ep = next((e for e in _TEMPLATED if e.method == method.upper() and e.matches(path)), None)
        if ep is None:
            raise PermissionError(f"허용 목록에 없는 Spring 경로입니다: {method.upper()} {path}")
        return ep

    def call(self, name: str, *, params: Mapping[str, str] | None = None, json_body: Any = None,
             auth: BearerToken | None = None, path_params: Mapping[str, Any] | None = None,
             accept: Collection[int] = ()) -> SpringResponse:
        """허용 목록 이름으로 부른다(예: call("post_search", params={"query": "바나힐"}),
        call("travel_plan_get", path_params={"id": 3}, auth=…, accept={401, 403}))."""
        ep = ENDPOINTS.get(name)
        if ep is None:
            raise PermissionError(f"허용 목록에 없는 Spring 경로 이름입니다: {name!r}")
        return self.request(ep.method, ep.render(path_params), params=params, json_body=json_body, auth=auth,
                            accept=accept)

    def request(self, method: str, path: str, *, params: Mapping[str, str] | None = None, json_body: Any = None,
                auth: BearerToken | None = None, accept: Collection[int] = ()) -> SpringResponse:
        ep = self.endpoint(method, path)
        if any(not 400 <= code < 500 for code in accept):
            raise ValueError("accept에는 4xx 상태만 넣습니다(5xx는 재시도·오류 처리)")
        if ep.access == "public" and auth is not None:
            raise ValueError(f"공개 경로({ep.name})에는 토큰을 붙이지 않습니다")
        if ep.access == "user" and not isinstance(auth, BearerToken):
            raise ValueError(f"로그인 경로({ep.name})에는 BearerToken이 필요합니다")
        if params is not None and not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
            raise TypeError("query 값은 문자열만 받습니다")
        headers = auth.header() if auth is not None else None
        attempts = 1 + (self.get_retries if ep.method == "GET" else 0)
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                return self._once(ep, path, params=params, json_body=json_body, headers=headers,
                                  accept=frozenset(accept))
            except _Retryable as e:
                if last:
                    raise SpringError("upstream_unavailable", endpoint=ep.name, status=e.status,
                                      reason=e.reason) from e.__cause__
        raise AssertionError("도달하지 않음")

    def _once(self, ep: Endpoint, path: str, *, params, json_body, headers, accept: frozenset[int]) -> SpringResponse:
        try:
            with self._http.stream(ep.method, path, params=params, json=json_body, headers=headers) as resp:
                status = resp.status_code
                if status >= 500:
                    raise _Retryable(status=status, reason="server_error")
                if 300 <= status < 400:
                    raise SpringError("upstream_unavailable", endpoint=ep.name, status=status, reason="redirect")
                if status >= 400 and status not in accept:
                    raise SpringError("bad_request", endpoint=ep.name, status=status)
                body = self._read_limited(resp, ep)
        except httpx.ConnectTimeout as e:   # 연결 자체가 안 됨 → 연결 오류로(GET만 재시도)
            raise _Retryable(reason="connect_timeout") from e
        except httpx.TimeoutException as e:   # 읽기·풀 타임아웃은 재시도하지 않음
            raise SpringError("timeout", endpoint=ep.name, reason=type(e).__name__) from e
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
            raise _Retryable(reason=type(e).__name__) from e
        except httpx.HTTPError as e:   # LocalProtocolError(헤더 값이 메시지에 들어감)·UnsupportedProtocol·DecodingError …
            # 원래 예외를 잇지 않는다(from None): h11의 'Illegal header value b"Bearer …"'처럼 토큰이 실릴 수 있음
            raise SpringError("upstream_unavailable", endpoint=ep.name, reason=type(e).__name__) from None
        if status == 204 or not body.strip():
            return SpringResponse(status, None)
        try:
            return SpringResponse(status, json.loads(body))
        except ValueError as e:
            if status >= 400:   # accept한 4xx의 본문이 JSON이 아니면 본문 없이(상태 코드만으로 처리)
                return SpringResponse(status, None)
            raise SpringError("upstream_unavailable", endpoint=ep.name, status=status, reason="not_json") from e

    def _read_limited(self, resp: httpx.Response, ep: Endpoint) -> bytes:
        length = resp.headers.get("content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            raise SpringError("upstream_unavailable", endpoint=ep.name, status=resp.status_code, reason="too_large")
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise SpringError("upstream_unavailable", endpoint=ep.name, status=resp.status_code,
                                  reason="too_large")
            chunks.append(chunk)
        return b"".join(chunks)


class _Retryable(Exception):
    def __init__(self, *, status: int | None = None, reason: str = "") -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


__all__ = [
    "CONNECT_TIMEOUT", "ENDPOINTS", "Endpoint", "GET_RETRIES", "MAX_RESPONSE_BYTES", "Method", "READ_TIMEOUT",
    "SpringClient",
    "SpringError", "SpringResponse", "check_base_url",
]
