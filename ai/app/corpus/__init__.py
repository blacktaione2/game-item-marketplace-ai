"""아이템 코퍼스와 train/eval 분리.

임포트 시점에 train/eval이 겹치지 않는지 검증한다. 평가셋 오염은 조용히
일어나서 수치만 부풀리기 때문에, 나중에 아이템을 추가하다 실수하면 그 자리에서
터지게 하는 편이 낫다.
"""

from app.corpus.eval_items import EVAL_ITEMS
from app.corpus.train_items import TRAIN_ITEMS

ALL_ITEMS = [*TRAIN_ITEMS, *EVAL_ITEMS]


def _assert_disjoint() -> None:
    train_ids = {i["item_id"] for i in TRAIN_ITEMS}
    eval_ids = {i["item_id"] for i in EVAL_ITEMS}
    overlap_ids = train_ids & eval_ids
    if overlap_ids:
        raise ValueError(f"train/eval item_id가 겹칩니다: {sorted(overlap_ids)}")

    train_names = {i["name"].strip() for i in TRAIN_ITEMS}
    eval_names = {i["name"].strip() for i in EVAL_ITEMS}
    overlap_names = train_names & eval_names
    if overlap_names:
        raise ValueError(f"train/eval 아이템 이름이 겹칩니다: {sorted(overlap_names)}")

    if len(train_ids) != len(TRAIN_ITEMS):
        raise ValueError("TRAIN_ITEMS 안에 중복 item_id가 있습니다")
    if len(eval_ids) != len(EVAL_ITEMS):
        raise ValueError("EVAL_ITEMS 안에 중복 item_id가 있습니다")


# 하드 필터가 걸리는 필드들. 값이 빠진 아이템은 그 필터가 걸린 검색에서
# **영구히 안 보인다** — 에러도 안 나고 결과에서 조용히 사라진다.
# `element`는 특히 함정이다: 속성이 없는 아이템도 `"무속성"`이라는 값을 가져야
# 하고, 비워두면 `"무속성 검"` 검색에서 사라진다.
HARD_FILTER_FIELDS = ("subcategory", "element")


def _assert_hard_filter_fields_present() -> None:
    for field in HARD_FILTER_FIELDS:
        missing = [item["item_id"] for item in ALL_ITEMS if not item.get(field)]
        if missing:
            raise ValueError(
                f"{field}가 없는 아이템이 있습니다: {sorted(missing)}. "
                f"{field} 필터가 걸린 검색에서 조용히 제외되므로 반드시 "
                "채워야 합니다."
            )


_assert_disjoint()
_assert_hard_filter_fields_present()

__all__ = ["TRAIN_ITEMS", "EVAL_ITEMS", "ALL_ITEMS"]
