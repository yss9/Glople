"""지역 해석기(design.md 8.3): 지역 이름 → {status, kind, label, candidates}. validate·어댑터·collect가 같은 함수를 쓴다.

표는 agent/resources/regions/*.json(평평한 모양, 나중에 Spring RG1 `GET /api/regions/resolve`로 옮길 수 있게).
- domestic.json: 시도 18(전남광주통합특별시 포함) + 시군 159. 자치구는 아직 없음
- countries.json: 나라 별칭 → ISO2, 대행 국가 라벨(미국·이탈리아·일본), DB의 위키데이터 표기
- overseas_cities.json: 해외 도시 → 나라, 반경

찾는 순서(앞에서 찾으면 끝):
1. 명시 이름: 정식 이름·별칭(섬 울릉도→울릉군, 강화도→강화군, 줄임말 경남→경상남도, 모호한 '광주'는 두 곳 모두에 적음)
2. 자동 별칭: 시군 이름에서 '시·군'을 뗀 것(강릉시 → 강릉). 명시 이름이 있으면 그쪽이 이긴다(제주 → 제주특별자치도)
3. 입력의 행정 접미사(특별시·광역시·특별자치도·특별자치시·도·시·군·구)를 떼고 1·2를 다시
4. '○○도'로 끝나는 섬 이름은 '도'를 떼고 다시(울릉도 → 울릉 → 울릉군)
5. 두 낱말('경기 광주')이면 마지막 낱말의 후보를 앞 낱말(시도)로 거름

결과가 하나면 ok, 둘 이상이면 ambiguous(광주광역시 / 경기도 광주시, 강원·경남 고성군), 없으면 not_found.
국내 좌표(lat·lng)와 시군구 코드는 오너가 DB에서 뽑기 전이라 null이다(api-notes). 해외 도시 82곳은 M6에서 도심 좌표를 채웠다
(반경 검색·날씨). 국내 날씨는 이 표의 이름으로 Open-Meteo 지오코딩을 한다(tools/adapters/weather.py).

M6에서 더한 것: distance_km(반경), location_in_region(여행지 행의 location이 그 지역 안인지 — 행마다 표기가 달라 해석기로 다시
해석해 비교), narrow_by_text(모호한 지역을 발화의 다른 낱말로 좁힘), region_query_label(Spring region 파라미터 글).
"""
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from glople_agent.config import AGENT_DIR

RESOURCES_DIR = AGENT_DIR / "resources" / "regions"

Status = Literal["ok", "ambiguous", "not_found"]
Kind = Literal["DOMESTIC", "OVERSEAS"]
Level = Literal["COUNTRY", "SIDO", "SIGUNGU", "CITY"]

ADMIN_SUFFIXES = ("특별자치시", "특별자치도", "특별시", "광역시", "도", "시", "군", "구")
DOMESTIC_COUNTRY_CODE = "KR"


@dataclass(frozen=True)
class Region:
    kind: Kind
    level: Level
    label: str                      # 국내는 DB location 꼴('강원특별자치도 강릉시'), 해외는 도시·나라 이름
    short_label: str                # 되묻기 후보에 보일 짧은 이름('경기 광주시')
    country_code: str | None = None
    country: str | None = None      # 해외 나라 이름(한국어)
    agent_country: str | None = None  # 글로플러 대행 국가 라벨(미국·이탈리아·일본)
    sido: str | None = None
    sigungu: str | None = None
    region_code: str | None = None
    sigungu_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_km: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class RegionResolution:
    query: str
    status: Status
    candidates: tuple[Region, ...] = ()

    @property
    def region(self) -> Region | None:
        return self.candidates[0] if self.status == "ok" else None

    @property
    def kind(self) -> Kind | None:
        """후보가 모두 같은 쪽(국내/해외)이면 그 값. 모호한 '광주'도 둘 다 국내라 DOMESTIC."""
        kinds = {c.kind for c in self.candidates}
        return kinds.pop() if len(kinds) == 1 else None

    @property
    def label(self) -> str | None:
        return self.region.label if self.region else None

    @property
    def is_domestic(self) -> bool:
        return self.kind == "DOMESTIC"

    def candidate_labels(self) -> list[str]:
        """되묻기에 보일 이름: 시도는 정식 이름(광주광역시), 시군은 짧은 이름(경기 광주시)."""
        return [c.label if c.level == "SIDO" else c.short_label for c in self.candidates]

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query, "status": self.status, "kind": self.kind, "label": self.label,
                "candidates": [c.to_dict() for c in self.candidates]}


def normalize_name(name: str) -> str:
    """비교용: NFC, '#' 떼기, 공백 하나로, 영문 소문자."""
    text = unicodedata.normalize("NFC", name or "").strip().lstrip("#").strip()
    return " ".join(text.split()).casefold()


def strip_admin_suffix(name: str) -> str | None:
    """'강릉시' → '강릉', '서울특별시' → '서울'. 떼고 나서 두 글자 미만이면 None."""
    for suffix in ADMIN_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            return name[: -len(suffix)]
    return None


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


