# 사람 말투 평가 세트 (`human_style.jsonl`)

`data/tool_calling/test.jsonl`은 생성기 템플릿으로 만든 문장이라, 모델이 "템플릿처럼 생긴 문장"에만 강한지 실제 채팅 말투에도 버티는지는 알 수 없습니다.
이 세트는 그 차이를 보려고 만든 **90개짜리 추가 평가 세트**입니다. 학습에는 쓰지 않습니다.

> **주의: 실제 사용자 로그가 아닙니다.** AI 에이전트(Claude)가 실제 사용자가 채팅 앱에 칠 법한 문장을 흉내 내어 직접 쓴 것입니다.
> 반말·존댓말 섞기, 오타와 붙여쓰기, 이모지, 슬랭(ㄱㄱ, ~임?, 낼, 담주, 게하), 긴 맥락 문장, 간접 요청, 방해 요소 등을 일부러 넣었지만,
> 실제 사용자 분포를 대표한다고 볼 수는 없습니다. 실제 로그가 쌓이면 그걸로 바꾸거나 보태는 게 맞습니다.

## 형식

`test.jsonl`과 같은 형식(`messages` + `tools` + `task`)에 필드 두 개를 더했습니다. `evaluate.py`는 더한 필드를 읽지 않으므로 그대로 넣으면 됩니다.

| 필드 | 내용 |
| :-- | :-- |
| `messages` | system(생성기와 같은 `SYSTEM_PROMPT`) → user → assistant. 도구 호출이면 `content: ""` + `tool_calls`(arguments는 `ensure_ascii=False` JSON 문자열), 되묻기·바로 답변이면 `content` 문장 |
| `tools` | [`tools/glople_tools.json`](../tools/glople_tools.json) 전체 (8개) |
| `task` | keywords / search / routes / detail / reviews / gloplers / weather / plan / parallel / clarify / direct |
| `difficulty` | easy / medium / hard (기준은 [labeling_rules.md](labeling_rules.md) 8절) |
| `note` | 왜 헷갈리는지, 정답이 왜 그런지 한 줄 설명 |

## 라벨 규칙

정답은 [`scripts/build_tool_dataset.py`](../scripts/build_tool_dataset.py)가 학습 데이터를 만들 때 쓰는 규칙을 그대로 따랐습니다.
지역은 사용자가 쓴 그대로(`제주도`를 `제주`로 바꾸지 않음), `query`는 사용자가 조건을 덧붙였을 때만 그대로 복사, `limit`은 개수를 말했을 때만,
`주말에` → `이번 주말`, `2박 3일` → 3, 성별 → `남성`/`여성`, MBTI는 대문자, 키워드는 94개 목록 표기 그대로, 필수 정보가 빠지면 되묻기 등입니다.
생성기에 없던 표현(낼, 담주, 사흘, 게하, 한글 수사 등)을 어떻게 정답으로 옮겼는지까지 포함한 전체 규칙은 [labeling_rules.md](labeling_rules.md)에 정리했습니다.

정답 인자에 들어가는 지역·장소 64개는 모두 생성기의 지역·장소 목록(train/test 모두, v1·v2 모두)에 없는 것입니다 (관심사 해시태그 혼행·캠핑·와인·배낭여행 4개는 지역·장소가 아니라서 셈에서 뺐습니다).
생성기 목록에 있는 지역(부산, 통영, 다낭, 교토)은 "사는 곳", "이미 다녀온 곳" 같은 방해 요소로만 나옵니다 (v2에서는 넷 다 train 지역). v1(`data/tool_calling`)·v2(`data/tool_calling_v2`) train/valid/test와 같은 발화는 하나도 없습니다.
v2 생성기는 이 파일의 발화를 읽어서 같은 문장을 아예 만들지 않습니다.

## 실행

`run_overnight.sh`가 GPU를 쓰는 동안에는 돌리지 말고, 밤샘 파이프라인이 끝난 뒤 실행합니다.
`--name`을 `base`/`finetuned`와 다르게 줘야 밤샘 평가 결과(`results/base.*`, `results/finetuned.*`)를 덮어쓰지 않습니다.

```bash
cd training

# 파인튜닝 모델
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl \
    --name finetuned_human --adapter adapters/qwen3-4b-tool-calling

# v2 데이터로 학습한 모델 (configs/qwen3-4b-lora-v2.yaml로 학습한 뒤)
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl \
    --name finetuned_v2_human --adapter adapters/qwen3-4b-tool-calling-v2

# 기본 모델
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl --name base_human

# report.py는 항상 results/report.md에 쓰므로, 밤샘 비교표를 먼저 따로 보관한다.
cp results/report.md results/report_test.md
.venv/bin/python scripts/report.py base_human finetuned_human finetuned_v2_human
```

