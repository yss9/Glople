"""[v1 보관본] 2026-10-17 밤샘 학습에 쓴 data/tool_calling 을 그대로 다시 만드는 생성기.

알려진 한계는 training/README.md 의 "v1의 알려진 한계" 참고. 새 데이터는 build_tool_dataset.py (v2) 로 만든다.

Glople 여행 에이전트용 tool-calling 학습 데이터를 만든다.

사용자 발화 한 턴에 대한 정답(도구 호출 / 되묻기 / 바로 답변)을 템플릿과 슬롯 값으로 합성한다.
test 세트는 train에 쓰지 않은 문장 템플릿과 지역·장소 값으로만 만들어서,
외운 문장을 맞히는지가 아니라 처음 보는 표현에서도 도구와 인자를 고르는지를 잰다.

출력: data/tool_calling/{train,valid,test}.jsonl  (mlx-lm chat 형식: messages + tools)
"""
import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = json.loads((ROOT / "tools" / "glople_tools.json").read_text(encoding="utf-8"))

SYSTEM_PROMPT = (
    "당신은 여행 커뮤니티 Glople의 여행 도우미입니다. "
    "여행지 추천·검색·일정 저장이 필요하면 도구를 호출하세요. "
    "도구에 꼭 필요한 정보가 빠졌으면 도구를 부르지 말고 짧게 되물으세요. "
    "여행과 관계없는 요청은 정중히 거절하세요."
)

# --- 슬롯 값 (train / test 분리) -------------------------------------------------

# Glople KeywordMask의 키워드 목록과 같다.
KEYWORDS = [
    "해안", "수영", "조각", "사원", "문화", "역사", "건축", "산", "등산", "호수",
    "폭포", "사막", "박물관", "미술관", "자연", "경치", "도시", "해변", "섬",
    "온천", "다이빙", "트레킹", "동물", "사파리", "야경", "시장", "열기구",
    "불교", "기차", "성당", "성", "원숭이", "온천욕", "탐험", "보트", "운하",
    "와인", "카누", "바다", "스노클링", "다문화", "협곡", "지열", "동굴",
    "사찰", "모스크", "탑", "성벽", "화산", "빙하", "설산", "캠핑", "하이킹",
    "스포츠", "레크리에이션", "축제", "음식", "전통", "공연", "예술", "유적",
    "랜드마크", "식물원", "미로", "정원", "유네스코", "해양", "아치",
    "호랑이", "철새", "낙타", "열대우림", "바위", "서핑", "산호", "빙벽",
    "하늘", "설경", "강", "대성당", "불상", "기념물", "전망대", "국립공원",
    "야생동물", "보트 투어", "역사적인 장소", "하이킹 코스", "전통 마을",
    "고대 유적", "야외 활동", "휴양지", "역사적 건축", "철도 여행",
]

REGIONS = {
    "train": [
        "서울", "부산", "제주", "강릉", "경주", "전주", "여수", "속초", "인천", "대구",
        "광주", "대전", "춘천", "통영", "안동", "포항", "남해", "양양", "가평", "단양",
        "도쿄", "오사카", "후쿠오카", "교토", "파리", "로마", "바르셀로나", "런던", "방콕", "다낭",
        "하노이", "타이베이", "뉴욕", "시드니", "발리", "싱가포르", "홍콩", "프라하", "이스탄불", "하와이",
    ],
    "test": ["목포", "거제", "울산", "순천", "공주", "태안", "울릉도", "담양",
             "삿포로", "나트랑", "치앙마이", "리스본", "빈", "밴쿠버"],
}

