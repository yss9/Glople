"""실행 컨텍스트. 토큰은 graph.invoke(..., context=AgentContext(...))로만 넘긴다.

state·messages·configurable·체크포인트·로그·프롬프트에는 토큰을 넣지 않는다(design.md 10절).
"""
import re
from dataclasses import dataclass
from datetime import datetime

_TOKEN_CHARS = re.compile(r"[!-~]+")   # 보이는 ASCII만(공백·제어 문자 없음). JWT는 base64url·점


def token_chars_ok(raw: str) -> bool:
    """HTTP 헤더에 그대로 넣을 수 있는 토큰인지. 아니면 보내지 않는다(h11 오류 메시지가 헤더 값을 그대로 담기 때문)."""
    return bool(_TOKEN_CHARS.fullmatch(raw))


class BearerToken:
    """JWT 래퍼. get_secret_value()를 일부러 두지 않는다: 체크포인트 직렬화기가 그 메서드로
    평문을 저장하기 때문(checkpoint jsonplus). 실수로 state에 들어가면 저장 대신 TypeError가 난다."""

    __slots__ = ("_v",)

    def __init__(self, raw: str) -> None:
        if not isinstance(raw, str):
            raise TypeError("BearerToken에는 문자열이 필요합니다")
        if not raw.strip():
            raise ValueError("BearerToken에 빈 토큰은 넣지 않습니다")
        self._v = raw

    def header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._v}"}

    def sendable(self) -> bool:
        """헤더로 보낼 수 있는 글자로만 됐는지(값은 돌려주지 않음)."""
        return token_chars_ok(self._v)

    def __repr__(self) -> str:
        return "BearerToken(***)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("BearerToken은 직렬화하지 않습니다")


@dataclass(frozen=True)
class AgentContext:
    user_id: str | None         # M5: my-info로 확인한 로그인 아이디. CLI는 고정 테스트 값
    bearer: BearerToken | None  # save_plan 노드만 꺼냄
    request_id: str
    now: datetime               # 테스트에서 고정 (나이→생년월일, 이번 주말)
    seed: int                   # 동점 섞기(S0 뒤 /api/search에 전달)

    def __post_init__(self) -> None:
        if self.bearer is not None and not isinstance(self.bearer, BearerToken):
            # 맨 문자열 토큰은 repr·로그에 그대로 찍히므로 받지 않는다
            raise TypeError("bearer는 BearerToken 또는 None이어야 합니다")
        if self.now.tzinfo is None:
            raise ValueError("now에는 시간대가 있어야 합니다(현지 날짜 계산)")
