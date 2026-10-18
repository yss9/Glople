# 여행 취향 자연어 검색 임베딩 (개편 3단계)

"조용한 바닷가 마을"처럼 문장으로 말한 여행 취향으로 여행지를 찾도록, 문장 임베딩 모델을 국내 여행지 1,934개에 맞게 파인튜닝합니다.
지금 서버의 키워드 검색(94개 키워드 비트마스크, `/api/search`)과 **같은 질의·같은 정답·같은 지표**로 비교합니다.

> 현재 상태 (2026-10-18): [`run_embedding.sh`](run_embedding.sh)로 MPS 본 학습(3에폭, best 에폭 2) → 평가 → 색인까지 끝났습니다.
> test nDCG@10 **0.802** (키워드 칩 검색 0.484, 학습 전 e5-base 0.466. AI silver 라벨·합성 질의 기준, [`results/e5-base-ft.compare.md`](results/e5-base-ft.compare.md)).
> 화면 '문장으로 찾기'(`/search/semantic`)와 검색 서버(127.0.0.1:8096)를 붙였습니다([서비스에 연결](#서비스에-연결)).

| 항목 | 내용 |
| :-- | :-- |
| 기본 모델 | [`intfloat/multilingual-e5-base`](https://huggingface.co/intfloat/multilingual-e5-base) (MIT, 278M, revision `d128750597153bb5987e10b1c3493a34e5a4502a` 고정) |
| 학습 | sentence-transformers `MultipleNegativesRankingLoss` + 어려운 음성 1개 + 거짓 음성 가리기, AdamW 2e-5, 배치 32, 3에폭(494 step) |
| 학습 데이터 | **합성** 선호 질의 5,248쌍 (태거 train 여행지 1,312개 × 4) |
| 평가 | test 질의 279개, 검색 대상 290개(태거 test 163 + test_new 127). nDCG@10, Recall@20, MRR |
| 정답 | 질의 키워드가 여행지의 키워드 라벨(keyword_tagger의 **AI silver 라벨**)에 얼마나 들어 있는지 + 지역·분류 조건 |
| 환경 | 이 폴더 전용 `.venv` (Python 3.12, torch 2.14.1, transformers 5.18.0, sentence-transformers 6.1.0, numpy 2.5.3). MLX는 쓰지 않음 |

## 어떻게 동작하나

```
keyword_tagger 라벨·분할 + 로컬 DB 추출본(설명문)
        │
        ▼
scripts/prepare_data.py ── 여행지 글 1,934개, 합성 질의(학습 5,248 / valid 285 / test 279 / test_iid 279), 어려운 음성
        │
        ├─▶ scripts/baseline.py ── 학습 전: 키워드 마스크 검색(서버), BM25, e5-base 그대로 ─▶ results/baseline.md
        │
        ▼
scripts/train.py (MPS) ── 에폭마다 valid nDCG@10, 가장 좋은 에폭을 outputs/<run>/best 에 저장
        │
        ▼
scripts/evaluate.py ── test·test_iid·valid 지표 + 기준선과 한 표 + 좋아진/나빠진 질의 ─▶ results/<run>.compare.md
        │
        ▼
scripts/build_index.py ── 여행지 전체 임베딩 ─▶ outputs/index/<run>/embeddings.npy + ids.json
        │
        ▼
scripts/search.py "조용한 바닷가 마을" ── 상위 10개
```

임베딩 검색의 원리는 간단합니다. 질의와 여행지 글을 같은 모델로 768차원 벡터로 바꾸고(길이 1로 정규화), 내적(= 코사인 유사도)이 큰 순서로 보여 줍니다.
파인튜닝은 "이 질의의 벡터가 정답 여행지 벡터와는 가깝고 다른 여행지와는 멀어지게" 모델 가중치를 조금씩 고치는 일입니다.

## 기본 모델 고르기

기준: 공개 저장소에 올려도 되는 라이선스, 한국어 검색 품질, M4 Pro 24GB에서 1시간 안팎에 학습되는 크기.
라이선스와 크기는 2026-10-18에 Hugging Face 모델 메타데이터로 확인했고, 한국어 수치는 e5 모델 카드의 Mr. TyDi(ko) MRR@10입니다.

| 후보 | 라이선스 | 크기 | 한국어 근거 | 판단 |
| :-- | :-- | --: | :-- | :-- |
| **intfloat/multilingual-e5-base** | MIT | 278M (768차원) | Mr. TyDi ko 55.8 (BM25 28.1) | **선택.** 검색(질의-문서)용으로 학습된 모델이고 512토큰 글까지 받음. 크기가 1시간 학습에 맞음 |
| intfloat/multilingual-e5-small | MIT | 118M (384차원) | Mr. TyDi ko 54.3 | 대안. 더 빠른 실험용 (`MODEL=intfloat/multilingual-e5-small`, revision 고정해 둠) |
| BAAI/bge-m3 | MIT | 568M (XLM-R large) | 다국어 검색 최상위권으로 알려짐 (직접 확인 안 함) | 제외. e5-base의 두 배 넘게 커서 24GB 통합 메모리로 1시간 안에 학습하기 어렵다고 봄 |
| snunlp/KR-SBERT-V40K-klueNLI-augSTS | **표시 없음** | 약 117M | 한국어 NLI/STS로 학습 | 제외. 라이선스 표시가 없어 공개 포트폴리오에 쓰기 어려움. 문장 유사도용이라 긴 글 검색에는 덜 맞음 |
| jhgan/ko-sroberta-multitask | **표시 없음** | 111M | 한국어 NLI/STS | 제외. 같은 이유 |

e5 계열은 학습 때 질의 앞에 `query: `, 글 앞에 `passage: `를 붙였기 때문에 쓸 때도 반드시 붙여야 합니다. 이 접두어는 `emb_common.MODEL_PRESETS`에 있고, 학습한 모델 폴더에는 `glople_embedding_meta.json`으로 같이 저장되어 평가·색인·검색이 자동으로 같은 접두어를 씁니다.
모델은 [`scripts/download_model.py`](scripts/download_model.py)가 고정 revision으로 필요한 파일만(약 1.1GB) Hugging Face 캐시에 받습니다. 지금은 캐시에 있습니다.

## 데이터

### 여행지 글 (검색 대상)

- **국내 1,934개만** 씁니다: keyword_tagger 1차(1,083) + 2차(851) 배치. 이름·지역·분류·저작권 표시·설명문은 로컬 DB 추출본(`keyword_tagger/data/db_descriptions.jsonl`, 1,828개)에서, DB에 없는 1차 106개는 1차 덤프(`descriptions.jsonl`)에서 가져옵니다.
- 2026-10-18 13:16에 keyword_tagger에 3차 배치(해외 WIKIDATA 800개, 분할 `test_overseas`)가 추가됐습니다. 지역·분류 조건, 서버 마스크 규칙, 질의 문장 틀이 모두 국내용이라 이번에는 뺍니다(`prepare_data.py`가 건너뛴 개수를 `results/data_stats.json`에 남김). 3차가 들어온 뒤 다시 만든 국내 데이터는 그 전과 바이트 단위로 같았습니다(sha256 확인).
- 여행지 글 모양: `이름 | 지역 | 분류 경로 | 설명문`. 예: `거제 옥화마을 | 경상남도 거제시 | 문화관광 > 도시.지역문화관광 > 마을관광지 | (설명문)`. 분류 경로는 서버와 같은 코드표(`src/main/resources/tour/category-keywords.csv`)로 만듭니다.
- e5 토크나이저 기준 길이: 평균 278, 중앙값 256, 90% 428토큰. 256토큰에서 자르므로 958개(50%)는 설명문 뒷부분이 잘립니다(`--max-passage-len`으로 바꿀 수 있음).

### 합성 질의 (AI가 만든 데이터)

**질의는 모두 합성입니다.** 실제 사용자 질의를 모은 것이 아니라, 여행지의 정답 키워드 1~3개(+ 가끔 지역·분류)를 골라 [`scripts/query_templates.py`](scripts/query_templates.py)의 표현과 문장 틀로 만든 문장입니다. 표현 표와 문장 틀도 AI(Claude)가 손으로 썼습니다.

1. 키워드 고르기: 개수는 1개 30% · 2개 50% · 3개 20%. '역사'처럼 흔한 키워드만 뽑히지 않도록 train 빈도^-0.5 가중치로 뽑습니다.
2. 말 바꾸기: 키워드마다 자연어 표현이 있습니다(바다 → 바닷가·푸른 바다, 사찰 → 절·산사, 휴양지 → 힐링·휴식 …). 학습 질의의 키워드 59%가 키워드 글자 그대로가 아닌 표현입니다.
3. 조건: 시도 지역 25%(경북·경상북도 …), 시군구 12%(경주·경주시, 구는 '중랑구'처럼 전체 이름), 분류 머리말 15%(절·해수욕장·공원·왕릉 …, 18개 묶음).
4. 꾸밈말 35%: '조용한', '사람 적은'처럼 **정답에 영향을 주지 않는** 말을 섞어 실제 질의처럼 잡음을 넣습니다.
5. 문장 틀: 학습용 19개, 평가 전용 8개. 활동 키워드(등산·캠핑 …)에는 '보러 가고 싶은데' 같은 볼거리 틀을 쓰지 않습니다.

| 질의 세트 | 여행지 (검색 대상) | 질의 수 | 재료 | 용도 |
| :-- | :-- | --: | :-- | :-- |
| 학습 쌍 | 태거 train 1,354 중 키워드 있는 1,312 | 5,248 | 학습용 표현·틀·꾸밈말 | 학습 |
| valid | 태거 valid 290 | 285 | 학습용 재료 | best 에폭 고르기 |
| **test** | 태거 test 163 + test_new 127 = 290 | 279 | **평가 전용 틀·꾸밈말·표현**(표현이 3개 이상인 키워드는 마지막 하나가 평가 전용) | 대표 지표 |
| test_iid | test와 같은 290 | 279 | 학습용 재료 | 참고: 문장 모양을 외웠는지 보기 |

test 예시 (`results/data_stats.json`에 더 있음):

- `가 볼 여행지를 찾는 중인데 굴 탐방, 모래사장, 자연이 꼭 있었으면 해요` ← 동굴·해변·자연 (가사도)
- `다음 여행은 자연! 서울에서 갈 만한 스폿 있나요?` ← 자연 + 지역 서울 (학동근린공원)
- `불상이랑 사원이 끌리는데 스폿 좀 찾아 주세요` ← 불상·사원 (개천사)
- `휴식 느낌 나는 곳에 가고 싶어요` ← 휴양지 (강동리조트 프라이빗콘도)

학습 질의와 글자까지 같은 문장은 test 0개, test_iid 11개, valid 6개입니다. 합성이라 어색한 문장도 있습니다(예: `탑 실컷 보고 올 수 있는 곳 알려 주세요`).

### 정답 (관련도)

질의 q의 키워드 집합을 K, 여행지 d의 정답 키워드를 G(d)라고 할 때 ([`emb_common.graded_relevance`](scripts/emb_common.py)):

- 지역·분류 조건이 있는데 d가 맞지 않으면 관련도 0 (시도 조건은 같은 시도, 시군구 조건은 같은 시군구, 분류 조건은 분류 코드 접두어)
- 아니면 관련도 = |K ∩ G(d)| / |K| (0~1). **1이면 '완전 관련'** 으로 Recall·MRR의 정답이고, 0과 1 사이는 nDCG에서 부분 점수만 받습니다.
- 질의를 만든 여행지는 항상 완전 관련이라 질의마다 정답이 1개 이상입니다. test 질의당 완전 관련 여행지는 평균 12.5개(1개 75 · 2~5개 72 · 6~20개 79 · 21개 이상 53)입니다.
- G(d)는 keyword_tagger의 **AI silver 라벨**입니다. 사람이 검수한 정답이 아니므로 모든 숫자는 "AI 라벨 기준"입니다.

### 분할과 누수 방지

- 태거 분할을 여행지 id 그대로 따릅니다. 학습 쌍의 정답·음성 여행지는 train에서만 나오고, valid·test·test_new 여행지는 학습에 한 번도 나오지 않습니다(`prepare_data.py`와 `train.py`가 assert로 확인).
- 평가 질의는 자기 분할 여행지만 검색합니다(test·test_iid는 test+test_new 290개, valid는 valid 290개). 그래서 학습 때 본 여행지 글은 test 검색 대상에 없습니다.
- 키워드 가중치는 train 여행지 빈도로만 계산합니다.

### 어려운 음성 (hard negative)

학습 쌍마다 음성 여행지 하나를 train에서 고릅니다(`prepare_data.pick_hard_negative`).

| 종류 | 개수 | 내용 |
| :-- | --: | :-- |
| 조건만 다름 | 925 | 지역·분류 조건이 있는 질의에서, 키워드는 다 맞지만 지역·분류가 다른 여행지 (조건이 있을 때 50%) |
| 키워드가 조금 겹침 | 4,096 | 정답 여행지와 키워드가 1개~절반 겹치지만 이 질의에는 완전 관련이 아닌 여행지 |
| 아무거나 | 227 | 위 둘이 없을 때, 이 질의에 완전 관련이 아닌 여행지 |

## 저작권: 설명문을 학습에 쓰는가 (소유자 결정)

1,934개 중 Type1(공공누리 제1유형: 출처표시) 849, **Type3(제3유형: 출처표시 + 변경금지) 854**, 표시 없음 231개입니다.
변경금지는 원문을 고쳐서(요약·의역·발췌 편집) 다시 내보내는 것을 막는 조건입니다. 원문을 학습 입력으로만 쓰고 결과로 내보내지 않는 것이 이 조건에 걸리는지는 별개의 질문이라, 여기서 판단하지 않고 **소유자가 정할 일로 남깁니다**(법률 자문 아님).

- **기본값 `all`**: 학습·색인 입력에는 모든 설명문을 쓰되, **출력에는 절대 내보내지 않습니다.** `search.py`, `results/*`, 색인 사이드카 `ids.json`에는 이름·지역·분류·키워드만 있고, 설명문이 든 `data/`는 gitignore입니다.
- 대안: `DESCRIPTION_POLICY=type1`(제1유형만 설명문, 나머지는 이름·지역·분류), `DESCRIPTION_POLICY=none`(설명문 없이, 서버 summary와 같은 정보). `train.py`·`baseline.py`·`evaluate.py`·`build_index.py`가 같은 `--description-policy`를 받습니다.
- 설명문으로 학습한 가중치를 공개(Hugging Face 등)해도 되는지는 또 다른 결정입니다(`docs/revamp-todo.md`의 재현성 항목과 같이). 지금 가중치는 `outputs/`(gitignore)에만 있습니다.
- 참고 측정: 학습 전 e5-base는 설명문을 넣으면 오히려 나빠졌습니다(test nDCG@10 0.410 vs 이름·지역·분류만 0.466, 아래 표). 학습 뒤에도 그런지는 `DESCRIPTION_POLICY=none`으로 한 번 더 학습해 비교해 볼 만합니다.

## 지표

질의마다 계산해 평균합니다 ([`emb_common.py`](scripts/emb_common.py)).

- **nDCG@10**: 상위 10개의 관련도(0~1, 부분 점수 포함)를 순위로 깎아 더한 값 ÷ 가장 좋은 순서일 때의 값. 1이면 이상적인 순서.
- **Recall@20**: 완전 관련 여행지 중 상위 20개 안에 든 비율. 분모는 min(완전 관련 수, 20)이라, 정답이 20개보다 많아도 상위 20개가 모두 정답이면 1.0입니다.
- **MRR**: 처음 나온 완전 관련 여행지 순위의 역수(1위 1.0, 2위 0.5 …). 키워드 검색처럼 21개만 돌려주는 방법은 그 밖이면 0.

## CPU에서 잰 기준선 (학습 전, 2026-10-18)

[`scripts/baseline.py`](scripts/baseline.py), 전체 표는 [`results/baseline.md`](results/baseline.md). e5-base는 CPU 6스레드로 질의 세트 셋을 18.5초에 평가했습니다.

| 방법 (test 279개 질의) | nDCG@10 | Recall@20 | MRR |
| :-- | --: | --: | --: |
| 키워드 검색: 질의 키워드를 칩으로 고름 (서버 마스크) | **0.484** | **0.555** | **0.493** |
| 키워드 검색: 문장에서 키워드 글자 추출 (서버 마스크) | 0.390 | 0.417 | 0.365 |
| BM25 글자 2-gram (설명문 포함) | 0.374 | 0.467 | 0.392 |
| BM25 글자 2-gram (이름·지역·분류만) | 0.420 | 0.439 | 0.415 |
| e5-base 학습 전 (설명문 포함) | 0.410 | 0.469 | 0.431 |
| e5-base 학습 전 (이름·지역·분류만) | 0.466 | 0.490 | 0.469 |
| (참고) 키워드 칩 + 정답 라벨 마스크 | 0.780 | 0.944 | 0.796 |

질의 종류별 nDCG@10 (괄호는 질의 수):

| 방법 | 키워드만 (168) | + 지역 (91) | + 분류 (10) | + 지역 + 분류 (10) |
| :-- | --: | --: | --: | --: |
| 키워드 칩 (서버 마스크) | **0.653** | 0.225 | 0.187 | 0.296 |
| BM25 (설명문 포함) | 0.379 | 0.341 | **0.518** | 0.454 |
| e5-base 학습 전 (이름·지역·분류만) | 0.431 | **0.495** | 0.376 | **0.883** |

- **키워드 검색의 구성**: 서버 `findTop21ByKeywordMask`를 그대로 옮겼습니다([`retrieval.keyword_mask_rank`](scripts/retrieval.py)). 여행지 마스크는 서버가 TOUR_API 행에 저장하는 값(국내 규칙(설명문) ∪ 분류 코드표 키워드, `DestinationInfo.computeKeywordMask`), 겹치는 키워드 수 내림차순, 같으면 DB id 오름차순(추출본 줄 순서 = `ORDER BY id`), 1개 이상 겹치는 행만, 최대 21개. 국내 규칙은 keyword_tagger의 `kt_common.korean_rule_predict`(Java와 비트 단위로 같다고 확인된 포트)를 그대로 가져다 씁니다.
- **'칩'은 키워드 쪽에 유리한 설정**입니다. 질의를 만든 키워드를 사용자가 94개 키워드 화면에서 정확히 골랐다고 칩니다. 키워드만 있는 질의에서는 0.653으로 가장 높지만, 서버 검색에는 지역 조건이 없어서 지역이 붙으면 0.225로 떨어집니다.
- 문장을 그대로 넣으면(글자 추출) '바닷가'·'절'처럼 키워드 글자가 아닌 표현을 못 알아들어 0.390입니다. 임베딩이 메우려는 차이가 이것입니다.
- **학습 전 e5-base는 아직 키워드 칩보다 낮습니다**(0.466 < 0.484). 다만 지역이 붙은 질의는 이미 키워드 검색의 두 배입니다.
- 설명문을 넣으면 BM25도 e5도 나빠집니다. 긴 설명문의 주변 정보(교통·주변 명소)가 질의와 상관없는 낱말을 많이 넣기 때문으로 보입니다.
- '(참고)' 줄은 정답 라벨을 마스크로 쓴 것이라 관련도 정의와 거의 같습니다(키워드만 있는 질의 1.000). 천장으로만 봅니다. 1.0이 안 되는 것은 지역·분류 조건을 못 쓰기 때문입니다.
- 분류 조건이 있는 질의는 10개씩뿐이라 그 열은 크게 흔들립니다.

## 학습

[`scripts/train.py`](scripts/train.py). 한 step은 이렇습니다.

1. 배치 B(=32)개 쌍: 질의 q_i, 정답 글 p_i, 어려운 음성 글 n_i
2. 같은 모델로 모두 임베딩 (질의 `query: `, 글 `passage: `)
3. q_i마다 후보 2B개(p_1..p_B, n_1..n_B)와 코사인 유사도 × scale(20 = 1/온도) → "정답이 p_i인 64지선다" 교차 엔트로피. 다른 질의의 정답이 공짜 음성(in-batch negative)이 됩니다. sentence-transformers의 `MultipleNegativesRankingLoss`가 이 계산을 합니다.
4. 역전파 → 기울기 자르기(1.0) → AdamW(lr 2e-5, weight decay 0.01) → 10% 선형 warmup 뒤 선형 감소
5. 에폭마다 valid nDCG@10을 재고, 가장 좋은 에폭의 모델을 `outputs/<run>/best`에 저장

**거짓 음성 가리기.** 같은 배치에 '바다' 질의가 둘이면, 한 질의의 정답이 다른 질의에는 "틀린 답"으로 들어가 맞는 것을 틀렸다고 가르칩니다. 그래서 배치마다 [질의 × 후보] 마스크를 만들어 **질의에 완전 관련인 남의 후보 칸은 유사도를 -inf로 바꿔** softmax에서 뺍니다(`MaskedCosSim`을 MNRL의 `similarity_fct`로 넘김). 마스크가 비어 있으면 원래 MNRL과 값이 같고, 마스크가 있으면 직접 짠 교차 엔트로피와 같으며 기울기도 유한한 것을 확인했습니다. 배치 32에서 후보 칸의 약 3~4%가 가려집니다.
처음에는 부딪히는 쌍을 다음 배치로 미루는 방식을 썼는데, 흔한 키워드 질의가 많아 배치 485개 중 346개가 절반도 안 차서 버렸습니다. 지금은 같은 여행지가 한 배치에 두 번 나오지 않게만 합니다.

**MPS 메모리 안전장치** (keyword_tagger 첫 MPS 실행이 캐시 23GB + 스왑으로 거의 멈췄던 일의 교훈):

- 패딩 길이 고정: 질의는 항상 64토큰, 글은 64의 배수(64/128/192/256)로만 맞춥니다. 모양이 매번 다르면 MPS가 모양마다 버퍼를 새로 잡습니다. `--padding max`면 글도 한 가지 길이(256)입니다.
- 평가가 끝날 때마다, 그리고 50 step마다 `torch.mps.empty_cache()`.
- MPS에서는 gradient checkpointing을 기본으로 켭니다(활성값 메모리를 줄이는 대신 약 30% 느림, `--grad-checkpointing off`로 끔).
- `run_embedding.sh`는 `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.7`로 MPS 메모리 상한을 둡니다. 넘으면 스왑으로 몇 시간 느려지는 대신 바로 오류가 나고, `train.py`가 종료 코드 3으로 끝나면 배치를 절반(16)으로 줄여 한 번 더 학습합니다.
- `--freeze-embeddings`: e5-base 278M 중 약 192M이 25만 토큰 단어 임베딩 행렬입니다. 얼리면 그만큼의 기울기·Adam 상태 메모리가 빠집니다(실험용 옵션, 기본은 끔).

**로그.** 예전 실행에서 버퍼링 때문에 로그를 잃은 일이 있어서, stdout/stderr를 줄 단위 버퍼링으로 바꾸고 모든 줄을 `print(..., flush=True)`로 쓰며 `outputs/<run>/train.log`에도 같은 줄을 바로 씁니다(`run_embedding.sh`는 `PYTHONUNBUFFERED=1`, `python -u`도 씀). `--log-every`(기본 10) step마다 이런 줄이 남습니다.

```
step 2/4 (에폭 1) loss 3.5188 lr 1.00e-05 10.40s/step 경과 0:00:20 ETA 0:00:26 | 가린 칸 4.2% | 글 길이 256 | cpu
```

ETA는 지금까지의 step 평균 시간 × 남은 step + 남은 평가 횟수 × 마지막 평가 시간입니다. MPS에서는 끝에 `mps 할당 …GB / 드라이버 …GB`가 붙습니다.

주요 옵션: `--device cpu|mps`(기본 **cpu**, GPU는 직접 골라야 함), `--epochs 3`, `--batch-size 32`, `--lr 2e-5`, `--scale 20`, `--max-query-len 64`, `--max-passage-len 256`, `--padding bucket|max`, `--no-hard-negatives`, `--no-false-negative-mask`, `--freeze-embeddings`, `--description-policy all|type1|none`, `--max-steps`·`--limit-train`·`--limit-eval`(스모크 테스트용), `--run-name`.

## 실행

```bash
cd training/embedding

# 설치 (한 번). 이 폴더 전용 가상환경 (training/.venv 의 MLX, keyword_tagger/.venv 와 별개)
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install --uploaded-prior-to 2026-10-03T00:00:00Z -r requirements.lock
.venv/bin/python -I scripts/download_model.py          # e5-base 고정 revision (이미 캐시에 있음)

# 1) 데이터 (표준 라이브러리만, 3초). keyword_tagger/data/db_descriptions.jsonl 이 있어야 함
python3 -I scripts/prepare_data.py

# 2) 학습 전 기준선 (CPU, 약 1분)
HF_HUB_OFFLINE=1 nice -n 10 .venv/bin/python -I scripts/baseline.py --device cpu

# 3) 스모크 테스트 (CPU, 작은 모델, 5 step, 약 15초) — 코드를 고친 뒤 GPU 학습 전에
HF_HUB_OFFLINE=1 .venv/bin/python -I scripts/train.py --device cpu --model klue/roberta-small \
    --max-steps 5 --batch-size 8 --limit-eval 40 --run-name smoke
rm -rf outputs/smoke results/smoke.train.json

# 4) GPU 한 번에: 학습(MPS) -> 평가 -> 색인 -> 검색 예시. GPU 를 쓰는 다른 학습이 돌고 있으면 멈춤(FORCE=1 로 무시)
caffeinate -i bash run_embedding.sh
#    설정 바꾸기: RUN_NAME=e5-base-ft-none DESCRIPTION_POLICY=none caffeinate -i bash run_embedding.sh
#    진행 보기:   tail -f outputs/e5-base-ft/train.log

# 5) 검색해 보기 (CPU)
HF_HUB_OFFLINE=1 .venv/bin/python -I scripts/search.py "조용한 바닷가 마을" --index outputs/index/e5-base-ft
```

- 학습 시간: CPU(8스레드, 배치 32, gradient checkpointing)에서 e5-base는 **10.3초/step**으로 쟀습니다. 3에폭 494 step(에폭마다 배치 165·165·164개)이면 CPU로 약 85분입니다. MPS 속도는 GPU가 다른 학습에 쓰이고 있어 재지 못했습니다. 첫 로그 줄들의 `s/step`과 ETA를 보세요. 1시간을 크게 넘으면 `EPOCHS=2`나 `MODEL=intfloat/multilingual-e5-small`을 씁니다.
- `run_embedding.sh`가 끝나면: `results/<run>.compare.md`(기준선과 한 표, 질의 종류·출처별 표, 학습 전보다 가장 좋아진·나빠진 질의 6개씩), `results/<run>.json`, `results/<run>.train.json`(에폭별 valid 지표), `outputs/<run>/train.log`, `outputs/<run>/pipeline.log`, `outputs/<run>/search_demo.txt`, `outputs/index/<run>/`.
- 실행 중에는 `run_embedding.sh`를 고치지 마세요(bash는 실행 중에도 파일을 이어서 읽습니다).
- 학습 전 모델 색인은 이미 만들어 두었습니다: `search.py "…" --index outputs/index/multilingual-e5-base`.

## 스모크 테스트 기록 (CPU, 2026-10-18)

숫자는 코드 경로가 도는지 보려는 것이라 성능으로 읽지 않습니다. 결과 파일은 지웠습니다.

- `klue/roberta-small`(68M, keyword_tagger 때 받아 둔 캐시. 문장 임베딩용 모델은 아님), 5 step, 배치 8: 약 1.1초/step. 데이터 → 배치·마스크 → 학습 → valid 평가(40개, 0.226 → 0.364) → best 저장 → `evaluate.py` → `build_index.py` → `search.py`까지 정상. 같은 명령을 두 번 돌려 지표가 같았습니다(결정적). `--freeze-embeddings --no-hard-negatives --padding max`, `--no-false-negative-mask` 조합도 돌았습니다.
- `run_embedding.sh`를 `DEVICE=cpu MODEL=klue/roberta-small EPOCHS=1 BATCH_SIZE=8 EXTRA_TRAIN_ARGS="--max-steps 3 --limit-eval 20"`로 끝까지 돌려 모든 단계가 이어지는 것을 확인했습니다.
- `intfloat/multilingual-e5-base`, 4 step, 배치 32, gradient checkpointing 켬: 10.3초/step. 접두어(`query: `/`passage: `)가 학습 → 저장 → 평가로 이어지는 것을 확인했습니다. 참고로 학습 쌍 128개만 본 이 체크포인트도 test nDCG@10이 0.410 → 0.522였습니다(279개 중 좋아짐 200 · 나빠짐 45 · 같음 34). 본 학습 결과가 아닙니다.

## 서비스에 연결

화면 '문장으로 찾기'(`/search/semantic`, 상단 메뉴 **여행지 추천 → 문장으로 찾기**)가 파인튜닝한 모델로 여행지를 찾습니다.

```
브라우저 /search/semantic (frontend/src/pages/semanticSearch/)
   │  GET /semantic-api/v1/search?q=…&limit=12&scope=all          (같은 출처, 로그인 토큰 안 실음)
   ▼
React 개발 서버 프록시 (frontend/src/setupProxy.js, SEMANTIC_SEARCH_URL 기본 http://127.0.0.1:8096)
   ▼
scripts/serve_search.py (127.0.0.1:8096, CPU, 읽기 전용)
   ├─ 'query: ' + 문장 → e5-base-ft 임베딩 (같은 문장은 LRU 캐시)
   └─ outputs/index/e5-base-ft-db/embeddings.npy @ 질의 벡터 → 범위로 거른 상위 limit 곳 (이름·지역·분류·점수만)
   ▼
카드를 누르면: Spring GET /api/destinations/lookup?name= 으로 같은 여행지(이름·출처·지역)를 찾아
기존 상세 화면 /recomendation/information/:placename 에 state.place 로 넘김 → 설명문·출처 표시는 상세 화면이 함
```

### 서비스 색인: DB 추출본 전체

학습·평가 색인(`outputs/index/e5-base-ft`, 라벨된 국내 1,934곳)과 따로, 서비스용 색인을 만듭니다. 학습·평가 분할과 `data/`는 그대로입니다.

```bash
HF_HUB_OFFLINE=1 nice -n 10 .venv/bin/python -u -I scripts/build_index.py --model-dir outputs/e5-base-ft/best --corpus db --device cpu
# -> outputs/index/e5-base-ft-db/ (진행률을 512곳마다 한 줄씩 찍음)
```

| 항목 | 값 (2026-10-18) |
| :-- | :-- |
| 원본 | `keyword_tagger/data/db_descriptions.jsonl` (로컬 DB의 `info IS NOT NULL` 행, 2026-10-17 추출) |
| 개수 | **9,407곳** = 국내 TOUR_API 1,828 + 해외 WIKIDATA 7,579 (모두 설명문 있음) |
| 그중 임베딩 학습·평가에 쓴 곳 | 국내 1,828곳 전부 (train 1,278 · valid 277 · test 146 · test_new 127). 해외는 0곳 |
| 여행지 글 | 학습과 같은 `이름 \| 지역 \| 분류 \| 설명문`. 국내 분류는 `tour/category-keywords.csv` 경로, 해외 분류는 서버 유형 코드표(`wiki/type-keywords.csv`)의 대표 유형 한국어 이름(없으면 '여행지', 590곳) |
| 크기·시간 | 9,407 × 768 float32, 27.6MB. CPU 4스레드로 약 4분 37초 |
| 사이드카 `ids.json` | 행마다 source·id(외부 id: TourAPI 콘텐츠 id / 위키데이터 QID)·이름·지역·나라·국내/해외·분류·분할. 설명문 원문 없음. 추출본 sha256 기록 |

- 국내·해외 기준은 서버 `/api/search`의 `scope`와 같습니다(TOUR_API이거나 나라가 대한민국이면 국내).
- **빠진 곳**: DB의 국내 약 19,900곳 중 설명문이 없는 약 18,100곳(분류 소개 `summary`만 있음)과 DB에 없는 1차 덤프 106곳은 추출본에 없어 색인에도 없습니다. 넣으려면 `summary`까지 꺼내는 추출(DB 접근 필요)을 새로 해야 합니다.
- 추출본을 새로 만들면 위 명령을 다시 돌리고 서버를 다시 띄웁니다.

### 검색 서버 실행

```bash
cd training/embedding
bash serve_search.sh start     # 뒤에서 실행(nohup). 로그 outputs/logs/serve_search.log, PID outputs/serve_search.pid
bash serve_search.sh status    # 떠 있는지 + 색인 개수
bash serve_search.sh stop
bash serve_search.sh           # 앞에서 실행 (Ctrl+C)
# 설정: INDEX=outputs/index/e5-base-ft-db PORT=8096 NUM_THREADS=2 bash serve_search.sh start
```

- 127.0.0.1에만 붙고, CORS는 켜지 않습니다. GET 말고는 405입니다. `HF_HUB_OFFLINE=1`로 모델을 로컬 폴더에서만 읽고 장치는 CPU만 씁니다.
- 띄우는 데 약 5초(모델 읽기), 질의 하나 15ms 안팎(CPU 2스레드, 그중 내적·정렬은 0.5ms), 같은 문장이면 캐시로 5ms 안팎입니다. 읽어 두는 것은 모델 가중치(파일 1.1GB)와 색인(28MB)입니다.
- 프런트 개발 서버는 `setupProxy.js`를 **띄울 때 한 번만** 읽습니다. 프록시를 더한 뒤 처음 쓸 때는 개발 서버를 다시 띄워야 `/semantic-api`가 넘어갑니다. 다른 주소로 보내려면 `SEMANTIC_SEARCH_URL=http://… npm start`.
- 검색 서버가 꺼져 있으면 프록시가 JSON 503을 돌려주고 화면은 "문장 검색 서버가 꺼져 있어요. 잠시 뒤 다시 시도해 주세요."를 보여 줍니다(프록시가 아직 없을 때의 404도 같은 안내).

### API

`GET /semantic-api/v1/search`

| 파라미터 | 값 | 기본값 |
| :-- | :-- | :-- |
| `q` | 찾을 문장. 앞뒤 공백을 떼고 연속 공백·줄바꿈을 하나로 줄인 뒤(NFC) 1~200자(코드 포인트). 제어 문자는 400 | 필수 |
| `limit` | 1~30 정수(숫자만, `3.0`·`+3`은 400) | 12 |
| `scope` | `all` · `domestic`(국내) · `overseas`(해외). 대소문자·앞뒤 공백 무시 | `all` |

- 같은 칸을 두 번 보내면 400, 모르는 칸은 무시합니다. 틀린 칸은 Spring과 같은 모양의 400 JSON에 모두 담깁니다.
- 응답은 화면에 필요한 칸만입니다. `id`는 DB 행 id가 아니라 외부 id(TourAPI 콘텐츠 id / 위키데이터 QID)이고, 설명문은 내보내지 않습니다. `score`는 코사인 유사도(소수 넷째 자리)로, 순서를 정할 뿐 '맞을 확률'이 아닙니다.
- 질의 글자는 서버 로그에 남지 않습니다. 접근 로그는 `GET /semantic-api/v1/search 200 15ms scope=all limit=12 결과 12 캐시 새로 계산`처럼 경로(쿼리 문자열 뺌)·상태·시간·범위·캐시만 씁니다. 개발 서버 프록시도 오류 로그를 찍기 전에 주소에서 쿼리 문자열을 지웁니다.
- `GET /semantic-api/v1/health`: `{"status":"ok","model":"e5-base-ft","corpus":"db","count":9407,"domestic":1828,"overseas":7579,"cached_queries":…}`

```bash
curl -s -G http://127.0.0.1:8096/semantic-api/v1/search --data-urlencode "q=조용한 바닷가 마을" -d limit=3
```

```json
{"scope": "all", "limit": 3, "count": 3, "items": [
  {"source": "TOUR_API", "id": "3559964", "name": "곡룡포마을", "location": "경상남도 통영시", "category": "마을관광지", "score": 0.538},
  {"source": "WIKIDATA", "id": "Q2895643", "name": "베나길", "location": "포르투갈", "category": "해변", "score": 0.5308},
  {"source": "WIKIDATA", "id": "Q275639", "name": "친퀘 테레", "location": "이탈리아 리구리아주", "category": "연안", "score": 0.5193}]}
```

```json
{"status": 400, "error": "BAD_REQUEST", "message": "검색 값이 올바르지 않습니다.", "path": "/semantic-api/v1/search",
 "errors": [{"field": "limit", "code": "out_of_range", "message": "limit는 1~30 사이 정수여야 합니다."}]}
```

### 직접 해 본 질의 (2026-10-18, 상위 3곳)

| 질의 (범위) | 1 | 2 | 3 |
| :-- | :-- | :-- | :-- |
| 조용한 바닷가 마을 (국내) | 곡룡포마을 (통영) | 고흥 신기거북이마을 | 강진 서중어촌체험마을 |
| 아이랑 가기 좋은 동물 체험 (국내) | 경주 버드파크 | 곤충생태관 (구리) | 미어캣파크 (종로) |
| 경주에서 역사 유적 둘러볼 곳 (국내) | 경주 신문왕릉 | 경주 효공왕릉 | 경주 동부 사적지대 |
| 단풍 보러 산에 가고 싶어 (전체) | 파리나코타산 (볼리비아) | 알프후벨 (스위스) | 고용산(고룡산) (아산) |
| 유럽의 오래된 성당 구경 (해외) | 산타마리아 마조레 대성전 (로마) | 아헨 대성당 | 밤베르크 대성당 |
| 일본 온천 마을 (해외) | 관쯔린 온천 (**대만**) | 유럽의 대 온천 마을 | 즈번 온천 (**대만**) |
| 알프스 호수 근처 산책 (해외) | 프레스파호 (**알바니아**) | 소프레스파호 | 오흐리드호 |

### 한계 (서비스)

- **학습은 국내 라벨 데이터 기준입니다.** 해외 7,579곳은 학습에 한 번도 나오지 않았고 평가도 하지 않았습니다. 해외 결과는 기본 모델(multilingual-e5)의 일반화에 기댑니다. 위 표처럼 '일본'·'알프스' 같은 **나라·지역 조건을 잘 못 지킵니다**(국내 지역 조건만 학습함). 해외 지역 조건 질의와 정답을 만들어 학습·평가하거나, 지역 낱말은 Spring `region` 필터로 따로 거르는 방식이 필요합니다.
- '조용한'·'아이랑' 같은 꾸밈말은 학습 정답에 없습니다([한계](#한계와-정해야-할-것) 2). '단풍'처럼 계절 말도 라벨이 없어서, 위 표에서는 '산'만 맞추고 해외 산이 먼저 나왔습니다.
- 설명문이 없는 국내 여행지(약 18,100곳)는 찾지 못합니다. 결과가 이미 설명문이 있는 곳에 몰립니다.
- 학습에 쓴 국내 train 1,278곳이 서비스 색인에 들어 있습니다(서비스이므로 당연하지만, 이 색인으로 잰 숫자는 평가 지표가 아닙니다).
- 상세 화면 연결은 이름으로 찾습니다(`/api/destinations/lookup`). 이름·출처·지역이 같은 곳을 고르고, 못 찾으면 이름만으로 상세 화면을 엽니다(그때는 위키백과 소개).
- 프런트 개발 서버 프록시만 연결했습니다. 배포(docker/nginx.conf)에는 `/semantic-api`가 없습니다.
- 서버(Java)에 붙이는 방식(지금처럼 파이썬 서버 / Spring이 대신 부르기 / FAISS·DB 벡터)은 소유자가 정할 일로 남깁니다([한계](#한계와-정해야-할-것) 8).

## 파일

| 경로 | 내용 | 커밋 |
| :-- | :-- | :-- |
| `scripts/emb_common.py` | 경로, 입출력, 지역 정규화, 여행지 글, 관련도·지표, 모델 설정(revision·접두어), 로그 | O |
| `scripts/query_templates.py` | 키워드 표현, 문장 틀, 꾸밈말, 지역·분류 이름, 받침 조사 (AI가 씀) | O |
| `scripts/prepare_data.py` | 여행지 표, 합성 질의, 어려운 음성, 누수 검사, 통계 | O |
| `scripts/retrieval.py` | 키워드 마스크 검색(서버 포트), BM25(글자 2-gram), 임베딩 검색 | O |
| `scripts/report.py` | 결과 표(Markdown) | O |
| `scripts/baseline.py` | 학습 전 기준선 (`--render-only`로 표만 다시 씀) | O |
| `scripts/train.py` | MNRL 파인튜닝 (MPS 안전장치, 실시간 로그) | O |
| `scripts/evaluate.py` | 학습한 모델 평가와 비교표 | O |
| `scripts/build_index.py`, `scripts/search.py` | 색인(.npy + ids.json, `--corpus labeled\|db`), 검색 CLI | O |
| `scripts/serve_search.py`, `serve_search.sh` | 검색 서버(127.0.0.1:8096, 읽기 전용)와 실행 스크립트(start·stop·status) | O |
| `tests/test_serve_search.py`, `tests/test_build_index.py` | 검색 서버 입력 검사·범위 필터·응답 모양·캐시·로그, 서비스 색인 대상 만들기 시험 (`.venv/bin/python -B -I -m unittest discover -s tests`, 29개) | O |
| `scripts/download_model.py` | 기본 모델을 고정 revision으로 받기 | O |
| `run_embedding.sh` | GPU 한 번에 실행 | O |
| `requirements.txt`, `requirements.lock` | 직접 설치하는 4개 / pip freeze 전체 (`--uploaded-prior-to 2026-10-03`) | O |
| `results/data_stats.json` | 데이터 개수·분포·합성 질의 예시·파일 sha256 (설명문 없음) | O |
| `results/baseline.json`, `results/baseline.md` | 학습 전 기준선 | O |
| `data/` | 여행지 글(설명문 원문 포함), 학습 쌍, 평가 질의 | X |
| `outputs/` | 학습한 모델, 로그(`logs/`), 색인(`index/`), 검색 서버 PID | X |
| `.venv/` | 이 폴더 전용 가상환경 | X |

## 한계와 정해야 할 것

1. **정답이 AI silver 라벨이고 질의가 합성입니다.** 숫자는 "AI 라벨과 합성 질의 기준으로 얼마나 맞는가"입니다. 실제 사용자 말투로 쓴 질의 세트(사람이 쓰거나 검수한 50~100개)를 따로 만들어야 진짜 개선인지 알 수 있습니다. 질의와 정답이 같은 키워드에서 나오므로, 모델은 결국 '키워드 라벨을 문장에서 맞히기'를 배웁니다.
2. **꾸밈말은 정답에서 무시합니다.** '조용한'·'붐비지 않는' 같은 말은 라벨이 없어서 평가하지 못합니다. 데모 질의 "조용한 바닷가 마을"의 '조용한'은 모델이 알아서 다룰 뿐 측정하지 않았습니다.
3. **설명문 사용(Type3 854개)은 소유자 결정입니다** ([저작권](#저작권-설명문을-학습에-쓰는가-소유자-결정)). 기본값은 '학습에는 쓰고 출력은 안 함'입니다. 학습한 가중치 공개 여부도 같이 정해야 합니다.
4. **국내만, 관광지 위주입니다.** 1,934개 중 1,900개가 관광지(12), 31개가 문화시설(14)이고(나머지 3개는 1차 덤프의 다른 유형), 2차는 제목이 '가'~'공'인 곳뿐입니다. 해외 800개(3차)는 이번 데이터에서 뺐습니다. 넣으려면 해외용 지역(국가)·분류(Wikidata 유형) 조건과 서버 해외 마스크 규칙(`WikiTypeKeywords`, 유네스코)을 따로 옮겨야 합니다.
5. **test 지역 쏠림.** test 163개는 서울·인천·광주(1차)라 '서울' 지역 질의가 많습니다. 분류 조건 질의는 test에 20개뿐입니다.
6. **DB에 없는 1차 106개**는 분류 코드가 없어 분류 조건 질의의 정답이 될 수 없고, 키워드 검색 동점 순서도 임의(DB 행 뒤, id 순)입니다.
7. **MPS 속도·메모리 미측정.** CPU 값으로만 추정했습니다. 첫 실행의 로그로 배치 크기·`--max-passage-len`(지금 256, 글의 절반이 잘림)을 다시 정해야 합니다.
8. **저장 방식.** 지금은 numpy `.npy` + `ids.json` 사이드카입니다(로컬 MySQL 8.4에는 VECTOR 타입 없음). 서비스 색인 9,407곳도 전수 내적·정렬이 0.5ms 안팎이라 충분합니다. 지금은 파이썬 검색 서버를 프런트 프록시로 바로 붙였고([서비스에 연결](#서비스에-연결)), 서버(Java) 연동 방식(Spring이 대신 부르기 / FAISS / DB 업그레이드)은 소유자가 정합니다.
9. **키워드 검색과 섞기.** 학습 전 e5도 지역 질의는 이미 키워드 검색보다 낫고, 키워드만 있는 질의는 키워드 칩이 낫습니다. 학습 뒤에도 그러면 두 점수를 섞는 방식(예: RRF)을 검토할 만합니다.
