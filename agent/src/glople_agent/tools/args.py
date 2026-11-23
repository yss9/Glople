"""도구 8개의 실행용 인자 모델(design.md 8.1).

LLM에 보내는 스키마는 glople_tools.json 그대로이고(contract.TOOLS), 이 모델은 실행 쪽 검증에만 쓴다.
둘은 도구 이름으로만 이어진다. enum 값은 여기 직접 적고 테스트가 JSON enum과 대조한다
(JSON에서 읽어 만들면 대조가 의미 없어짐). 필드 설명만 JSON에서 가져온다.
"""
import re
import types
from typing import Annotated, Any, Literal, NamedTuple, Union, get_args, get_origin

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, ValidationError,
                      field_validator)

from glople_agent import contract

Category = Literal["관광지", "문화시설", "축제", "레포츠", "음식점", "숙박"]
Gender = Literal["남성", "여성"]
When = Literal["오늘", "내일", "이번 주말", "다음 주"]

MBTI_RE = re.compile(r"^[EI][SN][TF][JP]$")


def _int_like(v: Any) -> Any:
    # 모델이 "3"처럼 숫자 문자열을 낼 때만 정수로 바꾼다(True·3.5·"세 곳"은 그대로 두어 StrictInt가 거부)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return v


IntArg = Annotated[StrictInt, BeforeValidator(_int_like)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


def _desc(tool: str, field: str) -> str | None:
    for t in contract.TOOLS:
        fn = t["function"]
        if fn["name"] == tool:
            return fn["parameters"]["properties"][field].get("description")
    raise KeyError(f"{tool}.{field}가 glople_tools.json에 없습니다")


class ToolArgs(BaseModel):
    """공통 설정: 모르는 키 거부(extra=forbid), 앞뒤 공백 제거, 문자열↔숫자 자동 변환 안 함."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class RecommendByKeywordsArgs(ToolArgs):
    keywords: list[NonEmptyStr] = Field(min_length=1, description=_desc("recommend_by_keywords", "keywords"))


class SearchDestinationsArgs(ToolArgs):
    region: NonEmptyStr = Field(description=_desc("search_destinations", "region"))
    category: Category | None = Field(default=None, description=_desc("search_destinations", "category"))
    query: NonEmptyStr | None = Field(default=None, description=_desc("search_destinations", "query"))
    limit: Annotated[IntArg, Field(ge=1)] | None = Field(default=None, description=_desc("search_destinations", "limit"))


class RecommendSimilarTravelerRoutesArgs(ToolArgs):
    age: Annotated[IntArg, Field(ge=1, le=120)] = Field(description=_desc("recommend_similar_traveler_routes", "age"))
    gender: Gender = Field(description=_desc("recommend_similar_traveler_routes", "gender"))
    mbti: str = Field(description=_desc("recommend_similar_traveler_routes", "mbti"))

    @field_validator("mbti")
    @classmethod
    def _mbti(cls, v: str) -> str:
        # Spring info-recommend는 형식이 틀리면 500이라 여기서 막는다(대문자로 맞춤)
        v = v.upper()
        if not MBTI_RE.match(v):
            raise ValueError("MBTI는 E/I·S/N·T/F·J/P 네 글자여야 합니다")
        return v


class GetDestinationDetailArgs(ToolArgs):
    name: NonEmptyStr = Field(description=_desc("get_destination_detail", "name"))


class SearchReviewsArgs(ToolArgs):
    query: NonEmptyStr = Field(description=_desc("search_reviews", "query"))


class SearchGloplersArgs(ToolArgs):
    hashtag: NonEmptyStr = Field(description=_desc("search_gloplers", "hashtag"))


class GetWeatherArgs(ToolArgs):
    region: NonEmptyStr = Field(description=_desc("get_weather", "region"))
    when: When | None = Field(default=None, description=_desc("get_weather", "when"))


class CreateTravelPlanArgs(ToolArgs):
    # 범위는 Spring S2 검사와 같게(days 1~30, 장소 1~30, 제목 100자). 확인 카드의 edit 검사는 plan/draft.py
    region: NonEmptyStr = Field(description=_desc("create_travel_plan", "region"))
    days: Annotated[IntArg, Field(ge=1, le=30)] = Field(description=_desc("create_travel_plan", "days"))
    places: list[NonEmptyStr] = Field(min_length=1, max_length=30, description=_desc("create_travel_plan", "places"))
    title: Annotated[str, Field(min_length=1, max_length=100)] | None = Field(
        default=None, description=_desc("create_travel_plan", "title"))


ARGS_MODELS: dict[str, type[ToolArgs]] = {
    "recommend_by_keywords": RecommendByKeywordsArgs,
    "search_destinations": SearchDestinationsArgs,
    "recommend_similar_traveler_routes": RecommendSimilarTravelerRoutesArgs,
    "get_destination_detail": GetDestinationDetailArgs,
    "search_reviews": SearchReviewsArgs,
    "search_gloplers": SearchGloplersArgs,
    "get_weather": GetWeatherArgs,
    "create_travel_plan": CreateTravelPlanArgs,
}


class ArgError(NamedTuple):
    field: str   # 인자 이름("" = 인자 전체)
    kind: str    # missing | extra | enum | type | value


def _kind(err_type: str) -> str:
    if err_type == "missing":
        return "missing"
    if err_type == "extra_forbidden":
        return "extra"
    if err_type == "literal_error":
        return "enum"
    if err_type.endswith(("_type", "_parsing")):
        return "type"
    return "value"   # 범위·길이·빈 값·MBTI 형식


def validate_args(name: str, args: dict) -> ToolArgs:
    """도구 이름과 인자 dict로 인자 모델을 만든다. 모르는 도구면 KeyError, 틀리면 ValidationError."""
    return ARGS_MODELS[name].model_validate(args)


def arg_errors(name: str, args: Any) -> list[ArgError]:
    """validate 노드용: 틀린 인자를 (필드, 종류)로. 문제없으면 빈 목록. 모르는 도구면 KeyError."""
    model = ARGS_MODELS[name]
    if not isinstance(args, dict):
        return [ArgError("", "type")]
    try:
        model.model_validate(args)
    except ValidationError as e:
        seen: dict[ArgError, None] = {}
        for err in e.errors():
            field = str(err["loc"][0]) if err["loc"] else ""
            seen.setdefault(ArgError(field, _kind(err["type"])), None)
        return list(seen)
    return []


def enum_values(model: type[BaseModel], field: str) -> tuple[str, ...] | None:
    """필드의 Literal 값(Optional이면 벗겨서). enum이 아니면 None. 계약 테스트가 JSON enum과 대조한다."""
    ann = model.model_fields[field].annotation
    if get_origin(ann) in (Union, types.UnionType):
        ann = next(a for a in get_args(ann) if a is not type(None))
    if get_origin(ann) is Literal:
        return tuple(get_args(ann))
    return None