# 일정 저장용: 지역별 장소
REGION_PLACES = {
    "train": {
        "부산": ["해운대해수욕장", "광안리해수욕장", "감천문화마을", "태종대", "자갈치시장"],
        "서울": ["경복궁", "남산서울타워", "북촌한옥마을", "명동", "인사동"],
        "제주": ["성산일출봉", "우도", "한라산", "협재해수욕장", "섭지코지"],
        "경주": ["불국사", "석굴암", "동궁과 월지", "첨성대", "황리단길"],
        "강릉": ["안목해변", "오죽헌", "경포대", "주문진수산시장"],
        "전주": ["전주한옥마을", "경기전", "남부시장", "오목대"],
        "도쿄": ["센소지", "시부야 스크램블 교차로", "도쿄타워", "신주쿠 교엔"],
        "오사카": ["도톤보리", "오사카성", "유니버설 스튜디오 재팬", "구로몬 시장"],
        "파리": ["에펠탑", "루브르 박물관", "몽마르트르 언덕", "개선문"],
        "로마": ["콜로세움", "트레비 분수", "바티칸 박물관", "판테온"],
    },
    "test": {
        "여수": ["돌산공원", "오동도", "향일암", "여수해상케이블카"],
        "교토": ["후시미 이나리 신사", "기요미즈데라", "아라시야마 대나무숲"],
        "순천": ["순천만습지", "순천만국가정원", "낙안읍성"],
        "리스본": ["벨렘탑", "제로니모스 수도원", "알파마 지구"],
    },
}

PLACES = {
    "train": sorted({p for places in REGION_PLACES["train"].values() for p in places}
                    | {"설악산", "남이섬", "보성 녹차밭", "사그라다 파밀리아", "왓 아룬", "바나힐",
                       "타임스스퀘어", "오페라 하우스", "빅벤", "스카이트리"}),
    "test": sorted({p for places in REGION_PLACES["test"].values() for p in places}
                   | {"죽녹원", "외도 보타니아", "카를교", "하롱베이", "울릉도 독도전망대"}),
}

# 장소 유형: 사용자가 쓰는 말 → category
CATEGORY_WORDS = {
    "관광지": ["가볼 만한 곳", "관광지", "명소", "볼거리"],
    "문화시설": ["박물관", "미술관", "전시관"],
    "축제": ["축제", "행사", "공연"],
    "레포츠": ["액티비티", "레포츠", "체험 활동"],
    "음식점": ["맛집", "식당", "먹을 곳"],
    "숙박": ["숙소", "호텔", "잘 곳"],
}
CATEGORY_WORDS_TEST = {"관광지": ["구경할 곳"], "음식점": ["밥집"], "숙박": ["게스트하우스"], "축제": ["페스티벌"]}

DESCRIPTORS = {
    "train": ["야경 예쁜", "아이와 가기 좋은", "조용한", "가성비 좋은", "현지인이 가는",
              "데이트하기 좋은", "사진 찍기 좋은", "비 오는 날 가기 좋은"],
    "test": ["혼자 가기 좋은", "반려견과 갈 수 있는", "부모님 모시고 가기 좋은"],
}

MBTIS = [a + b + c + d for a in "EI" for b in "SN" for c in "TF" for d in "JP"]
GENDER_WORDS = {"남성": ["남자", "남성"], "여성": ["여자", "여성"]}
WHEN_WORDS = {"오늘": ["오늘"], "내일": ["내일"], "이번 주말": ["이번 주말", "주말에"], "다음 주": ["다음 주"]}
DAYS_WORDS = [("당일치기", 1), ("1박 2일", 2), ("2박 3일", 3), ("3박 4일", 4), ("4박 5일", 5), ("3일", 3), ("일주일", 7)]
PLAN_TITLES = ["가족 여행", "우정 여행", "혼자 떠나는 여행", "졸업 여행", "봄 여행"]
HASHTAGS = ["맛집투어", "사진여행", "배낭여행", "가족여행", "쇼핑"]


# --- 한국어 조사 ---------------------------------------------------------------

def has_batchim(word):
    last = word.strip()[-1]
    if "가" <= last <= "힣":
        return (ord(last) - ord("가")) % 28 != 0
    return False


def josa(word, with_batchim, without_batchim):
    return word + (with_batchim if has_batchim(word) else without_batchim)


def with_ro(word):
    # ㄹ 받침 뒤에는 '로'
    last = word.strip()[-1]
    if "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 == 8:
        return word + "로"
    return josa(word, "으로", "로")


