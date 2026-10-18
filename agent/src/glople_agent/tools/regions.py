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
좌표(lat·lng)와 시군구 코드는 오너가 DB에서 뽑기 전이라 null이다(api-notes).
"""
import json
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


__all__ = [
    "ADMIN_SUFFIXES", "RESOURCES_DIR", "Region", "RegionResolution", "RegionResolver", "default_resolver",
    "normalize_name", "resolution_summary", "resolve", "strip_admin_suffix",
]