결과는 `results/{base_human,finetuned_human,finetuned_v2_human}.summary.json`과 `.predictions.jsonl`에 남습니다.
`clarify`·`direct`는 `evaluate.py`가 "도구를 부르지 않았는지"만 채점하고 답변 문장은 비교하지 않습니다.

### 난이도별 정확도

`evaluate.py`는 작업별 정확도만 내므로, 난이도별로 보려면 predictions 파일과 줄 순서로 맞춰 봅니다 (predictions는 데이터 순서대로 쓰입니다).

```bash
.venv/bin/python - <<'EOF'
import collections, json
data = [json.loads(l) for l in open("eval_sets/human_style.jsonl", encoding="utf-8")]
for name in ["base_human", "finetuned_human", "finetuned_v2_human"]:
    preds = [json.loads(l) for l in open(f"results/{name}.predictions.jsonl", encoding="utf-8")]
    hit, total = collections.Counter(), collections.Counter()
    for d, p in zip(data, preds):
        assert d["messages"][1]["content"] == p["user"]
        total[d["difficulty"]] += 1
        hit[d["difficulty"]] += p["correct"]
    print(name, {k: f"{hit[k]}/{total[k]}" for k in ["easy", "medium", "hard"]})
EOF
```

틀린 예시는 `predictions.jsonl`의 `"correct": false` 줄과 이 파일의 `note`를 같이 보면 왜 틀렸는지 빨리 알 수 있습니다.

## 구성

| task | 개수 | easy | medium | hard | 주로 넣은 함정 |
| :-- | --: | --: | --: | --: | :-- |
| keywords | 10 | 3 | 2 | 5 | 동의어·오타 → 목록 표기, 여러 단어 키워드, 사는 곳 지역명, limit 없는 도구에 개수 |
| search | 12 | 2 | 6 | 4 | 붙여쓰기, 새 카테고리 표현(묵을 데·게하·밥집), 조건 query, 한글 수사 limit, 이미 간 지역 |
| routes | 8 | 1 | 4 | 3 | 소문자·한글 별칭 MBTI, 한글 수사 나이, 동행자 성별 |
| detail | 7 | 2 | 4 | 1 | 조사 떼기, 사진·위치 요청, 거절한 후기 |
| reviews | 7 | 2 | 4 | 1 | "다녀온 사람들 어땠대" 같은 간접 요청, 거절한 날씨 |
| gloplers | 7 | 1 | 4 | 2 | # 떼기, 관심사 해시태그, "현지 사는 분" 같은 간접 요청 |
| weather | 8 | 3 | 3 | 2 | 낼·담주·이번주말 → enum, 시점 없으면 when 생략 |
| plan | 8 | 1 | 4 | 3 | 1박2일·이틀·사흘·일주일 → days, 이름 지정 vs 여행 성격 |
| parallel | 9 | 0 | 5 | 4 | 같은 도구 두 번, 세 개 호출, 장소/지역 나눠 쓰기 |
| clarify | 8 | 1 | 1 | 6 | 필수 인자 일부만 빠짐, 가리키는 대상 없음 |
| direct | 6 | 3 | 2 | 1 | 감사 인사 속 지역명, 여행 맥락의 번역 요청 |
| **합계** | **90** | **19** | **39** | **32** | hard 36% |

## 검증

만든 뒤 `python3 -I`로 다음을 확인했습니다.

- 90줄 모두 JSON으로 읽히고, 키는 `messages`/`tools`/`task`/`difficulty`/`note`만 있음
- `tools`가 `glople_tools.json`과 완전히 같고, system 문장이 생성기 `SYSTEM_PROMPT`와 같음
- 모든 도구 이름이 존재하고, 필수 인자·enum 값·정수 타입을 지키며 스키마에 없는 인자가 없음
- 키워드가 모두 94개 목록 안에 있고, MBTI는 대문자 4글자, hashtag에 `#` 없음
- region·query·name·hashtag·places·title 값이 사용자 발화 안에 글자 그대로 있음
- `evaluate.py`의 `expected_calls`/`canonical`로 정답을 읽을 수 있음
- 기존 train/valid/test와 겹치는 발화 없음, 세트 안 중복 없음 (v2 데이터를 만든 뒤 v2 train/valid/test와도 다시 확인: 0개)

