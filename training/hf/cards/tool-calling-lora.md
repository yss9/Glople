---
license: apache-2.0
base_model: $base
language:
- ko
library_name: mlx
pipeline_tag: text-generation
tags:
- mlx
- lora
- tool-calling
- function-calling
- qwen3
- korean
- travel
---

# $repo

여행 커뮤니티 Glople의 여행 도우미 에이전트(LangGraph)가 **라우터**로 쓰는 도구 호출 LoRA입니다(버전 $version).
한국어 한 마디를 받아 도구 8개 중 무엇을 어떤 인자로 부를지, 아니면 되묻거나 바로 답할지를 고릅니다.
개인 포트폴리오·학습 프로젝트로, Apple Silicon 맥에서 MLX로 학습했습니다.

- 기반 모델: [`$base`](https://huggingface.co/$base) (원본 Qwen/Qwen3-4B-Instruct-2507, Apache-2.0)
- 형식: **MLX LoRA 어댑터**(`mlx_lm.lora`가 저장한 `adapters.safetensors` + `adapter_config.json`). PEFT 형식이 아닙니다.
- `glople_tool_spec.json`: 학습 때와 같은 system 프롬프트와 도구 8개 정의. 이 둘을 그대로 써야 학습 분포와 맞습니다.

## 쓰는 법

```python
import json
from huggingface_hub import snapshot_download
from mlx_lm import load, generate

adapter = snapshot_download("<namespace>/$repo")
model, tokenizer = load("$base", adapter_path=adapter)
spec = json.load(open(f"{adapter}/glople_tool_spec.json", encoding="utf-8"))
messages = [{"role": "system", "content": spec["system"]}, {"role": "user", "content": "부산 내일 날씨 어때?"}]
prompt = tokenizer.apply_chat_template(messages, tools=spec["tools"], add_generation_prompt=True, tokenize=False)
print(generate(model, tokenizer, prompt=prompt, max_tokens=256))   # <tool_call>{"name": "get_weather", ...}</tool_call>
```

OpenAI 호환 서버로 띄울 때(프로젝트에서 쓰는 방식): `mlx_lm.server --model $base --adapter-path <받은 폴더>`.

## 학습

| 항목 | 값 |
|---|---|
| 방식 | LoRA (4bit 기반 모델 위, QLoRA와 같은 구성), 마지막 assistant 턴만 학습(mask_prompt) |
| LoRA | rank $rank, scale $scale, dropout $dropout, 위쪽 $num_layers개 층 |
| 단계 | $iters단계 × 배치 $batch_size, 학습률 $lr(cosine), 최대 길이 $max_seq 토큰 |
| 데이터 | 코드 템플릿으로 만든 **합성 한국어 대화**(사람 말투 변형, 되묻기·거절·병렬 호출·일정 저장 포함). $data_note 여행지 설명문 원문은 들어 있지 않습니다. |
| 누설 검사 | 평가 세트 발화·값이 학습 데이터에 없는지 검사한 뒤 학습 |

## 평가 (첫 판단)

학습에 쓰지 않은 세 세트입니다. 대표 지표는 '전체 정답률'(되묻기·바로 답 문제까지 모든 문제에서 정답과 같은 행동)이고,
'도구 + 인자 완전 일치'는 도구를 불러야 하는 문제만 셉니다. 사람 말투 세트 두 개는 학습 생성기와 다른, 사람이 쓸 법한 말투로 따로 쓴 문장입니다.
첫 번째 세트는 v3를 설계할 때 오답을 보았으므로 실제보다 좋게 나올 수 있고, 두 번째 세트는 생성기를 보지 않고 새로 썼습니다.

$eval_table

에이전트에서는 이 모델의 호출을 그대로 믿지 않고, 코드가 인자 형식·근거(발화에 없는 값을 지어냈는지)를 다시 검사합니다.

## 도구 결과 뒤

$after_tools

## 한계

- 도구 8개와 system 프롬프트가 Glople 서비스 전용입니다. 다른 도구 목록에 그대로 쓰면 성능을 보장하지 않습니다.
- 사람 말투 세트에서는 test보다 낮습니다. 지역·일수 같은 인자를 지어내는 경우가 있어 에이전트의 근거 검사와 함께 쓰는 것을 전제로 합니다.
- 답변 문장은 에이전트가 별도 프롬프트와 가드로 만듭니다. 이 어댑터 혼자 대화를 끝까지 이어 가는 용도로 평가하지 않았습니다.
