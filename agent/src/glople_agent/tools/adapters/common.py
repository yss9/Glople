"""어댑터 공용: 응답 모양 검사, 날짜·나이 변환."""
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from typing import Any

from glople_agent.tools.backend import KST


def rows_of(data: Any) -> list[dict]:
    """응답 본문 → dict 행 목록. 배열이 아니면 빈 목록(204·null), 배열 안의 dict가 아닌 값은 버린다."""
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, Mapping)]


def kst_date(value: Any) -> date | None:
    """Spring Timestamp(ISO 문자열 '2024-06-02T05:10:00.000+00:00' 또는 epoch 밀리초) → 한국 날짜."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(KST).date()
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(KST).date()
    return None


def iso_or_none(d: date | None) -> str | None:
    return d.isoformat() if d else None


def parse_birth(value: Any) -> date | None:
    """UserInfo.age(생년월일 'yyyyMMdd' 또는 'yyyy-MM-dd') → 날짜."""
    if not isinstance(value, str):
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def age_on(birth: date | None, today: date) -> int | None:
    if birth is None:
        return None
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def age_band(age: int | None) -> str | None:
    return None if age is None or age < 0 else f"{age // 10 * 10}대"


def first_texts(values: Iterable[Any], n: int) -> list[str]:
    return [v for v in values if isinstance(v, str)][:n]


__all__ = ["age_band", "age_on", "first_texts", "iso_or_none", "kst_date", "parse_birth", "rows_of"]
