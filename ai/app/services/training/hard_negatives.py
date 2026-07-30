"""임베딩 파인튜닝용 (anchor, positive, negative) 트리플 자동 생성.

**왜 규칙 + LLM 하이브리드인가**

계획서는 "hard negative 페어를 LLM으로 자동 생성"이라고 적혀 있지만, 실제
데이터를 보면 `+7/+8/+9 강화 롱소드`처럼 **강화 수치만 다른 쌍은 구조화
필드(enhancement_level)로 결정론적으로 뽑아낼 수 있다.** 이건 LLM에 맡길
이유가 없다 — 공짜이고, 재현 가능하고, 100% 정확하다.

반대로 LLM이 아니면 못 만드는 것도 있다:
  - 사용자가 실제로 칠 법한 검색어(anchor). 아이템명을 그대로 치는 사용자는 없다
  - "불속성 검 vs 얼음속성 검"처럼 **속성/직업/티어만 뒤바뀐** 의미적 near-miss.
    구조화 필드에 속성 정보가 없으므로 규칙으로는 못 만든다

그래서 역할을 나눈다:
  - 규칙: 강화 수치 형제(structural), 같은 카테고리 내 최대 텍스트 중첩(corpus)
  - LLM: anchor 질의 생성 + 의미적 hard negative(synthetic)

Hard negative는 가능하면 **코퍼스에 실재하는 아이템**을 쓴다. 모델이 실제
카탈로그 안에서 헷갈리는 쌍을 구분하도록 학습시키는 게 목적이기 때문이다.
구조적 형제가 없는 아이템만 LLM 합성 negative로 채운다.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

from app.services.llm.base import LLMClient

logger = logging.getLogger(__name__)

_ENHANCE_PREFIX = re.compile(r"^\+\d+\s*(강화\s*)?")


def base_name(name: str) -> str:
    """'+9 강화 롱소드' -> '롱소드'. 강화 수치를 뗀 아이템 원형."""
    return _ENHANCE_PREFIX.sub("", name).strip()


def item_text(item: dict[str, Any]) -> str:
    """색인/학습에 쓰는 아이템 텍스트 표현.

    indexer.embedding_text()와 같은 형태를 유지해야 학습과 서빙이 어긋나지
    않는다.
    """
    return f"{item.get('name', '')} {item.get('description', '')}".strip()


def char_bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"\s+", "", text)
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def text_similarity(a: str, b: str) -> float:
    """문자 bigram Jaccard. hard negative가 정말 '표면적으로 비슷한지' 검증용."""
    ba, bb = char_bigrams(a), char_bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


@dataclass
class Triplet:
    anchor: str
    positive: str
    negative: str
    negative_type: str  # structural | corpus | synthetic | easy
    item_id: int
    # 품질 리포트를 이름 대 이름으로 공정하게 비교하기 위해 따로 보관한다.
    # positive는 "이름 + 설명"이고 synthetic negative는 이름뿐이라, 전체
    # 텍스트끼리 비교하면 길이 차 때문에 유사도가 과소평가된다.
    positive_name: str = ""
    negative_name: str = ""
    negative_item_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "positive": self.positive,
            "negative": self.negative,
            "negative_type": self.negative_type,
            "item_id": self.item_id,
            "negative_item_id": self.negative_item_id,
        }


def mine_structural_negatives(
    items: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """강화 수치만 다른 형제 아이템. 계획서가 지목한 바로 그 '+8 vs +9' 케이스."""
    by_base: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_base.setdefault(base_name(item["name"]), []).append(item)

    result: dict[int, list[dict[str, Any]]] = {}
    for siblings in by_base.values():
        if len(siblings) < 2:
            continue
        for item in siblings:
            others = [
                s
                for s in siblings
                if s["item_id"] != item["item_id"]
                and s.get("enhancement_level") != item.get("enhancement_level")
            ]
            if others:
                result[item["item_id"]] = others
    return result


def mine_corpus_negatives(
    items: list[dict[str, Any]], top_k: int = 2
) -> dict[int, list[dict[str, Any]]]:
    """같은 카테고리 안에서 텍스트가 가장 많이 겹치는 다른 아이템.

    강화 형제가 없는 아이템에도 '실재하는 아이템' negative를 주기 위한 폴백.
    같은 카테고리라 카테고리 필터로는 못 거르고, 텍스트가 겹치므로 BM25로도
    혼동되기 쉬운 — 즉 충분히 어려운 negative다.
    """
    result: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        same_category = [
            other
            for other in items
            if other["item_id"] != item["item_id"]
            and other["category"] == item["category"]
        ]
        if not same_category:
            continue
        scored = sorted(
            same_category,
            key=lambda o: text_similarity(item_text(item), item_text(o)),
            reverse=True,
        )
        result[item["item_id"]] = scored[:top_k]
    return result


def pick_easy_negative(
    item: dict[str, Any], items: list[dict[str, Any]], rng: random.Random
) -> dict[str, Any] | None:
    """다른 카테고리에서 무작위. hard negative와 대비되는 기준선 역할."""
    candidates = [o for o in items if o["category"] != item["category"]]
    return rng.choice(candidates) if candidates else None


_PROMPT = """당신은 게임 아이템 거래소의 검색 품질을 개선하기 위한 학습 데이터를 만드는 도우미입니다.
아래 아이템 하나를 보고, JSON 객체 하나만 출력하세요. 설명이나 코드블록은 쓰지 마세요.