def join_words(rng, words):
    if len(words) == 1:
        return words[0]
    style = rng.choice(["comma", "rang", "wa", "hago"])
    if style == "comma":
        return ", ".join(words)
    head, last = words[:-1], words[-1]
    if style == "rang":
        return " ".join(josa(w, "이랑", "랑") for w in head) + " " + last
    if style == "wa":
        return " ".join(josa(w, "과", "와") for w in head) + " " + last
    return " ".join(w + "하고" for w in head) + " " + last


# --- 예시 생성기 -----------------------------------------------------------------
# 각 생성기는 (사용자 발화, 정답) 을 돌려준다. 정답은 tool_calls 리스트이거나 문자열(바로 답변).

def call(tool_name, /, **arguments):
    return {"name": tool_name, "arguments": arguments}


def gen_keywords(rng, split):
    keywords = rng.sample(KEYWORDS, rng.choice([1, 2, 2, 3]))
    kws = join_words(rng, keywords)
    templates = {
        "train": [
            "{kws} 좋아하는데 여행지 추천해줘",
            "{kws} 즐길 수 있는 여행지 어디 있어?",
            "{kws} 위주로 갈 만한 곳 추천해 주세요",
            "요즘 {kws}에 꽂혔어. 어디로 여행 가면 좋을까?",
            "{kws_ro} 여행지 골라줘",
            "{kws} 테마로 여행 가고 싶어요",
            "이번 휴가엔 {kws} 즐기고 싶은데 어디가 좋아?",
            "{kws} 있는 여행지 추천 부탁해요",
        ],
        "test": [
            "{kws} 할 수 있는 여행지 몇 군데만 알려줘",
            "{kws} 느낌 나는 여행지 뭐 있을까요?",
        ],
    }[split]
    text = rng.choice(templates).format(kws=kws, kws_ro=with_ro(kws))
    return text, [call("recommend_by_keywords", keywords=keywords)]


def category_word(rng, split, category):
    words = CATEGORY_WORDS[category]
    if split == "test" and category in CATEGORY_WORDS_TEST:
        words = CATEGORY_WORDS_TEST[category]
    return rng.choice(words)


def gen_search(rng, split):
    region = rng.choice(REGIONS[split])
    category = rng.choice(list(CATEGORY_WORDS))
    cat = category_word(rng, split, category)
    desc = rng.choice(DESCRIPTORS[split]) if rng.random() < 0.4 else None
    limit = rng.choice([3, 5, 10]) if rng.random() < 0.2 else None

    phrase = f"{desc} {cat}" if desc else cat
    if limit:
        phrase += rng.choice([f" {limit}곳", f" {limit}개", f" {limit}군데"])
    templates = {
        "train": [
            "{region} {phrase} 추천해줘",
            "{region_e} {phrase} 있어?",
            "{region} 가는데 {phrase} 좀 찾아줘",
            "{region_eseo} {phrase} 알려주세요",
            "이번에 {region} 여행 가요. {phrase} 어디가 좋아요?",
            "{region} 근처 {phrase} 찾아줄래?",
        ],
        "test": [
            "{region} 쪽에 {phrase} 뭐 있는지 궁금해",
            "다음 달에 {region} 가는데 {phrase} 골라줘",
        ],
    }[split]
    text = rng.choice(templates).format(
        region=region, region_e=region + "에", region_eseo=region + "에서", phrase=phrase)

    arguments = {"region": region, "category": category}
    if desc:
        arguments["query"] = desc
    if limit:
        arguments["limit"] = limit
    return text, [call("search_destinations", **arguments)]


