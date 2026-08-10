"""에이전트가 답변이 가리키는 아이템을 실제로 붙잡는가.

**이 경로는 한 번도 동작한 적이 없었다.** `_remember_item` 은 파싱에 실패하면
조용히 넘기도록 만들어져 있는데(부가 정보라서 맞는 판단이다), 넘기는 조건이
`json.loads` 결과가 리스트가 아닐 때였다. 그런데 MCP 직렬화 때문에 **리스트로
오는 경우가 애초에 없었다** — 항목 하나면 pretty-print 된 객체 하나, 여럿이면
객체들이 개행으로 이어 붙은 것(유효한 JSON 도 아니다).

그래서 복합 질의의 `resolved_item` 이 늘 `None` 이었고, 화면에 "이 답변이
가리키는 아이템" 카드가 뜬 적이 없다. 예외도 로그도 없었다 — **조용한 경로는
테스트가 없으면 죽어 있어도 모른다.**

실제 배포에서 확인한 모양을 그대로 표본으로 쓴다.
"""

from __future__ import annotations

import json

from app.services.agent.agent import _parse_items, _remember_item, _resolve_item


class _Call:
    def __init__(self, name: str) -> None:
        self.name = name


ITEM = {
    "item_id": 24,
    "name": "불꽃의 대검",
    "category": "무기",
    "subcategory": "검",
    "element": "화염",
    "listing_price": 143000,
    "enhancement_level": 0,
    "required_level": 120,
    "sale_type": "AUCTION",
}


class TestParseItems:
    def test_배포에서_실제로_오는_모양_객체_하나(self):
        """**이게 회귀 표본이다.** `size=3` 을 줘도 결과가 하나면 이 모양이 온다."""
        text = json.dumps(ITEM, ensure_ascii=False, indent=2)
        assert _parse_items(text) == [ITEM]

    def test_여러_객체가_개행으로_이어_붙은_모양(self):
        # MCP 는 원소마다 블록을 만들고 call_tool_text 가 개행으로 잇는다.
        # 이건 **유효한 JSON 이 아니다** — json.loads 로는 못 읽는다.
        second = {**ITEM, "item_id": 12, "name": "+9 강화 롱소드"}
        text = "\n".join(
            json.dumps(entry, ensure_ascii=False, indent=2) for entry in (ITEM, second)
        )
        try:
            json.loads(text)
            raise AssertionError("표본이 유효한 JSON 이면 이 검사는 공허하다")
        except json.JSONDecodeError:
            pass
        assert [entry["item_id"] for entry in _parse_items(text)] == [24, 12]

    def test_배열로_와도_받는다(self):
        # 직렬화 방식이 바뀌어도 계속 동작해야 한다 — 한 모양만 가정하지 않는다.
        assert _parse_items(json.dumps([ITEM], ensure_ascii=False)) == [ITEM]

    def test_JSON_이_아니면_빈_목록(self):
        # 도메인 밖일 때 도구는 문장을 준다(ADR-0039). 터지면 안 된다.
        assert _parse_items("이 질의는 거래소가 다루는 범위 밖입니다.") == []

    def test_앞부분만_읽히면_그만큼은_살린다(self):
        text = json.dumps(ITEM, ensure_ascii=False) + "\n뒤에 붙은 설명 문장"
        assert [entry["item_id"] for entry in _parse_items(text)] == [24]