{{
  "queries": ["이 아이템을 찾으려고 사용자가 실제로 칠 법한 한국어 검색어 {n_queries}개"],
  "hard_negatives": ["이 아이템과 아주 비슷해 보이지만 검색 결과로는 '틀린' 가상의 아이템 이름 {n_negatives}개"]
}}

queries 작성 규칙:
- 아이템 이름을 그대로 베끼지 마세요. 실제 사용자는 정확한 이름을 모릅니다.
- 스타일을 섞으세요: 짧은 키워드형("9강 롱소드"), 자연어 문장형("공격력 높은 강화검 있나요"),
  약어/은어형("렙100 활", "만렙 무기").
- 아이템의 실제 속성(용도, 직업, 속성, 강화 수치, 가격대)에 근거해야 합니다.

hard_negatives 작성 규칙:
- **아주 조금만 다르게** 만드세요. 완전히 다른 아이템은 쓸모없습니다.
- 좋은 변형 축: 속성 교체(불->얼음), 직업 교체(마법사->궁수),
  강화 수치 교체(+9->+6), 등급/티어 교체(전설->희귀).
- 사람이 봤을 때 "이건 검색 결과로 나오면 안 되는데 이름은 비슷하네" 싶어야 합니다.

예시 입력: "+9 강화 롱소드 / 공격력 +120, 치명타 확률 8% 증가"
예시 출력: {{"queries": ["9강 롱소드", "치명타 붙은 강화 검", "공격력 높은 롱소드 삽니다"], "hard_negatives": ["+6 강화 롱소드", "+9 강화 숏소드", "+9 강화 롱보우"]}}

