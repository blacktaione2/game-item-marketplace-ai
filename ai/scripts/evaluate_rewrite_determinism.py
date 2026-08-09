"""같은 질의를 반복 재작성했을 때 결과가 얼마나 흔들리는지 측정한다.

실행:
  python -m scripts.evaluate_rewrite_determinism --temperature 1.0   # 기준선(예전 동작)
  python -m scripts.evaluate_rewrite_determinism --temperature 0.0   # 현재 기본값

## 왜 필요한가

`understand_query()`의 비결정성이 이 프로젝트에서 세 번 비용을 냈다.

- 리랭커 하한 캘리브레이션 불가 — 재작성 토큰 하나 차이로 점수가 1.31점 이동,
  같은 스크립트 두 번 실행에 홀드아웃 판정이 뒤집혔다(ADR-0014)
- 0건 판정을 캐시할 수 없음 — 판정 근거인 필터 추출이 흔들려서(ADR-0016)
- 모든 검색 품질 측정의 시행 간 분산

그런데 **지금까지 그 크기를 잰 적이 없다.** `"5만원 이하 검"`이 두 가지로
갈렸다는 n=2 일화가 전부였다. 이 스크립트가 비율로 만든다.

## 필터와 텍스트를 따로 잰다

하류 영향이 다르기 때문이다.

| 흔들리는 것 | 결과 |
|---|---|
| **필터** | 어떤 문서가 후보에 드는지가 바뀐다 → 0건 판정이 뒤집힌다 |
| **재작성 텍스트** | 순위·리랭커 점수가 흔들린다 |

합쳐서 하나의 숫자로 보고하면 어느 쪽이 문제인지 안 보인다.

## 텍스트는 두 기준으로 잰다

재작성 텍스트는 BM25(`query_text`)와 kNN(`query_vector`) **양쪽**에 들어간다.

- **토큰 집합** — BM25는 nori 분석 후 bag-of-words라 어순이 무관하다
- **문자열 완전 일치** — kNN 임베딩은 어순에 민감하다

문자열만 보면 불안정을 과대평가하고, 토큰집합만 보면 과소평가한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from app.core.config import get_settings
from app.corpus.rerank_floor_queries import FLOOR_QUERIES
from app.services.llm.openai_client import OpenAIClient
from app.services.search.query_understanding import understand_query

# 캐시 전제 조건 판정 기준 (계획 단계에서 합의)
FILTER_AGREEMENT_REQUIRED = 1.0
TOKEN_AGREEMENT_REQUIRED = 0.9


def mode_agreement(values: list[str]) -> float:
    """최빈값과 일치하는 실행의 비율."""
    return Counter(values).most_common(1)[0][1] / len(values)


async def measure(llm: OpenAIClient, query: str, runs: int) -> dict:
    """한 질의를 `runs` 회 돌려 재작성/필터의 흔들림을 모은다.

    ## 실패한 호출은 **결정적인 척한다** — 그래서 세어서 버린다

    `understand_query` 는 실패하면 예외를 안 내고 `rewritten_query=query`,
    빈 필터, `degraded=True` 로 **폴백**한다. 그 폴백은 매번 **글자까지 같다.**
    그래서 예전 판본처럼 `degraded` 를 안 보면,

      429 가 열 번 중 네 번 나면 그 네 번이 서로 완전히 일치하므로
      **모드 일치율이 올라간다** — 장애가 "더 결정적"으로 보인다.

    이 저장소는 이미 같은 함정을 반대 방향으로 겪었다(사례 19: 실패를 `is not
    False` 로 세어 미검출률이 63%로 뛴 건). 처방도 같다 — **분모에서 빼고, 실패가
    조금이라도 많으면 아예 채점하지 않는다.** 빼기만 하면 표본 절반이 사라진 채
    그럴듯한 숫자가 남는다.
    """
    filters: list[str] = []
    exact: list[str] = []
    tokens: list[str] = []
    degraded = 0

    for _ in range(runs):
        result = await understand_query(llm, query)
        if result.degraded:
            # 폴백은 측정값이 아니라 **결측**이다. 세어두고 버린다.
            degraded += 1
            continue
        # 키 순서에 영향받지 않도록 정렬해서 직렬화한다 — 같은 필터인데 다른
        # 문자열로 세면 불안정을 과대평가한다.
        filters.append(
            json.dumps(
                result.filters.model_dump(exclude_none=True),
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        text = result.rewritten_query.strip()
        exact.append(text)
        tokens.append(" ".join(sorted(set(text.split()))))

    return {
        "query": query,
        "filters": filters,
        "exact": exact,
        "tokens": tokens,
        "degraded": degraded,
        "runs": runs,
    }


#: 폴백이 이 비율을 넘으면 **채점하지 않는다.** 결측을 분모에서 빼기만 하면
#: 표본이 반쯤 사라진 채 그럴듯한 숫자가 남는다 (사례 19).
MAX_DEGRADED_RATE = 0.05

#: **질의 하나가 가져야 할 최소 유효 실행 비율.** 전체 비율만 보면 한 질의의
#: 붕괴를 다른 질의들이 희석한다 — 질의 10건 × 10회에서 한 질의가 **5회
#: 폴백이어도 전체는 정확히 5.0%** 라 통과했고, 그 질의는 남은 5개로
#: `mode_agreement` **1.00(만점)** 을 냈다. 결정성은 질의별로 재는 값이므로
#: 분모도 질의별로 지켜야 한다 (ADR-0049).
MIN_VALID_FRACTION_PER_QUERY = 0.8


def report(rows: list[dict], runs: int, temperature: float) -> bool:
    """표를 찍고 **채점이 유효한지**를 돌려준다."""
    total_runs = sum(r["runs"] for r in rows)
    total_degraded = sum(r["degraded"] for r in rows)
    rate = total_degraded / total_runs if total_runs else 0.0

    print(f"\n{'=' * 78}")
    print(f"질의 {len(rows)}건 × {runs}회, temperature={temperature}")
    # **판정이 쓴 값을 전부 출력한다.** 폴백 수를 안 찍으면, 장애로 올라간
    # 일치율과 진짜 결정성을 구별할 방법이 없다.
    print(f"폴백(degraded) {total_degraded}/{total_runs}회 = {rate:.1%}"
          f"  (상한 {MAX_DEGRADED_RATE:.0%})")
    print(f"{'=' * 78}")

    if rate > MAX_DEGRADED_RATE:
        print("\n  !! 폴백이 너무 많아 **채점하지 않는다.**")
        print("     폴백은 원본 질의를 그대로 돌려주므로 매번 동일하고,")
        print("     그대로 세면 장애가 '더 결정적'으로 보인다.")
        for row in rows:
            if row["degraded"]:
                print(f"       {row['query']}  {row['degraded']}/{row['runs']}회 폴백")
        return False

    # **질의별로도 본다.** 전체 비율은 한 질의의 붕괴를 다른 질의들이 희석한다 —
    # 질의 10건 × 10회에서 한 질의가 5회 폴백이어도 전체는 정확히 5.0% 라
    # 통과했고, 그 질의는 남은 5개로 `mode_agreement` **1.00** 을 냈다.
    starved = [
        f"{r['query']} ({len(r['filters'])}/{r['runs']}회 유효)"
        for r in rows
        if len(r["filters"]) < r["runs"] * MIN_VALID_FRACTION_PER_QUERY
    ]
    if starved:
        print("\n  !! 유효 표본이 모자란 질의가 있어 **채점하지 않는다** "
              f"(질의별 최소 {MIN_VALID_FRACTION_PER_QUERY:.0%}):")
        for entry in starved:
            print(f"       {entry}")
        print("     결정성은 질의별로 재는 값이라 분모도 질의별로 지켜야 한다.")
        return False

    print(
        f"{'질의':<22}{'폴백':>8}{'필터':>10}{'토큰집합':>10}{'문자열':>10}"
        f"{'필터일치':>10}{'토큰일치':>10}"
    )
    print("-" * 78)

    for row in rows:
        # **폴백 수를 채점된 표에도 찍는다.** 예전에는 거부할 때만 찍어서,
        # 통과한 실행에서는 어떤 질의가 몇 번 흔들렸는지 알 수 없었다.
        print(
            f"{row['query'][:20]:<22}"
            f"{row['degraded']:>6}회"
            f"{len(set(row['filters'])):>8}가지"
            f"{len(set(row['tokens'])):>8}가지"
            f"{len(set(row['exact'])):>8}가지"
            f"{mode_agreement(row['filters']):>10.2f}"
            f"{mode_agreement(row['tokens']):>10.2f}"
        )

    filter_agreements = [mode_agreement(r["filters"]) for r in rows]
    token_agreements = [mode_agreement(r["tokens"]) for r in rows]
    exact_agreements = [mode_agreement(r["exact"]) for r in rows]

    unstable_filters = [r["query"] for r in rows if len(set(r["filters"])) > 1]
    unstable_tokens = [r["query"] for r in rows if len(set(r["tokens"])) > 1]
    unstable_exact = [r["query"] for r in rows if len(set(r["exact"])) > 1]

    print("-" * 78)
    print(f"{'평균 모드 일치율':<24}"
          f"필터 {sum(filter_agreements) / len(rows):.3f}  "
          f"토큰집합 {sum(token_agreements) / len(rows):.3f}  "
          f"문자열 {sum(exact_agreements) / len(rows):.3f}")
    print(f"{'불안정 질의 수':<24}"
          f"필터 {len(unstable_filters)}/{len(rows)}  "
          f"토큰집합 {len(unstable_tokens)}/{len(rows)}  "
          f"문자열 {len(unstable_exact)}/{len(rows)}")

    if unstable_filters:
        # **em dash 를 쓰지 않는다.** cp949 콘솔에서 `UnicodeEncodeError` 로
        # **스크립트가 죽는다** — 표는 이미 다 나온 뒤라 결과를 잃지는 않지만
        # exit 1 이 되어 자동화가 실패로 읽는다. 이 파일은 Windows 에서 돈다.
        print("\n  필터가 흔들린 질의 - 0건 판정이 뒤집힐 수 있는 쪽:")
        for row in rows:
            if len(set(row["filters"])) > 1:
                print(f"    {row['query']}")
                for value, count in Counter(row["filters"]).most_common():
                    print(f"      {count}회  {value}")

    if unstable_tokens:
        print("\n  토큰 집합이 흔들린 질의 - 순위가 흔들리는 쪽:")
        for row in rows:
            if len(set(row["tokens"])) > 1:
                print(f"    {row['query']}")
                for value, count in Counter(row["exact"]).most_common():
                    print(f"      {count}회  \"{value}\"")

    # 캐시 전제 조건 판정
    filter_ok = min(filter_agreements) >= FILTER_AGREEMENT_REQUIRED
    token_ok = sum(token_agreements) / len(rows) >= TOKEN_AGREEMENT_REQUIRED
    print(f"\n{'=' * 78}\n재작성 캐싱 전제 조건\n{'=' * 78}")
    # **분모를 지어내지 않는다.** 예전에는 `{runs}/{runs} 동일` 이라고 찍었는데,
    # 폴백을 빼고 채점하므로 실제 분모는 질의마다 다를 수 있다.
    valid_min = min(len(r["filters"]) for r in rows)
    print(
        f"  필터 추출이 질의별 전 실행에서 동일 (최소 유효 {valid_min}회)   "
        f"{'충족' if filter_ok else '미충족'}  "
        f"(최저 {min(filter_agreements):.2f})"
    )
    print(
        f"  토큰집합 평균 모드 일치율 >= {TOKEN_AGREEMENT_REQUIRED}   "
        f"{'충족' if token_ok else '미충족'}  "
        f"({sum(token_agreements) / len(rows):.3f})"
    )
    print(
        "\n  => "
        + (
            "재작성 캐싱을 다음 단계로 검토 가능하다."
            if filter_ok and token_ok
            else "아직 재작성 캐싱을 검토할 단계가 아니다."
        )
    )
    print(
        "  주의: 이 기준이 충족돼도 **0건 판정 캐싱은 열리지 않는다.**\n"
        "        ADR-0016의 금지 이유 2(0건은 매물 하나만 등록돼도 거짓이 되는\n"
        "        가장 낡기 쉬운 답)는 재작성 결정성과 무관하게 남는다."
    )
    return True


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="생략하면 settings.openai_temperature. 기준선은 1.0(예전 동작)",
    )
    args = parser.parse_args()

    settings = get_settings()
    temperature = (
        args.temperature if args.temperature is not None else settings.openai_temperature
    )
    llm = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        temperature=temperature,
    )

    rows = [await measure(llm, q.query, args.runs) for q in FLOOR_QUERIES]
    if not report(rows, args.runs, temperature):
        # **채점 불가는 성공이 아니다.** exit 0 으로 끝내면 자동화도 사람도
        # "돌았고 괜찮았다"로 읽는다.
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
