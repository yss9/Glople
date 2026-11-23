"""기본 모델을 고정한 revision 으로 Hugging Face 캐시(~/.cache/huggingface)에 받습니다.

- 학습·평가에 필요한 파일만 받습니다 (onnx·openvino·pytorch_model.bin 은 받지 않음, e5-base 는 약 1.1GB).
- 한 번 받은 뒤에는 HF_HUB_OFFLINE=1 로 네트워크 없이 돌릴 수 있습니다 (run_embedding.sh 가 그렇게 함).

사용: .venv/bin/python scripts/download_model.py [--model intfloat/multilingual-e5-base]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from emb_common import DEFAULT_MODEL, MODEL_FILE_PATTERNS, MODEL_PRESETS, setup_line_buffering  # noqa: E402


def main() -> None:
    setup_line_buffering()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    from huggingface_hub import snapshot_download

    preset = MODEL_PRESETS.get(args.model)
    if preset is None:
        raise SystemExit(f"{args.model} 는 emb_common.MODEL_PRESETS 에 없습니다. revision 과 접두어를 먼저 정하세요.")
    path = snapshot_download(args.model, revision=preset["revision"], allow_patterns=MODEL_FILE_PATTERNS)
    print(f"받음: {args.model}@{preset['revision'][:12]} ({preset['license']}) -> {path}", flush=True)


if __name__ == "__main__":
    main()
