"""Glople 여행 에이전트용 tool-calling 학습 데이터를 만든다 (v3).

v2(data/tool_calling_v2, 보관본 scripts/build_tool_dataset_v2.py)에서 바뀐 점 — v2 오답 분석(2026-10-18)을 반영
- 조건(query) 문구: train 8개 → 수십 개. v2는 처음 보는 조건을 외운 8개 중 비슷한 것으로 바꿔 썼다
  ('반려견과 갈 수 있는' → '반려견과 가기 좋은'). 문구 풀을 크게 늘려 사용자 말을 그대로 옮기게 한다.
  test 쪽 조건 주제(혼자·반려견·부모님·뷰·초보·늦은 밤·무료)는 train에 쓰지 않는다.
- 장소 유형 말(category)을 늘린다. test 전용 말은 그대로 test에만 둔다.
- '몇 군데만'처럼 막연한 개수에는 limit을 붙이지 않는다. 후기 검색(search_reviews)에는 limit 칸이 없다.
- 비슷한 여행자 루트: 나이·성별·MBTI가 모두 있는 짧은 말투면 되묻지 않고 바로 부른다.
- 키워드: 목록에 없는 꾸밈말(조용한, 예쁜 …)은 빼고, 비슷한 말(바닷가 → 바다)은 목록의 키워드로 바꾼다.
- '○○에 사는데'처럼 사는 곳을 말해도 목적지가 아니면 키워드 추천으로 간다.
- 여행지 이름이 들어간 무관한 요청(번역·환율 …)은 거절한다.
- 사람 말투 평가 세트(human_style.jsonl)의 문장은 쓰지 않는다. 같은 유형의 다른 말투만 늘렸다.

이하 v2 설명.

사용자 발화 한 턴에 대한 정답(도구 호출 / 되묻기 / 바로 답변)을 템플릿과 슬롯 값으로 합성한다.
test 세트는 train에 쓰지 않은 문장 템플릿과 지역·장소·조건·카테고리 말·해시태그·일정 이름으로만 만들어서,
외운 문장을 맞히는지가 아니라 처음 보는 표현에서도 도구와 인자를 고르는지를 잰다.
키워드 94개, MBTI, 성별·시점·일수 표현은 도구가 받는 닫힌 값이라 train과 test가 같이 쓴다.

v1(data/tool_calling, 2026-10-17 밤샘 학습에 쓴 버전)에서 바뀐 점
- 되묻기 문장의 조사를 마지막 단어 받침에 맞춘다 (v1: '나이와 성별가' → v2: '나이와 성별이', 'MBTI가').
- 지역 없이 키워드인 장소 유형(박물관·미술관·축제·공연)만 말하면 recommend_by_keywords.
  지역을 되묻는 것은 키워드가 아닌 카테고리 말(맛집, 숙소, 액티비티, 관광지 …)일 때만이다.
- test의 지역·장소·조건·카테고리 말·해시태그·일정 이름·문장을 train과 완전히 분리하고, 겹치면 실행을 멈춘다.
- 작업별 개수를 GENERATORS 가중치대로 정확히 채운다 (세부 종류가 있는 작업은 GENERATOR_KINDS 가중치까지).
  문장 풀이 모자라면 다른 작업으로 메우지 않고 멈춘다.
- 바로 답변·되묻기·거절 문장 풀과 여행지 정보·글로플러 문장 풀을 크게 늘리고, 어색한 템플릿을 고쳤다.
- 같은 발화에 정답이 두 가지로 붙으면 실행을 멈춘다. 사람 말투 평가 세트(eval_sets/human_style.jsonl) 발화는 만들지 않는다.

출력: data/tool_calling_v3/{train,valid,test}.jsonl  (mlx-lm chat 형식: messages + tools)
"""
import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = json.loads((ROOT / "tools" / "glople_tools.json").read_text(encoding="utf-8"))
EVAL_SET = ROOT / "eval_sets" / "human_style.jsonl"
SPLITS = ("train", "test")

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

# 템플릿 고르기용 분류: '~할 수 있는'은 활동 키워드에만, '~볼 수 있는'은 볼거리 키워드에만 붙인다.
# 나머지(문화, 와인, 음식 …)는 어느 명사에나 어울리는 공통 템플릿만 쓴다.
KEYWORD_ACTIVITIES = [
    "수영", "등산", "다이빙", "트레킹", "사파리", "온천욕", "탐험", "카누", "스노클링", "캠핑",
    "하이킹", "스포츠", "레크리에이션", "서핑", "보트 투어", "야외 활동", "철도 여행",
]
KEYWORD_SIGHTS = [
    "해안", "조각", "사원", "산", "호수", "폭포", "사막", "박물관", "미술관", "경치", "해변", "섬",
    "동물", "야경", "시장", "열기구", "성당", "성", "원숭이", "운하", "바다", "협곡", "동굴", "사찰",
    "모스크", "탑", "성벽", "화산", "빙하", "설산", "축제", "공연", "유적", "랜드마크", "식물원",
    "정원", "아치", "호랑이", "철새", "낙타", "열대우림", "바위", "산호", "빙벽", "하늘", "설경",
    "강", "대성당", "불상", "기념물", "전망대", "국립공원", "야생동물", "역사적인 장소", "전통 마을",
    "고대 유적", "역사적 건축",
]

REGIONS = {
    "train": [
        "서울", "부산", "제주", "강릉", "경주", "전주", "여수", "속초", "인천", "대구",
        "광주", "대전", "춘천", "통영", "안동", "포항", "남해", "양양", "가평", "단양",
        "수원", "평창", "정선", "파주", "부여", "문경", "청주", "서귀포",
        "도쿄", "오사카", "후쿠오카", "교토", "파리", "로마", "바르셀로나", "런던", "방콕", "다낭",
        "하노이", "타이베이", "뉴욕", "시드니", "발리", "싱가포르", "홍콩", "프라하", "이스탄불", "하와이",
        "상하이", "마드리드", "암스테르담", "베네치아", "두바이", "쿠알라룸푸르", "샌프란시스코", "취리히",
    ],
    "test": [
        "목포", "거제", "울산", "순천", "공주", "태안", "울릉도", "담양", "청송", "고창", "강화도", "합천",
        "삿포로", "나트랑", "치앙마이", "리스본", "비엔나", "밴쿠버", "아테네", "헬싱키", "마카오", "코타키나발루",
    ],
}

# 일정 저장용: 지역별 장소 (지역은 같은 split의 REGIONS에 있어야 한다)
REGION_PLACES = {
    "train": {
        "부산": ["해운대해수욕장", "광안리해수욕장", "감천문화마을", "태종대", "자갈치시장"],
        "서울": ["경복궁", "남산서울타워", "북촌한옥마을", "명동", "인사동"],
        "제주": ["성산일출봉", "우도", "한라산", "협재해수욕장", "섭지코지"],
        "경주": ["불국사", "석굴암", "동궁과 월지", "첨성대", "황리단길"],
        "강릉": ["안목해변", "오죽헌", "경포대", "주문진수산시장"],
        "전주": ["전주한옥마을", "경기전", "남부시장", "오목대"],
        "여수": ["돌산공원", "오동도", "향일암", "여수해상케이블카"],
        "속초": ["영금정", "아바이마을", "청초호", "속초관광수산시장"],
        "통영": ["동피랑마을", "미륵산", "통영 중앙시장"],
        "도쿄": ["센소지", "시부야 스크램블 교차로", "도쿄타워", "신주쿠 교엔"],
        "오사카": ["도톤보리", "오사카성", "유니버설 스튜디오 재팬", "구로몬 시장"],
        "교토": ["후시미 이나리 신사", "기요미즈데라", "아라시야마 대나무숲", "긴카쿠지"],
        "후쿠오카": ["다자이후 텐만구", "오호리 공원", "캐널시티 하카타", "나카스 포장마차 거리"],
        "파리": ["에펠탑", "루브르 박물관", "몽마르트르 언덕", "개선문"],
        "로마": ["콜로세움", "트레비 분수", "바티칸 박물관", "판테온"],
        "런던": ["대영박물관", "타워 브리지", "버킹엄 궁전"],
        "바르셀로나": ["구엘 공원", "카사 바트요", "람블라스 거리"],
        "방콕": ["왕궁", "왓 포", "카오산 로드"],
        "싱가포르": ["마리나 베이 샌즈", "가든스 바이 더 베이", "센토사섬"],
        "뉴욕": ["센트럴 파크", "자유의 여신상", "브루클린 브리지"],
        "프라하": ["프라하성", "구시가지 광장", "천문시계"],
        "다낭": ["미케비치", "오행산", "용다리"],
        "하노이": ["호안끼엠 호수", "문묘", "하노이 기찻길 마을"],
    },
    "test": {
        "순천": ["순천만습지", "순천만국가정원", "낙안읍성"],
        "리스본": ["벨렘탑", "제로니모스 수도원", "알파마 지구"],
        "목포": ["유달산", "갓바위", "삼학도"],
        "거제": ["외도 보타니아", "바람의 언덕", "학동흑진주몽돌해변", "매미성"],
        "담양": ["죽녹원", "메타세쿼이아길", "관방제림"],
        "공주": ["공산성", "무령왕릉", "마곡사"],
        "울릉도": ["나리분지", "도동항", "울릉도 독도전망대"],
        "삿포로": ["오도리 공원", "삿포로 시계탑", "모이와산 전망대"],
        "치앙마이": ["도이수텝", "님만해민", "왓 체디 루앙"],
        "밴쿠버": ["스탠리 공원", "그랜빌 아일랜드", "개스타운"],
        "비엔나": ["쇤브룬 궁전", "벨베데레 궁전", "슈테판 대성당"],
        "아테네": ["파르테논 신전", "아크로폴리스", "플라카 지구"],
    },
}

