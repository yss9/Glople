"""Glople에서 학습한 모델을 Hugging Face Hub에 올릴 준비(stage)와 올리기(push).

- stage(기본): hf/staging/<이름>/ 에 올릴 파일만 모으고(하드링크, 안 되면 복사) README.md(모델 카드)를 결과 파일에서 채운다.
  올리기 전에 이 폴더를 직접 열어 보고 판단한다. 네트워크를 쓰지 않는다.
- push: stage한 폴더를 그대로 올린다. 기본은 **비공개** 저장소(--public을 줄 때만 공개).
  토큰은 이 스크립트가 다루지 않는다. 먼저 `.venv/bin/hf auth login`으로 로그인하거나 HF_TOKEN 환경 변수를 둔다.

올리지 않는 것(일부러 뺌):
- 학습·평가 데이터(data/), 예측 파일(*_predictions.jsonl): TourAPI 설명문(공공누리 제3유형: 출처표시·변경금지)·위키백과 글이 들어 있다.
- 중간 체크포인트(0000100_adapters.safetensors …): 마지막 adapters.safetensors만.
- 임베딩 폴더의 README.md: sentence-transformers가 자동으로 만든 카드라 학습 문장 예시(설명문 포함)가 들어 있다. 우리 카드로 바꾼다.

공개 여부: 도구 호출 LoRA는 합성 대화로만 학습해 설명문이 없다. 태거·임베딩은 설명문으로 학습한 가중치라
공개해도 되는지는 오너가 정한다(docs/revamp-todo.md의 '재현성 고정' 항목). 그래서 둘 다 기본 비공개이고, 카드에도 그렇게 적는다.

사용 (training/에서, training/.venv: huggingface_hub 2.2.0):
  .venv/bin/python -I hf/publish.py stage                       # 전부(있는 것만)
  .venv/bin/python -I hf/publish.py stage --model llm-v4
  .venv/bin/python -I hf/publish.py push --model llm-v3 --namespace <HF 사용자 이름>
  .venv/bin/python -I hf/publish.py push --model llm-v3 --namespace <HF 사용자 이름> --public
"""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from string import Template

TRAINING = Path(__file__).resolve().parents[1]
HF_DIR = TRAINING / "hf"
STAGING = HF_DIR / "staging"
CARDS = HF_DIR / "cards"
LLM_BASE = "mlx-community/Qwen3-4B-Instruct-2507-4bit"

# 첫 판단 평가(scripts/evaluate.py) 요약 파일 이름: 세트 → 모델 → 이름
EVAL_NAMES = {
    "v2 test (300개)": {"기본": "base_v2test", "v1": "finetuned_v1_v2test", "v2": "finetuned_v2_v2test",
                       "v3": "finetuned_v3_v2test", "v4": "finetuned_v4_v2test"},
    "사람 말투 (90개, v3 설계 때 오답을 본 세트)": {"기본": "base_human", "v1": "finetuned_human", "v2": "finetuned_v2_human",
                                          "v3": "finetuned_v3_human", "v4": "finetuned_v4_human"},
    "새 사람 말투 (90개, 생성기를 보지 않고 새로 씀)": {"기본": "base_human2", "v1": "finetuned_v1_human2",
                                            "v2": "finetuned_v2_human2", "v3": "finetuned_v3_human2",
                                            "v4": "finetuned_v4_human2"},
}


DATA_NOTES = {
    "v3": "첫 판단(도구 호출 / 되묻기 / 바로 답)만 학습했습니다.",
    "v4": "v3 데이터에 도구 결과를 받은 뒤의 턴(결과로 답하기, 사용자가 이어서 해 달라고 할 때만 한 번 더 부르기, 빈 결과·오류 알리기)을 더했습니다.",
}
V3_AFTER_TOOLS = ("v3는 도구 결과를 받은 뒤의 턴을 학습하지 않았습니다. 그래서 결과를 그대로 다시 보내면 무시하거나 또 부르는 일이 많아"
                  "(결과를 근거로 답함 65/85), 에이전트는 결과를 답변 전용 프롬프트로 따로 보내 답을 만듭니다(80/85). v4가 이 부분을 학습한 버전입니다.")


def llm(version: str, data_dir: str) -> dict:
    return {"kind": "llm", "version": version, "src": TRAINING / "adapters" / f"qwen3-4b-tool-calling-{version}",
            "files": ["adapters.safetensors", "adapter_config.json"], "card": "tool-calling-lora.md",
            "repo": f"glople-qwen3-4b-tool-calling-lora-{version}", "data_dir": TRAINING / "data" / data_dir}


