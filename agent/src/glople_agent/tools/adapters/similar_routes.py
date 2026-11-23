"""recommend_similar_traveler_routes → POST /api/info-recommend(공개, RecommendController.getRecommendations).

요청 본문은 UserInfo 엔티티 JSON이다. 도구 인자와 모양이 달라 바꿔야 한다(tools-backend-mapping 2.3).
- age(나이 정수) → 생년월일 문자열 yyyyMMdd(ctx.now 기준 `올해-나이` + 오늘 월일). UserInfo.age는 나이가 아니라 생년월일이고
  후보를 문자열 BETWEEN(±5년)으로 고르므로 DB와 같은 형식이어야 한다. 2월 29일은 평년이면 2월 28일.
- gender '남성'/'여성' → 'M'/'F'. 그대로 보내면 후보가 0건이 되거나(성별 문자열 비교) 벡터가 여성으로 계산된다.
- mbti 대문자 네 글자(인자 모델이 이미 검사). 값이 빠지거나 형식이 틀리면 서버가 500이라 미리 막는다.
응답 UserInfo 배열(유사도순 12명: id, age=생년월일, gender=M/F, mbti, travelDestinations)에서 생년월일은 나이대로만 바꿔
LLM에는 나이대·성별·MBTI·목적지 앞 3개만, 카드에는 목적지 전부를 넣는다(생년월일은 어디에도 남기지 않음).
"""
from datetime import date

from glople_agent.sanitize import clean_text
from glople_agent.tools.adapters.common import age_band, age_on, first_texts, parse_birth, rows_of
from glople_agent.tools.args import RecommendSimilarTravelerRoutesArgs
from glople_agent.tools.backend import BackendResult, ToolContext
from glople_agent.tools.spring import SpringClient

ENDPOINT = "info_recommend"
GENDER_CODES = {"남성": "M", "여성": "F"}
GENDER_LABELS = {"M": "남성", "F": "여성"}
ROUTES_SHOWN = 3
DEST_CHARS = 30


def birthdate_for(age: int, today: date) -> str:
    """나이 → 생년월일 yyyyMMdd(오늘 월일). 2월 29일은 그 해가 평년이면 28일."""
    year = today.year - age
    try:
        return date(year, today.month, today.day).strftime("%Y%m%d")
    except ValueError:
        return date(year, 2, 28).strftime("%Y%m%d")


def request_body(args: RecommendSimilarTravelerRoutesArgs, today: date) -> dict:
    return {"age": birthdate_for(args.age, today), "gender": GENDER_CODES[args.gender], "mbti": args.mbti.upper()}


def to_result(data: object, today: date) -> BackendResult:
    rows = rows_of(data)
    if not rows:
        return BackendResult("empty")
    items, cards = [], []
    for u in rows[:ROUTES_SHOWN]:
        band = age_band(age_on(parse_birth(u.get("age")), today))
        dests = [clean_text(d, DEST_CHARS) for d in first_texts(u.get("travelDestinations") or [], 50)]
        base = {"ageBand": band, "gender": GENDER_LABELS.get(u.get("gender")),
                "mbti": clean_text(u.get("mbti"), 4).upper() or None}
        items.append({**base, "destinations": dests[:3]})
        cards.append({"id": u.get("id"), **base, "destinations": dests})
    return BackendResult("ok", items, cards, extra={"found": len(rows)})


def run(client: SpringClient, args: RecommendSimilarTravelerRoutesArgs, ctx: ToolContext) -> BackendResult:
    today = ctx.today
    return to_result(client.call(ENDPOINT, json_body=request_body(args, today)).data, today)