class TestRememberItem:
    def test_검색_결과를_id_로_색인한다(self):
        seen: dict[int, dict] = {}
        _remember_item(_Call("search_items"), json.dumps(ITEM, ensure_ascii=False), seen)
        assert seen[24]["name"] == "불꽃의 대검"

    def test_다른_도구는_무시한다(self):
        # forecast_item_price 의 출력에는 item_id 가 있지만 검색 결과가 아니다 —
        # 화면 카드는 검색이 돌려준 항목 모양이어야 한다.
        seen: dict[int, dict] = {}
        _remember_item(
            _Call("forecast_item_price"), json.dumps(ITEM, ensure_ascii=False), seen
        )
        assert seen == {}

    def test_item_id_가_없는_항목은_건너뛴다(self):
        # 도메인 밖 안내(`note` 하나짜리 dict)가 그 경우다.
        seen: dict[int, dict] = {}
        _remember_item(
            _Call("search_items"), json.dumps({"note": "범위 밖입니다"}), seen
        )
        assert seen == {}

    def test_옛_판본이라면_실패한다(self):
        """**공허 방지 — 옛 구현과 현재 구현을 같은 표본에 나란히 돌린다.**

        옛 구현(`리스트가 아니면 포기`)만 단언하면 *표본이 구별된다*는 것만
        보인다. **현재 구현이 그 표본에서 실제로 다르게 동작하는지**는 별개이고,
        그게 이 검사가 지켜야 할 것이다 (ADR-0056). 사례 48 과 같은 이유로
        한 검사 안에서 양쪽을 돌린다.
        """
        text = json.dumps(ITEM, ensure_ascii=False, indent=2)

        def old_implementation(raw: str) -> dict[int, dict]:
            seen: dict[int, dict] = {}
            try:
                items = json.loads(raw)
            except (TypeError, ValueError):
                return seen
            if not isinstance(items, list):
                return seen
            for item in items:
                seen[item["item_id"]] = item
            return seen

        now: dict[int, dict] = {}
        _remember_item(_Call("search_items"), text, now)

        assert old_implementation(text) == {}, "옛 구현이 통과하면 표본이 잘못됐다"
        assert list(now) == [ITEM["item_id"]], "현재 구현은 같은 표본에서 읽어내야 한다"
        assert old_implementation(text) != now, "둘이 같으면 이 표본은 아무것도 구별하지 못한다"


class TestResolveItem:
    """카드가 뜨기 시작했으니, **무엇을 가리키는지**가 비로소 문제가 된다.

    `_remember_item` 이 고쳐지기 전에는 `resolved_item` 이 늘 `None` 이라 이
    로직이 한 번도 실행되지 않았다. 이제 실행되므로 여기에 검사가 필요하다.
    """

    SECOND = {**ITEM, "item_id": 12, "name": "+9 강화 롱소드"}

    def test_예측이_고른_아이템을_돌려준다(self):
        seen = {24: ITEM, 12: self.SECOND}
        assert _resolve_item(12, seen)["name"] == "+9 강화 롱소드"

    def test_고른_것이_없으면_검색한_것_중_첫_번째(self):
        seen = {24: ITEM, 12: self.SECOND}
        assert _resolve_item(None, seen)["item_id"] == 24

    def test_검색도_예측도_없으면_없음(self):
        assert _resolve_item(None, {}) is None

    def test_예측이_고른_id_를_못_찾으면_다른_것을_주지_않는다(self):
        """**이게 이번에 고친 것이다.**

        모델이 검색을 거치지 않은 id 로 예측하면 — 질의에 적힌 번호를 그대로
        쓰거나 지어낸 경우 — 예전 판본은 "검색한 것 중 첫 번째"로 내려갔다.
        답변은 99번을 말하는데 카드는 24번을 가리킨다. 사용자가 확인할 방법이
        없는 채로 그럴듯하므로, **빈 카드가 맞다.**
        """
        seen = {24: ITEM, 12: self.SECOND}
        assert _resolve_item(99, seen) is None

    def test_옛_폴백이라면_엉뚱한_것을_준다(self):
        """**공허 방지 — 옛 판본과 현재 판본을 같은 표본에 나란히 돌린다.**

        옛 구현만 단언하면 *표본이 구별된다*는 것만 보인다. 현재 구현이 그
        표본에서 실제로 다르게 답하는지는 별개이고, 그게 이 검사가 지켜야 할
        것이다 (ADR-0056, 사례 48 과 같은 이유).
        """
        seen = {24: ITEM, 12: self.SECOND}

        def old_implementation(focus_id, seen_items):
            resolved = seen_items.get(focus_id) if focus_id is not None else None
            if resolved is None and seen_items:
                resolved = next(iter(seen_items.values()))
            return resolved

        old = old_implementation(99, seen)
        now = _resolve_item(99, seen)

        assert old is not None and old["item_id"] == 24, (
            "옛 구현이 None 을 주면 표본이 잘못됐다 — 구별할 것이 없다"
        )
        assert now is None, "현재 판본은 빈 카드를 줘야 한다"
        assert old != now, "둘이 같으면 이 표본은 아무것도 구별하지 못한다"
