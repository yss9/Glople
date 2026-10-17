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
| v2 | `data/tool_calling_v2` | [`configs/qwen3-4b-lora-v2.yaml`](configs/qwen3-4b-lora-v2.yaml) | `adapters/qwen3-4b-tool-calling-v2` | 데이터만 만듦, 아직 학습 안 함 |

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

## 파일

- [`scripts/prepare_model.py`](scripts/prepare_model.py): 4bit 가중치는 그대로 두고 채팅 템플릿만 공식 것으로 바꾼 로컬 모델 폴더 생성. 변환본 템플릿은 응답 앞에 빈 `<think></think>`를 넣어서, 그대로 학습하면 매번 빈 think 블록을 출력하도록 배웁니다.
- [`scripts/build_tool_dataset.py`](scripts/build_tool_dataset.py): 발화 → 도구 호출 / 되묻기 / 바로 답변 데이터 합성 (v2, 기본 출력 `data/tool_calling_v2`). train/test 분리와 정답 충돌을 검사한 뒤 만들고, 끝에 작업 비율표를 출력
- [`scripts/build_tool_dataset_v1.py`](scripts/build_tool_dataset_v1.py): v1 생성기 보관본 (밤샘 학습 데이터 `data/tool_calling`을 그대로 다시 만듦)
- [`configs/qwen3-4b-lora.yaml`](configs/qwen3-4b-lora.yaml): 학습 설정 (v1 데이터, `run_overnight.sh`가 사용)
- [`configs/qwen3-4b-lora-v2.yaml`](configs/qwen3-4b-lora-v2.yaml): v2 데이터 학습 설정 (`data`, `adapter_path`만 다름)
- [`scripts/evaluate.py`](scripts/evaluate.py): 호출 여부 판단, 도구 이름, 도구+인자 완전 일치, JSON 오류율 측정
- [`scripts/report.py`](scripts/report.py): 기본 모델과 파인튜닝 모델 비교표
- [`eval_sets/`](eval_sets/README.md): 사람 말투 평가 세트 90개(`human_style.jsonl`)와 라벨링 규칙(`labeling_rules.md`)