class RegionResolver:
    def __init__(self, resources_dir: Path | None = None) -> None:
        base = Path(resources_dir) if resources_dir else RESOURCES_DIR
        self._explicit: dict[str, list[Region]] = {}
        self._auto: dict[str, list[Region]] = {}
        self._sido_names: dict[str, set[str]] = {}   # 시도 정식 이름 → 그 시도의 이름들(두 낱말 거르기용)
        countries = _load(base / "countries.json")
        by_country = {c["name"]: c for c in countries}
        for c in countries:
            domestic = c["iso2"] == DOMESTIC_COUNTRY_CODE
            region = Region(kind="DOMESTIC" if domestic else "OVERSEAS", level="COUNTRY",
                            label="국내" if domestic else c["name"], short_label="국내" if domestic else c["name"],
                            country_code=c["iso2"], country=None if domestic else c["name"],
                            agent_country=c.get("agentCountry"))
            self._add(self._explicit, [c["name"], *c.get("aliases", []), *c.get("dbLabels", [])], region)
        for e in _load(base / "domestic.json"):
            region = Region(kind="DOMESTIC", level=e["level"], label=e["label"], short_label=e["shortLabel"],
                            country_code=DOMESTIC_COUNTRY_CODE, sido=e["sido"], sigungu=e.get("sigungu"),
                            region_code=e.get("regionCode"), sigungu_code=e.get("sigunguCode"),
                            lat=e.get("lat"), lng=e.get("lng"))
            names = [e["label"], e["shortLabel"], *e.get("aliases", [])]
            if e["level"] == "SIDO":
                self._sido_names.setdefault(e["sido"], set()).update(normalize_name(n) for n in names)
            else:
                names.append(e["sigungu"])
                if (base_name := strip_admin_suffix(e["sigungu"])) is not None:
                    self._add(self._auto, [base_name], region)
            self._add(self._explicit, names, region)
        for e in _load(base / "overseas_cities.json"):
            country = by_country.get(e.get("country") or "")
            region = Region(kind="OVERSEAS", level="CITY", label=e["name"], short_label=e["name"],
                            country_code=e.get("countryCode"), country=e.get("country"),
                            agent_country=e.get("agentCountry") or (country or {}).get("agentCountry"),
                            lat=e.get("lat"), lng=e.get("lng"), radius_km=e.get("radiusKm"))
            self._add(self._explicit, [e["name"], *e.get("aliases", [])], region)

    @staticmethod
    def _add(index: dict[str, list[Region]], names: Iterable[str], region: Region) -> None:
        for name in names:
            key = normalize_name(name)
            if key and region not in index.setdefault(key, []):
                index[key].append(region)

    def _lookup(self, key: str) -> list[Region]:
        return list(self._explicit.get(key) or self._auto.get(key) or [])

    def _candidates(self, key: str) -> list[Region]:
        found = self._lookup(key)
        if not found and (base := strip_admin_suffix(key)) is not None:
            found = self._lookup(base)
        if not found and key.endswith("도") and len(key) > 2:   # 섬: 울릉도 → 울릉
            found = self._lookup(key[:-1])
        return found

    def resolve(self, name: str) -> RegionResolution:
        key = normalize_name(name)
        if not key:
            return RegionResolution(name or "", "not_found")
        found = self._candidates(key)
        if not found and " " in key:   # '경기 광주', '강원 고성'
            head, last = key.rsplit(" ", 1)
            found = self._candidates(last)
            if len(found) > 1:
                narrowed = [r for r in found if r.sido and head in self._sido_names.get(r.sido, set())]
                found = narrowed or found
        found = _prefer_city(list(dict.fromkeys(found)))
        if not found:
            return RegionResolution(name, "not_found")
        return RegionResolution(name, "ok" if len(found) == 1 else "ambiguous", tuple(found))


def _prefer_city(found: list[Region]) -> list[Region]:
    """도시 국가(싱가포르·홍콩·마카오)는 나라와 도시가 같은 곳이라 도시 하나로 본다."""
    codes = {r.country_code for r in found}
    if len(found) > 1 and len(codes) == 1 and all(r.kind == "OVERSEAS" for r in found):
        cities = [r for r in found if r.level == "CITY"]
        if len(cities) == 1:
            return cities
    return found


@lru_cache(maxsize=4)
def default_resolver(resources_dir: Path | None = None) -> RegionResolver:
    return RegionResolver(resources_dir)


def resolve(name: str) -> RegionResolution:
    """기본 표로 지역 이름을 해석한다."""
    return default_resolver().resolve(name)


def resolution_summary(res: RegionResolution) -> Mapping[str, Any]:
    """trace·카드용 짧은 요약(좌표·코드 없이)."""
    return {"status": res.status, "kind": res.kind, "label": res.label, "candidates": res.candidate_labels()}


# --- M6: 좌표 거리, 결과 행의 location 대조, 발화로 후보 고르기 -------------------------------------------

