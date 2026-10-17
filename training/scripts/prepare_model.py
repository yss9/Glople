"""학습용 로컬 모델 폴더를 만든다.

mlx-community 4bit 변환본의 가중치는 그대로 쓰고, 채팅 템플릿만 공식 Qwen3-4B-Instruct-2507 것으로 바꾼다.
변환본 템플릿은 마지막 assistant 턴 앞에 빈 <think></think>를 넣어서, 그대로 학습하면
생각 모드가 없는 Instruct 모델이 매번 빈 think 블록을 출력하도록 배우게 된다.
"""
import json
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MLX_REPO = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
OFFICIAL_REPO = "Qwen/Qwen3-4B-Instruct-2507"
TARGET = ROOT / "models" / "qwen3-4b-instruct-2507-4bit"


def main():
    source = Path(snapshot_download(MLX_REPO))
    official_config = json.loads(Path(hf_hub_download(OFFICIAL_REPO, "tokenizer_config.json")).read_text())
    template = official_config["chat_template"]

    TARGET.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        link = TARGET / path.name
        if link.is_symlink() or link.exists():
            link.unlink()
        if path.name in ("chat_template.jinja", "tokenizer_config.json"):
            continue
        link.symlink_to(path.resolve())

    tokenizer_config = json.loads((source / "tokenizer_config.json").read_text())
    tokenizer_config["chat_template"] = template
    (TARGET / "tokenizer_config.json").write_text(json.dumps(tokenizer_config, ensure_ascii=False, indent=2))
    (TARGET / "chat_template.jinja").write_text(template)
    print(f"model ready: {TARGET}")


if __name__ == "__main__":
    main()
