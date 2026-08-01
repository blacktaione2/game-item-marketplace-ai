# AI 서버 이미지.
#
# **모델을 이미지에 넣지 않는다.** `models/` 는 650MB(임베딩 466 · 리랭커 130 ·
# 라우터 55)인데, 그 대부분은 낭비가 아니라 다국어 어휘 때문이다 —
# paraphrase-multilingual-MiniLM-L12-v2 는 XLM-R 토크나이저(25만 어휘)를 써서
# 임베딩 행렬만 250,002 x 384 = 9,600만 파라미터다(본체는 2,100만).
# 이미지에 넣으면 torch까지 합쳐 2GB를 넘고, 모델을 다시 만들 때마다 이미지를
# 다시 만들어야 한다. 대신 볼륨에 두고 `ai-init` 컨테이너가 채운다.
#
# 베이스는 python:3.11-slim — amd64/arm64 공식 제공. 배포 대상이 ARM이다.

FROM python:3.11-slim
WORKDIR /app

# torch를 **CPU 휠 인덱스에서 먼저** 설치한다. 기본 인덱스는 리눅스에서 CUDA
# 의존성까지 끌고 와 GB 단위로 불어난다. 이 순서는 CI(.github/workflows/ai.yml)와
# 같아야 한다 — 다르면 "CI는 통과하는데 이미지는 다르다"가 생긴다.
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY app app
COPY scripts scripts
COPY data data

# 모델 볼륨의 마운트 지점. settings.embedding_model 등이 상대 경로
# `models/...` 를 쓰므로 WORKDIR 기준으로 여기가 맞다.
RUN mkdir -p models && useradd --system --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