EARTH_RADIUS_KM = 6371.0088
# 지역 낱말 뒤에 붙는 말(되묻기 답 "경기도요", "강화요", "서울에서"). 긴 것부터 뗀다
TRAILING_PARTICLES = tuple(sorted({
    "이요", "요", "이에요", "예요", "입니다", "이고", "이랑", "랑", "에서", "에서요", "으로", "로", "으로요", "로요", "에", "에요",
    "은", "는", "이", "가", "을", "를", "의", "도", "쪽", "쪽이요", "쪽요", "꺼", "거", "거요", "이야", "야",
}, key=len, reverse=True))
_TOKEN_SPLIT = re.compile(r"[\s,./?!~()\[\]{}\"'“”‘’·]+")


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 대권 거리(km, haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def strip_particle(token: str) -> str:
    """'경기도요' → '경기도', '강화에서' → '강화'. 떼고 두 글자 미만이면 그대로."""
    for p in TRAILING_PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            return token[: -len(p)]
    return token


def text_tokens(text: str) -> list[str]:
    return [strip_particle(t) for t in _TOKEN_SPLIT.split(text or "") if t]


def _same_place(found: Region, target: Region) -> bool:
    """found(행의 location에서 해석한 곳)가 target(사용자가 말한 곳) 안인지."""
    if target.level == "COUNTRY":
        return found.country_code == target.country_code
    if target.kind == "DOMESTIC":
        if found.kind != "DOMESTIC" or found.sido != target.sido:
            return False
        return target.level == "SIDO" or (found.level == "SIGUNGU" and found.sigungu == target.sigungu)
    return found.level == "CITY" and found.label == target.label


def location_in_region(location: str | None, region: Region, *, country: str | None = None,
                       resolver: "RegionResolver | None" = None) -> bool:
    """여행지 행의 location('인천 강화군', '부산광역시 수영구', '일본 도쿄도')이 region 안인지.

    DB location 표기는 행마다 다르다('인천 강화군' / '인천광역시 강화군', 위키데이터 '일본 도쿄도'). 그래서 글자 비교 대신
    지역 해석기로 location을 다시 해석해 비교한다: 먼저 location 전체(두 낱말 '경기도 광주시'를 한 곳으로), 안 되면 낱말마다
    해석이 하나로 정해지는 것만(모호한 '광주시' 한 낱말로는 맞다고 하지 않음)."""
    r = resolver or default_resolver()
    text = " ".join(x for x in (location or "",) if x).strip()
    if not text:
        return False
    whole = r.resolve(text)
    if whole.status == "ok" and _same_place(whole.candidates[0], region):
        return True
    words = text.split()
    if country and region.level == "COUNTRY":
        words.append(country)
    for word in words:
        res = r.resolve(word)
        if res.status == "ok" and _same_place(res.candidates[0], region):
            return True
    return False


def narrow_by_text(res: RegionResolution, text: str, *, resolver: "RegionResolver | None" = None) -> Region | None:
    """모호한 지역(광주 → 광주광역시 / 경기 광주시)을 발화의 다른 낱말로 하나로 좁힌다. 못 좁히면 None.

    발화 낱말(조사를 뗀 것) 중 해석이 하나로 정해지는 시도·시군·도시가 후보 하나만 가리키면 그 후보.
    예: '광주 축제 알려줘 경기도요' → 경기도 → 경기 광주시, '… 광주광역시요' → 광주광역시. 후보 둘을 가리키거나 아무것도
    가리키지 않으면 None(되묻기 그대로)."""
    if res.status != "ambiguous":
        return res.region
    r = resolver or default_resolver()
    picked: set[int] = set()
    for token in text_tokens(text):
        found = r.resolve(token)
        if found.status != "ok":
            continue
        hit = found.candidates[0]
        for i, c in enumerate(res.candidates):
            if c == hit or (hit.level == "SIDO" and hit.kind == "DOMESTIC" and c.sido == hit.sido):
                picked.add(i)
    return res.candidates[picked.pop()] if len(picked) == 1 else None


def region_query_label(region: Region) -> str:
    """Spring region 파라미터와 되묻기 후보에 쓰는 이름: 시도는 정식 이름, 시군은 '시도 시군'(두 낱말이 같은 곳만 고름),
    해외 도시·나라는 그 이름. 국내 전체는 '국내'(Spring이 대한민국으로 바꿈)."""
    if region.kind == "DOMESTIC" and region.level == "COUNTRY":
        return "국내"
    return region.label


__all__ = [
    "ADMIN_SUFFIXES", "EARTH_RADIUS_KM", "RESOURCES_DIR", "Region", "RegionResolution", "RegionResolver",
    "TRAILING_PARTICLES", "default_resolver", "distance_km", "location_in_region", "narrow_by_text",
    "normalize_name", "region_query_label", "resolution_summary", "resolve", "strip_admin_suffix", "strip_particle",
    "text_tokens",
]