# 지역 목록 밖의 장소도 그 장소가 속한 지역과 같은 split에 둔다 (소매물도는 train 지역 통영, 태화강 국가정원은 test 지역 울산).
PLACES = {
    "train": sorted({p for places in REGION_PLACES["train"].values() for p in places}
                    | {"설악산", "남이섬", "보성 녹차밭", "사그라다 파밀리아", "왓 아룬", "바나힐",
                       "타임스스퀘어", "오페라 하우스", "빅벤", "스카이트리", "카를교", "하롱베이", "소매물도"}),
    "test": sorted({p for places in REGION_PLACES["test"].values() for p in places}
                   | {"대왕암공원", "간절곶", "태화강 국가정원"}),
}

# 장소 유형: 사용자가 쓰는 말 → category
CATEGORY_WORDS = {
    "관광지": ["가볼 만한 곳", "관광지", "명소", "볼거리", "갈 만한 데", "놀러 갈 곳", "관광 명소"],
    "문화시설": ["박물관", "미술관", "전시관", "역사관", "문화센터", "전시장"],
    "축제": ["축제", "행사", "공연", "이벤트", "지역 축제"],
    "레포츠": ["액티비티", "레포츠", "체험 활동", "즐길 거리", "야외 체험"],
    "음식점": ["맛집", "식당", "먹을 곳", "맛있는 집", "먹을 데", "레스토랑"],
    "숙박": ["숙소", "호텔", "잘 곳", "머물 곳", "묵을 곳", "리조트"],
}
# test 전용 카테고리 말. 여섯 카테고리 모두 train과 다른 말만 쓴다.
CATEGORY_WORDS_TEST = {
    "관광지": ["구경할 곳", "둘러볼 곳", "핫플"],
    "문화시설": ["기념관", "과학관", "갤러리"],
    "축제": ["페스티벌", "박람회", "콘서트"],
    "레포츠": ["체험거리", "수상 레저"],
    "음식점": ["밥집", "음식점"],
    "숙박": ["게스트하우스", "펜션", "민박"],
}

# 지역 없이 이 카테고리 말만 있으면 지역을 되묻는다 (키워드도 아니고 키워드와 뜻이 겹치지도 않는 말들).
# 문화시설·축제는 넣지 않는다: 박물관·미술관·축제·공연은 키워드라서 recommend_by_keywords로 가고,
# 전시관·행사·페스티벌·갤러리처럼 키워드와 뜻이 겹치는 말은 지역 없이 쓰면 정답이 두 갈래라 아예 만들지 않는다.
CLARIFY_CATEGORIES = ["관광지", "레포츠", "음식점", "숙박"]
# 카테고리 말이면서 키워드인 말. 지역이 있으면 search_destinations의 category, 없으면 키워드 추천.
KEYWORD_CATEGORY_WORDS = sorted({w for words in CATEGORY_WORDS.values() for w in words if w in KEYWORDS})

# 조건(query) → 어울리는 카테고리. '가성비 좋은 축제'처럼 어색한 조합을 만들지 않으려고 카테고리를 제한한다.
_ALL = list(CATEGORY_WORDS)
DESCRIPTORS = {
    "train": {
        "야경 예쁜": ["관광지", "음식점", "숙박"],
        "아이와 가기 좋은": _ALL,
        "조용한": ["관광지", "문화시설", "음식점", "숙박"],
        "가성비 좋은": ["레포츠", "음식점", "숙박"],
        "현지인이 가는": ["관광지", "축제", "음식점"],
        "데이트하기 좋은": _ALL,
        "사진 찍기 좋은": ["관광지", "문화시설", "축제", "음식점", "숙박"],
        "비 오는 날 가기 좋은": ["관광지", "문화시설", "음식점"],
        # v3: 조건 문구를 크게 늘린다 (test 쪽 주제인 혼자·반려견·부모님·뷰·초보·늦은 밤·무료는 쓰지 않음)
        "주차하기 편한": ["관광지", "문화시설", "음식점", "숙박"],
        "역에서 가까운": ["관광지", "문화시설", "음식점", "숙박"],
        "단체로 가기 좋은": _ALL,
        "친구들이랑 가기 좋은": _ALL,
        "분위기 좋은": ["관광지", "음식점", "숙박"],
        "새로 생긴": ["문화시설", "음식점", "숙박"],
        "예약 없이 갈 수 있는": ["관광지", "문화시설", "음식점"],
        "줄 안 서도 되는": ["관광지", "음식점"],
        "깔끔한": ["음식점", "숙박"],
        "한적한": ["관광지", "음식점", "숙박"],
        "유명한": _ALL,
        "인스타 감성인": ["관광지", "음식점", "숙박"],
        "실내에서 즐길 수 있는": ["관광지", "문화시설", "레포츠"],
        "더운 날 가기 좋은": ["관광지", "문화시설", "레포츠"],
        "걷기 편한": ["관광지"],
        "휠체어로 다니기 편한": ["관광지", "문화시설", "숙박"],
        "외국어 안내가 되는": ["관광지", "문화시설", "레포츠"],
        "바다 가까운": ["관광지", "음식점", "숙박"],
        "시내 중심에 있는": ["관광지", "음식점", "숙박"],
        "공항에서 가까운": ["숙박", "음식점"],
        "조식 나오는": ["숙박"],
        "수영장 있는": ["숙박"],
        "오래 머물기 좋은": ["숙박"],
        "해산물 맛있는": ["음식점"],
        "채식 메뉴가 있는": ["음식점"],
        "디저트 맛있는": ["음식점"],
        "브런치 먹기 좋은": ["음식점"],
        "역사 공부하기 좋은": ["관광지", "문화시설"],
        "아이들이 좋아하는": ["관광지", "문화시설", "축제", "레포츠"],
        "기념품 사기 좋은": ["관광지"],
        "산책하기 좋은": ["관광지"],
        "단풍 예쁜": ["관광지"],
        "벚꽃 구경하기 좋은": ["관광지", "축제"],
        "주말에도 덜 붐비는": ["관광지", "문화시설", "음식점"],
        "전통적인": ["관광지", "축제", "음식점", "숙박"],
        "규모가 큰": ["문화시설", "축제"],
        "가볍게 즐길 수 있는": ["레포츠", "축제"],
        "스릴 있는": ["레포츠"],
        "물놀이할 수 있는": ["레포츠", "관광지"],
        "커플이 하기 좋은": ["레포츠"],
        "반나절이면 되는": ["레포츠", "관광지"],
    },
    "test": {
        "혼자 가기 좋은": _ALL,
        "반려견과 갈 수 있는": ["관광지", "축제", "음식점", "숙박"],
        "부모님 모시고 가기 좋은": ["관광지", "문화시설", "축제", "음식점", "숙박"],
        "뷰 좋은": ["관광지", "음식점", "숙박"],
        "초보도 할 수 있는": ["레포츠"],
        "밤늦게까지 하는": ["축제", "음식점"],
        "무료로 즐길 수 있는": ["관광지", "문화시설", "축제"],
    },
}

# v3: 개수를 정하지 않은 말. 검색·후기 모두 limit을 붙이지 않는다.
VAGUE_COUNTS = [" 몇 군데", " 몇 곳만", " 조금만", " 두어 군데", " 여러 곳"]

# v3: 키워드 목록에 없는 꾸밈말. 키워드 추천 인자에는 넣지 않는다.
NON_KEYWORD_ADJECTIVES = ["예쁜", "멋진", "한적한", "유명한", "사람 적은", "분위기 있는", "힐링되는", "가까운"]
# v3: 사용자가 흔히 쓰는 비슷한 말 → 목록의 키워드 (human_style에 나온 말은 쓰지 않는다)
KEYWORD_SYNONYMS = {
    "바닷가": "바다", "해수욕장": "해변", "절": "사찰", "온천탕": "온천", "밤 풍경": "야경",
    "스쿠버 다이빙": "다이빙", "캠핑장": "캠핑", "서핑 스팟": "서핑", "꽃 정원": "정원", "옛날 성": "성",
}

MBTIS = [a + b + c + d for a in "EI" for b in "SN" for c in "TF" for d in "JP"]
GENDER_WORDS = {"남성": ["남자", "남성"], "여성": ["여자", "여성"]}
WHEN_WORDS = {"오늘": ["오늘"], "내일": ["내일"], "이번 주말": ["이번 주말", "주말에"], "다음 주": ["다음 주"]}
DAYS_WORDS = [("당일치기", 1), ("1박 2일", 2), ("2박 3일", 3), ("3박 4일", 4), ("4박 5일", 5), ("3일", 3), ("일주일", 7)]
PLAN_TITLES = {
    "train": ["가족 여행", "우정 여행", "혼자 떠나는 여행", "졸업 여행", "봄 여행", "여름휴가", "첫 해외여행", "주말 나들이"],
    "test": ["가을 여행", "겨울 휴가", "커플 여행", "부모님과 함께"],
}
HASHTAGS = {
    "train": ["맛집투어", "사진여행", "배낭여행", "가족여행", "쇼핑", "호캉스", "한달살기", "신혼여행",
              "골프여행", "뚜벅이여행", "드라이브", "가성비여행", "럭셔리여행", "아이동반"],
    "test": ["카페투어", "효도여행", "자전거여행", "워케이션", "미식투어", "로컬투어"],
}


# --- 한국어 조사 ---------------------------------------------------------------
# 조사는 슬롯 값의 마지막 글자 받침을 따른다. 영문자·숫자는 한국어로 읽은 소리로 판단한다
# (MBTI → 엠비티아이 → 가, 1박 2일 → 일 → 로).