### 독립 검수

만든 사람과 다른 에이전트가 90개 발화마다 정답을 먼저 따로 붙인 뒤 저장된 라벨과 비교했습니다.

- 도구 이름·인자·되묻기/바로 답변 문장이 틀린 라벨은 없었습니다.
- 정답이 두 갈래로 읽히는 발화 2개는 뜻을 거의 바꾸지 않고 고쳤습니다 (버린 것은 없음).
  - `유네스코 세계유산 같은 고대 유적지` → `유네스코 세계유산이나 고대 유적지`: `A 같은 B`는 A를 키워드로 넣을지 애매함
  - `지난주 울진 다녀온 거 일정으로 남기고 싶어요` → `다음 달에 울진 가는데 일정으로 미리 남겨두고 싶어요`: 다녀온 곳이 방해 요소로도 읽힘
- labeling_rules.md 8절의 hard 기준(고유어 일수, 필수 인자 일부만 빠진 되묻기)에 해당하는데 medium이던 4개를 hard로 올렸습니다: `이틀` 일정, 나이만·성별만 빠진 루트 되묻기, 일수·장소만 빠진 일정 되묻기.
  그래서 hard 비율이 31%에서 36%가 됐습니다.
- 검수에서 찾은 규칙 빈틈(`A 같은 B`, 요청 대상인 사는 곳·다녀온 곳, hard 판정)은 [labeling_rules.md](labeling_rules.md) 3·4·8·9절에 더했습니다.

## v2 생성기 라벨 규칙과 맞춰 본 결과 (2026-10-17)

생성기 v2에서 "지역 없이 키워드인 장소 유형(박물관·미술관·축제·공연)만 말하면 recommend_by_keywords, 지역 되묻기는 키워드가 아닌 카테고리 말(맛집·숙소·액티비티·관광지 …)일 때만"으로 규칙을 분명히 했습니다.
90개 레코드를 이 규칙과 다시 맞춰 봤고, **어긋나는 레코드는 없어서 레코드는 고치지 않았습니다.** 이 규칙에 걸리는 레코드는 다음과 같습니다.

| 줄 | 발화 | 정답 | 규칙과 |
| --: | :-- | :-- | :-- |
| 6 | 야경 맛집인 여행지 알려줘 사진 찍으러 가게 | keywords `["야경"]` | 맞음 (`맛집`은 '잘하는 곳'이라는 슬랭이라 카테고리 말이 아님, labeling_rules.md 4절) |
| 7 | 박물관 미술관 돌아다니는 거 좋아해. 그런 여행지 세 군데만? | keywords `["박물관", "미술관"]` | 맞음 (지역 없음) |
| 15 | 피렌체 미술관 어디 가야 돼? | search `문화시설` | 맞음 (지역 있음) |
| 18 | 부다페스트 축제 하는 거 있어? | search `축제` | 맞음 (지역 있음) |
| 76 | 보령 가려는데 축제랑 숙소 둘 다 찾아줘 | search `축제` + search `숙박` | 맞음 (지역 있음) |
| 81 | 맛집 추천 좀 해줘 배고파 | 지역 되묻기 | 맞음 (키워드가 아닌 말) |

(줄 번호는 파일의 1부터 센 줄입니다.)

- [labeling_rules.md](labeling_rules.md)는 4절에 원래 "지역이 있으면 search, 없으면 keywords"가 있었지만 되묻기 쪽 조건이 빠져 있어서, 4절·6절에 "지역 되묻기는 키워드가 아닌 카테고리 말일 때만", 키워드와 뜻이 겹치는 말(전시관·행사·페스티벌·갤러리)과 여행이 정해진 듯한 문장의 예외를 더했습니다. 카테고리 말 표도 v2 생성기 목록으로 바꿨습니다.
- 주의: v1 학습 데이터(`data/tool_calling`, 밤샘 학습에 사용)는 이 규칙과 어긋납니다. train에 `박물관 추천해줘 → 어느 지역의 박물관을 찾아드릴까요?` 같은 되묻기가 6개 있습니다.
  v1 모델이 7번 줄을 되묻기로 답한다면 이 라벨 충돌 때문일 수 있습니다. v2 데이터에는 이런 충돌이 없습니다.

## 독립 재평가 세트 `human_style_v2.jsonl` (2026-10-18)

