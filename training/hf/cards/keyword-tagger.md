---
license: cc-by-sa-4.0
base_model: klue/roberta-base
language:
- ko
library_name: transformers
pipeline_tag: text-classification
tags:
- multi-label-classification
- korean
- travel
- keyword-tagging
---

# $repo

여행지 이름과 소개 글을 읽고 Glople 여행 취향 키워드(전체 94개 중 학습한 $label_count개)를 여러 개 붙이는 다중 라벨 분류 모델입니다.
Glople의 키워드 추천(여행지마다 키워드 비트마스크)에 규칙 기반 마스크 대신, 또는 키워드별로 섞어서 씁니다. 개인 포트폴리오·학습 프로젝트입니다.

- 기반 모델: [`klue/roberta-base`](https://huggingface.co/klue/roberta-base)
- 입력: `tokenizer(이름, 소개 글, truncation=True, max_length=512)`의 input_ids·attention_mask(token_type_ids 없음)
- 출력: 키워드마다 sigmoid 확률. `tagger_config.json`의 `label_space`(순서)와 `thresholds`(키워드별 임계값, 지금은 모두 같은 값)로 고릅니다.

## 쓰는 법

```python
import json, torch
from huggingface_hub import snapshot_download
from transformers import AutoModelForSequenceClassification, AutoTokenizer

path = snapshot_download("<namespace>/$repo")
tokenizer = AutoTokenizer.from_pretrained(path)
model = AutoModelForSequenceClassification.from_pretrained(path).eval()
cfg = json.load(open(f"{path}/tagger_config.json", encoding="utf-8"))
enc = tokenizer("불국사", "경주 토함산에 있는 신라 시대 사찰로 …", truncation=True, max_length=cfg["max_length"],
                return_token_type_ids=False, return_tensors="pt")
with torch.no_grad():
    probs = torch.sigmoid(model(**enc).logits)[0]
print([k for k, p in zip(cfg["label_space"], probs) if p >= cfg["thresholds"][k]])
```

## 학습 데이터와 평가

- 국내 여행지: 한국관광공사 TourAPI 소개 글. 해외 여행지: 한국어 위키백과 도입부(CC BY-SA 4.0) + Wikidata 항목·유형(CC0).
- 라벨: **AI 라벨러(Claude)가 라벨링 가이드에 따라 붙인 silver 라벨**입니다. 사람이 검수한 정답이 아니므로, 아래 숫자는 모두
  "AI 라벨과 얼마나 같은가"를 잰 값입니다. 분할 크기: `$split_sizes`
- 임계값은 valid에서 고르고, 아래 시험 세트는 학습·임계값 선택에 쓰지 않았습니다.

$tagger_table

서비스에서는 valid F1로 키워드마다 규칙/태거/합집합 중 하나를 골라 섞어 씁니다(태거 29개, 규칙 61개, 합집합 4개).
태거가 거의 예측하지 않는 드문 키워드는 규칙을 써야 빈 결과가 나지 않기 때문입니다.

## 라이선스와 공개 범위 (읽어 주세요)

- 기반 모델 `klue/roberta-base`는 Hugging Face 메타데이터에는 라이선스 표시가 없지만(2026-10-18 확인), 이 저장소의 이전 점검 기록은 CC BY-SA 4.0으로 적었습니다(직접 확인한 것은 아님).
  그래서 이 파인튜닝 가중치도 보수적으로 **CC BY-SA 4.0**(저작자 표시·동일조건 변경허락)으로 둡니다. 공개 전에 KLUE 공식 안내를 한 번 더 확인하세요.
- 학습 글에는 **공공누리 제3유형(출처표시·변경금지)** 인 TourAPI 소개 글과 CC BY-SA 4.0인 위키백과 글이 들어 있습니다.
  가중치에 원문이 그대로 들어 있지는 않지만, 이런 글로 학습한 가중치를 공개해도 되는지는 저장소 주인이 판단합니다. 그래서 기본은 비공개입니다.
- 이 저장소에는 학습 데이터·예측 파일을 올리지 않습니다.

## 한계

- macro F1이 낮습니다: 학습 예가 적은 키워드(유네스코, 박물관 등)는 거의 맞히지 못합니다.
- '경치', '자연', '문화'처럼 넓은 키워드에서 오탐이 많습니다.
- 해외는 위키백과 도입부 기준이라, 짧은 글은 키워드가 적게 나옵니다.
