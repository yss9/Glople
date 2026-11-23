"""요청자 확인(design.md 10.2): Authorization 헤더 → Spring `GET /api/users/my-info`(그 사용자의 토큰) → userId.

- 에이전트는 JWT 비밀키를 갖지 않는다. 토큰을 풀어 보지 않고 Spring에 물어 본다. 응답에서는 userId만 쓰고 나머지
  (생년월일·닉네임 …)는 바로 버린다.
- 캐시: sha256(토큰) → (userId, 만료 시각), 기본 60초. 토큰 원문은 캐시 키로도 두지 않는다. 실패(401)는 캐시하지 않는다.
- 'Bearer undefined'·'Bearer null'·빈 값·헤더로 보낼 수 없는 글자(공백·제어 문자·ASCII 밖)·너무 긴 값은 Spring에 묻지 않고 401.
- Spring 호출은 SpringClient 허용 목록의 my_info(로그인 경로)만. 토큰은 그 요청의 헤더에만 붙고 클라이언트 기본 헤더에는 없다.
- 토큰은 Principal.bearer(BearerToken: repr 가림, 직렬화 거부)로만 들고 다닌다. 이 객체는 AgentContext로만 그래프에 들어간다.
"""
import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

import anyio

from glople_agent.context import BearerToken, token_chars_ok
from glople_agent.service.errors import ApiError, unauthorized
from glople_agent.tools.spring import SpringClient, SpringError

NO_TOKEN_VALUES = frozenset({"", "undefined", "null"})
MAX_USER_ID_CHARS = 100


@dataclass(frozen=True)
class Principal:
    """확인된 요청자. bearer는 save_plan(일정 저장)·일정 검토의 Spring 요청에만 쓴다."""

    user_id: str
    bearer: BearerToken = field(repr=False)


def bearer_from_header(value: str | None, *, max_chars: int = 4096) -> str | None:
    """'Bearer <토큰>' → 토큰. 토큰이 없다고 볼 값이면 None(→ 401)."""
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    if token in NO_TOKEN_VALUES or len(token) > max_chars or not token_chars_ok(token):
        return None
    return token


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class Authenticator:
    def __init__(self, client: SpringClient, *, ttl: float = 60.0, max_entries: int = 1024,
                 max_token_chars: int = 4096, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.ttl = ttl
        self.max_entries = max_entries
        self.max_token_chars = max_token_chars
        self.clock = clock
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self.spring_calls = 0   # 테스트: 캐시가 Spring 호출을 줄이는지

    async def authenticate(self, authorization: str | None) -> Principal:
        token = bearer_from_header(authorization, max_chars=self.max_token_chars)
        if token is None:
            raise unauthorized()
        key = token_key(token)
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and hit[1] > now:
            self._cache.move_to_end(key)
            return Principal(hit[0], BearerToken(token))
        bearer = BearerToken(token)
        user_id = await anyio.to_thread.run_sync(self._my_info, bearer)
        self._cache[key] = (user_id, now + self.ttl)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return Principal(user_id, bearer)

    def _my_info(self, bearer: BearerToken) -> str:
        self.spring_calls += 1
        try:
            resp = self.client.call("my_info", auth=bearer, accept={401, 403, 404})
        except SpringError:   # 연결·5xx·타임아웃·리다이렉트(메시지에 토큰 없음, 원래 예외는 잇지 않음)
            raise ApiError(503, "AUTH_UNAVAILABLE", "로그인 확인 서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.",
                           retryable=True) from None
        if resp.status != 200 or not isinstance(resp.data, dict):
            raise unauthorized()
        user_id = resp.data.get("userId")
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > MAX_USER_ID_CHARS:
            raise unauthorized()
        return user_id


__all__ = ["Authenticator", "NO_TOKEN_VALUES", "Principal", "bearer_from_header", "token_key"]