`human_style.jsonl`은 v3 학습 데이터를 설계하면서 내용을 들여다봤기 때문에, 그 점수는 이제 실제보다 좋게 나올 수 있습니다.
그래서 **생성기와 학습 데이터를 보지 않은 채로** 새 90개를 따로 썼습니다. v1·v2·v3 모델이 처음 보는 말투에 얼마나 일반화하는지 다시 재는 용도이고, 학습에는 쓰지 않습니다.

- 작성: AI 에이전트(Claude), 2026-10-18. 실제 사용자 로그가 아닙니다.
- 작성 조건: 이 README, [labeling_rules.md](labeling_rules.md), [`tools/glople_tools.json`](../tools/glople_tools.json), `keyword_tagger/keywords.json`(94개), `human_style.jsonl`의 첫 두 줄(형식 확인용)만 읽었습니다.
  `scripts/build_tool_dataset*.py`, `data/`, `results/`, `adapters/`, `human_style.jsonl`의 나머지 줄은 열지 않았습니다.
- 형식은 `human_style.jsonl`과 같습니다(`messages`/`tools`/`task`/`difficulty`/`note`). 실행할 때는 위 명령에서 `--data eval_sets/human_style_v2.jsonl`로 바꾸고 `--name`을 따로 줍니다 (예: `finetuned_v3_human2`).

### 구성

| task | 개수 | easy | medium | hard | 주로 넣은 함정 |
| :-- | --: | --: | --: | --: | :-- |
| keywords | 10 | 3 | 2 | 5 | 오타·영문·띄어쓰기 → 목록 표기(트래킹, 스쿠버다이빙, UNESCO, 국립 공원, 하이킹코스), 목록에 없는 말 버리기(오로라·단풍), 사는 곳, limit 없는 도구에 개수, 지역 없는 공연·축제 |
| search | 12 | 2 | 6 | 4 | 조건 query 글자 그대로(오타 `가성비 조은` 포함), `몇 개만`·`두세 군데` vs `5곳`·`세 군데`·`열 곳`, 게하·펜션·밥집·수상 레저·볼거리, 사는 곳·다녀온 곳, 니스·칸·나라 |
| parallel | 9 | 0 | 5 | 4 | 같은 도구 두 번(축제+숙박, 낼+담주 날씨), 호출 세 개, 지역/장소 나눠 쓰기, 사는 곳 |
| routes | 8 | 1 | 4 | 3 | 압축 말투(`intp 31 남`), 소문자·대소문자 섞인 MBTI, 한글 별칭, 한글 수사 나이, 동행자 성별 |
| weather | 8 | 3 | 3 | 2 | 낼·담주·이번주말·주말에 → enum, 시점 없으면 생략, 기후(岐阜), 사는 곳, 거절한 후기 |
| plan | 8 | 1 | 4 | 3 | 당일치기·일주일·나흘·사흘, 이름 지정 vs 가족여행, 사는 곳 |
| clarify | 8 | 1 | 1 | 6 | 루트 나이만·성별과 MBTI 빠짐, 일정 장소만·일수와 장소 빠짐, 사는 곳만 있는 펜션 요청, 목록 밖 말만 있는 취향 |
| detail | 7 | 2 | 4 | 1 | 지역+장소에서 장소만, 로만 바스, 장소명 속 `박물관`, 거절한 후기 |
| reviews | 7 | 2 | 4 | 1 | "가본 사람들 반응"·"갔다온 사람들 글" 간접 요청, 도하, 거절한 날씨 |
| gloplers | 7 | 1 | 4 | 2 | # 떼기, 현지 가이드·현지 전문가, 오만, 사는 곳, 키워드처럼 보이는 트래킹 |
| direct | 6 | 2 | 3 | 1 | 여행지가 나오는 거절(코드·코인·숙제 대필), 감사 인사 속 다녀온 지역 |
| **합계** | **90** | **18** | **40** | **32** | hard 36% |

- 사는 곳이 나오는 발화 7개(keywords·search·parallel·weather·plan·clarify·gloplers), 한국어 단어처럼 보이는 해외 지명 7개(니스·칸·나라·기후·바스·도하·오만), 여행지가 나오는 거절 3개를 여러 task에 나눠 넣었습니다.
- direct 답변 문장은 생성기 고정 문장(`GREETING_ANSWER` 등)을 볼 수 없어서 같은 뜻으로 직접 썼습니다. `evaluate.py`는 clarify·direct 문장을 비교하지 않으므로 점수에는 영향이 없습니다. 생성기 문장과 맞추려면 direct 6줄의 assistant `content`만 바꾸면 됩니다.

### 검증

`python3 -I`로 다음을 확인했습니다 (모두 통과).