대상 아이템:
이름: {name}
설명: {description}
카테고리: {category} / 판매방식: {sale_type} / 가격: {price}원
강화수치: +{enhancement_level} / 요구레벨: {required_level}"""


async def generate_llm_pairs(
    llm: LLMClient, item: dict[str, Any], n_queries: int, n_negatives: int
) -> tuple[list[str], list[str]]:
    """아이템 하나당 LLM 1회 호출로 anchor 질의와 합성 hard negative를 함께 받는다."""
    prompt = _PROMPT.format(
        n_queries=n_queries,
        n_negatives=n_negatives,
        name=item["name"],
        description=item.get("description", ""),
        category=item.get("category", ""),
        sale_type=item.get("sale_type", ""),
        price=int(item.get("price", 0)),
        enhancement_level=item.get("enhancement_level", 0),
        required_level=item.get("required_level", 0),
    )
    try:
        raw = await llm.complete(prompt)
        payload = _parse_json(raw)
        queries = [q.strip() for q in payload.get("queries", []) if q and q.strip()]
        negatives = [
            n.strip() for n in payload.get("hard_negatives", []) if n and n.strip()
        ]
        return queries[:n_queries], negatives[:n_negatives]
    except Exception:
        logger.warning("LLM 페어 생성 실패 (item_id=%s)", item.get("item_id"), exc_info=True)
        return [], []


def _parse_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSON을 찾을 수 없습니다: {raw[:200]}")
    return json.loads(text[start : end + 1])


def sanitize_synthetic(
    item: dict[str, Any],
    synthetic: list[str],
    items_by_name: dict[str, dict[str, Any]],
    forbidden_names: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """LLM이 만든 합성 negative를 검수한다.

    세 가지를 잡는다:
      1. **자기 자신을 negative로 뱉은 경우** — 정답을 오답으로 가르치는
         false negative가 되므로 반드시 버려야 한다. LLM에게 "아주 조금만
         다르게" 만들라고 시켰으니 충분히 나올 수 있는 사고다.
      2. **실재하는 코퍼스 아이템과 이름이 같은 경우** — 버릴 게 아니라
         오히려 좋다. 실제 카탈로그 아이템으로 승격시켜서 전체 텍스트와
         item_id를 붙여주면 근거 있는 negative가 된다.
      3. **평가 전용 아이템 이름과 겹치는 경우**(`forbidden_names`) — 폐기한다.
         홀드아웃 아이템 이름이 학습 데이터에 negative로 들어가면, 모델이 그
         이름을 "오답"으로 밀어내도록 학습해서 평가가 부당하게 불리해진다.
         수치가 부풀려지는 방향은 아니지만 어느 쪽이든 오염은 오염이다.

    `items_by_name`에는 **학습용 아이템만** 넘겨야 한다. 평가 아이템이 들어가면
    2번 승격 경로로 홀드아웃 텍스트가 학습셋에 흘러든다.
    """
    own = normalize_name(item["name"])
    forbidden = forbidden_names or set()
    kept: list[str] = []
    promoted: list[dict[str, Any]] = []
    dropped = 0

    for text in synthetic:
        key = normalize_name(text)
        if key == own or key in forbidden:
            dropped += 1
            continue
        matched = items_by_name.get(key)
        if matched is not None:
            if matched["item_id"] != item["item_id"]:
                promoted.append(matched)
            else:
                dropped += 1
            continue
        kept.append(text)

    return kept, promoted, dropped


def build_triplets(
    item: dict[str, Any],
    queries: list[str],
    synthetic_negatives: list[str],
    structural: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    easy: dict[str, Any] | None,
    include_easy: bool,
    promoted: list[dict[str, Any]] | None = None,
) -> list[Triplet]:
    """질의 하나당 negative 하나씩 붙여 트리플을 만든다.

    negative 우선순위: structural > corpus > synthetic.
    실재 아이템을 우선 쓰되, 없으면 LLM 합성으로 채운다.
    """
    positive = item_text(item)
    positive_name = item["name"]
    triplets: list[Triplet] = []

    grounded: list[tuple[str, dict[str, Any]]] = [("structural", s) for s in structural]
    grounded += [("corpus", c) for c in (promoted or [])]
    grounded += [("corpus", c) for c in corpus]

    for i, query in enumerate(queries):
        if grounded:
            kind, neg_item = grounded[i % len(grounded)]
            triplets.append(
                Triplet(
                    anchor=query,
                    positive=positive,
                    negative=item_text(neg_item),
                    negative_type=kind,
                    item_id=item["item_id"],
                    positive_name=positive_name,
                    negative_name=neg_item["name"],
                    negative_item_id=neg_item["item_id"],
                )
            )
        if synthetic_negatives:
            synthetic = synthetic_negatives[i % len(synthetic_negatives)]
            triplets.append(
                Triplet(
                    anchor=query,
                    positive=positive,
                    negative=synthetic,
                    negative_type="synthetic",
                    item_id=item["item_id"],
                    positive_name=positive_name,
                    negative_name=synthetic,
                )
            )

    if include_easy and easy is not None and queries:
        triplets.append(
            Triplet(
                anchor=queries[0],
                positive=positive,
                negative=item_text(easy),
                negative_type="easy",
                item_id=item["item_id"],
                positive_name=positive_name,
                negative_name=easy["name"],
                negative_item_id=easy["item_id"],
            )
        )

    return triplets


def quality_report(triplets: list[Triplet]) -> dict[str, Any]:
    """negative 유형별 (positive, negative) 표면 유사도.

    hard negative가 easy negative보다 유사도가 확실히 높아야 '어렵다'는 주장이
    성립한다. 이 수치가 뒤집히면 생성 로직이 잘못된 것이다.

    두 가지 척도를 함께 낸다. 하나만 보면 유형별로 유불리가 갈리기 때문이다:
      - **이름 유사도**: "+8 vs +9 롱소드"류를 잡는다. synthetic negative는
        이름만 있으므로 이 척도가 공정하다.
      - **전체 텍스트 유사도**: 설명문까지 포함. corpus negative는 같은
        카테고리의 설명 어휘가 겹쳐서 어려운 것이므로 이 척도가 공정하다.
    """
    by_type: dict[str, list[tuple[float, float]]] = {}
    for t in triplets:
        name_sim = text_similarity(t.positive_name or t.positive, t.negative_name or t.negative)
        full_sim = text_similarity(t.positive, t.negative)
        by_type.setdefault(t.negative_type, []).append((name_sim, full_sim))

    stats = {
        kind: {
            "count": len(values),
            "avg_name_similarity": round(sum(v[0] for v in values) / len(values), 4),
            "avg_full_similarity": round(sum(v[1] for v in values) / len(values), 4),
        }
        for kind, values in sorted(by_type.items())
    }
    return {"total": len(triplets), "by_negative_type": stats}
