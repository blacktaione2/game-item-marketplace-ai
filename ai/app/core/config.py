from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "ai-server"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    # **0이 기본값이다.** 안 넘기면 OpenAI가 1.0을 쓰는데, 그 상태로 오래
    # 돌아서 측정이 반복적으로 깨졌다 — 같은 질의의 재작성이 실행마다 달라져
    # 리랭커 하한 캘리브레이션이 불가능했고(ADR-0014), 0건 판정도 캐시할 수
    # 없었다(ADR-0016). 이 파이프라인에서 실행 간 다양성은 아무 이득이 없다.
    # 다양성이 자산인 곳(하드네거티브 생성)만 자기 클라이언트를 따로 만든다.
    openai_temperature: float = 0.0

    elasticsearch_url: str = "http://localhost:9200"
    index_prefix: str = "items"

    # 도메인 파인튜닝된 모델(Phase 4). 베이스는
    # sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 이고 차원은 384로 동일.
    # models/ 는 gitignore 대상이라 새로 clone한 환경에는 없다 —
    # scripts.finetune_embedding 으로 먼저 생성해야 한다.
    embedding_model: str = "models/embedding-finetuned"
    embedding_dims: int = 384

    # ms-marco-MiniLM 계열 중 다국어 버전(mMARCO). 영어 전용
    # ms-marco-MiniLM-L-6-v2는 한국어를 자모 단위로 쪼개버려 리랭킹이
    # 무의미해진다 — ADR-0006 참고.
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    reranker_onnx_dir: str = "models/reranker-onnx-int8"
    # 리랭킹 대상 상한 (레이턴시 예산 — 계획서상 top-15~20)
    rerank_candidates: int = 20

    # 시세 예측(Phase 5). models/ 는 gitignore 대상이라 새로 clone한 환경에는
    # 없다 — scripts.train_forecast 로 먼저 학습해야 한다.
    forecast_model_dir: str = "models/price-lstm"
    forecast_window: int = 14
    forecast_horizon: int = 7
    # 이 일수 미만이면 Cold Start 백오프로 넘긴다. 학습 윈도우 하나를 만들려면
    # window+horizon=21일이 필요하므로 그보다 여유를 둔 값.
    forecast_min_history_days: int = 30
    # 콜드스타트 때 트렌드를 상속받을 유사 아이템 수
    forecast_donor_count: int = 3

    # 이상거래 탐지(Phase 5). 역시 models/ 아래라 gitignore 대상이다.
    anomaly_model_dir: str = "models/trade-autoencoder"
    # 임계값 = 정상 홀드아웃 재구성 오차의 이 백분위수. 통계값이라기보다
    # 운영값이다 — GM 검토 큐가 하루에 소화 가능한 알림 수가 실질 상한이다.
    anomaly_alert_percentile: float = 99.0

    # --- Phase 6: 라우팅 / 에이전트 / 시맨틱 캐시 ---
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str = "gimp_local_pw"

    intent_base_model: str = "monologg/koelectra-small-v3-discriminator"
    intent_model_dir: str = "models/intent-router"
    # 이 값 미만이면 분류를 신뢰하지 않고 compound(에이전트)로 보낸다.
    intent_confidence_threshold: float = 0.70

    agent_max_steps: int = 5
    # 도구 타임아웃. 첫 호출에는 모델 지연 로딩이 붙어 수 초~십수 초가 걸린다.
    # 게다가 그 로딩이 동기라 이벤트 루프를 막는 동안은 타임아웃이 발동조차
    # 못 한다(로드맵 기술 부채 항목). 그래서 넉넉하게 잡는다.
    agent_tool_timeout_seconds: float = 20.0

    semantic_cache_enabled: bool = True
    # scripts.evaluate_semantic_cache 로 산정한 값. 함정 쌍(한 글자 차이로 답이
    # 뒤집히는 질의)이 0.9787까지 올라와서, 오탐 0%를 지키려면 이 정도로
    # 높여야 한다. 그 대가로 적중률이 낮다 — 상세는 ADR-0012.
    semantic_cache_threshold: float = 0.98
    semantic_cache_max_entries: int = 2000
    # 키에 박히는 버전. 재색인/모델 재학습 시 올리면 캐시가 통째로 무효화된다.
    cache_version: str = "v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
