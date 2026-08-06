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
    # **파인튜닝의 출발점.** `embedding_model` 과 반드시 달라야 한다 —
    # 그건 파인튜닝의 *출력* 경로다.
    #
    # 이 값이 없던 동안 세 스크립트가 `embedding_model` 을 베이스로 썼고, 그래서
    # (1) 새 환경에서 finetune_embedding 이 자기 출력물을 베이스로 찾다가
    #     HuggingFace 저장소 id로 해석돼 401로 죽었고(컨테이너화가 드러냈다),
    # (2) evaluate_embedding / compare_eval_sets 의 before-after 비교가
    #     **같은 모델을 자기 자신과 비교**하고 있었다.
    # Phase 4 수치가 틀렸다는 뜻은 아니다 — 당시엔 EMBEDDING_MODEL 환경변수로
    # 스톡 모델을 가리켜 쟀을 것이다. 틀린 건 **커밋된 상태의 재현성**이다.
    embedding_base_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
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

    # 동기 CPU 호출(임베딩·리랭커·분류기)을 내보낼 전용 스레드풀 크기.
    # 2인 이유는 측정값이다 — 1은 직렬, 4는 torch 초과 구독으로 오히려 느려진다.
    # 자세한 표는 app/core/threadpool.py.
    cpu_pool_workers: int = 2

    semantic_cache_enabled: bool = True
    # scripts.evaluate_semantic_cache 로 산정한 값. 함정 쌍(한 글자 차이로 답이
    # 뒤집히는 질의)이 0.9787까지 올라와서, 오탐 0%를 지키려면 이 정도로
    # 높여야 한다. 그 대가로 적중률이 낮다 — 상세는 ADR-0012.
    semantic_cache_threshold: float = 0.98
    semantic_cache_max_entries: int = 2000
    # 키에 박히는 버전. 올리면 캐시가 통째로 무효화된다.
    #
    # **응답의 모양이 바뀔 때도 올려야 한다.** 처음엔 "재색인/모델 재학습 시"만
    # 적어뒀는데, 그건 *내용*이 낡는 경우다. 저장된 응답은 **그때의 스키마**로
    # 굳어 있어서, 필드를 추가하거나 형태를 바꾸면 캐시 적중이 옛 모양을 그대로
    # 돌려준다 — 코드는 고쳤는데 화면은 안 고쳐진 것처럼 보인다.
    #
    # v2: `resolved_item` 이 `{item_id, name}` 에서 검색 결과 항목 전체로
    #     바뀌었다(ADR-0037). 적중 시 이름만 있는 카드가 그려졌다.
    cache_version: str = "v2"

    # --- Phase 8: 인증 (ADR-0023) ---
    # **백엔드 application.yml의 jwt.secret과 같은 값이어야 한다.** 대칭키
    # (HS256)라 발급자와 검증자가 같은 키를 본다. 다르면 발급은 성공하는데 이
    # 서버만 401을 내므로 증상이 헷갈린다 — 기본값도 백엔드와 맞춰뒀다.
    jwt_secret: str = "gimp_local_dev_secret_change_me_32b"
    jwt_issuer: str = "gimp-backend"

    # --- Phase 8: 요청 한도 (ADR-0024) ---
    # `/api/assistant` 하나에만 건다 — 실제로 돈이 나가는 유일한 경로다.
    #
    # **부하테스트는 이 한도에 걸린다.** 그때 이 기본값을 고치지 말고 환경변수로
    # 넘긴다: `RATE_LIMIT_ASSISTANT_PER_MIN=100000 python -m uvicorn ...`
    # 백엔드가 `application-loadtest.yml` 프로파일을 쓰는 것과 같은 목적이다 —
    # 완화한 값이 **파일에 남지 않아야** 되돌리는 걸 잊을 수 없다.
    # 끄지 않고 올리는 이유는 리미터 경로가 그대로 돌아야 오버헤드가 측정에
    # 포함되고 `ai_rate_limited_total`이 0인지로 오염을 확인할 수 있어서다.
    rate_limit_enabled: bool = True
    rate_limit_assistant_per_min: int = 20
    # 일일 한도 (ADR-0031). 공개 배포에서 **로그인한 사람의 폭주**를 막는 계층이다.
    #
    # 50 의 근거: 분당 20회면 물리 상한이 하루 28,800회인데, 데모 목적으로 필요한 건
    # 한 번 둘러보는 정도(검색 몇 번 + 시세 + 이상거래 + 에이전트 = 20~30회)다.
    # 그 2배로 잡았다. 계정 5개를 다 소진해도 하루 250회이고 요청당 LLM 2회이므로
    # 최악 500호출이다.
    #
    # **이건 세 계층 중 하나일 뿐이다.** 실제 비용 상한은 OpenAI 대시보드의 월
    # 사용량 한도이고, 그게 최후 안전망이다.
    rate_limit_assistant_per_day: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