MODELS = {
    "llm-v3": llm("v3", "tool_calling_v3"),
    "llm-v4": llm("v4", "tool_calling_v4"),
    "tagger-v3": {"kind": "tagger", "src": TRAINING / "keyword_tagger" / "outputs" / "roberta-base-v3",
                  "files": ["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                            "tagger_config.json"],
                  "card": "keyword-tagger.md", "repo": "glople-keyword-tagger-roberta-base-v3",
                  "results": TRAINING / "keyword_tagger" / "results" / "roberta-base-v3.json"},
    "embedding": {"kind": "embedding", "src": TRAINING / "embedding" / "outputs" / "e5-base-ft" / "best",
                  "files": None, "skip": {"README.md"}, "card": "embedding.md",
                  "repo": "glople-multilingual-e5-base-travel-ft",
                  "results": TRAINING / "embedding" / "results" / "e5-base-ft.compare.md"},
}


def pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def overall(name: str) -> str:
    """scripts/report.py의 '전체 정답률(모든 문제)'과 같은 계산: 예측 파일에서 correct인 줄의 비율."""
    path = TRAINING / "results" / f"{name}.predictions.jsonl"
    if not path.exists():
        return "-"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    correct = sum(1 for r in rows if r.get("correct"))
    return f"{correct / len(rows) * 100:.1f}% ({correct}/{len(rows)})"


def llm_eval_table() -> str:
    rows = ["| 평가 세트 | 모델 | **전체 정답률 (모든 문제)** | 호출 여부 판단 | 도구 이름 | 도구 + 인자 완전 일치 (부를 문제만) | JSON 오류 |",
            "|---|---|--:|--:|--:|--:|--:|"]
    for set_name, names in EVAL_NAMES.items():
        for model, name in names.items():
            path = TRAINING / "results" / f"{name}.summary.json"
            if not path.exists():
                continue
            s = json.loads(path.read_text(encoding="utf-8"))
            rows.append(f"| {set_name} | {model} | {overall(name)} | {pct(s.get('decision_accuracy'))}"
                        f" | {pct(s.get('tool_name_accuracy'))} | {pct(s.get('exact_match'))} | {pct(s.get('json_error_rate'))} |")
    return "\n".join(rows)


def after_tools_section() -> str:
    path = TRAINING / "results" / "report_after_tools_v4.md"
    if not path.exists():
        return "(v4 학습 뒤 `run_v4.sh`가 `results/report_after_tools_v4.md`를 만들면 여기에 들어갑니다.)"
    body = path.read_text(encoding="utf-8").strip()
    body = re.sub(r"^#+ .*\n", "", body, count=1)            # 그 파일의 맨 위 제목은 뺀다
    body = body.split("\n## 예시", 1)[0]                     # 모델 답 예시(평가 발화 포함)는 카드에 넣지 않는다
    return re.sub(r"^## ", "### ", body, flags=re.M).strip()  # 카드의 '## 도구 결과 뒤' 아래로


def tool_spec(data_dir: Path) -> dict:
    """학습 기록 첫 줄에서 system 프롬프트와 도구 8개(합성 데이터, 설명문 없음)를 꺼낸다."""
    for split in ("test", "valid", "train"):
        path = data_dir / f"{split}.jsonl"
        if path.exists():
            with path.open(encoding="utf-8") as f:
                record = json.loads(f.readline())
            system = next(m["content"] for m in record["messages"] if m["role"] == "system")
            return {"system": system, "tools": record["tools"]}
    raise SystemExit(f"{data_dir}에 학습 데이터가 없어 system 프롬프트·도구를 꺼낼 수 없습니다")


def tagger_values(results_path: Path) -> dict:
    r = json.loads(results_path.read_text(encoding="utf-8"))
    out = {"label_count": r["label_space_size"], "split_sizes": json.dumps(r["split_sizes"], ensure_ascii=False)}
    rows = ["| 시험 세트 | 항목 수 | micro F1 | 정밀도 | 재현율 | macro F1 |", "|---|--:|--:|--:|--:|--:|"]
    labels = {"test": "국내 test", "test_new": "국내 test_new (새로 모은 곳)", "test_overseas": "해외 test_overseas"}
    for split, label in labels.items():
        m = r[split]
        rows.append(f"| {label} | {m['samples']['items']} | {m['micro']['f1']:.3f} | {m['micro']['precision']:.3f}"
                    f" | {m['micro']['recall']:.3f} | {m['macro']['f1']:.3f} |")
    out["tagger_table"] = "\n".join(rows)
    return out


def embedding_values(compare_path: Path) -> dict:
    text = compare_path.read_text(encoding="utf-8")
    m = re.search(r"### test.*?\n\n(\|.*?)\n\n", text, flags=re.S)
    return {"embedding_table": m.group(1).strip() if m else "(results/e5-base-ft.compare.md를 보세요)"}


def stage(name: str) -> Path | None:
    spec = MODELS[name]
    src: Path = spec["src"]
    if not src.exists() or (spec["files"] and not (src / spec["files"][0]).exists()):
        print(f"[{name}] 건너뜀: {src.relative_to(TRAINING)}에 모델이 아직 없습니다")
        return None
    out = STAGING / name
    if out.exists():
        shutil.rmtree(out)
    files = spec["files"] or sorted(p.relative_to(src).as_posix() for p in src.rglob("*")
                                    if p.is_file() and p.relative_to(src).as_posix() not in spec.get("skip", ()))
    for rel in files:
        link_or_copy(src / rel, out / rel)

    values = {"repo": spec["repo"], "base": LLM_BASE}
    if spec["kind"] == "llm":
        cfg = json.loads((src / "adapter_config.json").read_text(encoding="utf-8"))
        cfg["model"] = LLM_BASE   # 로컬 경로 대신 Hub 이름(--model로 따로 주므로 불러오기에는 영향 없음)
        (out / "adapter_config.json").unlink()
        (out / "adapter_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        (out / "glople_tool_spec.json").write_text(
            json.dumps(tool_spec(spec["data_dir"]), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        lora = cfg["lora_parameters"]
        values.update(version=spec["version"], rank=lora["rank"], scale=lora["scale"], dropout=lora["dropout"],
                      num_layers=cfg["num_layers"], iters=cfg["iters"], batch_size=cfg["batch_size"],
                      lr=cfg["learning_rate"], max_seq=cfg["max_seq_length"],
                      eval_table=llm_eval_table(), data_note=DATA_NOTES[spec["version"]],
                      after_tools=after_tools_section() if spec["version"] == "v4" else V3_AFTER_TOOLS)
    elif spec["kind"] == "tagger":
        values.update(tagger_values(spec["results"]))
    else:
        values.update(embedding_values(spec["results"]))
    card = Template((CARDS / spec["card"]).read_text(encoding="utf-8")).substitute(values)
    (out / "README.md").write_text(card, encoding="utf-8")

    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"[{name}] {out.relative_to(TRAINING)}/ ({total / 1e6:.1f} MB)")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print(f"    {p.relative_to(out).as_posix():40s} {p.stat().st_size / 1e6:9.2f} MB")
    return out


def push(name: str, namespace: str, public: bool) -> None:
    from huggingface_hub import HfApi   # stage는 네트워크·라이브러리 없이도 돌게 여기서만 불러온다

    out = STAGING / name
    if not (out / "README.md").exists():
        sys.exit(f"먼저 stage하세요: .venv/bin/python -I hf/publish.py stage --model {name}")
    repo_id = f"{namespace}/{MODELS[name]['repo']}"
    api = HfApi()
    api.create_repo(repo_id, repo_type="model", private=not public, exist_ok=True)
    # private 값은 저장소가 이미 있으면 무시된다(huggingface_hub 2.2.0 create_repo 설명). 올리기 전에 실제 상태를 본다.
    private = api.repo_info(repo_id, repo_type="model").private
    if private == public:
        sys.exit(f"{repo_id}가 이미 {'비공개' if private else '공개'}로 있어 요청과 다릅니다. Hub 설정에서 바꾼 뒤 다시 실행하세요")
    api.upload_folder(repo_id=repo_id, repo_type="model", folder_path=str(out),
                      commit_message=f"Glople {name} 올리기")
    print(f"올림: https://huggingface.co/{repo_id} ({'비공개' if private else '공개'})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("stage", "push"))
    ap.add_argument("--model", default="all", choices=("all", *MODELS))
    ap.add_argument("--namespace", help="push: Hugging Face 사용자 또는 조직 이름")
    ap.add_argument("--public", action="store_true", help="push: 공개 저장소로(기본 비공개)")
    args = ap.parse_args()
    names = list(MODELS) if args.model == "all" else [args.model]
    if args.action == "stage":
        for name in names:
            stage(name)
        return
    if not args.namespace:
        sys.exit("push에는 --namespace가 필요합니다")
    if args.model == "all":
        sys.exit("push는 모델을 하나씩 고르세요(--model)")
    push(args.model, args.namespace, args.public)


if __name__ == "__main__":
    main()
