# Glople 에이전트 도구 호출 모델 학습 (개편)

Glople 여행 에이전트가 쓸 도구 8개를 소형 오픈 모델이 직접 고르고 인자를 채우도록 LoRA로 파인튜닝합니다.
Apple Silicon 맥에서 [MLX](https://github.com/ml-explore/mlx-lm)로 로컬 학습합니다.

| 항목 | 내용 |
| :-- | :-- |
| 기본 모델 | `Qwen3-4B-Instruct-2507` (mlx-community 4bit 변환본 + 공식 채팅 템플릿) |
| 방식 | QLoRA (4bit 가중치 고정, 상위 16개 층에 rank 16 LoRA), assistant 응답만 손실 계산 |
| 데이터 | 템플릿 합성 train 3,000 / valid 200 / test 300. test는 train에 없는 문장 템플릿·지역·장소로 만들도록 했지만 v1은 일부 겹칩니다 (v2는 완전히 분리, 아래 [데이터 버전](#데이터-버전)) |
| 도구 | [`tools/glople_tools.json`](tools/glople_tools.json): 키워드 추천, 지역 검색, 유사 여행자 루트, 여행지 정보, 후기 검색, 글로플러 검색, 날씨, 일정 저장 |

## 실행

```bash
cd training
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install mlx-lm huggingface_hub
bash run_overnight.sh          # 모델 준비 → 데이터 생성 → 학습 → 평가 → 비교표 (v1 설정)
```

M4 Pro(24GB) 기준 800회 반복에 약 4시간, 평가 두 번에 약 30분이 걸립니다. 진행 상황은 `logs/train.log`, 결과는 `results/report.md`에 남습니다.

`run_overnight.sh`는 v1 데이터(`data/tool_calling`)와 v1 설정·어댑터 경로로 학습하고 평가합니다. 데이터 생성 단계의 `build_tool_dataset.py`는 이제 v2를 `data/tool_calling_v2`에 쓰므로 v1 데이터를 덮어쓰지 않습니다.
`data/tool_calling`이 없는 곳(새로 받은 저장소 등)에서는 학습 단계가 데이터를 찾지 못해 멈추므로, 먼저 `.venv/bin/python scripts/build_tool_dataset_v1.py`로 v1 데이터를 만들거나 아래 v2 명령을 씁니다.

## 데이터 버전

| 버전 | 데이터 | 학습 설정 | 어댑터 | 상태 |
| :-- | :-- | :-- | :-- | :-- |
| v1 | `data/tool_calling` | [`configs/qwen3-4b-lora.yaml`](configs/qwen3-4b-lora.yaml) | `adapters/qwen3-4b-tool-calling` | 2026-10-17 밤샘 학습에 사용 |
| v2 | `data/tool_calling_v2` | [`configs/qwen3-4b-lora-v2.yaml`](configs/qwen3-4b-lora-v2.yaml) | `adapters/qwen3-4b-tool-calling-v2` | 2026-10-17~18 학습·평가 완료 ([결과](#v2-결과-2026-10-18)). 생성기 보관본 `scripts/build_tool_dataset_v2.py` |
| v3 | `data/tool_calling_v3` | [`configs/qwen3-4b-lora-v3.yaml`](configs/qwen3-4b-lora-v3.yaml) | `adapters/qwen3-4b-tool-calling-v3` | 2026-10-18 학습·평가 완료 ([`run_v3.sh`](run_v3.sh), [바뀐 점](#v3에서-바뀐-점), [결과](#v3-결과-2026-10-18)) |
| v4 | `data/tool_calling_v4` | [`configs/qwen3-4b-lora-v4.yaml`](configs/qwen3-4b-lora-v4.yaml) | `adapters/qwen3-4b-tool-calling-v4` | 2026-10-18 데이터·설정 준비, 학습 전 ([`run_v4.sh`](run_v4.sh), [v4 절](#v4-도구-결과-뒤-다턴-데이터-m7)) |

### v2 결과 (2026-10-18)

학습: 800단계, 약 4.5시간. 검증 손실은 1.137에서 200단계 0.021, 400단계 0.003, 800단계 0.002로 내려갔습니다. 400단계 뒤로는 거의 평평했습니다.

| 평가 세트 | 지표 | 기본 | v1 | v2 |
| :-- | :-- | --: | --: | --: |
| v2 test 300 | **전체 정답률(모든 문제)** | 58.7% | 82.3% | **84.0%** |
| | 도구 + 인자 완전 일치(도구를 불러야 하는 문제만) | 55.6% | 83.3% | 83.3% |
| | 되묻기 | 77.8% | 55.6% | **83.3%** |
| | 지역 검색 | 7.5% | **47.2%** | 32.1% |
| 사람 말투 90 | **전체 정답률(모든 문제)** | 52.2% | 73.3% | **77.8%** |
| | 도구 + 인자 완전 일치 | 48.7% | **78.9%** | 75.0% |
| | 되묻기 | 50.0% | 25.0% | **100.0%** |

전체 비교표: [`results/report_v2_test.md`](results/report_v2_test.md), [`results/report_human_v2.md`](results/report_human_v2.md).

- **지표 하나만 보면 속습니다.** '도구 + 인자 완전 일치'는 도구를 불러야 하는 문제만 셉니다. 그래서 v2가 크게 나아진 되묻기·직답 문제가 빠지고, 사람 말투 세트에서는 v2가 v1보다 낮아 보였습니다. 전체 정답률은 v2가 더 높습니다(66 → 70개). 그래서 `report.py`가 전체 정답률을 맨 위에 내도록 바꿨습니다.
- **v2가 나아진 점: 지어내지 않음.** v1은 정보가 없으면 지역 '나가', 나이 30, 성별, 일정 장소를 지어내 도구를 불렀습니다. v2는 이런 경우 되묻습니다.
- **v2가 나빠진 점: 조건(query)을 옮겨 쓰지 못함.** v2 test 오답 48개 중 32개가 지역 검색의 query 칸입니다. train의 조건 문구가 8개뿐이라, 모델이 사용자 말을 옮겨 적는 대신 외운 8개 중 비슷한 것으로 바꿨습니다('반려견과 갈 수 있는' → '반려견과 가기 좋은', '부모님 모시고' → '아이와 가기 좋은'). 그 밖에는 개수('5곳')나 장소 유형 말('과학관')을 query에 넣었고, 막연한 '몇 개만'에 limit 3을 붙였습니다. search_reviews에는 limit 칸이 없는데도 붙였습니다.
- **사람 말투에서 남은 오답:** 목록에 없는 키워드('조용한', '와이너리', '스노쿨링')를 그대로 씀, 사는 곳('부산 사는 사람인데')을 목적지로 봄, 정보가 다 있는 루트 요청을 되물음(지나치게 조심함), 여행지가 나온 번역 요청에 도구를 부름.

### v3에서 바뀐 점

v2 오답 유형을 [`scripts/build_tool_dataset.py`](scripts/build_tool_dataset.py)에 반영했습니다(파일 첫머리 설명 참고).

- 조건 문구를 train 8개에서 49개로 늘렸습니다. test 쪽 주제(혼자·반려견·부모님·뷰·초보·늦은 밤·무료)는 train에 쓰지 않았습니다.
- 그 밖에 바꾼 것:
  - 장소 유형 말을 늘렸습니다.
  - 막연한 개수에는 limit을 붙이지 않습니다. 후기 검색에도 이런 말을 넣었습니다.
  - 정보가 다 있는 짧은 루트 요청을 넣었습니다.
  - 키워드는 비슷한 말을 목록의 키워드로 바꾸고, 목록에 없는 꾸밈말은 뺍니다.
  - 사는 곳을 말해도 키워드 추천으로 가는 예시를 넣었습니다.
  - 여행지 이름이 든 무관한 요청은 거절합니다.
- 사람 말투 평가 세트의 문장은 쓰지 않았습니다. 그래도 v3를 이 세트의 오답을 보고 설계했으므로, 이 세트 점수는 실제보다 좋게 나올 수 있습니다. 그래서 생성기를 보지 않은 채 새로 쓴 세트 [`eval_sets/human_style_v2.jsonl`](eval_sets/README.md)로도 기본·v1·v2·v3를 함께 잽니다.
- v3 test 대신 **v2 test로 평가**합니다. 그래야 v1·v2와 같은 시험지로 비교됩니다. 생성기의 검사로 v3 train과 v2 test의 지역·장소·조건·문장이 겹치지 않음을 확인했고, 발화가 같은 것도 0건입니다.
- iters를 800에서 500으로 줄였습니다(v2는 400단계에서 이미 평평했음).

### v3 결과 (2026-10-18)

학습: 500단계, 약 2시간 40분(04:31~07:10). 검증 손실은 0.837에서 200단계 0.018, 400단계 0.016, 500단계 0.005로 내려갔습니다.
전체 정답률(모든 문제 기준)입니다.

| 평가 세트 | 기본 | v1 | v2 | v3 | v3 + 에이전트 근거 검사 |
| :-- | --: | --: | --: | --: | --: |
| v2 test 300 (같은 시험지) | 58.7% | 82.3% | 84.0% | **91.0%** | 92.3% (277) |
| 사람 말투 90 (v3 설계 때 오답을 본 세트) | 52.2% | 73.3% | 77.8% | **81.1%** | 88.9% (80) |
| 새 사람 말투 90 (생성기를 보지 않고 새로 씀) | 57.8% | 65.6% | 67.8% | **72.2%** | 82.2% (74) |

비교표: [`results/report_v3_test.md`](results/report_v3_test.md), [`results/report_human_v3.md`](results/report_human_v3.md), [`results/report_human2.md`](results/report_human2.md).
마지막 열은 `agent/`의 근거 검사(`python -m glople_agent.cli grounding`)로 같은 예측을 다시 채점한 값입니다. 근거 검사가 정답을 오답으로 바꾼 경우는 모든 세트에서 0건이었습니다.

- **조건(query) 옮겨 쓰기는 고쳐졌습니다.** v2 test의 지역 검색 정답률이 32.1%에서 69.8%가 됐고, 조건 문구를 바꿔 쓴 오답은 15건에서 0건이 됐습니다. 후기 검색은 limit을 붙이지 않게 되어 84.6%에서 100%가 됐습니다.
- **새로 쓴 세트에서도 올랐습니다(67.8% → 72.2%).** v3 생성기에 넣은 규칙이 처음 보는 말투에도 통한다는 뜻입니다. 이 세트는 원래 세트보다 어려워서 점수가 전반적으로 낮습니다.
- **남은 오답(새 세트, v3 25개):**
  - 키워드를 목록 단어로 바꾸지 못함 5개: 트래킹, 스쿠버다이빙, 오로라, 해변 산책, 낙타 타기
  - 날씨 시점을 정해진 값으로 못 바꿈 5개: 낼, 담주 등
  - 한국어처럼 보이는 해외 지명: 나라
  - 여행지 이름이 든 대필 요청에 도구를 부름
  - 정보가 다 있는데 되물음: 엣티제(ESTJ) 같은 별명
- **v2 test에 남은 오답 14개:** 장소 유형 말('과학관', '게스트하우스')을 query에 넣은 경우입니다. 이 경우를 오답으로 볼지는 라벨 규칙의 문제입니다. 실제 검색에서는 오히려 도움이 될 수 있어서, 규칙을 바꿀지 정해야 합니다.
- 다음(v4) 후보:
  - 키워드 동의어·외래어·오타 표기 예시
  - 날씨 시점 줄임말
  - 별명 MBTI
  - 장소 유형 말을 query에 넣을지 라벨 규칙 결정

### v4: 도구 결과 뒤 다턴 데이터 (M7)

2026-10-18 [`run_v4.sh`](run_v4.sh)로 학습·평가했습니다(결과는 아래 'v4 결과'). 설계 문서([`agent/docs/design.md`](../agent/docs/design.md) 15.3)의 'v3 다턴 데이터'가 이 v4입니다.

**왜 만드나.** v1~v3 데이터에는 도구 결과(role=tool) 뒤 턴이 하나도 없습니다. 그래서 v3에 학습 때와 같은 system + 도구 8개로 [사용자, 호출, 결과]를 보내면(방식 A, 일반 에이전트 루프) 결과를 무시하고 되묻거나, 도구를 또 부르거나, 학습한 되묻기 문장을 그대로 냈습니다. 결과를 근거로 답한 것은 85개 중 65개였습니다. 답변 전용 프롬프트(방식 B)로는 80개였습니다([`agent/docs/m2-after-tools.md`](../agent/docs/m2-after-tools.md)). v4는 방식 A에서도 결과를 읽고 끝내도록 가르칩니다.

**무엇을 배우나.** 기록 하나에 정답 턴(마지막 assistant) 하나입니다. mlx-lm은 마지막 assistant만 학습하므로(`mask_prompt`), 한 대화의 단계마다 기록을 따로 만듭니다.

| 정답 턴 | 맥락 → 정답 | train | valid | test |
| :-- | :-- | --: | --: | --: |
| first_turn | v3 기록 그대로: 사용자 → 도구 호출 / 되묻기 / 바로 답변 | 2,096 | 108 | 149 |
| chain_first | '결과를 보고 이어서 해 줘' 발화 → **첫 호출만** | 143 | 15 | 34 |
| after_answer | 호출 → 결과(있음) → 결과만 근거로 한 2~4문장 답 | 1,056 | 64 | 63 |
| after_limit | 호출 → 빈 결과·미지원·오류 → 제한을 그대로 알리는 답(같은 호출을 다시 부르지 않음) | 342 | 20 | 90 |
| after_call | 호출 → 결과 → **두 번째 호출**(인자는 결과에서) | 127 | 18 | 25 |
| after2_answer | 호출 → 결과 → 호출 → 결과 → 두 결과를 합친 답(2라운드 뒤에는 늘 답) | 107 | 17 | 25 |
| 합계 | | 3,871 | 242 | 386 |

- 첫 판단은 v3 train 3,000개에서 작업 비율대로 70%를 그대로 가져왔습니다(SYSTEM_PROMPT·도구 정의·train/test 분리 같음). 발화에 사람 말투 세트의 지역 '보성'이 든 v3 기록 3개(train, valid 1개)는 뺐습니다.
- 두 번째 호출은 사용자가 결과에 따라 이어서 해 달라고 했을 때만 만듭니다(드물게, 4종). 같은 서명(이름+인자)은 다시 부르지 않고, 2라운드를 넘지 않습니다.
  - 키워드 추천 → 맨 위 장소 후기(train 46)
  - 키워드 추천 → 그 장소 날씨(26, 날씨 지역 = 그 장소의 where, 자치구까지 적힌 국내 where는 시 단위로)
  - 비슷한 여행자 루트 → 첫 여행지 후기(45)
  - '정보가 없으면 후기라도' → 상세가 빈 결과일 때만 후기(10). 상세가 있으면 그대로 답하고(25), 첫 결과가 비면 이어 가지 않고 제한을 알립니다(13).
- train 도구 결과(결과 하나 기준): 키워드 ok 315 · 빈 20 · 오류 20, 루트 ok 194 · 오류 11, 후기 ok 206 · 빈 111 · 오류 9, 글로플러 ok 59 · 빈 134(국내·대행 국가 밖) · 오류 3, 지역 검색 ok 178 · 빈 32 · 미지원 67(국내 음식점·숙박) · 오류 14, 상세 ok 145(같은 이름 두 지역 → 후보 되묻기 포함) · 빈 43 · 오류 6, 날씨 ok 160 · 빈 4 · 오류 11.

**어떻게 만들었나** ([`scripts/build_multiturn_dataset.py`](scripts/build_multiturn_dataset.py), `agent/.venv`로 실행, LLM 호출 없음).

- 도구 결과는 에이전트의 실제 도구 코드로 만듭니다: `glople_agent.tools.registry.run_tool` + `FakeBackend`(가짜 Spring·가짜 Open-Meteo). 오류는 `FakeBackend(errors=…)` → `tool_error_to_json`입니다. 결과 JSON은 에이전트가 모델에 보내는 봉투 그대로이고, Type3 설명문은 원래 들어 있지 않습니다. `agent/`는 고치지 않았습니다.
- train·valid 결과는 학습 전용 fixture([`scripts/multiturn_fixtures.py`](scripts/multiturn_fixtures.py) → `fixtures/v4_backend/`)로 만듭니다. 에이전트 fixture와 같은 모양이지만 행은 새로 만들었습니다.
  - 키워드 장소 309, S1 여행지 515, 여행자 320, 후기 541, 글로플러 27
  - 날씨 지오코딩 35곳: 에이전트 표 사본에서 평가 쪽 지역을 빼고, train 시군을 더했습니다.
  - 에이전트 fixture의 장소·후기 제목·닉네임 111개는 하나도 넣지 않았습니다. 평가 때 모델이 보는 결과는 학습 때 본 적 없는 이름입니다.
  - 기록마다 행 일부만 남기고 순서를 섞은 변형 6개 중 하나를 씁니다. 같은 검색어라도 결과가 달라야 모델이 결과를 외우지 않고 읽습니다.
  - test는 에이전트 기본 fixture(평가와 같은 것)로 만듭니다.
- 답은 코드 템플릿 + 정해진 변주로 씁니다.
  - 이름·지역(where)·분류·숫자는 결과에 있는 것만 씁니다.
  - notes의 제한('…조건 없이 골랐어요', '국내 글로플러는 아직 없어요', '음식점·숙소 정보는 아직 모으지 않았어요', 가짜 날씨 안내)은 그대로 옮깁니다.
  - 설명(note·summary)은 옮기거나 바꿔 말하지 않고 '화면 카드'로 돌립니다.
  - 개수는 고유어('세 곳'), 날짜는 결과 글자 그대로(2026-10-19)입니다. 측정 스크립트가 답의 숫자를 결과 글자와 그대로 맞춰 보기 때문입니다.
- 모든 답을 에이전트 답변 가드(`glople_agent.graph.guard.check_answer`, 카드 원문 겹침 포함)와 추가 검사로 확인하고, 하나라도 걸리면 버립니다.
  - 추가 검사: 2~4문장, measure_after_tools 판정이 '근거로 답함/제한을 알림', 결과가 둘 이상이면 이름도 둘 이상, notes 제한 문구, 소개 옮김 없음, LoRA 말버릇('비슷한 여행자를 찾으려면') 없음
  - 두 번째 호출은 인자 모델(`ARGS_MODELS`)과 근거 검사(`check_call`, 근거 = 발화 + 앞 결과의 이름·지역)를 통과해야 합니다.
  - 최종 실행에서 버린 것은 2,048토큰을 넘을 수 있는 기록 27개와 notes 문구가 끊긴 답 3개입니다.
- 시드 41로 같은 파일이 나옵니다(해시 시드를 바꿔도 같음을 md5로 확인). 만들 때 쓴 에이전트 파일 해시는 `data/tool_calling_v4/manifest.json`에 남깁니다.

예시(train):

```text
사용자: 설경하고 등산 좋아하는데 여행지 추천해 주고 제일 잘 맞는 곳 후기도 보여줘
  [chain_first]  → recommend_by_keywords {"keywords": ["설경", "등산"]}
  결과: 설악산(강원특별자치도 속초시), 후지산(일본 시즈오카현), 북한산, 무등산, 대관령 양떼목장
  [after_call]   → search_reviews {"query": "설악산"}
  결과: {"status": "empty", "notes": ["아직 설악산 후기가 없어요."]}
  [after2_answer] 말씀하신 설경·등산 키워드로 여행지 다섯 곳을 찾았어요. 가장 먼저 나온 곳은 강원특별자치도 속초시에 있는 설악산이고,
                  후지산(일본 시즈오카현), 북한산(서울특별시 강북구), 무등산(광주광역시 북구)과 대관령 양떼목장(강원특별자치도 평창군)도
                  함께 추천드려요. 다만 설악산 후기는 아직 올라온 글이 없어요.
사용자: 제주 현지 가이드 찾아줘 → search_gloplers {"hashtag": "제주"} → empty, notes ["제주 글로플러를 찾지 못했어요.", "국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."]
  [after_limit]  제주 글로플러는 찾지 못했어요. 국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본). 다른 지역이나 관심사로 글로플러를 찾아볼까요?
사용자: 국립현대미술관 청주 정보 찾아 주고 없으면 다녀온 사람들 리뷰라도 찾아줘 → get_destination_detail → ok(충청북도 청주시, 문화시설)
  [after_answer] 국립현대미술관 청주는 충청북도 청주시에 있는 문화시설이에요. 사진과 자세한 소개는 화면 카드에서 볼 수 있어요.   (상세가 있으니 후기는 부르지 않음)
```

**누설 검사.** 생성기가 만들 때 확인하고, [`scripts/check_multiturn_dataset.py`](scripts/check_multiturn_dataset.py)가 따로 다시 확인합니다. 둘 다 0건입니다.

- 평가 발화 753개(v2 test·v3 test·human_style·human_style_v2)가 v4 train·valid에 없습니다.
- v2/v3 test의 지역·장소·조건·해시태그·일정 이름 79개가 train·valid의 발화·호출·결과·답 어디에도 없습니다. test 카테고리 말 16개는 발화에 없습니다.
- 사람 말투 두 세트의 정답 인자 가운데 v3 train 풀에 없는 지역·장소 134개도 없습니다.
  - human_style_v2 인자 중 13개(가평·남해·단양·안동·여수·전주·춘천·평창·하노이·우도·등산·서핑·사진여행)는 v3 train 지역·장소·키워드·해시태그와 같은 값입니다. 이 세트를 생성기를 보지 않고 쓰다 보니 겹친 것이고, v3 train부터 있던 겹침입니다.
- train·valid 도구 결과의 항목 이름에 에이전트 평가 fixture 이름이 없습니다.
- split 사이 같은 발화 0건입니다.

**토큰 길이** (Qwen3 채팅 템플릿 + 도구 8개, CPU에서 tokenizers로 잼, `check_multiturn_dataset.py`):

| split | 기록 | 평균 | p50 | p90 | p99 | 최대 | 학습 토큰 평균 |
| :-- | --: | --: | --: | --: | --: | --: | --: |
| train | 3,871 | 1,420 | 1,350 | 1,625 | 1,784 | 1,885 | 51 |
| valid | 242 | 1,443 | 1,380 | 1,652 | 1,813 | 1,845 | 54 |
| test | 386 | 1,412 | 1,392 | 1,555 | 1,722 | 1,878 | 43 |

종류별 평균은 first_turn 1,329, after_limit 1,428, after_call 1,531, after_answer 1,568, after2_answer 1,707입니다. 2,048을 넘는 기록이 없어 `max_seq_length`는 v3와 같은 2048입니다.

**학습 설정** ([`configs/qwen3-4b-lora-v4.yaml`](configs/qwen3-4b-lora-v4.yaml)): v3에서 data·adapter_path·steps_per_eval(200 → 100)만 바꿨습니다.

- iters 500 × 배치 4 = 2,000개, train의 약 0.52 epoch입니다. v2는 400단계, v3는 200단계에서 검증 손실이 이미 거의 평평했습니다. 도구 결과 뒤 답도 템플릿 문장이라 비슷하게 빨리 수렴할 것으로 봤습니다.
- v3 실측 속도(초당 약 294토큰, 전체 시퀀스 기준)로 어림하면 학습 약 2.7시간, 검증 6번을 넣어 약 2.9시간입니다.
- 100단계마다 저장하므로 더 이른 체크포인트도 고를 수 있습니다.

**실행.**

```bash
cd training
../agent/.venv/bin/python scripts/build_multiturn_dataset.py      # 데이터(CPU, 이미 만들어 둠, 같은 시드면 같은 파일)
.venv/bin/python scripts/check_multiturn_dataset.py               # 형식·토큰 길이·누설·에이전트 코드 변경 확인(CPU)
caffeinate -ims bash run_v4.sh 2>&1 | tee logs/v4_pipeline.log    # GPU가 빌 때: 확인 → 학습 → 평가 → 비교표
```

- `run_v4.sh`는 확인 스크립트가 실패하면 학습하지 않습니다. 에이전트 도구 코드가 데이터를 만든 뒤 바뀌었다는 경고만 나오면 멈추지 않고, `REBUILD_V4=1`이면 학습 전에 데이터를 다시 만듭니다.
- 평가 1, 첫 판단: `evaluate.py`로 v4를 v2 test·human_style·human_style_v2에서 잽니다. 기본·v1·v2·v3와 비교한 표는 `results/report_v4_test.md`, `report_human_v4.md`, `report_human2_v4.md`입니다.
- 평가 2, 도구 결과 뒤: [`scripts/evaluate_after_tools.py`](scripts/evaluate_after_tools.py)가 `agent/scripts/measure_after_tools.py`와 같은 문제·판정을 다시 돌립니다.
  - `cases`(agent/.venv): 측정 스크립트의 `cases()`를 그대로 불러 문제를 만듭니다. human_style_v2와 v4 test의 이어서 부르기 문제를 더합니다.
  - `run`(training/.venv): 로컬 모델+어댑터(mlx generate)나 `--url` 서버로 풉니다. 방식 A와 방식 B(`--mode B`)를 고를 수 있습니다.
  - `score`(agent/.venv): 답변 가드로 다시 채점하고, 옮긴 판정이 원본과 같은지 확인한 뒤 `results/report_after_tools_v4.md`를 씁니다.
  - v3·v4를 방식 A(protocol·human2·chain)와 방식 B(protocol)로 비교합니다. 이미 띄운 서버로 재려면 `run --url http://127.0.0.1:8090/v1/chat/completions`를 씁니다.

**열린 문제.**

- **에이전트가 이 작업 중에 바뀌었습니다(M6, 2026-10-18 14:3x~15:0x).** S1 여행지·상세와 날씨가 실제 결과를 내게 됐고, 데이터는 그 코드로 만들었습니다.
  - 같은 측정 스크립트의 문제도 85+20개에서 161+20개(날씨·검색·상세 ok가 늘어남)로 바뀌었습니다. 그래서 `m2-after-tools.md`의 v3 숫자(65/85)와 새 표의 숫자는 바로 비교하지 말고, 같은 실행의 v3 열과 비교합니다.
  - 도구 코드가 또 바뀌면 `check_multiturn_dataset.py`가 알려 주니 데이터를 다시 만듭니다.
- **원래 판정은 날씨 답을 늘 '결과 이름을 하나도 안 씀'으로 셉니다.** 날씨 결과에는 이름이 없는데 측정 스크립트가 이름부터 보기 때문입니다(M6 전에는 날씨가 늘 미지원이라 없던 경우). 비교표에 '원래 판정'과 이름 없는 결과는 숫자만 보는 'v4 판정'을 같이 냅니다. 템플릿 답으로 돌려 보니 원래 판정 127/161, v4 판정 161/161이었고, 차이 34개가 모두 날씨였습니다.
- **에이전트 쪽 M7 루프는 만들었습니다.** `GLOPLE_AFTER_TOOLS=loop`이면 그래프가 `collect → route_after_tools`로 v4에 결과를 보여 주고, 두 번째 호출은 최대 2라운드·같은 서명 금지·'발화 + 앞 결과의 이름·지역' 근거 검사를 거칩니다([`agent/README.md`](../agent/README.md) 'M7 도구 결과 뒤 루프').
- **날씨 이어서 부르기**는 where를 그대로 넘깁니다. '일본 시즈오카현'·'호주 퀸즐랜드주'처럼 지역 해석기가 모르는 곳은 빈 결과가 되어 그대로 알리는 답을 배웁니다. 해석기가 도·주 단위를 다루게 되면 다시 만듭니다.
- **답 문장이 템플릿이라 다양성이 낮습니다.** 설계 15.3의 '기본 모델 후보 생성 → 근거 필터' 방식 대신, 이번에는 LLM 호출 없이 만들라는 지시대로 했습니다. 실제 대화 로그가 생기면 사람 검수 답을 섞는 것이 다음 후보입니다.
- **가짜 날씨 안내도 그대로 배웁니다.** 가짜 날씨(fixture) 결과에는 '오프라인 가짜 날씨(fixture)라 실제 날씨가 아니에요.' note가 붙고, 답이 이를 옮기도록 배웁니다. 실제 Open-Meteo 결과에는 이 note가 없어서 답에도 나오지 않습니다.
- **첫 판단 데이터는 v3 그대로입니다.** v3 결과 절의 '다음 후보'(키워드 동의어·날씨 시점 줄임말·별명 MBTI)는 v4에 넣지 않았습니다. 첫 판단 기록이 v3의 70%로 줄어서 첫 판단 점수가 조금 떨어질 수 있습니다. `run_v4.sh`가 v3와 같은 시험지로 비교합니다.
- 프롬프트 주입 흉내 후기 제목은 학습 fixture에 있지만 결과에 거의 나오지 않습니다(train 0건). 주입 내성은 에이전트 쪽 정제와 가드에 맡깁니다.

### v4 결과 (2026-10-18)

학습: 500단계, 약 3시간 16분(15:17~18:33, 같은 시간에 CPU 작업이 겹침). 검증 손실은 1.637에서 100단계 0.198, 300단계 0.089, 500단계 0.048로 내려갔습니다.

**1. 첫 판단** (전체 정답률, 같은 시험지. 비교표 [`results/report_v4_test.md`](results/report_v4_test.md), [`report_human_v4.md`](results/report_human_v4.md), [`report_human2_v4.md`](results/report_human2_v4.md))

| 평가 세트 | v3 | v4 | v3 + 근거 검사 | v4 + 근거 검사 |
| :-- | --: | --: | --: | --: |
| v2 test 300 | **91.0%** | 87.0% | **92.3%** (277) | 89.3% (268) |
| 사람 말투 90 (v3 설계 때 오답을 본 세트) | **81.1%** | 78.9% | **88.9%** (80) | 87.8% (79) |
| 새 사람 말투 90 (생성기를 보지 않고 새로 씀) | 72.2% | **73.3%** | 82.2% (74) | 82.2% (74) |

- v4는 첫 판단이 v2 test에서 4%p 내려갔습니다. v3가 맞히고 v4가 틀린 14개 중 대부분은 v3에서 고쳤던 버릇이 돌아온 것입니다: 발화 문구를 검색 조건(query)에 옮겨 씀('다음 달에', '있으면 알려줘'), 없는 값을 채움(나이 0, 시점 '오늘', 지역 '서울'), 병렬 호출 하나 빠뜨림.
- 첫 판단 기록이 v3의 약 70%로 줄고(다턴 기록이 그만큼 자리를 차지) 학습 단계 수는 같아서, 첫 판단을 덜 본 영향으로 봅니다. 지어낸 값은 에이전트 근거 검사가 대부분 잡아서 근거 검사 뒤 차이는 0~3%p입니다.

**2. 도구 결과 뒤** (같은 문제·판정, 첫 판단은 정답 호출로 고정. 비교표 [`results/report_after_tools_v4.md`](results/report_after_tools_v4.md))

| 행동 (v2 test + 사람 말투 문제) | v3 일반 루프(A) | v4 일반 루프(A) | v3 답변 전용(B) | v4 답변 전용(B) |
| :-- | --: | --: | --: | --: |
| 결과가 있을 때 결과를 근거로 답함 (v4 판정, 161) | 128 | **159** | 155 | **161** |
| 결과에 없는 숫자 (161) | 7 | 2 | 0 | 0 |
| 결과가 있는데 도구를 또 부름 (161) | 3 | **0** | 0 | 0 |
| 빈 결과·오류를 그대로 알림 (20) | 9 | **20** | 19 | 19 |
| 에이전트 답변 가드 통과 (글로 답한 것) | 159 | **215** | 176 | 179 |
| 이어서 부르기에서 맞는 두 번째 호출 (결과 있음 39) | 15 | **18** | - | - |
| 평균 응답 시간(초, 결과 있음) | 3.92 | 3.48 | 1.02 | 1.27 |

- v4는 일반 루프(A)에서도 결과를 읽고 끝냅니다. v3 A의 약점(결과 무시, 또 부르기, 제한을 지어냄)이 거의 사라져 답변 전용(B)과 비슷한 근거성에 도달했습니다. 사람 말투 새 세트(human_style_v2)에서도 근거로 답함이 11/20 → 20/20입니다.
- 남은 약점은 가드의 `unknown_number` 30건입니다. 그중 24건은 비슷한 여행자 결과가 1~2명인데 '여행자 세 명'이라고 센 경우입니다. v4 학습 데이터의 비슷한 여행자 결과 239개가 **모두 3명**이었고 답 138개가 '세 명'이라고 써서, 개수를 세지 않고 문구째 외운 것입니다. 가드가 이 답을 막아 템플릿으로 바꾸므로 사용자에게 나가지는 않습니다. 다음 데이터에서는 결과 항목 수(1~5)를 섞어야 합니다.
- 이어서 부르기(두 번째 호출)는 18/39로 아직 낮습니다. 사용자가 '결과에 따라 이어서' 해 달라고 할 때만 쓰는 드문 경로라, 에이전트 기본값을 정할 때는 이 숫자와 첫 판단 손실을 함께 봅니다(에이전트 실측: [`agent/docs/m7-loop.md`](../agent/docs/m7-loop.md), [`agent/docs/m4-ab.md`](../agent/docs/m4-ab.md)).

### v1의 알려진 한계

밤샘 학습에 쓴 v1 데이터에서 리뷰로 확인된 문제입니다.

- **test가 train과 완전히 분리되지 않았습니다.** test 300개 중 45개(15%)가 train에도 쓰인 값을 하나 이상 씁니다.
  일정 저장 test 34개 중 20개는 지역이 train 지역인 여수·교토였고(장소 이름은 test 전용), 문화시설·레포츠에는 test 전용 카테고리 말이 없어서 검색·여러 도구 18개가 train 말(박물관·액티비티·체험 활동 등)을 그대로 썼고, 일정 이름 12개도 train과 같은 목록이었습니다.
  해시태그 목록과 해시태그 문장 템플릿도 train과 같았습니다(이번 test에는 해시태그 예시가 우연히 없었음).
  그래서 v1 test 점수(`results/report.md`)를 "처음 보는 지역·장소·표현"에 대한 성능으로 읽으면 실제보다 좋게 볼 수 있습니다.
- **라벨 충돌.** 지역 없는 `박물관 추천해줘`, `공연 찾아줘` 같은 요청(train 6개)이 지역 되묻기로 붙어 있어서, 같은 말을 쓰는 키워드 추천 예시(train 40개)나 recommend_by_keywords 도구 설명("지역을 정하지 않고 취향만 말했을 때")과 어긋납니다.
- **되묻기 조사 오류.** `나이와 성별가 필요해요`(train 14개). `evaluate.py`는 도구를 부르지 않았는지만 채점해서 점수에는 영향이 없지만, 모델이 틀린 조사를 배웁니다.
- **작업 비율이 가중치와 다릅니다.** 중복 발화를 버리고 다시 뽑는 동안 문장 풀이 작은 작업이 다른 작업으로 채워졌습니다.
  train 기준 바로 답변 0.4%(의도 3.9%, 12개), 되묻기 2.9%(5.8%), 글로플러 4.7%(7.8%), 여행지 정보 5.7%(8.7%)이고, 그만큼 키워드(16.5%)·검색(20.4%)·루트(13.0%)·일정(11.3%)이 많습니다.
- 명사 키워드에 동사 템플릿이 붙은 어색한 문장이 있습니다 (test `유적이랑 박물관 할 수 있는 여행지 몇 군데만 알려줘`, train `건축이랑 탑 즐길 수 있는 여행지 어디 있어?`).

v1 생성기는 [`scripts/build_tool_dataset_v1.py`](scripts/build_tool_dataset_v1.py)로 따로 보관했습니다. 같은 시드로 돌리면 밤샘 학습에 쓴 `data/tool_calling/` 세 파일과 바이트 단위로 같은 파일이 나옵니다(md5 확인).

### v2에서 바뀐 점

[`scripts/build_tool_dataset.py`](scripts/build_tool_dataset.py)만 고쳤고 시드(13), 크기(3,000/200/300), `SYSTEM_PROMPT`, 도구 정의, 기록 형식, 인자 규칙, 키워드 94개(KeywordMask와 같음)는 그대로입니다.
라벨 규칙 전체는 [`eval_sets/labeling_rules.md`](eval_sets/labeling_rules.md)에 있습니다.

- **test 완전 분리**: 지역·장소·조건(query)·카테고리 말(여섯 카테고리 모두)·해시태그·일정 이름·문장 템플릿·고정 문장을 train과 겹치지 않게 나눴습니다.
  하나라도 겹치거나 한쪽 지역명이 다른 쪽 장소 이름 안에 들어 있으면 생성 전에 `AssertionError`로 멈춥니다. 키워드 94개, MBTI, 성별·시점·일수 표현은 도구가 받는 닫힌 값이라 같이 씁니다.
  v2 test는 v1 train과도 지역·장소·카테고리 말·발화가 겹치지 않아서 v1·v2 어댑터를 같은 v2 test로 비교할 수 있습니다. 단, 지역 없는 박물관류 요청 5개(`박물관 하나만 골라줘` 등)는 v1 학습 데이터의 라벨(지역 되묻기)과 정답이 다릅니다.
- **라벨 규칙 통일**: 지역 없이 박물관·미술관·축제·공연만 말하면 recommend_by_keywords, 지역 되묻기는 키워드가 아닌 카테고리 말(맛집·숙소·액티비티·관광지 …)일 때만입니다.
  두 경우를 같은 문장 틀(`○○ 추천해줘` 등)로 만들어 차이를 배우게 했습니다. 생성 전에 생성기마다 2만 번씩 뽑아 같은 발화에 정답이 두 가지로 붙는 경우가 없는지 검사합니다.
- **조사**: 슬롯 뒤 조사는 모두 받침 판정 함수로 붙입니다 (영문자·숫자는 읽는 소리 기준, MBTI → 엠비티아이 → 가). 되묻기는 `나이와 성별이`, `성별과 MBTI가`처럼 마지막 단어를 따릅니다.
- **작업 비율**: 작업마다 가중치 × 크기를 반올림한 개수만큼 채웁니다. 되묻기·바로 답변·키워드·글로플러·여러 도구는 그 안의 세부 종류(예: 인사/감사/기능 질문/거절)까지 가중치대로 채우고, valid도 작업 비율대로 떼어 냅니다. 문장 풀이 모자라면 다른 작업으로 메우지 않고 멈춥니다.
  이를 위해 문장 풀을 늘렸습니다: 바로 답변(train 인사 55·감사 39·지역이 들어간 감사·기능 질문 40·범위 밖 요청 60 × 앞말 4가지), 되묻기(루트 정보 7가지 조합, 일정에서 장소만 빠짐, 후기 대상 없음, 취향·지역 모두 없음 추가), 여행지 정보(train 장소 54 → 100개, 템플릿 5 → 9개), 글로플러(train 지역 40 → 56개, 템플릿 5 → 10개, 해시태그 5 → 14개).
- **문장 다듬기**: 키워드를 활동·볼거리·그 밖으로 나눠 '할 수 있는'·'즐길 수 있는'은 활동 키워드에만, '볼 수 있는'·'구경하기 좋은'은 볼거리 키워드에만 붙입니다. 조건(query)은 어울리는 카테고리에만 붙여 `가성비 좋은 축제` 같은 조합을 만들지 않습니다. test 지역 `빈`은 `비엔나`로 바꿨습니다.
- 사람 말투 평가 세트(`eval_sets/human_style.jsonl`)와 같은 발화는 만들지 않습니다.

v2 작업별 개수 (생성 스크립트가 끝에 의도 비율과 같이 출력합니다):

| 작업 | 의도 비율 | train | valid | test |
| :-- | --: | --: | --: | --: |
| keywords | 13.6% | 408 | 27 | 41 |
| search | 17.5% | 524 | 35 | 53 |
| routes | 9.7% | 292 | 19 | 29 |
| detail | 8.7% | 262 | 18 | 26 |
| reviews | 8.7% | 263 | 17 | 26 |
| gloplers | 7.8% | 232 | 16 | 23 |
| weather | 8.7% | 263 | 17 | 26 |
| plan | 8.7% | 263 | 17 | 26 |
| parallel | 6.8% | 203 | 14 | 20 |
| clarify | 5.8% | 174 | 12 | 18 |
| direct | 3.9% | 116 | 8 | 12 |

### v2 학습과 평가 (밤샘 파이프라인이 끝난 뒤)

`run_overnight.sh`가 `results/report.md`까지 만들고 끝난 뒤, GPU가 비었을 때 순서대로 돌립니다. 평가는 300개에 약 15분, 사람 말투 세트 90개에 약 5분입니다.
`evaluate.py`의 기본 `--data`는 v1 test이므로 v2 test는 `--data`로 지정하고, `--name`은 `base`/`finetuned`와 다르게 줘서 밤샘 결과를 덮어쓰지 않게 합니다.

```bash
cd training

# 0. report.py는 항상 results/report.md에 쓰므로 밤샘 비교표(v1 test)를 먼저 보관
cp results/report.md results/report_test.md

# 1. 사람 말투 평가 세트로 기본 모델과 v1 모델 평가 (v2 학습 전에 해 둘 수 있음)
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl --name base_human
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl --name finetuned_human \
    --adapter adapters/qwen3-4b-tool-calling

# 2. v2 학습 (약 4시간). 데이터는 이미 만들어 두었고, 다시 만들어도 시드 고정이라 같은 파일이 나옵니다.
#    .venv/bin/python scripts/build_tool_dataset.py   # → data/tool_calling_v2
.venv/bin/mlx_lm.lora --config configs/qwen3-4b-lora-v2.yaml 2>&1 | tee logs/train_v2.log

# 3. v2 test로 기본·v1·v2 모델 비교
.venv/bin/python scripts/evaluate.py --data data/tool_calling_v2/test.jsonl --name base_v2test
.venv/bin/python scripts/evaluate.py --data data/tool_calling_v2/test.jsonl --name finetuned_v1_v2test \
    --adapter adapters/qwen3-4b-tool-calling
.venv/bin/python scripts/evaluate.py --data data/tool_calling_v2/test.jsonl --name finetuned_v2_v2test \
    --adapter adapters/qwen3-4b-tool-calling-v2
.venv/bin/python scripts/report.py base_v2test finetuned_v1_v2test finetuned_v2_v2test
cp results/report.md results/report_v2_test.md

# 4. 사람 말투 평가 세트로 v2 모델 평가 후 세 모델 비교
.venv/bin/python scripts/evaluate.py --data eval_sets/human_style.jsonl --name finetuned_v2_human \
    --adapter adapters/qwen3-4b-tool-calling-v2
.venv/bin/python scripts/report.py base_human finetuned_human finetuned_v2_human
cp results/report.md results/report_human.md
```

사람 말투 평가 세트의 구성, 난이도별 정확도를 보는 방법, 라벨 규칙은 [`eval_sets/README.md`](eval_sets/README.md)에 있습니다.
v1 모델이 지역 없는 박물관·미술관 요청(평가 세트 7번 줄)을 되묻는다면 위의 v1 라벨 충돌 때문일 수 있습니다.

2~4단계는 [`run_v2.sh`](run_v2.sh) 하나로 돌릴 수 있습니다(`caffeinate -i bash training/run_v2.sh`). 평가 하나가 실패해도 나머지 평가는 계속합니다. 비교표는 `results/report_v2_test.md`, `results/report_human_v2.md`로 남기고, `results/report.md`는 밤샘 v1 비교표(`report_test.md`)로 되돌립니다. 실행 중에는 이 스크립트를 고치지 마세요(bash가 실행 중에도 파일을 이어서 읽습니다).

## API 서버로 띄우기

에이전트(로드맵 4)는 파인튜닝한 모델을 OpenAI 호환 API로 부릅니다. [`scripts/serve.sh`](scripts/serve.sh)가 `mlx_lm.server`를 `127.0.0.1`에 띄우고, 도구 호출은 응답의 `choices[0].message.tool_calls`에 담깁니다. 학습이 GPU를 쓰는 동안에는 띄우지 마세요.

```bash
cd training
bash scripts/serve.sh                                         # v1 어댑터, 8090 포트
bash scripts/serve.sh adapters/qwen3-4b-tool-calling-v2 8091  # v2 어댑터
# 서버 응답을 evaluate.py와 같은 기준으로 채점해 로컬 평가와 어긋나지 않는지 확인
.venv/bin/python scripts/check_server.py --data data/tool_calling/test.jsonl --limit 30
```

## Hugging Face Hub에 올리기

학습한 LoRA(v3·v4), 키워드 태거, 임베딩 모델을 올릴 준비는 [`hf/`](hf/README.md)에 있습니다. `hf/publish.py stage`가 올릴 파일만 모으고 결과 파일로 모델 카드를 채웁니다. 실제 올리기(`push`)는 오너가 토큰으로 직접 하고, 기본은 비공개 저장소입니다.

## 파일

- [`scripts/prepare_model.py`](scripts/prepare_model.py): 4bit 가중치는 그대로 두고 채팅 템플릿만 공식 것으로 바꾼 로컬 모델 폴더 생성. 변환본 템플릿은 응답 앞에 빈 `<think></think>`를 넣어서, 그대로 학습하면 매번 빈 think 블록을 출력하도록 배웁니다.
- [`scripts/build_tool_dataset.py`](scripts/build_tool_dataset.py): 발화 → 도구 호출 / 되묻기 / 바로 답변 데이터 합성 (v2, 기본 출력 `data/tool_calling_v2`). train/test 분리와 정답 충돌을 검사한 뒤 만들고, 끝에 작업 비율표를 출력
- [`scripts/build_tool_dataset_v1.py`](scripts/build_tool_dataset_v1.py): v1 생성기 보관본 (밤샘 학습 데이터 `data/tool_calling`을 그대로 다시 만듦)
- [`configs/qwen3-4b-lora.yaml`](configs/qwen3-4b-lora.yaml): 학습 설정 (v1 데이터, `run_overnight.sh`가 사용)
- [`configs/qwen3-4b-lora-v2.yaml`](configs/qwen3-4b-lora-v2.yaml): v2 데이터 학습 설정 (`data`, `adapter_path`만 다름)
- [`scripts/evaluate.py`](scripts/evaluate.py): 호출 여부 판단, 도구 이름, 도구+인자 완전 일치, JSON 오류율 측정
- [`scripts/report.py`](scripts/report.py): 기본 모델과 파인튜닝 모델 비교표
- [`run_v2.sh`](run_v2.sh): v2 학습과 기본·v1·v2 비교 평가를 한 번에 실행
- [`scripts/build_multiturn_dataset.py`](scripts/build_multiturn_dataset.py): v4 다턴 데이터 생성(`agent/.venv`로 실행, 에이전트 도구 코드·답변 가드 사용, 출력 `data/tool_calling_v4` + `manifest.json`)
- [`scripts/multiturn_fixtures.py`](scripts/multiturn_fixtures.py): v4 학습 전용 가짜 Spring·Open-Meteo 데이터(→ `fixtures/v4_backend/`, 에이전트 fixture와 같은 모양, 이름은 겹치지 않음)
- [`scripts/check_multiturn_dataset.py`](scripts/check_multiturn_dataset.py): v4 데이터 형식·토큰 길이·누설·에이전트 코드 변경 확인(CPU, `training/.venv`)
- [`scripts/evaluate_after_tools.py`](scripts/evaluate_after_tools.py): 도구 결과 뒤 평가(`cases` → `run` → `score`, `agent/scripts/measure_after_tools.py`와 같은 문제·판정)
- [`configs/qwen3-4b-lora-v4.yaml`](configs/qwen3-4b-lora-v4.yaml), [`run_v4.sh`](run_v4.sh): v4 학습 설정과 확인·학습·평가 파이프라인
- [`scripts/serve.sh`](scripts/serve.sh), [`scripts/check_server.py`](scripts/check_server.py): OpenAI 호환 서버 실행과 서버 응답 채점
- [`hf/publish.py`](hf/publish.py), [`hf/cards/`](hf/cards/): Hub 업로드 준비(stage)·올리기(push)와 모델 카드 틀
- [`eval_sets/`](eval_sets/README.md): 사람 말투 평가 세트 90개(`human_style.jsonl`)와 라벨링 규칙(`labeling_rules.md`)
