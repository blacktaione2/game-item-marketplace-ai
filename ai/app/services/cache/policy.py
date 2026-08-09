"""의도별 캐시 정책.

캐시 가능 여부와 TTL은 **의도마다 다르다.** 하나의 TTL을 전역으로 쓰면 FAQ는
불필요하게 자주 만료되고 시세는 낡은 값을 내보내게 된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.router.intents import Intent

_HOUR = 3600
_KST = timezone(timedelta(hours=9))

# (캐시 가능 여부, 고정 TTL 초). TTL이 None이면 동적 계산(_dynamic_ttl).
_POLICY: dict[Intent, tuple[bool, int | None]] = {
    # 답이 바뀌지 않는다.
    Intent.FAQ_SMALLTALK: (True, 24 * _HOUR),
    # 매물은 등록/판매로 계속 바뀐다.
    Intent.ITEM_SEARCH: (True, 10 * 60),
    # 예측이 일 단위로 갱신되므로 날짜가 바뀌면 무효 — 동적 TTL.
    Intent.PRICE_FORECAST: (True, None),
    # **캐시하지 않는다.** 개별 거래 단건 판정이라 재사용될 일이 거의 없고,
    # 유사 질의로 잘못 히트하면 그게 곧 보안 오판이 된다. 캐시 적중률
    # 몇 퍼센트와 맞바꿀 위험이 아니다.
    Intent.ANOMALY_CHECK: (False, None),
    # 여러 도구 결과를 합성한 것이라 구성요소 중 하나만 바뀌어도 낡는다.
    Intent.COMPOUND: (True, 5 * 60),
    Intent.UNKNOWN: (True, 5 * 60),
}


# 시맨틱(유사도) 매칭을 허용할 의도.
#
# 실측 결과 이 코퍼스의 질의-질의 유사도로는 "같은 질문"과 "다른 질문"이
# 분리되지 않는다(scripts.evaluate_semantic_cache). 함정 쌍의 평균 유사도가
# 동의 쌍과 사실상 같거나 오히려 높았고, 파인튜닝 모델과 베이스 모델 모두
# 그랬다. `+8 롱소드 시세`와 `+9 롱소드 시세`처럼 한 글자 차이로 답이 완전히
# 달라지는 질의가 문장 임베딩에서는 거의 같은 벡터이기 때문이다.
#
# 그래서 시맨틱 매칭을 전 의도에 열지 않고 **오탐의 대가가 작은 곳에만**
# 허용한다. FAQ/스몰토크는 답 공간이 좁고 표현만 다른 같은 질문이 대부분이며,
# 틀려도 "다른 안내 문구가 나가는" 정도다. 반면 시세·검색·이상거래는 오탐이
# 곧 잘못된 가격이나 잘못된 판정이 된다.
#
# 나머지 의도는 정확 일치 캐시로만 동작한다. (ADR-0012)
_SEMANTIC_ALLOWED = {Intent.FAQ_SMALLTALK}


def is_cacheable(intent: Intent, response: dict[str, Any] | None = None) -> bool:
    """이 응답을 캐시에 저장해도 되는가.

    `response`를 주면 의도 정책 위에 **응답 내용에 따른 예외**를 하나 더 본다.
    안 주면 의도 정책만 판정한다(기존 호출 형태 유지).
    """
    if not _POLICY.get(intent, (False, None))[0]:
        return False

    # **"조건에 맞는 결과가 없다"는 응답은 저장하지 않는다.** 이유가 둘이고
    # 둘 다 이 프로젝트에서 실측된 것이다.
    #
    # 1. 0건 판정의 근거인 **필터 추출이 비결정적이다.** 같은 질의가 실행마다
    #    다르게 재작성되고 그 과정에서 필터도 달라질 수 있다(ADR-0014). 0건
    #    응답을 질의 문자열로 캐시하면 여러 추출 결과 중 하나를 임의로 골라
    #    TTL 동안 고정하는 셈이다. 판정은 매 요청의 실제 필터 결과로 다시
    #    해야 하고, 질의 텍스트를 키로 미리 굳혀선 안 된다.
    # 2. 0건은 **가장 낡기 쉬운 답**이다. 매물이 하나 등록되면 즉시 거짓이
    #    되고, 오류의 방향("없다")이 사용자에게 더 나쁘다.
    if response is not None and response.get("no_results"):
        return False

    # **"이 거래소가 다루는 주제가 아니다" 도 저장하지 않는다** (ADR-0039).
    # 위와 **같은 이유 1, 다른 이유 2**다 — 둘을 한 덩어리로 읽으면 안 된다.
    #
    # 이유 1은 그대로 적용된다: 판정 근거가 LLM 의 비결정적 판단이라, 질의
    # 문자열로 캐시하면 여러 판정 중 하나를 임의로 골라 TTL 동안 고정한다.
    # 오거부(도메인 안을 밖이라고 판정)가 굳으면 멀쩡한 검색이 TTL 내내 막힌다.
    #
    # **이유 2는 적용되지 않는다.** 0건은 매물 하나만 등록돼도 거짓이 되지만,
    # `"삼성전자 주식"` 은 내일도 이 거래소 밖이다. 즉 이 응답은 낡지 않는다 —
    # 그런데도 저장하지 않는 근거는 오로지 이유 1이다. 판정이 결정적으로 바뀌면
    # (예: 룰이나 보정된 분류기로 옮기면) 이 금지는 다시 볼 수 있다.
    if response is not None and response.get("out_of_domain"):
        return False

    # **설명 LLM 이 죽어 내려앉은 응답도 저장하지 않는다** (ADR-0041 의 뒤늦은 짝).
    #
    # 위 둘과 이유가 또 다르다. 여기서는 판정이 비결정적인 것도 아니고 답이 낡는
    # 것도 아니다 — **답이 맞다.** 다만 그 답의 품질이 *그 순간 프로바이더가
    # 죽어 있었다*는 일시적 사실로 정해졌다. 시세 분기의 TTL 은 자정까지이므로,
    # OpenAI 가 5분 뒤 복구돼도 같은 질의는 몇 시간 동안 축약 문장을 받는다.
    #
    # **장애를 캐시에 굳히지 않는다**가 이 조항의 전부다. 대가는 장애 중 적중률이
    # 떨어지는 것인데, 장애 중에 아끼려는 건 애초에 그 프로바이더 호출이라
    # 잃을 게 없다.
    if response is not None and response.get("degraded"):
        return False

    return True


def allows_semantic(intent: Intent) -> bool:
    return intent in _SEMANTIC_ALLOWED


def ttl_seconds(intent: Intent, now: datetime | None = None) -> int:
    cacheable, fixed = _POLICY.get(intent, (False, None))
    if not cacheable:
        return 0
    if fixed is not None:
        return fixed
    return _until_midnight(now or _kst_now())


def _kst_now() -> datetime:
    """한국시간 기준 현재 시각 (naive).

    **`datetime.now()` 를 쓰고 있었다.** 그건 프로세스가 도는 곳의 로컬 시각이라,
    개발 박스(KST)에서는 맞고 **배포 컨테이너(UTC)에서는 9시간 어긋난다.**

    그리고 `core/rate_limit.py` 의 주석이 *"`cache/policy.py::_until_midnight` 과
    같은 계산이다"* 라고 단언하고 있었는데 사실이 아니었다 — 그쪽은 처음부터
    명시적 KST 다. **두 자정이 서로 다른 시각이었다.** 같은 말을 두 곳에서 하되
    한쪽만 시간대를 정하면, 나중에 읽는 사람은 정한 쪽을 보고 안 정한 쪽도
    그렇겠거니 한다.

    맞추는 방향을 KST 로 잡은 이유는 그쪽이 **설명 가능한 값**이기 때문이다.
    시세 시리즈의 날짜 경계도, 사용자에게 말할 "자정"도 하나여야 한다.
    """
    return datetime.now(timezone.utc).astimezone(_KST).replace(tzinfo=None)


def _until_midnight(now: datetime) -> int:
    """다음 자정까지 남은 초.

    시세 예측은 일별 시세 시리즈를 기준으로 계산되므로 날짜가 바뀌면 근거가
    바뀐다. 고정 TTL을 쓰면 자정 직전에 캐시된 값이 다음 날까지 살아남는다.
    """
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(int((tomorrow - now).total_seconds()), 60)
