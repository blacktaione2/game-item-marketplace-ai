"""더미 아이템 데이터를 테넌트 인덱스에 색인한다.

아이템 정의는 app/corpus/ 로 옮겼다 (학습/평가 분리 때문).
색인은 train + eval 전체를 넣는다 — 평가 질의가 현실적인 방해 문서들 사이에서
정답을 찾아야 의미 있는 측정이 되기 때문이다.

실행: python -m scripts.seed_items          (기본 테넌트 nexon)
      python -m scripts.seed_items --tenant ncsoft --recreate
"""

from __future__ import annotations

import argparse
import asyncio

from app.corpus import ALL_ITEMS as ITEMS
from app.core.config import get_settings
from app.services.search.es_client import get_es_client
from app.services.search.indexer import index_items
from app.services.search.mapping import index_name


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="nexon")
    parser.add_argument(
        "--recreate", action="store_true", help="기존 인덱스를 지우고 다시 만든다"
    )
    args = parser.parse_args()

    es = get_es_client()
    try:
        name = index_name(get_settings().index_prefix, args.tenant)
        if args.recreate and await es.indices.exists(index=name):
            await es.indices.delete(index=name)
            print(f"기존 인덱스 삭제: {name}")

        count = await index_items(es, args.tenant, ITEMS)
        print(f"색인 완료: {name} <- {count}건")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())