def gen_routes(rng, split):
    age = rng.randint(19, 65)
    gender = rng.choice(["남성", "여성"])
    gw = rng.choice(GENDER_WORDS[gender])
    mbti = rng.choice(MBTIS)
    mbti_text = mbti if rng.random() < 0.7 else mbti.lower()
    age_text = rng.choice([f"{age}살", f"{age}세"])
    templates = {
        "train": [
            "저는 {age} {gw}이고 MBTI는 {mbti}예요. 비슷한 사람들 여행 루트 보여주세요",
            "{mbti} {age} {gw}인데 나랑 비슷한 여행자들은 어디 다녀왔어?",
            "나 {gw}, {age}, {mbti}. 성향 비슷한 사람 루트 추천해줘",
            "{age} {mbti} {gw}한테 맞는 여행 루트 있을까?",
            "MBTI {mbti}이고 {age} {gw}입니다. 비슷한 여행자 코스 추천 부탁드려요",
        ],
        "test": [
            "{gw}이고 {age}, 엠비티아이는 {mbti}야. 나 같은 사람들 여행 루트 궁금해",
            "{mbti}인 {age} {gw} 여행자 루트 알려줘",
        ],
    }[split]
    text = rng.choice(templates).format(age=age_text, gw=gw, mbti=mbti_text)
    return text, [call("recommend_similar_traveler_routes", age=age, gender=gender, mbti=mbti)]


def gen_detail(rng, split):
    place = rng.choice(PLACES[split])
    templates = {
        "train": ["{p} 어떤 곳이야?", "{p}에 대해 알려줘", "{p} 정보 좀 보여줘",
                  "{p} 소개해 주세요", "{p} 가보려는데 어떤 곳인지 궁금해요"],
        "test": ["{p} 설명 부탁해", "{p} 볼 만해? 어떤 곳인지 알려줘"],
    }[split]
    return rng.choice(templates).format(p=place), [call("get_destination_detail", name=place)]


def gen_reviews(rng, split):
    subject = rng.choice(PLACES[split] + REGIONS[split])
    templates = {
        "train": ["{s} 후기 보여줘", "{s} 다녀온 사람들 리뷰 있어?", "{s} 리뷰 찾아줘",
                  "사람들이 {s} 어땠다고 해?", "{s} 여행 후기 검색해줘"],
        "test": ["{s} 실제로 가본 사람들 얘기 듣고 싶어", "{s} 후기 몇 개만 보여줄래?"],
    }[split]
    return rng.choice(templates).format(s=subject), [call("search_reviews", query=subject)]


def gen_gloplers(rng, split):
    if rng.random() < 0.2:
        tag = rng.choice(HASHTAGS)
        text = rng.choice(["#{t} 글로플러 검색해줘", "{t} 해시태그 단 글로플러 찾아줘"]).format(t=tag)
        return text, [call("search_gloplers", hashtag=tag)]

    region = rng.choice(REGIONS[split])
    templates = {
        "train": ["{r} 현지 가이드 찾아줘", "{r_e} 사는 글로플러 있어?", "{r} 여행 도와줄 글로플러 추천해줘",
                  "{r} 전문가랑 상담하고 싶어요", "{r} 글로플러 목록 보여줘"],
        "test": ["{r} 잘 아는 현지인 연결해 줄 수 있어?", "{r} 쪽 글로플러 누가 있는지 보여줘"],
    }[split]
    return rng.choice(templates).format(r=region, r_e=region + "에"), [call("search_gloplers", hashtag=region)]


def gen_weather(rng, split):
    region = rng.choice(REGIONS[split])
    when = rng.choice(list(WHEN_WORDS) + [None])
    ww = rng.choice(WHEN_WORDS[when]) if when else ""
    templates = {
        "train": ["{r} {w} 날씨 어때?", "{w} {r} 비 와?", "{w} {r} 가는데 날씨 괜찮아?", "{r} {w} 기온 궁금해요"],
        "test": ["{r} {w} 우산 챙겨야 할까?", "{w} {r} 날씨 좀 봐줘"],
    }[split]
    text = " ".join(rng.choice(templates).format(r=region, w=ww).split())
    arguments = {"region": region}
    if when:
        arguments["when"] = when
    return text, [call("get_weather", **arguments)]