_LATIN_FINAL = {"L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "R": "ㄹ"}    # 엘, 엠, 엔, 알. 나머지 글자는 받침 없음
_DIGIT_FINAL = {"0": "ㅇ", "1": "ㄹ", "3": "ㅁ", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ"}  # 영(십·백·천…), 일, 삼, 육, 칠, 팔


def _final_consonant(word):
    """마지막 글자의 받침을 돌려준다. 받침이 없으면 빈 문자열. 따옴표·괄호 같은 기호는 건너뛴다."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            jong = (ord(ch) - ord("가")) % 28
            return "" if jong == 0 else ("ㄹ" if jong == 8 else "받침")
        if ch.isascii() and ch.isalpha():
            return _LATIN_FINAL.get(ch.upper(), "")
        if ch.isdigit():
            return _DIGIT_FINAL.get(ch, "")
    return ""


def has_batchim(word):
    return _final_consonant(word) != ""


def josa(word, with_batchim, without_batchim):
    return word + (with_batchim if has_batchim(word) else without_batchim)


def with_ro(word):
    # 받침 없거나 ㄹ 받침 뒤에는 '로', 나머지 받침 뒤에는 '으로'
    return word + ("으로" if _final_consonant(word) not in ("", "ㄹ") else "로")


def josa_slots(name, word):
    """템플릿 슬롯 값과 조사가 붙은 형태들. 템플릿에서는 조사를 직접 쓰지 말고 {name_i}처럼 이 값을 쓴다."""
    return {
        name: word,
        name + "_i": josa(word, "이", "가"),
        name + "_eul": josa(word, "을", "를"),
        name + "_eun": josa(word, "은", "는"),
        name + "_gwa": josa(word, "과", "와"),
        name + "_ro": with_ro(word),
        name + "_ieyo": josa(word, "이에요", "예요"),
        name + "_ya": josa(word, "이야", "야"),
        name + "_igo": josa(word, "이고", "고"),
        name + "_ira": josa(word, "이라는", "라는"),
    }


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


def join_missing(words):
    """되묻기용 나열: 나이 / 나이와 성별 / 나이, 성별, MBTI. 뒤에 붙는 조사는 호출한 쪽이 마지막 단어에 맞춘다."""
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return josa(words[0], "과", "와") + " " + words[1]
    return ", ".join(words)


# --- 문장 템플릿 (train / test 분리) --------------------------------------------------

KEYWORD_TEMPLATES = {
    "train": {
        "any": [
            "{kws} 좋아하는데 여행지 추천해줘",
            "{kws} 위주로 갈 만한 곳 추천해 주세요",
            "요즘 {kws}에 꽂혔어. 어디로 여행 가면 좋을까?",
            "{kws} 테마로 여행 가고 싶어요",
            "{kws_ro} 유명한 여행지 골라줘",
            "여행 가면 {kws} 위주로 다니는 편이에요. 어디가 좋을까요?",
        ],
        "activity": [
            "{kws} 즐길 수 있는 여행지 어디 있어?",
            "이번 휴가엔 {kws} 즐기고 싶은데 어디가 좋아?",
        ],
        "sight": [
            "{kws} 볼 수 있는 여행지 추천 부탁해요",
            "{kws} 보러 여행 가고 싶은데 어디가 좋아?",
        ],
    },
    "test": {
        "any": [
            "{kws_gwa} 관련된 여행지 몇 군데만 알려줘",
            "여행 가면 {kws_i} 제일 중요해요. 어디가 좋을까요?",
            "{kws} 좋아하는 사람한테 딱인 여행지 있을까?",
        ],
        "activity": ["{kws} 할 수 있는 여행지 몇 군데만 알려줘"],
        "sight": ["{kws} 구경하기 좋은 여행지 뭐 있을까요?"],
    },
}

# 지역 없이 대상 하나만 말하는 짧은 요청. 같은 틀에 키워드(박물관 …)가 들어가면 recommend_by_keywords,
# 키워드가 아닌 카테고리 말(맛집 …)이 들어가면 지역을 되묻는다. 두 경우를 같은 틀로 만들어 차이를 배우게 한다.
SUBJECT_REQUEST_TEMPLATES = {
    "train": [
        "{c} 추천해줘",
        "{c} 찾아줘",
        "{c} 좀 알려주세요",
        "{c} 추천 부탁드려요",
        "{c} 어디가 좋아?",
        "{c_eul} 찾고 있어요",
        "혹시 {c} 추천해 주실 수 있나요?",
        "{c} 리스트 좀 뽑아줘",
        "{c} 몇 군데 골라줄래?",
        "{c} 어디 어디 있어?",
        "{c} 좀 골라 주세요",
        "{c} 위주로 찾아줘",
    ],
    "test": [
        "{c} 좀 알려줄래?",
        "{c} 뭐 있는지 궁금해요",
        "{c} 하나만 골라줘",
        "{c_eul} 알아보는 중인데 추천해 줄래요?",
    ],
}
# 여행이 이미 정해진 듯한 요청. 목적지가 빠진 것이 분명하므로 카테고리 말로만 만들어 지역을 되묻는다.
# ('다음 달에 여행 가는데 박물관 알아보고 싶어요'는 키워드 추천과 지역 되묻기 두 갈래로 읽혀 만들지 않는다.)
TRIP_REQUEST_TEMPLATES = {
    "train": [
        "이번 주말에 놀러 가는데 {c} 추천 좀 해줘",
        "가족이랑 여행 가는데 {c} 추천해 줄 수 있어요?",
        "다음 달에 여행 가는데 {c} 미리 알아보고 싶어요",
        "곧 여행 갈 건데 {c} 미리 좀 찾아 줄래?",
    ],
    "test": [
        "여행 가기 전에 {c} 좀 찾아봐 줘",
        "친구들이랑 놀러 갈 건데 {c} 추천 좀",
    ],
}

SEARCH_TEMPLATES = {
    "train": [
        "{region} {phrase} 추천해줘",
        "{region}에 {phrase} 있어?",
        "{region} 가는데 {phrase} 좀 찾아줘",
        "{region}에서 {phrase} 알려주세요",
        "이번에 {region} 여행 가요. {phrase} 어디가 좋아요?",
        "{region} 근처 {phrase} 찾아줄래?",
    ],
    "test": [
        "{region} 쪽에 {phrase} 뭐 있는지 궁금해",
        "다음 달에 {region} 가는데 {phrase} 골라줘",
        "{region} 여행 계획 중인데 {phrase} 있으면 알려줘",
    ],
}

ROUTES_TEMPLATES = {
    "train": [
        "저는 {age} {gw}이고 MBTI는 {mbti_ieyo}. 비슷한 사람들 여행 루트 보여주세요",
        "{mbti} {age} {gw}인데 나랑 비슷한 여행자들은 어디 다녀왔어?",
        "나 {gw}, {age}, {mbti}. 성향 비슷한 사람 루트 추천해줘",
        "{age} {mbti} {gw}한테 맞는 여행 루트 있을까?",
        "MBTI {mbti}이고 {age} {gw}입니다. 비슷한 여행자 코스 추천 부탁드려요",
        # v3: 세 가지가 다 있는 짧은 말투 → 되묻지 않고 바로 부른다
        "{mbti} {gw} {age}인데 비슷한 사람들 여행 루트 보여줘",
        "{age} / {gw} / {mbti} 비슷한 여행자들 어디 갔어?",
        "나이 {age}, {gw}, {mbti} 기준으로 비슷한 사람 루트 궁금해요",
        "{gw} {mbti} {age} 나랑 성향 비슷한 여행자 코스 ㄱㄱ",
    ],
    "test": [
        "{gw}이고 {age}, 엠비티아이는 {mbti_ya}. 나 같은 사람들 여행 루트 궁금해",
        "{mbti}인 {age} {gw} 여행자 루트 알려줘",
    ],
}

DETAIL_TEMPLATES = {
    "train": [
        "{p} 어떤 곳이야?", "{p}에 대해 알려줘", "{p} 정보 좀 보여줘", "{p} 소개해 주세요",
        "{p} 가보려는데 어떤 곳인지 궁금해요", "{p} 어떤 데야?", "{p_eun} 어떤 곳인가요?",
        "{p} 관련 정보 찾아줘", "{p} 뭐가 유명한지 알려줄래?",
    ],
    "test": [
        "{p} 설명 부탁해", "{p} 볼 만해? 어떤 곳인지 알려줘", "{p} 소개 좀 해줄래요?",
        "{p_i} 어떤 곳인지 간단히 알려 주세요",
    ],
}

REVIEWS_TEMPLATES = {
    "train": [
        "{s} 후기 보여줘", "{s} 다녀온 사람들 리뷰 있어?", "{s} 리뷰 찾아줘", "사람들이 {s} 어땠다고 해?",
        "{s} 여행 후기 검색해줘", "{s} 후기 좀 찾아 주세요", "{s} 갔다 온 사람들 후기 궁금해", "{s} 리뷰 어때?",
        # v3: 막연한 개수를 말해도 search_reviews에는 limit 칸이 없다
        "{s} 후기 조금만 찾아줘", "{s} 다녀온 사람들 글 몇 개 보여줘", "{s} 리뷰 두어 개만 찾아줄래?",
    ],
    "test": [
        "{s} 실제로 가본 사람들 얘기 듣고 싶어", "{s} 후기 몇 개만 보여줄래?",
        "{s} 다녀온 분들 평가 좀 알려 주세요", "{s} 방문 후기 있으면 보여줘",
    ],
}

GLOPLER_REGION_TEMPLATES = {
    "train": [
        "{r} 현지 가이드 찾아줘", "{r}에 사는 글로플러 있어?", "{r} 여행 도와줄 글로플러 추천해줘",
        "{r} 전문가랑 상담하고 싶어요", "{r} 글로플러 목록 보여줘", "{r} 현지 전문가 있나요?",
        "{r} 잘 아는 글로플러 찾아 주세요", "{r} 여행 상담해 줄 사람 있어?", "{r} 글로플러 검색해줘",
        "{r}에서 활동하는 글로플러 보여줘",
    ],
    "test": [
        "{r} 잘 아는 현지인 연결해 줄 수 있어?", "{r} 쪽 글로플러 누가 있는지 보여줘",
        "{r} 현지 가이드해 줄 글로플러 있을까요?", "{r} 여행 전문가 좀 찾아봐 줘",
    ],
}
GLOPLER_TAG_TEMPLATES = {
    "train": ["#{t} 글로플러 검색해줘", "{t} 해시태그 단 글로플러 찾아줘", "#{t} 태그 있는 글로플러 보여줘",
              "{t} 전문 글로플러 있어?", "#{t} 관련 글로플러 추천해 주세요", "{t} 쪽 잘 아는 글로플러 찾아줘",
              "해시태그 #{t} 달린 글로플러 목록 보여줘"],
    "test": ["#{t} 쪽으로 활동하는 글로플러 알려줘", "{t} 해시태그로 글로플러 찾아볼래?", "#{t} 하는 글로플러 있을까요?"],
}

WEATHER_TEMPLATES = {
    "train": ["{r} {w} 날씨 어때?", "{w} {r} 비 와?", "{w} {r} 가는데 날씨 괜찮아?", "{r} {w} 기온 궁금해요"],
    "test": ["{r} {w} 우산 챙겨야 할까?", "{w} {r} 날씨 좀 봐줘"],
}

PLAN_TEMPLATES = {
    "train": [
        "{r} {d_ro} {pl} 넣어서 일정 저장해줘",
        "{pl} 가는 {r} {d} 일정 만들어서 저장해 주세요",
        "{r} {d} 여행 일정 짰어. {pl} 순서로 저장해줘",
    ],
    "test": ["{r} {d} 코스로 {pl} 담아서 내 일정에 추가해 줘"],
}
# 사용자가 일정 이름을 정할 때 문장 앞에 붙는 말. 이것도 문장 틀이라 test는 train과 다른 말을 쓴다.
PLAN_TITLE_TEMPLATES = {
    "train": ["{t_ira} 이름으로 "],
    "test": ["제목은 {t_ro} 해서 "],
}

PARALLEL_TEMPLATES = {
    "train": {
        "detail_reviews": ["{p} 어떤 곳인지랑 후기도 보여줘", "{p} 정보랑 사람들 리뷰 같이 알려줘"],
        "weather_search": ["{w} {r} 가는데 날씨랑 {c} 같이 알려줘", "{r} {w} 날씨 보고 {c}도 추천해줘"],
        "search_gloplers": ["{r} {c} 찾아주고 현지 글로플러도 추천해줘", "{r} {c} 알려주고 글로플러도 찾아줘"],
    },
    "test": {
        "detail_reviews": ["{p} 소개하고 후기 둘 다 보여줘"],
        "weather_search": ["{r} {w} 날씨 확인하고 {c}도 찾아줘"],
        "search_gloplers": ["{r} {c} 검색하면서 그 지역 글로플러도 보여줘"],
    },
}

# 되묻기: 루트 추천에서 사용자가 준 정보 → 템플릿. 빠진 정보만 되묻는다.
ROUTES_CLARIFY_TEMPLATES = {
    "train": {
        "": [
            "나랑 비슷한 사람들 여행 루트 보여줘", "비슷한 여행자들 루트 추천해 주세요",
            "나 같은 사람들은 어디로 여행 갔는지 궁금해", "성향 비슷한 사람들이 다녀온 코스 알려줘",
            "저랑 비슷한 여행자들 루트 좀 볼 수 있을까요?", "비슷한 사람들 여행 코스 추천해줘",
            "나랑 취향 비슷한 여행자 루트 있어?", "나랑 잘 맞을 것 같은 여행 루트 추천해줘",
            "비슷한 또래 여행자들은 어디 갔어?", "저 같은 여행자들이 다녀온 루트가 궁금해요",
        ],
        "mbti": [
            "{mbti}인데 비슷한 사람들 루트 추천해줘", "MBTI {mbti_ieyo}. 저랑 비슷한 여행자 코스 있을까요?",
            "저 {mbti}인데 성향 비슷한 사람들 여행 루트 궁금해요", "{mbti}들은 주로 어디로 여행 가? 루트 보여줘",
        ],
        "gender": [
            "{gw}인데 나랑 비슷한 사람들 여행 루트 보여줘", "저 {gw_ieyo}. 비슷한 여행자 루트 있을까요?",
            "{gw} 여행자들이 다녀온 루트 추천해줘",
        ],
        "age": [
            "{age}인데 비슷한 사람들 루트 궁금해", "저 {age_ieyo}. 비슷한 또래 여행자 코스 보여 주세요",
            "{age} 여행자들은 어디 다녀왔어? 루트 보여줘",
        ],
        "gender+mbti": [
            "{mbti} {gw}인데 비슷한 여행자 루트 있어?", "나 {gw_igo} {mbti_ya}. 비슷한 사람 루트 추천해줘",
            "{gw}, {mbti}. 성향 비슷한 여행자 코스 보여줘",
        ],
        "age+mbti": [
            "{age} {mbti_ieyo}. 비슷한 사람들 여행 루트 보여주세요", "{mbti} {age}인데 나랑 비슷한 여행자 어디 갔어?",
            "나 {age}, {mbti}. 비슷한 사람 루트 추천해줘",
        ],
        "age+gender": [
            "{age} {gw}인데 비슷한 여행자 루트 있어?", "저는 {age} {gw}입니다. 비슷한 여행자 코스 추천해 주세요",
            "{gw}, {age}. 나 같은 사람들 여행 루트 궁금해",
        ],
    },
    "test": {
        "": ["성향 비슷한 여행자들 코스 궁금해", "나 같은 여행자들은 어디 가는지 루트 좀 보여줄래?",
             "취향 비슷한 사람들 다녀온 루트 알려줄래요?"],
        "mbti": ["{mbti} 여행자 루트 알려줘", "엠비티아이는 {mbti_ya}. 나 같은 사람들 루트 궁금해"],
        "gender": ["{gw} 혼자 여행 다니는데 비슷한 사람 루트 있어?"],
        "age": ["{age} 직장인인데 비슷한 사람들 여행 코스 궁금해요"],
        "gender+mbti": ["{gw_igo} {mbti_ya}. 나 같은 사람들 여행 루트 궁금해"],
        "age+mbti": ["{mbti}인 {age} 여행자 루트 알려줘"],
        "age+gender": ["{gw_igo} {age_ya}. 나랑 비슷한 사람들 루트 보여줘"],
    },
}

# {w}에는 시점 말이 들어가거나 빈칸이 된다.
WEATHER_CLARIFY_TEMPLATES = {
    "train": [
        "{w} 날씨 어때?", "{w} 비 와?", "{w} 날씨 알려줘", "{w} 날씨 좀 알려주세요",
        "{w} 여행 가는데 날씨 괜찮을까?", "{w} 많이 추워?", "{w} 우산 필요해?", "{w} 기온 몇 도야?",
        "{w} 눈 온대?", "{w} 날씨 예보 좀 봐줄래?", "놀러 가려는데 {w} 날씨 어떨까요?",
    ],
    "test": ["{w} 날씨 확인해 줄래?", "{w} 바람 많이 불어?", "{w} 소풍 가기 괜찮은 날씨야?", "{w} 덥대?"],
}

PLAN_CLARIFY_TEMPLATES = {
    "train": {
        "none": ["일정 저장해줘", "여행 일정 만들어줘", "내 여행 일정 하나 저장해 줄래?", "새 여행 일정 등록하고 싶어요",
                 "여행 계획 저장해줘", "일정 추가해줘", "이번 여행 일정 저장해 주세요", "여행 일정 하나 만들어서 저장해 줘"],
        "region": ["{r} 일정 저장해줘", "{r} 여행 일정 만들어줘", "{r} 가는 일정 하나 저장해 주세요",
                   "다음 달에 {r} 가는데 일정 저장해 줘", "{r} 여행 계획 저장해줄래?"],
        "region_days": ["{r} {d} 일정 저장해줘", "{r} {d_ro} 일정 하나 만들어 주세요", "{d} {r} 여행 일정 저장해 줄래?"],
    },
    "test": {
        "none": ["내 일정에 추가해 줘", "여행 계획 하나 만들어서 담아 줘", "일정 새로 하나 등록해 줄래요?"],
        "region": ["{r} 여행 일정 내 계정에 남겨 줘", "{r} 가는 거 일정으로 등록해 줄래?"],
        "region_days": ["{r} {d} 일정으로 등록해 줘", "{r} {d_ro} 다녀올 건데 일정 저장 부탁해요"],
    },
}

# 후기를 찾을 대상이 없는 요청 ('거기', '그 동네'가 가리키는 곳이 대화에 없음)
REVIEWS_CLARIFY_TEXTS = {
    "train": [
        "후기 좀 보여줘", "리뷰 찾아줘", "다녀온 사람들 후기 있어?", "거기 리뷰 어때?", "그 동네 후기 좀 찾아봐 줘",
        "사람들 후기 보고 싶어요", "실제로 가본 사람들 리뷰 있나요?", "거기 가본 사람들 평 어때?", "후기 검색해 줘",
        "그곳 리뷰 몇 개 보여주세요", "여행 후기 좀 찾아 주세요", "그 장소 다녀온 사람들 글 있어?",
        "리뷰부터 보고 정할래. 보여줘", "아까 말한 데 후기 있어?", "후기 많은 순으로 보여줘", "방금 그곳 리뷰 찾아줘",
        "리뷰 좀 검색해 줄래?", "사람들 반응 어땠는지 후기 찾아봐", "가 본 사람들 리뷰 궁금해", "거기 후기 괜찮아?",
        "그 여행지 후기 있나요?", "거기 다녀온 사람들 얘기 좀 찾아줘",
    ],
    "test": ["그 장소 후기 좀 알려줘", "다른 사람들은 거기 어땠대?", "리뷰 몇 개만 찾아줄래요?"],
}

# 취향도 지역도 없는 추천 요청
OPEN_CLARIFY_TEXTS = {
    "train": [
        "여행지 추천해줘", "어디로 여행 가면 좋을까?", "여행 가고 싶은데 어디가 좋아?", "이번 휴가 어디로 떠날지 추천해 줘",
        "놀러 갈 데 아무 데나 골라줘", "추천 여행지 알려주세요", "요즘 뜨는 여행지 어디야?",
        "어디든 좋으니까 여행지 하나 골라줘", "여행 가고 싶다 추천 좀", "연휴에 떠날 만한 여행지 있을까요?",
        "여행 추천 부탁드립니다", "갈 만한 여행지 아무거나 알려줘", "여행 어디로 가야 할지 모르겠어. 추천해 줘",
        "이번 방학에 여행 가려는데 어디 좋을까요?", "기분 전환하러 떠나고 싶은데 어디로 갈까?", "여행지 좀 골라 주세요",
        "주말에 어디 갈지 추천해 줘", "여행 가려는데 어디 갈까?", "아무 여행지나 하나 추천해 주세요",
        "국내든 해외든 상관없으니 여행지 추천해줘", "어디로 떠나면 좋을지 골라 줄래?", "여행지 추천 좀 해 줄 수 있어?",
        "여름휴가 어디로 갈지 추천 부탁해요", "친구랑 여행 갈 건데 어디가 좋을까?",
    ],
    "test": ["여행 어디로 갈지 고민인데 골라줄래?", "여행지 하나만 찍어줘", "떠나고 싶은데 어디로 갈지 정해 줘",
             "가볍게 다녀올 여행지 추천 좀 해 주세요"],
}

GREETINGS = {
    "train": [
        "안녕", "안녕하세요", "반가워", "하이", "안녕!", "안녕하세요!", "안녕하세요~", "반가워요", "hi", "hello",
        "헬로", "하이루", "안뇽", "방가방가", "좋은 아침", "좋은 아침이에요", "굿모닝", "좋은 저녁이에요", "안녕 반가워",
        "처음 뵙겠습니다", "처음 왔어요", "여기 처음 써 봐요", "안녕하세요 처음 써 봐요", "오랜만이야", "또 왔어",
        "다시 왔어요", "저기요", "계세요?", "하이 ㅎㅎ", "안녕하세요 반가워요", "헬로우", "여보세요?", "안녕하세용",
        "하잉", "반가워 친구", "안녕 도우미", "도우미님 안녕하세요", "Glople 도우미 안녕", "잘 지냈어?", "안녕하십니까",
        "좋은 하루예요", "안녕 오늘도 잘 부탁해", "반갑다", "hey", "안녕~~", "안녕하세요 좋은 아침입니다",
        "반가워요 잘 부탁드려요", "안녕 나 왔어", "헬로 반가워", "안녕하세요 도우미님", "하이 반가워요", "안녕! 잘 지내?",
        "오 안녕하세요", "굿모닝 반가워", "안녕하세요 오랜만이에요",
    ],
    "test": ["ㅎㅇ", "반갑습니다", "하이요", "안녕하세염", "좋은 오후예요", "처음 인사드려요", "헬로 헬로", "오 안녕?",
             "안녕 오랜만!", "반가워용", "안녕하신가요", "굿 애프터눈"],
}
THANKS = {
    "train": [
        "고마워", "감사합니다", "덕분에 잘 다녀왔어", "도움 많이 됐어", "고마워요", "감사해요", "정말 감사합니다",
        "고맙습니다", "ㄱㅅ", "ㄳ", "ㄱㅅㄱㅅ", "덕분에 일정 잘 짰어요", "추천해 준 곳 진짜 좋았어", "덕분에 여행 잘 마쳤어요",
        "오 좋다 고마워", "완전 도움 됐어요 감사합니다", "친절하게 알려줘서 고마워", "많은 도움이 됐습니다", "수고했어",
        "수고하셨어요", "알려준 대로 다녀왔는데 최고였어", "고마워 덕분에 살았다", "와 감사합니다 ㅠㅠ", "역시 최고야 고마워",
        "덕분에 가족들이 좋아했어요", "오늘도 고마워", "늘 감사해요", "잘 썼어 고마워", "땡스", "thank you", "고마워 진짜",
        "너무 고마워!", "감사감사", "감사드려요", "덕분에 즐거운 여행 했어요", "이번에도 도움 받았네 고마워", "큰 도움 됐어요",
        "친구들도 다 만족했어 고마워", "thanks",
    ],
    "test": ["땡큐", "정말 고마워요", "덕분이에요 감사합니다", "고마워 최고다", "감사합니당", "진심으로 감사드립니다",
             "고마워 잘 다녀올게", "알려주셔서 고마워요"],
}
# 감사 인사 속 지역명은 도구 인자가 아니다 (labeling_rules.md 3절)
THANKS_WITH_REGION = {
    "train": ["덕분에 {r} 여행 잘 다녀왔어요", "{r} 다녀왔는데 추천해 준 데 다 좋았어. 고마워!", "{r}에서 진짜 즐거웠어요. 감사합니다"],
    "test": ["{r} 여행 무사히 마쳤어요. 도와주셔서 감사해요", "{r} 갔다 왔는데 덕분에 완전 좋았어 고마워"],
}
ABILITY_QUESTIONS = {
    "train": [
        "뭐 할 수 있어?", "어떤 기능 있어?", "너는 뭘 도와줄 수 있어?", "여기서 뭘 물어보면 돼?", "어떻게 쓰는 거야?",
        "사용법 좀 알려줄래?", "무엇을 도와주시나요?", "이 챗봇은 뭐 하는 거예요?", "할 줄 아는 거 알려줘",
        "어디까지 도와줄 수 있어?", "너 무슨 봇이야?", "처음인데 뭐부터 물어보면 돼?", "기능 목록 보여줘", "도움말", "help",
        "넌 뭐 하는 애야?", "너한테 뭘 시킬 수 있어?", "어떤 걸 물어볼 수 있나요?", "할 수 있는 일이 뭐예요?",
        "자기소개 해줘", "무슨 도움 받을 수 있어?", "너 뭐 할 줄 알아?", "이 서비스 어떻게 이용해요?",
        "여기 도우미는 뭘 해 주나요?", "기능 설명 좀 해주세요", "너 할 줄 아는 게 뭐야?", "뭘 부탁하면 돼?",
        "어떤 질문에 답해 줄 수 있어?", "도와줄 수 있는 거 다 말해 봐", "Glople 도우미는 무슨 일을 해?",
        "어떤 걸 해 줄 수 있는지 궁금해요", "이 앱에서 너는 뭐 담당이야?", "할 수 있는 거 목록으로 알려줘",
        "뭐 해 줄 수 있는지부터 알려줘", "어떤 서비스 제공해?", "주로 뭘 도와줘?", "여기서 할 수 있는 게 뭐가 있어?",
        "너 뭐 해 줄 수 있는데?", "무슨 기능 쓸 수 있어요?", "이 도우미 어떻게 활용하면 돼?",
    ],
    "test": ["넌 누구야?", "정체가 뭐야?", "너 뭐 하는 AI야?", "무슨 일을 할 수 있는지 설명해 줄래요?",
             "이거 어디에 쓰는 거예요?", "너한테 뭐 물어보면 되는데?", "사용 방법 알려 주세요", "기능이 뭐가 있나요?"],
}
OFF_TOPIC_REQUESTS = {
    "train": [
        "파이썬 코드 짜줘", "주식 종목 추천해줘", "수학 숙제 도와줘", "이메일 대신 써줘", "자바스크립트 에러 좀 봐줘",
        "엑셀 수식 좀 만들어 주세요", "김치찌개 레시피 알려줘", "연애 상담 좀 해줘", "보고서 요약 부탁드려요",
        "비트코인 지금 사도 돼?", "로또 번호 추천해줘", "어제 축구 경기 결과 알려줘", "노트북이 느린데 고치는 법 알려줘",
        "자기소개서 첨삭해 주실 수 있나요?", "이 문장 맞춤법 검사해줘", "다이어트 식단 짜줘", "헬스 운동 루틴 짜줘",
        "넷플릭스 드라마 추천해줘", "아이폰이랑 갤럭시 중에 뭐 살까?", "SQL 쿼리 짜줘", "부동산 투자 어떻게 해야 돼?",
        "생일 축하 메시지 써줘", "시 한 편 써 줄래?", "고양이 이름 지어줘", "삼행시 지어줘", "끝말잇기 하자",
        "재밌는 농담 하나 해줘", "오늘 운세 봐줘", "타로 봐 줄 수 있어?", "리포트 대신 써줘", "논문 요약해 주세요",
        "이 문장 영어로 번역해줘", "영어 단어 외우는 법 알려줘", "회의록 정리해줘", "면접 예상 질문 뽑아줘",
        "컴퓨터 조립 견적 짜줘", "게임 공략 알려줘", "오늘 저녁 뭐 해 먹지?", "강아지 훈련 방법 알려줘", "청소 꿀팁 알려줘",
        "연말정산 어떻게 해?", "대출 이자 계산해줘", "발표 대본 써줘", "소설 줄거리 만들어줘", "영화 추천해줘",
        "노래 추천해 주세요", "책 추천해줘", "회사 그만둘까 고민인데 조언해줘", "사주 봐줘", "내 사진 보정해 줄 수 있어?",
        "유튜브 채널 이름 지어줘", "블로그 글 대신 써줘", "결혼식 축사 써줘", "이력서 좀 고쳐 주세요",
        "머신러닝 개념 설명해줘", "리액트 컴포넌트 만들어줘", "맥북 단축키 알려줘", "와이파이가 안 되는데 어떻게 해?",
        "수능 수학 문제 풀어줘", "화학 공식 설명해 주세요",
    ],
    "test": [
        "영어 번역 좀 해줘", "코인 시세 알려줘", "중국어 숙제 좀 봐줄래?", "자바 코드 디버깅해 줘", "해외 주식 뭐 살까?",
        "오늘 야구 스코어 알려줘", "엄마 생신 축하 문자 써 줘", "포토샵 하는 법 알려줘", "레포트 표절 검사해 줘",
        "퇴사 사유서 대신 써 줄래요?", "피아노 악보 구해 줘", "반찬 레시피 좀 알려 주세요", "짧은 동화 하나 지어 줘",
        "영어 이메일 첨삭해 줘", "노트북 추천해 줘", "다이어트 방법 알려 줘", "카톡 답장 대신 써 줘",
        "물리 문제 풀이 부탁해요", "주식 차트 분석해 줘",
    ],
}
# 거절 문장 앞에 붙는 말. '혹시'는 '~하자'로 끝나는 제안 문장에는 붙이지 않는다.
# 요청은 반말·존댓말, 질문·부탁이 섞여 있으므로 어느 쪽에도 어울리는 말만 쓴다
# (v2 첫 버전의 '다른 거 하나만 물어볼게. '는 '… 물어볼게. 이력서 좀 고쳐 주세요'처럼 말투와 내용이 어긋났다).
OFF_TOPIC_LEADS = {
    "train": ["", "혹시 ", "여행 얘기는 아닌데 ", "그나저나 "],
    "test": ["", "저기 ", "여행이랑은 상관없는데 "],
}

GREETING_ANSWER = "안녕하세요! Glople 여행 도우미예요. 가고 싶은 지역이나 여행 취향을 알려주시면 여행지를 찾아드릴게요."
THANKS_ANSWER = "도움이 되어서 기뻐요! 여행 계획이 더 필요하면 언제든 말씀해 주세요."
ABILITY_ANSWER = ("취향 키워드로 여행지 추천, 지역별 관광지·맛집·숙소 검색, 비슷한 여행자의 루트 추천, "
                  "여행 후기와 글로플러 찾기, 날씨 확인, 여행 일정 저장을 도와드릴 수 있어요.")
DECLINE_ANSWER = "저는 여행 계획을 돕는 Glople 도우미라서 그 요청은 도와드리기 어려워요. 여행지 추천이나 일정이 필요하면 말씀해 주세요."

WEATHER_CLARIFY_ANSWER = "어느 지역 날씨를 알려드릴까요?"
PLAN_CLARIFY_ANSWERS = {
    "none": "어느 지역으로 며칠 동안, 어떤 장소를 넣어서 저장할까요?",
    "region": "며칠 일정으로, 어떤 장소를 넣어서 저장할까요?",
    "region_days": "어떤 장소를 넣어서 저장할까요?",
}
REVIEWS_CLARIFY_ANSWER = "어느 장소나 지역의 후기를 찾아드릴까요?"
OPEN_CLARIFY_ANSWER = ("어떤 여행을 좋아하시는지 알려주시겠어요? "
                       "바다, 야경, 사찰처럼 취향이나 가고 싶은 지역을 말씀해 주시면 찾아드릴게요.")
ROUTE_FIELDS = ["나이", "성별", "MBTI"]


# --- 예시 생성기 -----------------------------------------------------------------
# 각 생성기는 (사용자 발화, 정답) 을 돌려준다. 정답은 tool_calls 리스트이거나 문자열(바로 답변).

def call(tool_name, /, **arguments):
    return {"name": tool_name, "arguments": arguments}


def tidy(text):
    """빈 슬롯 때문에 생긴 겹친 공백을 정리한다."""
    return " ".join(text.split())


def pick_kind(rng, kinds, kind):
    """세부 종류를 정한다. build()는 종류별 할당량을 채우려고 kind를 넘기고, 검사용으로 부를 때는 가중치대로 고른다."""
    if kind is not None:
        return kind
    return rng.choices([k for k, _ in kinds], weights=[w for _, w in kinds])[0]


def keyword_kind(keyword):
    if keyword in KEYWORD_ACTIVITIES:
        return "activity"
    if keyword in KEYWORD_SIGHTS:
        return "sight"
    return "theme"


KEYWORD_KINDS = [("category", 12), ("taste", 88)]
# v3: 사는 곳을 말하는 앞말. 목적지가 아니라서 지역 검색이 아니라 키워드 추천이다.
RESIDENCE_LEADS = {
    "train": ["{r}에 사는데 ", "{r}에 살고 있는데 ", "평소엔 {r}에서 지내는데 ", "{r} 토박이인데 "],
    "test": [],
}


def gen_keywords(rng, split, kind=None):
    if pick_kind(rng, KEYWORD_KINDS, kind) == "category":
        # 지역 없이 키워드인 장소 유형만 말한 요청 ('박물관 추천해줘'). 같은 틀의 '맛집 추천해줘'는 gen_clarify가 되묻는다.
        keywords = rng.sample(KEYWORD_CATEGORY_WORDS, rng.choice([1, 1, 2]))
        text = rng.choice(SUBJECT_REQUEST_TEMPLATES[split]).format(**josa_slots("c", join_words(rng, keywords)))
        return text, [call("recommend_by_keywords", keywords=keywords)]

    keywords = rng.sample(KEYWORDS, rng.choice([1, 2, 2, 3]))
    shown = list(keywords)
    if split == "train":
        # v3: 비슷한 말로 말해도 인자는 목록의 키워드
        synonyms = {canon: word for word, canon in KEYWORD_SYNONYMS.items()}
        if rng.random() < 0.2:
            for i, k in enumerate(keywords):
                if k in synonyms:
                    shown[i] = synonyms[k]
                    break
            else:
                word, canon = rng.choice(list(KEYWORD_SYNONYMS.items()))
                if canon not in keywords:
                    keywords[0], shown[0] = canon, word
    kinds = {keyword_kind(k) for k in keywords}
    pools = KEYWORD_TEMPLATES[split]
    templates = list(pools["any"])
    if len(kinds) == 1 and kinds != {"theme"}:
        templates += pools[kinds.pop()]
    words = join_words(rng, shown)
    if split == "train" and rng.random() < 0.2:
        # v3: 목록에 없는 꾸밈말은 인자에 넣지 않는다
        words = f"{rng.choice(NON_KEYWORD_ADJECTIVES)} {words}"
    text = rng.choice(templates).format(**josa_slots("kws", words))
    if split == "train" and rng.random() < 0.08:
        text = rng.choice(RESIDENCE_LEADS[split]).format(r=rng.choice(REGIONS[split])) + text
    return text, [call("recommend_by_keywords", keywords=keywords)]


def category_word(rng, split, category):
    words = CATEGORY_WORDS_TEST[category] if split == "test" else CATEGORY_WORDS[category]
    return rng.choice(words)


def gen_search(rng, split):
    region = rng.choice(REGIONS[split])
    category = rng.choice(list(CATEGORY_WORDS))
    cat = category_word(rng, split, category)
    fitting = [d for d, categories in DESCRIPTORS[split].items() if category in categories]
    desc = rng.choice(fitting) if rng.random() < 0.55 else None
    limit = rng.choice([3, 5, 10]) if rng.random() < 0.2 else None

    phrase = f"{desc} {cat}" if desc else cat
    if limit:
        phrase += rng.choice([f" {limit}곳", f" {limit}개", f" {limit}군데"])
    elif split == "train" and rng.random() < 0.15:
        # v3: 막연한 개수는 limit도 query도 아니다
        phrase += rng.choice(VAGUE_COUNTS)
    text = rng.choice(SEARCH_TEMPLATES[split]).format(region=region, phrase=phrase)

    arguments = {"region": region, "category": category}
    if desc:
        arguments["query"] = desc
    if limit:
        arguments["limit"] = limit
    return text, [call("search_destinations", **arguments)]


def route_slots(rng, age, gender):
    mbti = rng.choice(MBTIS)
    slots = {}
    slots.update(josa_slots("age", rng.choice([f"{age}살", f"{age}세"])))
    slots.update(josa_slots("gw", rng.choice(GENDER_WORDS[gender])))
    slots.update(josa_slots("mbti", mbti if rng.random() < 0.7 else mbti.lower()))
    return slots, mbti


def gen_routes(rng, split):
    age = rng.randint(19, 65)
    gender = rng.choice(["남성", "여성"])
    slots, mbti = route_slots(rng, age, gender)
    text = rng.choice(ROUTES_TEMPLATES[split]).format(**slots)
    return text, [call("recommend_similar_traveler_routes", age=age, gender=gender, mbti=mbti)]


def gen_detail(rng, split):
    place = rng.choice(PLACES[split])
    text = rng.choice(DETAIL_TEMPLATES[split]).format(**josa_slots("p", place))
    return text, [call("get_destination_detail", name=place)]


def gen_reviews(rng, split):
    subject = rng.choice(PLACES[split] + REGIONS[split])
    return rng.choice(REVIEWS_TEMPLATES[split]).format(s=subject), [call("search_reviews", query=subject)]


GLOPLER_KINDS = [("hashtag", 20), ("region", 80)]


def gen_gloplers(rng, split, kind=None):
    if pick_kind(rng, GLOPLER_KINDS, kind) == "hashtag":
        tag = rng.choice(HASHTAGS[split])
        return rng.choice(GLOPLER_TAG_TEMPLATES[split]).format(t=tag), [call("search_gloplers", hashtag=tag)]
    region = rng.choice(REGIONS[split])
    return rng.choice(GLOPLER_REGION_TEMPLATES[split]).format(r=region), [call("search_gloplers", hashtag=region)]


def gen_weather(rng, split):
    region = rng.choice(REGIONS[split])
    when = rng.choice(list(WHEN_WORDS) + [None])
    ww = rng.choice(WHEN_WORDS[when]) if when else ""
    text = tidy(rng.choice(WEATHER_TEMPLATES[split]).format(r=region, w=ww))
    arguments = {"region": region}
    if when:
        arguments["when"] = when
    return text, [call("get_weather", **arguments)]


def gen_plan(rng, split):
    region = rng.choice(list(REGION_PLACES[split]))
    places = rng.sample(REGION_PLACES[split][region], rng.randint(2, min(4, len(REGION_PLACES[split][region]))))
    days_word, days = rng.choice(DAYS_WORDS)
    title = rng.choice(PLAN_TITLES[split]) if rng.random() < 0.25 else None
    slots = {"r": region, "pl": join_words(rng, places)}
    slots.update(josa_slots("d", days_word))
    text = rng.choice(PLAN_TEMPLATES[split]).format(**slots)
    if title:
        text = rng.choice(PLAN_TITLE_TEMPLATES[split]).format(**josa_slots("t", f"'{title}'")) + text

    arguments = {"region": region, "days": days, "places": places}
    if title:
        arguments["title"] = title
    return text, [call("create_travel_plan", **arguments)]


PARALLEL_KINDS = [("weather_search", 1), ("detail_reviews", 1), ("search_gloplers", 1)]


def gen_parallel(rng, split, kind=None):
    templates = PARALLEL_TEMPLATES[split]
    kind = pick_kind(rng, PARALLEL_KINDS, kind)
    if kind == "detail_reviews":
        place = rng.choice(PLACES[split])
        return rng.choice(templates[kind]).format(p=place), [
            call("get_destination_detail", name=place), call("search_reviews", query=place)]

    region = rng.choice(REGIONS[split])
    category = rng.choice(list(CATEGORY_WORDS))
    cat = category_word(rng, split, category)
    if kind == "weather_search":
        when = rng.choice(list(WHEN_WORDS))
        ww = rng.choice(WHEN_WORDS[when])
        return rng.choice(templates[kind]).format(w=ww, r=region, c=cat), [
            call("get_weather", region=region, when=when),
            call("search_destinations", region=region, category=category)]

    return rng.choice(templates[kind]).format(r=region, c=cat), [
        call("search_destinations", region=region, category=category),
        call("search_gloplers", hashtag=region)]


CLARIFY_KINDS = [("routes", 30), ("search", 25), ("weather", 13), ("plan", 20), ("reviews", 6), ("open", 6)]


def gen_clarify(rng, split, kind=None):
    kind = pick_kind(rng, CLARIFY_KINDS, kind)
    if kind == "routes":
        given = rng.choice(list(ROUTES_CLARIFY_TEMPLATES[split]))
        slots, _ = route_slots(rng, rng.randint(19, 65), rng.choice(["남성", "여성"]))
        text = rng.choice(ROUTES_CLARIFY_TEMPLATES[split][given]).format(**slots)
        given_fields = {{"age": "나이", "gender": "성별", "mbti": "MBTI"}[g] for g in given.split("+") if g}
        missing = join_missing([f for f in ROUTE_FIELDS if f not in given_fields])
        # 조사는 나열의 마지막 단어를 따른다: 나이가 / 나이와 성별이 / 나이, 성별, MBTI가
        return text, f"비슷한 여행자를 찾으려면 {josa(missing, '이', '가')} 필요해요. 알려주시겠어요?"
    if kind == "weather":
        when_words = [w for words in WHEN_WORDS.values() for w in words] + [""]
        text = tidy(rng.choice(WEATHER_CLARIFY_TEMPLATES[split]).format(w=rng.choice(when_words)))
        return text, WEATHER_CLARIFY_ANSWER
    if kind == "plan":
        given = rng.choice(["none", "region", "region", "region_days", "region_days"])
        slots = {"r": rng.choice(REGIONS[split])}
        slots.update(josa_slots("d", rng.choice(DAYS_WORDS)[0]))
        return rng.choice(PLAN_CLARIFY_TEMPLATES[split][given]).format(**slots), PLAN_CLARIFY_ANSWERS[given]
    if kind == "reviews":
        return rng.choice(REVIEWS_CLARIFY_TEXTS[split]), REVIEWS_CLARIFY_ANSWER
    if kind == "open":
        return rng.choice(OPEN_CLARIFY_TEXTS[split]), OPEN_CLARIFY_ANSWER

    # 지역 없이 키워드가 아닌 카테고리 말만 있음 → 지역을 되묻는다 (키워드인 말은 gen_keywords가 추천으로 만든다)
    cat = category_word(rng, split, rng.choice(CLARIFY_CATEGORIES))
    templates = SUBJECT_REQUEST_TEMPLATES[split] + TRIP_REQUEST_TEMPLATES[split]
    text = rng.choice(templates).format(**josa_slots("c", cat))
    return text, f"어느 지역의 {josa(cat, '을', '를')} 찾아드릴까요?"


DIRECT_KINDS = [("greeting", 20), ("thanks", 25), ("ability", 15), ("decline", 32), ("decline_place", 8)]
# v3: 여행지 이름이 들어 있어도 도구로 할 수 없는 요청은 거절한다
OFF_TOPIC_PLACE_REQUESTS = {
    "train": [
        "{r} 가서 쓸 영어 문장 몇 개 만들어줘", "{r} 여행 가는데 환율 계산 좀 해줘", "{r} 가는 친구한테 보낼 편지 써줘",
        "{r} 출장 보고서 대신 써줘", "{r} 여행 브이로그 대본 써줘", "{r} 사진 보정해 줄 수 있어?",
    ],
    "test": [],
}


def gen_direct(rng, split, kind=None):
    kind = pick_kind(rng, DIRECT_KINDS, kind)
    if kind == "decline_place":
        if not OFF_TOPIC_PLACE_REQUESTS[split]:  # test에는 이 종류를 만들지 않는다
            kind = "decline"
        else:
            region = rng.choice(REGIONS[split])
            return rng.choice(OFF_TOPIC_PLACE_REQUESTS[split]).format(r=region), DECLINE_ANSWER
    if kind == "greeting":
        return rng.choice(GREETINGS[split]), GREETING_ANSWER
    if kind == "thanks":
        if rng.random() < 0.4:
            region = rng.choice(REGIONS[split])
            return rng.choice(THANKS_WITH_REGION[split]).format(r=region), THANKS_ANSWER
        return rng.choice(THANKS[split]), THANKS_ANSWER
    if kind == "ability":
        return rng.choice(ABILITY_QUESTIONS[split]), ABILITY_ANSWER
    request = rng.choice(OFF_TOPIC_REQUESTS[split])
    lead = rng.choice(OFF_TOPIC_LEADS[split])
    if lead == "혹시 " and request.endswith("하자"):
        lead = ""
    return lead + request, DECLINE_ANSWER


GENERATORS = [
    (gen_keywords, 14), (gen_search, 18), (gen_routes, 10), (gen_detail, 9), (gen_reviews, 9),
    (gen_gloplers, 8), (gen_weather, 9), (gen_plan, 9), (gen_parallel, 7), (gen_clarify, 6), (gen_direct, 4),
]


def task_name(func):
    return func.__name__.removeprefix("gen_")


# 세부 종류가 있는 작업. 작업 개수 안에서 종류별 개수도 가중치대로 채운다 (작은 종류가 큰 종류에 밀리지 않도록).
GENERATOR_KINDS = {
    "keywords": KEYWORD_KINDS, "gloplers": GLOPLER_KINDS, "parallel": PARALLEL_KINDS,
    "clarify": CLARIFY_KINDS, "direct": DIRECT_KINDS,
}


# --- 검사 ----------------------------------------------------------------------

def _strings(obj):
    """리스트·딕셔너리(값만) 안의 문자열을 모두 꺼낸다."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    else:
        for value in obj:
            yield from _strings(value)


# test가 train과 하나도 겹치면 안 되는 값들 (키워드·MBTI·성별·시점·일수 같은 닫힌 값은 뺀다)
ENTITY_POOLS = {
    "지역": REGIONS,
    "장소": PLACES,
    "일정 지역": {s: list(REGION_PLACES[s]) for s in SPLITS},
    "조건(query)": {s: list(DESCRIPTORS[s]) for s in SPLITS},
    "카테고리 말": {"train": CATEGORY_WORDS, "test": CATEGORY_WORDS_TEST},
    "해시태그": HASHTAGS,
    "일정 이름": PLAN_TITLES,
}
PHRASING_POOLS = {
    "키워드 템플릿": KEYWORD_TEMPLATES, "대상만 말하는 요청 템플릿": SUBJECT_REQUEST_TEMPLATES,
    "여행 맥락 요청 템플릿": TRIP_REQUEST_TEMPLATES,
    "검색 템플릿": SEARCH_TEMPLATES, "루트 템플릿": ROUTES_TEMPLATES, "정보 템플릿": DETAIL_TEMPLATES,
    "후기 템플릿": REVIEWS_TEMPLATES, "글로플러 템플릿": GLOPLER_REGION_TEMPLATES,
    "해시태그 템플릿": GLOPLER_TAG_TEMPLATES, "날씨 템플릿": WEATHER_TEMPLATES, "일정 템플릿": PLAN_TEMPLATES,
    "일정 이름 앞말": PLAN_TITLE_TEMPLATES,
    "여러 도구 템플릿": PARALLEL_TEMPLATES, "루트 되묻기": ROUTES_CLARIFY_TEMPLATES,
    "날씨 되묻기": WEATHER_CLARIFY_TEMPLATES, "일정 되묻기": PLAN_CLARIFY_TEMPLATES,
    "후기 되묻기": REVIEWS_CLARIFY_TEXTS, "추천 되묻기": OPEN_CLARIFY_TEXTS, "인사": GREETINGS, "감사": THANKS,
    "지역 감사": THANKS_WITH_REGION, "기능 질문": ABILITY_QUESTIONS, "범위 밖 요청": OFF_TOPIC_REQUESTS,
}


def check_split_disjoint():
    """test 슬롯 값·문장이 train과 겹치거나, 라벨 규칙이 서로 부딪치게 설정되어 있으면 멈춘다."""
    problems = []

    train_values = set()
    for pool in ENTITY_POOLS.values():
        train_values |= set(_strings(pool["train"]))
    for name, pool in ENTITY_POOLS.items():
        overlap = set(_strings(pool["test"])) & train_values
        if overlap:
            problems.append(f"test 값 중 train과 겹침 ({name}): {sorted(overlap)}")

    # 지역명이 다른 split의 지역·장소 이름 안에 들어 있어도 같은 곳을 가리킬 수 있다 (예: 여수 ↔ 여수해상케이블카).
    for a, b in (("train", "test"), ("test", "train")):
        names = REGIONS[b] + PLACES[b]
        for region in REGIONS[a]:
            hits = [n for n in names if region in n]
            if hits:
                problems.append(f"{a} 지역 {region!r} → {b} 이름 안에 들어 있음: {hits}")

    train_phrases = set()
    for pool in PHRASING_POOLS.values():
        train_phrases |= set(_strings(pool["train"]))
    for name, pool in PHRASING_POOLS.items():
        overlap = set(_strings(pool["test"])) & train_phrases
        if overlap:
            problems.append(f"test 문장 중 train과 겹침 ({name}): {sorted(overlap)}")

    for split in SPLITS:
        extra = set(REGION_PLACES[split]) - set(REGIONS[split])
        if extra:
            problems.append(f"{split} 일정 지역이 {split} 지역 목록에 없음: {sorted(extra)}")
        for desc, categories in DESCRIPTORS[split].items():
            if not set(categories) <= set(CATEGORY_WORDS):
                problems.append(f"{split} 조건 {desc!r}에 붙은 카테고리가 잘못됨: {categories}")
    if set(CATEGORY_WORDS_TEST) != set(CATEGORY_WORDS):
        problems.append("test 카테고리 말이 여섯 카테고리를 모두 덮지 않음")
    if len(KEYWORDS) != 94 or len(set(KEYWORDS)) != 94:
        problems.append("KEYWORDS는 KeywordMask와 같은 94개여야 함")
    if not (set(KEYWORD_ACTIVITIES) | set(KEYWORD_SIGHTS)) <= set(KEYWORDS) or \
            set(KEYWORD_ACTIVITIES) & set(KEYWORD_SIGHTS):
        problems.append("키워드 활동/볼거리 분류가 KEYWORDS 밖이거나 서로 겹침")
    if set(KEYWORD_CATEGORY_WORDS) != {"박물관", "미술관", "축제", "공연"}:
        problems.append(f"키워드이기도 한 카테고리 말이 바뀜: {KEYWORD_CATEGORY_WORDS} (라벨 규칙 문서도 같이 고칠 것)")
    for split, words in (("train", CATEGORY_WORDS), ("test", CATEGORY_WORDS_TEST)):
        clarify_words = {w for c in CLARIFY_CATEGORIES for w in words[c]}
        if clarify_words & set(KEYWORDS):
            problems.append(f"{split} 지역 되묻기용 카테고리 말에 키워드가 있음: {sorted(clarify_words & set(KEYWORDS))}")
    for words in CATEGORY_WORDS_TEST.values():
        if set(words) & set(KEYWORDS):
            problems.append(f"test 카테고리 말에 키워드가 있음: {sorted(set(words) & set(KEYWORDS))}")

    if problems:
        raise AssertionError("train/test 분리·라벨 규칙 검사 실패\n- " + "\n- ".join(problems))


def label_key(task, answer):
    return task + " " + (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False, sort_keys=True))


def check_label_conflicts(seed, draws):
    """모든 생성기를 많이 돌려서 같은 발화에 정답이 두 가지로 붙는 경우가 있는지 찾는다.
    돌려주는 값은 (split, 작업)별로 찾은 서로 다른 발화 수(문장 풀 크기의 하한)."""
    rng = random.Random(seed + 1)  # 학습 데이터 rng와 따로 쓴다
    seen, pool_sizes, conflicts = {}, {}, []
    for split in SPLITS:
        for func, _ in GENERATORS:
            task = task_name(func)
            texts = set()
            for _ in range(draws):
                text, answer = func(rng, split)
                label = label_key(task, answer)
                if seen.setdefault(text, label) != label:
                    conflicts.append((text, seen[text], label))
                texts.add(text)
            pool_sizes[split, task] = len(texts)
    if conflicts:
        lines = [f"{t!r}: {a} / {b}" for t, a, b in conflicts[:20]]
        raise AssertionError(f"같은 발화에 정답이 두 가지인 경우 {len(conflicts)}건\n" + "\n".join(lines))
    return pool_sizes


def load_eval_texts():
    if not EVAL_SET.exists():
        return set()
    return {json.loads(line)["messages"][1]["content"]
            for line in EVAL_SET.read_text(encoding="utf-8").splitlines() if line.strip()}


# --- 데이터 만들기 -----------------------------------------------------------------

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


def split_counts(count, weights):
    """count개를 가중치대로 나눈다 (최대 나머지 방식)."""
    total = sum(weights)
    raw = [count * w / total for w in weights]
    base = [int(x) for x in raw]
    by_remainder = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in by_remainder[:count - sum(base)]:
        base[i] += 1
    return base


def quotas(count):
    """GENERATORS 가중치대로 나눈 작업별 개수."""
    return split_counts(count, [w for _, w in GENERATORS])


MAX_MISSES = 5000  # 한 작업에서 새 발화가 이만큼 연속으로 안 나오면 문장 풀이 모자란 것


def build(split, count, rng, exclude, labels):
    """작업(과 세부 종류)마다 할당량만큼 중복 없는 발화를 만든다. labels(발화 → 정답)는 split 사이에서 같이 쓴다."""
    records, redraw_rate, kind_counts = [], {}, {}
    for (func, _), quota in zip(GENERATORS, quotas(count)):
        task = task_name(func)
        kinds = GENERATOR_KINDS.get(task, [(None, 1)])
        total_accepted = total_draws = 0
        for (kind, _), kind_quota in zip(kinds, split_counts(quota, [w for _, w in kinds])):
            accepted = misses = 0
            while accepted < kind_quota:
                total_draws += 1
                text, answer = func(rng, split) if kind is None else func(rng, split, kind)
                label = label_key(task, answer)
                known = labels.get(text)
                if known is not None and known != label:
                    raise AssertionError(f"같은 발화에 정답이 두 가지입니다: {text!r}\n  {known}\n  {label}")
                if known is not None or text in exclude:
                    misses += 1
                    if misses > MAX_MISSES:
                        raise RuntimeError(f"{split}/{task}/{kind}: 중복 없이 {kind_quota}개를 채우지 못했습니다 "
                                           f"({accepted}개). 문장 풀을 늘리세요.")
                    continue
                misses = 0
                labels[text] = label
                records.append(to_record(text, answer, task))
                accepted += 1
            if kind is not None:
                kind_counts.setdefault(task, {})[kind] = accepted
            total_accepted += accepted
        redraw_rate[task] = 1 - total_accepted / total_draws if total_draws else 0.0
    rng.shuffle(records)
    return records, redraw_rate, kind_counts


def split_train_valid(records, n_valid, rng):
    """valid도 작업 비율이 의도와 같도록 작업별 할당량만큼 떼어 낸다."""
    by_task = {}
    for record in records:
        by_task.setdefault(record["task"], []).append(record)
    train, valid = [], []
    for (func, _), quota in zip(GENERATORS, quotas(n_valid)):
        group = by_task.get(task_name(func), [])
        valid += group[:quota]
        train += group[quota:]
    rng.shuffle(train)
    rng.shuffle(valid)
    return train, valid


def print_report(splits, redraw, pool_sizes):
    total = sum(w for _, w in GENERATORS)
    print()
    print("작업 비율 (의도 → 실제)과 문장 풀 여유")
    print(f"{'작업':<10}{'의도':>7}" + "".join(f"{name:>9}" for name in splits)
          + f"{'다시 뽑음 train/test':>22}{'풀 하한 train/test':>20}")
    for func, weight in GENERATORS:
        task = task_name(func)
        cells = []
        for records in splits.values():
            n = sum(r["task"] == task for r in records)
            cells.append(f"{n / len(records) * 100:8.1f}%")
        print(f"{task:<10}{weight / total * 100:6.1f}%" + "".join(cells)
              + f"{redraw['train'][task] * 100:13.0f}% /{redraw['test'][task] * 100:4.0f}%"
              + f"{pool_sizes['train', task]:>12} /{pool_sizes['test', task]:>6}")
    print("다시 뽑음: 이미 나온 발화라서 버리고 다시 뽑은 비율. 높을수록 그 작업의 문장 풀이 꽉 찼다는 뜻")


def main():
    parser = argparse.ArgumentParser()
    # v1 데이터(data/tool_calling)는 이 스크립트로 다시 만들 수 없으므로 기본 출력 폴더를 v2로 둔다.
    parser.add_argument("--out", default=str(ROOT / "data" / "tool_calling_v3"))
    parser.add_argument("--train", type=int, default=3000)
    parser.add_argument("--valid", type=int, default=200)
    parser.add_argument("--test", type=int, default=300)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--conflict-draws", type=int, default=20000, help="정답 충돌 검사에서 생성기마다 뽑을 횟수")
    args = parser.parse_args()

    check_split_disjoint()
    print("train/test 분리 검사 통과: 지역·장소·조건·카테고리 말·해시태그·일정 이름·문장 템플릿이 겹치지 않음")
    pool_sizes = check_label_conflicts(args.seed, args.conflict_draws)
    print(f"정답 충돌 검사 통과: 생성기마다 {args.conflict_draws}번씩 뽑아 같은 발화에 다른 정답이 붙은 경우 없음")
    eval_texts = load_eval_texts()
    print(f"제외한 평가 세트 발화: {len(eval_texts)}개 ({EVAL_SET.relative_to(ROOT)})")

    rng = random.Random(args.seed)
    labels = {}
    train_valid, redraw_train, kinds_train = build("train", args.train + args.valid, rng, eval_texts, labels)
    rng.shuffle(train_valid)
    train, valid = split_train_valid(train_valid, args.valid, rng)
    test, redraw_test, kinds_test = build("test", args.test, rng, eval_texts, labels)

    splits = {"train": train, "valid": valid, "test": test}
    texts = {name: {r["messages"][1]["content"] for r in records} for name, records in splits.items()}
    for name, records in splits.items():
        if len(texts[name]) != len(records):
            raise AssertionError(f"{name} 안에 같은 발화가 있습니다")
    if texts["train"] & texts["valid"] or texts["train"] & texts["test"] or texts["valid"] & texts["test"]:
        raise AssertionError("split 사이에 같은 발화가 있습니다")
    if eval_texts & (texts["train"] | texts["valid"] | texts["test"]):
        raise AssertionError("평가 세트와 같은 발화가 있습니다")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, records in splits.items():
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        tasks = {}
        for record in records:
            tasks[record["task"]] = tasks.get(record["task"], 0) + 1
        print(f"{name}: {len(records)}  {dict(sorted(tasks.items()))}")
    print_report(splits, {"train": redraw_train, "test": redraw_test}, pool_sizes)
    print("세부 종류별 개수 (train+valid / test):")
    for task, kinds in GENERATOR_KINDS.items():
        cells = ", ".join(f"{k} {kinds_train[task][k]}/{kinds_test[task][k]}" for k, _ in kinds)
        print(f"  {task}: {cells}")


if __name__ == "__main__":
    main()
