---
license: other
base_model: intfloat/multilingual-e5-base
language:
- ko
library_name: sentence-transformers
pipeline_tag: sentence-similarity
tags:
- sentence-transformers
- feature-extraction
- retrieval
- korean
- travel
---

# $repo

"조용한 바닷가 마을"처럼 문장으로 쓴 여행 취향과 여행지(이름·지역·분류·소개 글)를 같은 공간에 놓는 검색용 임베딩 모델입니다.
`intfloat/multilingual-e5-base`를 Glople 여행지 데이터로 파인튜닝했습니다. 개인 포트폴리오·학습 프로젝트입니다.

- 기반 모델: [`intfloat/multilingual-e5-base`](https://huggingface.co/intfloat/multilingual-e5-base) (MIT, revision d128750597153bb5987e10b1c3493a34e5a4502a)
- 접두사: 질의는 `query: `, 여행지 글은 `passage: ` (e5 규칙 그대로). 질의 최대 64 토큰, 여행지 글 최대 256 토큰.
- 학습: sentence-transformers MultipleNegativesRankingLoss + 어려운 음성 예, 3에폭 중 valid nDCG@10이 가장 높은 2에폭 저장.
  세부 값은 `glople_embedding_meta.json`.

## 쓰는 법

```python
from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer

model = SentenceTransformer(snapshot_download("<namespace>/$repo"))
q = model.encode(["query: 아이랑 가기 좋은 동물 체험"], normalize_embeddings=True)
docs = model.encode(["passage: 서울대공원 | 경기 과천 | 관광지 | …", "passage: 불국사 | 경북 경주 | 관광지 | …"],
                    normalize_embeddings=True)
print(q @ docs.T)
```

## 평가

학습에 없는 문장 틀·표현으로 만든 **합성 질의**와, AI가 붙인 키워드 라벨(silver)로 정한 관련도 기준입니다. 사람이 판정한 관련도가 아닙니다.

$embedding_table

## 라이선스와 공개 범위 (읽어 주세요)

- 학습 글에는 **공공누리 제3유형(출처표시·변경금지)** 인 한국관광공사 TourAPI 소개 글이 들어 있습니다.
  가중치에 원문이 그대로 들어 있지는 않지만, 이런 글로 학습한 가중치를 공개해도 되는지는 저장소 주인이 판단합니다. 그래서 기본은 비공개입니다.
- sentence-transformers가 자동으로 만든 모델 카드에는 학습 문장 예시가 들어 있어 올리지 않고 이 카드로 바꿨습니다. 학습 데이터도 올리지 않습니다.

## 한계

- 키워드 라벨이 있는 **국내 여행지 1,934곳**으로만 학습·평가했습니다(해외 라벨 800곳은 이번 학습에서 뺐습니다). 해외 여행지 검색 품질은 재지 않았습니다.
- 관련도가 AI 키워드 라벨 기준이라, 라벨에 없는 취향(예: 특정 음식점 분위기)은 평가하지 않았습니다.