def gen_plan(rng, split):
    region = rng.choice(list(REGION_PLACES[split]))
    places = rng.sample(REGION_PLACES[split][region], rng.randint(2, min(4, len(REGION_PLACES[split][region]))))
    days_word, days = rng.choice(DAYS_WORDS)
    title = rng.choice(PLAN_TITLES) if rng.random() < 0.25 else None
    pl = join_words(rng, places)
    templates = {
        "train": [
            "{r} {d_ro} {pl} 넣어서 일정 저장해줘",
            "{pl} 가는 {r} {d} 일정 만들어서 저장해 주세요",
            "{r} {d} 여행 일정 짰어. {pl} 순서로 저장해줘",
        ],
        "test": ["{r} {d} 코스로 {pl} 담아서 내 일정에 추가해 줘"],
    }[split]
    text = rng.choice(templates).format(r=region, d=days_word, d_ro=with_ro(days_word), pl=pl)
    if title:
        text = f"'{title}'{'이라는' if has_batchim(title) else '라는'} 이름으로 " + text

    arguments = {"region": region, "days": days, "places": places}
    if title:
        arguments["title"] = title
    return text, [call("create_travel_plan", **arguments)]


def gen_parallel(rng, split):
    kind = rng.choice(["weather_search", "detail_reviews", "search_gloplers"])
    if kind == "detail_reviews":
        place = rng.choice(PLACES[split])
        template = {"train": ["{p} 어떤 곳인지랑 후기도 보여줘", "{p} 정보랑 사람들 리뷰 같이 알려줘"],
                    "test": ["{p} 소개하고 후기 둘 다 보여줘"]}[split]
        return rng.choice(template).format(p=place), [
            call("get_destination_detail", name=place), call("search_reviews", query=place)]

    region = rng.choice(REGIONS[split])
    category = rng.choice(list(CATEGORY_WORDS))
    cat = category_word(rng, split, category)
    if kind == "weather_search":
        when = rng.choice(list(WHEN_WORDS))
        ww = rng.choice(WHEN_WORDS[when])
        template = {"train": ["{w} {r} 가는데 날씨랑 {c} 같이 알려줘", "{r} {w} 날씨 보고 {c}도 추천해줘"],
                    "test": ["{r} {w} 날씨 확인하고 {c}도 찾아줘"]}[split]
        return rng.choice(template).format(w=ww, r=region, c=cat), [
            call("get_weather", region=region, when=when),
            call("search_destinations", region=region, category=category)]

    template = {"train": ["{r} {c} 찾아주고 현지 글로플러도 추천해줘", "{r} {c} 알려주고 글로플러도 찾아줘"],
                "test": ["{r} {c} 검색하면서 그 지역 글로플러도 보여줘"]}[split]
    return rng.choice(template).format(r=region, c=cat), [
        call("search_destinations", region=region, category=category),
        call("search_gloplers", hashtag=region)]


def gen_clarify(rng, split):
    kind = rng.choice(["routes", "weather", "plan", "search"])
    if kind == "routes":
        mbti = rng.choice(MBTIS)
        options = [
            ("나랑 비슷한 사람들 여행 루트 보여줘", "나이, 성별, MBTI"),
            (f"{mbti}인데 비슷한 사람들 루트 추천해줘", "나이와 성별"),
            (f"{rng.randint(20, 50)}살 {rng.choice(['남자', '여자'])}인데 비슷한 여행자 루트 있어?", "MBTI"),
        ]
        if split == "test":
            options = [("성향 비슷한 여행자들 코스 궁금해", "나이, 성별, MBTI")]
        text, missing = rng.choice(options)
        return text, f"비슷한 여행자를 찾으려면 {missing}가 필요해요. 알려주시겠어요?"
    if kind == "weather":
        texts = {"train": ["날씨 어때?", "내일 날씨 알려줘", "주말에 비 와?"], "test": ["이번 주말 날씨 좀 봐줘"]}[split]
        return rng.choice(texts), "어느 지역 날씨를 알려드릴까요?"
    if kind == "plan":
        if rng.random() < 0.5:
            texts = {"train": ["일정 저장해줘", "여행 일정 만들어줘"], "test": ["내 일정에 추가해 줘"]}[split]
            return rng.choice(texts), "어느 지역으로 며칠 동안, 어떤 장소를 넣어서 저장할까요?"
        region = rng.choice(REGIONS[split])
        return f"{region} 일정 저장해줘", "며칠 일정으로, 어떤 장소를 넣어서 저장할까요?"

    category = rng.choice(list(CATEGORY_WORDS))
    cat = category_word(rng, split, category)
    text = rng.choice({"train": ["{c} 추천해줘", "{c} 찾아줘"], "test": ["{c} 좀 알려줄래?"]}[split]).format(c=cat)
    return text, f"어느 지역의 {josa(cat, '을', '를')} 찾아드릴까요?"


