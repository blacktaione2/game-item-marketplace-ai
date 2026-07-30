"""평가 전용 질의셋을 만든다 (아이템당 검색어 N개 + 정답 item_id).

**학습 데이터가 아니다.** 여기서 나온 질의는 파인튜닝에 절대 쓰지 않고
평가에만 쓴다. 대상도 EVAL_ITEMS뿐이다.

실행: python -m scripts.generate_eval_queries
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path

from app.corpus import EVAL_ITEMS
from app.services.llm.dependencies import get_llm_client

_PROMPT = """당신은 게임 아이템 거래소의 검색 품질을 평가할 테스트 질의를 만드는 도우미입니다.
아래 아이템을 찾으려고 사용자가 실제로 칠 법한 한국어 검색어 {n}개를 만들어,
JSON 객체 하나로만 출력하세요. 설명이나 코드블록은 쓰지 마세요.

{{"queries": ["검색어1", "검색어2", ...]}}

규칙:
- 아이템 이름을 그대로 베끼지 마세요. 사용자는 정확한 이름을 모릅니다.
- 스타일을 섞으세요: 짧은 키워드형, 자연어 문장형, 약어/은어형.
- 아이템의 실제 특성(용도, 직업, 속성, 강화 수치, 레벨대, 가격대)에 근거해야 합니다.
- 이 아이템을 다른 아이템과 구분지어 주는 특징을 최소 하나는 담으세요.

아이템:
이름: {name}
설명: {description}
카테고리: {category} / 판매방식: {sale_type} / 가격: {price}원
강화수치: +{enhancement_level} / 요구레벨: {required_level}"""


def _parse(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    payload = json.loads(text[start : end + 1])
    return [q.strip() for q in payload.get("queries", []) if q and q.strip()]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/eval_queries.jsonl")
    parser.add_argument("--queries-per-item", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    llm = get_llm_client()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(item: dict):
        async with semaphore:
            prompt = _PROMPT.format(
                n=args.queries_per_item,
                name=item["name"],
                description=item.get("description", ""),
                category=item.get("category", ""),
                sale_type=item.get("sale_type", ""),
                price=int(item.get("price", 0)),
                enhancement_level=item.get("enhancement_level", 0),
                required_level=item.get("required_level", 0),
            )
            try:
                return item["item_id"], _parse(await llm.complete(prompt))
            except Exception as e:
                print(f"  질의 생성 실패 item_id={item['item_id']}: {e}")
                return item["item_id"], []

    print(f"평가 질의 생성: EVAL_ITEMS {len(EVAL_ITEMS)}건 x {args.queries_per_item}개")
    results = dict(await asyncio.gather(*(one(i) for i in EVAL_ITEMS)))

    rows = []
    for item in EVAL_ITEMS:
        for query in results.get(item["item_id"], [])[: args.queries_per_item]:
            rows.append(
                {
                    "query": query,
                    "gold_item_id": item["item_id"],
                    "gold_name": item["name"],
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    covered = len({r["gold_item_id"] for r in rows})
    print(f"생성 완료: {out_path}  질의 {len(rows)}건 / 아이템 커버리지 {covered}/{len(EVAL_ITEMS)}")
    if covered < len(EVAL_ITEMS):
        missing = {i["item_id"] for i in EVAL_ITEMS} - {r["gold_item_id"] for r in rows}
        print(f"경고: 질의가 없는 아이템 {sorted(missing)}")


if __name__ == "__main__":
    asyncio.run(main())
