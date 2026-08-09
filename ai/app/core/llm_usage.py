"""요청 하나가 실제로 쓴 LLM 호출 수를 센다.

## 왜 세야 하나 — 상수가 틀렸다

`llm_calls` 는 분기마다 **손으로 적은 상수**였다. 직선 경로 셋은 그래도 맞는다:

| 분기 | 상수 | 실측 |
|---|---|---|
| 검색 | 2 (질의이해 + 도메인 판정) | 2 |
| 시세 | 3 (검색 2 + 설명 1) | 3 |
| 이상거래 | 1 (설명) | 1 |
| **복합(에이전트)** | `len(tool_calls) + 1` | **어긋난다** |

에이전트만 값이 데이터에 달려 있고, 그 공식이 두 군데서 틀렸다.

1. **한 응답이 도구를 둘 이상 부른다.** OpenAI 의 `parallel_tool_calls` 는 기본이
   켜짐이라 한 스텝에 도구 2개가 온다(실측: 두 번 실행 모두 관측). 그러면
   `len(tool_calls)` 는 LLM 왕복 수가 아니다.
2. **도구가 쓰는 LLM 을 안 센다.** `search_items` 는 `run_search` 를 돌리므로
   호출당 **2회**를 더 쓴다. 이쪽이 훨씬 크다.

실측(`gpt-5.4-mini-2026-03-17`, 복합 질의 3건 × 2회):

```
스텝별 도구 {1:1, 2:1, 3:1}  보고 4 / 실제 8
스텝별 도구 {1:1, 2:1}       보고 3 / 실제 5
스텝별 도구 {1:2, 2:2}       보고 5 / 실제 7   <- 병렬
```

**전부 과소였다.** 문서 세 곳이 못박은 범위 `1~6` 은 상한을 넘는 게 아니라
애초에 다른 것을 세고 있었다.

## 왜 공식을 고치지 않고 세는가

고치려면 "스텝 수 + 1 + 2 × search_items 성공 횟수" 같은 식이 되는데, 그건
**도구 내부 구현을 상류가 알고 있어야** 성립한다. 도구가 하나 늘거나
`run_search` 가 호출을 하나 더 쓰면 조용히 다시 틀린다 — 지금까지 세 번 그랬다
(ADR-0036 · 0039 · 0041 에서 손으로 고쳤다).

`metrics.py` 가 이미 답을 적어뒀다: *"`ai_llm_calls_total` 은 응답의 상수에서
오고 `ai_llm_provider_calls_total` 은 실제 호출을 센다 — **둘이 어긋나면 그
상수가 틀린 것**"*. 교차검증 장치는 있었고 **돌려본 적이 없었을 뿐**이다.

## 왜 ContextVar 에 **가변 객체**를 넣나

`asyncio.gather` 는 코루틴을 태스크로 감싸고, 태스크는 컨텍스트를 **복사**한다.
그래서 `ContextVar.set()` 을 자식에서 부르면 부모에 안 보인다 — 검색 파이프라인이
질의이해와 도메인 판정을 `gather` 로 던지므로 이 경로가 바로 그 경우다.
**객체를 담고 그 객체를 변형하면** 복사된 컨텍스트도 같은 객체를 가리키므로
증가가 보인다.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass
class LlmUsage:
    """이 스코프에서 나간 LLM 호출 수. **실패도 센다.**

    실패한 호출도 요금이 나갔을 수 있고, 무엇보다 *"공짜로 끝나는 경로"* 를
    만들지 않는다는 이 저장소의 규칙(`_ask_metered`)과 같은 방향이다.
    """

    calls: int = 0


_usage: ContextVar[LlmUsage | None] = ContextVar("llm_usage", default=None)


@contextmanager
def count_llm_calls() -> Iterator[LlmUsage]:
    """이 블록 안에서 나간 LLM 호출을 센다. 중첩하면 안쪽만 센다."""
    usage = LlmUsage()
    token = _usage.set(usage)
    try:
        yield usage
    finally:
        _usage.reset(token)


def note_call() -> None:
    """호출 1건. `metrics.record_llm_call()` 이 부른다 — 계측 지점을 늘리지 않는다."""
    usage = _usage.get()
    if usage is not None:
        usage.calls += 1
