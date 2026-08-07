from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """기동 확인 + **설정 여부를 눈으로 확인할 수 있는 자리.**

    `llm_fallback` 이 여기 있는 이유가 있다. 원래는 `get_llm_client()` 가 기동 시
    `logger.info` 로 남기게 해뒀는데, **이 앱은 로깅을 설정하지 않는다** — 파이썬
    루트 로거 기본값이 `WARNING` 이라 그 줄은 어디에도 안 나온다. 배포 후
    `docker logs | grep` 이 빈 결과를 냈고, 그건 "폴백이 없다" 와 "로그가 안
    보인다" 를 구분해주지 못했다.

    > **로그로 확인하라고 안내하려면 그 레벨이 실제로 출력되는지부터 확인해야
    > 한다.** 이 저장소가 반복해 적은 "판정에 쓴 값을 출력에 넣어라" 의 인프라
    > 버전이다 — 값을 넣어도 그 통로가 막혀 있으면 없는 것과 같다.

    **키 자체는 절대 싣지 않는다.** 구성 여부(bool)만 낸다. `/health` 는 인증 없이
    열려 있으므로(사설망 전제) 여기 실을 수 있는 것은 비밀이 아닌 사실뿐이다.
    """
    return {
        "status": "ok",
        "service": settings.service_name,
        # 폴백 프로바이더가 붙었는가. 값이 아니라 **구성 여부**다.
        "llm_fallback": bool(settings.anthropic_api_key),
        # 붙었을 때만 어떤 모델인지 밝힌다 — 안 붙었는데 모델명이 보이면
        # "설정됐다" 로 오독된다.
        "llm_fallback_model": (
            settings.anthropic_model if settings.anthropic_api_key else None
        ),
    }