GREETING_ANSWER = "안녕하세요! Glople 여행 도우미예요. 가고 싶은 지역이나 여행 취향을 알려주시면 여행지를 찾아드릴게요."
THANKS_ANSWER = "도움이 되어서 기뻐요! 여행 계획이 더 필요하면 언제든 말씀해 주세요."
ABILITY_ANSWER = ("취향 키워드로 여행지 추천, 지역별 관광지·맛집·숙소 검색, 비슷한 여행자의 루트 추천, "
                  "여행 후기와 글로플러 찾기, 날씨 확인, 여행 일정 저장을 도와드릴 수 있어요.")
DECLINE_ANSWER = "저는 여행 계획을 돕는 Glople 도우미라서 그 요청은 도와드리기 어려워요. 여행지 추천이나 일정이 필요하면 말씀해 주세요."


def gen_direct(rng, split):
    pools = {
        "train": [
            (["안녕", "안녕하세요", "반가워", "하이"], GREETING_ANSWER),
            (["고마워", "감사합니다", "덕분에 잘 다녀왔어", "도움 많이 됐어"], THANKS_ANSWER),
            (["뭐 할 수 있어?", "어떤 기능 있어?", "너는 뭘 도와줄 수 있어?"], ABILITY_ANSWER),
            (["파이썬 코드 짜줘", "주식 종목 추천해줘", "수학 숙제 도와줘", "이메일 대신 써줘"], DECLINE_ANSWER),
        ],
        "test": [
            (["ㅎㅇ", "반갑습니다"], GREETING_ANSWER),
            (["땡큐", "정말 고마워요"], THANKS_ANSWER),
            (["넌 누구야?"], ABILITY_ANSWER),
            (["영어 번역 좀 해줘", "코인 시세 알려줘"], DECLINE_ANSWER),
        ],
    }[split]
    texts, answer = rng.choice(pools)
    return rng.choice(texts), answer


GENERATORS = [
    (gen_keywords, 14), (gen_search, 18), (gen_routes, 10), (gen_detail, 9), (gen_reviews, 9),
    (gen_gloplers, 8), (gen_weather, 9), (gen_plan, 9), (gen_parallel, 7), (gen_clarify, 6), (gen_direct, 4),
]


def to_record(text, answer, task):
    if isinstance(answer, str):
        assistant = {"role": "assistant", "content": answer}
    else:
        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in answer
            ],
        }
    return {
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}, assistant],
        "tools": TOOLS,
        "task": task,
    }


def build(split, count, rng, exclude):
    funcs, weights = zip(*GENERATORS)
    records, seen = [], set(exclude)
    attempts = 0
    while len(records) < count:
        attempts += 1
        if attempts > count * 200:
            raise RuntimeError(f"{split}: 중복 없이 {count}개를 만들지 못했습니다 ({len(records)}개)")
        func = rng.choices(funcs, weights=weights)[0]
        text, answer = func(rng, split)
        if text in seen:
            continue
        seen.add(text)
        records.append(to_record(text, answer, func.__name__.removeprefix("gen_")))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "tool_calling"))
    parser.add_argument("--train", type=int, default=3000)
    parser.add_argument("--valid", type=int, default=200)
    parser.add_argument("--test", type=int, default=300)
    parser.add_argument("--seed", type=int, default=13)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    train_valid = build("train", args.train + args.valid, rng, exclude=set())
    rng.shuffle(train_valid)
    train, valid = train_valid[:args.train], train_valid[args.train:]
    test = build("test", args.test, rng, exclude={r["messages"][1]["content"] for r in train_valid})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, records in [("train", train), ("valid", valid), ("test", test)]:
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        tasks = {}
        for record in records:
            tasks[record["task"]] = tasks.get(record["task"], 0) + 1
        print(f"{name}: {len(records)}  {dict(sorted(tasks.items()))}")


if __name__ == "__main__":
    main()
