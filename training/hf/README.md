# Hugging Face Hub에 올리기

학습한 모델 세 가지(+ v4)를 Hub에 올리는 스크립트와 모델 카드입니다. **올리기(push)는 오너가 직접** 합니다(토큰이 필요하고, 공개 범위도 오너가 정함).

| 이름 | 무엇 | 크기 | 기본 공개 범위 | 카드 |
|---|---|--:|---|---|
| `llm-v3`, `llm-v4` | 도구 호출 MLX LoRA (Qwen3-4B-Instruct-2507 4bit 위) | 약 59MB | 비공개 (합성 대화로만 학습해 공개해도 무리가 적음) | [`cards/tool-calling-lora.md`](cards/tool-calling-lora.md) |
| `tagger-v3` | 키워드 태거 (klue/roberta-base 다중 라벨) | 약 444MB | 비공개 (TourAPI 제3유형·위키백과 글로 학습) | [`cards/keyword-tagger.md`](cards/keyword-tagger.md) |
| `embedding` | 여행지 검색 임베딩 (multilingual-e5-base 파인튜닝) | 약 1.1GB | 비공개 (TourAPI 제3유형 글로 학습) | [`cards/embedding.md`](cards/embedding.md) |

## 순서 (training/에서)

```bash
.venv/bin/python -I hf/publish.py stage
```

`hf/staging/<이름>/`에 올릴 파일만 모이고(하드링크), `README.md`가 `results/`의 평가 숫자로 채워집니다. 네트워크는 쓰지 않습니다. 폴더를 열어 카드와 파일 목록을 직접 확인하세요.

```bash
.venv/bin/hf auth login
```

토큰은 Hugging Face 설정 → Access Tokens에서 **write** 권한으로 만듭니다. 토큰은 이 저장소 어디에도 적지 않습니다(`hf`가 `~/.cache/huggingface/`에 저장).

```bash
.venv/bin/python -I hf/publish.py push --model llm-v3 --namespace <HF 사용자 이름>
```

기본은 비공개 저장소입니다. 공개하려면 끝에 `--public`을 붙입니다. 저장소 이름은 `<사용자>/glople-qwen3-4b-tool-calling-lora-v3`처럼 정해져 있습니다(`publish.py`의 `MODELS`).

## 일부러 올리지 않는 것

- 학습·평가 데이터(`data/`), 태거 예측 파일(`*_predictions.jsonl`): TourAPI 설명문(공공누리 제3유형: 출처표시·변경금지)과 위키백과 글이 들어 있습니다.
- LoRA 중간 체크포인트(`0000100_adapters.safetensors` …): 마지막 `adapters.safetensors`만 올립니다.
- 임베딩 폴더의 자동 생성 `README.md`: sentence-transformers가 만든 카드에 학습 문장 예시(설명문 포함)가 7개 들어 있어, 우리 카드로 바꿉니다.
- LoRA의 `adapter_config.json`은 로컬 경로(`models/...`) 대신 Hub 이름(`mlx-community/Qwen3-4B-Instruct-2507-4bit`)으로 바꿔 올립니다. 대신 학습 때와 같은 system 프롬프트·도구 8개를 `glople_tool_spec.json`으로 함께 올립니다(합성 데이터에서 꺼낸 것).

## 공개 전에 확인할 것 (오너 판단)

- 태거·임베딩: 제3유형 글로 학습한 가중치를 공개해도 되는지(`docs/revamp-todo.md`의 '재현성 고정' 항목). 가중치에 원문이 그대로 들어 있지는 않지만, 판단은 오너 몫이라 기본을 비공개로 두었습니다.
- 태거 기반 모델 `klue/roberta-base`는 Hub 메타데이터에 라이선스 표시가 없습니다(2026-10-18 확인). 이전 점검 기록(`docs/revamp-todo.md`의 '학습 README에 라이선스·재배포 섹션 추가' 항목)이 CC BY-SA 4.0으로 적어 두어 카드는 보수적으로 `cc-by-sa-4.0`입니다. 공개 전에 KLUE 공식 안내를 확인하세요. 임베딩 카드는 학습 데이터 조건을 오너가 정하기 전이라 `license: other`입니다.
- LoRA: 기반 모델이 Apache-2.0이라 카드도 `apache-2.0`입니다.