- 90줄 모두 JSON으로 읽히고 `json.dumps(ensure_ascii=False)`로 다시 쓰면 같은 줄이 됨, 키는 `messages`/`tools`/`task`/`difficulty`/`note`만
- system 메시지와 `tools`의 직렬화가 `human_style.jsonl` 1번 줄과 바이트 단위로 같음 (`tools`는 `glople_tools.json`과도 같음)
- 모든 도구 이름이 존재, 필수 인자·enum·정수/문자열/배열 타입을 지키고 스키마에 없는 인자 없음, arguments는 `ensure_ascii=False` JSON 문자열
- 키워드가 모두 94개 목록 안에 있고 중복 없음, MBTI는 대문자 4글자, hashtag에 `#` 없음
- region·query·name·hashtag·places·title 값이 발화 안에 글자 그대로 있음, `evaluate.py`의 `expected_calls`/`canonical`과 같은 방식으로 정답을 읽을 수 있음
- task 개수가 위 표와 같고, 세트 안 중복 발화 없음(공백 무시해도 0), `human_style.jsonl`의 user 발화와 같은 발화 0개(공백 무시해도 0, 이 비교에만 파일을 프로그램으로 읽음)

그 뒤 90개를 labeling_rules.md와 한 줄씩 다시 맞춰 보고 3개를 고쳤습니다.

- `와이프랑 다닐 건데 내 기준으로 봐줘. 마흔둘 남자 엔팁` → `와이프랑 다닐 여행 루트 찾는 중인데 …`: 무엇을 찾는지(루트)가 문장에 없었음
- `… 만좌모 순서로 일정 넣어줘` → `… 일정 저장해줘`: `일정 넣어줘`는 저장 요청인지 애매함
- `광주 사는 대학생인데 … 친구들이랑 갈 펜션` → `… 멀리 놀러 갈 펜션`: 사는 곳 근처 펜션을 찾는다는 뜻으로도 읽힘

쓰기 전에 정답이 두 갈래로 읽혀서 버리거나 바꾼 표현도 있습니다: `근처 밥집`(근처가 query인지 애매), `일본 기후`(나라+도시 표기, 기후=climate로도 읽힘), `온천욕`(온천과 둘 다 넣을지 애매), `서핑 할 수 있는 휴양지`(휴양지가 취향인지 그냥 여행지인지), `해산물 맛집`(음식 키워드인지), `계곡`(협곡과 다른 말인데 비슷해 보임), `칸 영화제 때 묵을 데`(지역이 암시만 됨), `가평 쁘띠프랑스 후기`·`조지아 트빌리시 리뷰`(후기 query 후보 둘), 남친의 나이·MBTI까지 적은 루트 요청(두 사람 다 부를지), `가볼 만해?`(후기인지 상세인지), `#써핑`(해시태그 오타를 고칠지), `서핑 말고 다른 액티비티`(query 범위), `아재`(성별 규칙 없음), `체험거리`를 역사 체험에 쓰기(레포츠와 뜻이 어긋남), `지금` 날씨(enum 없음), 후기 블로그 대필(후기 검색과 거절 두 갈래).

### 확인하지 못한 것

- 작성자가 생성기와 `data/`를 보지 않는 조건이라, 학습 데이터와의 겹침은 작성 뒤 따로 프로그램으로 확인했습니다(2026-10-18).
  - 발화 겹침(공백 무시): v1 0개, v2 1개, v3 0개. v2 train에 있던 `여수 맛집 추천해줘`는 정답은 그대로 두고 `여수 가서 밥 먹을 데 추천 좀`으로 바꿨습니다(그 줄 note에 기록).
  - 정답 값(지역·장소·query·hashtag) 87종 중 생성기 train 지역과 같은 것 9개(가평, 남해, 단양, 안동, 여수, 전주, 춘천, 평창, 하노이), train 장소 1개(우도), test 지역 8개, test 장소 7개입니다. 지역 이름 자체는 닫힌 값에 가까워 그대로 두었습니다.
  - v3 생성기에 새로 넣은 표현 중 이 세트에 나온 것은 `스쿠버 다이빙`→다이빙 1개입니다(이 세트는 `스쿠버다이빙`). 작성자는 v3 생성기를 보지 않았으므로 우연히 겹친 것이고, 이 한 문제는 v3에 조금 유리할 수 있습니다.
- 독립 검수(다른 에이전트가 정답을 먼저 붙인 뒤 비교)는 아직 하지 않았습니다. 위 검토는 작성자 본인이 한 것입니다.
