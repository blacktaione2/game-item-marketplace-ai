"""크로스인코더를 ONNX로 변환하고 int8 양자화해서 디스크에 저장한다.

런타임에도 모델이 없으면 자동으로 만들지만, 첫 검색 요청이 변환 시간을
전부 뒤집어쓰게 되므로 배포 전에 이 스크립트로 미리 만들어두는 걸 권장.

실행: python -m scripts.build_reranker_onnx
"""

from pathlib import Path

from app.core.config import get_settings
from app.services.search.reranker import build_quantized_model


def main() -> None:
    settings = get_settings()
    output_dir = Path(settings.reranker_onnx_dir)

    build_quantized_model(settings.reranker_model, output_dir)

    for path in sorted(output_dir.glob("*.onnx")):
        print(f"  {path.name}: {path.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
