"""내부 기능을 MCP 도구로 노출하는 서버.

Phase 3~5에서 만든 파이프라인을 감싸기만 한다 — 새 기능은 없다. 계획서의
"내부 역량을 MCP로 감싸되 외부 서비스 연동은 하지 않는다"는 스코프 그대로다.

## 도구 출력을 추리는 이유

각 파이프라인의 반환값은 HTTP 응답용이라 임베딩 순위, 타이밍, 전체 문서
본문까지 들어 있다. 그걸 그대로 넘기면 **LLM 컨텍스트를 낭비하고 비용이
불어난다.** MCP 도구의 반환값은 API 응답이 아니라 모델이 읽을 페이로드이므로
판단에 필요한 필드만 남긴다.

## 타임아웃 주의

클라이언트의 `read_timeout_seconds`는 async 대기로 구현돼 있어서, 도구가
**동기 CPU 작업으로 이벤트 루프를 막으면 제시간에 발동하지 못한다.**
임베딩 인코딩이 그 경우다(로드맵 기술 부채 항목). 그 항목을 해결하기 전까지
검색 도구의 타임아웃은 보장이 아니라 최선 노력이다.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from app.core.ids import IdSpace
from app.services.anomaly.pipeline import detect_trade
from app.services.forecast.pipeline import forecast_price
from app.services.search.es_client import get_es_client
from app.services.search.pipeline import search as run_search
from app.services.llm.dependencies import get_llm_client

SERVER_NAME = "gimp-marketplace-tools"


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        version="0.1.0",
        instructions=(
            "게임 아이템 거래소의 검색·시세예측·이상거래 탐지 도구입니다. "
            "시세 예측과 이상거래 탐지는 아이템 id나 거래 id가 필요하므로, "
            "id를 모르면 먼저 search_items로 찾으세요."
        ),
    )

    @server.tool(
        description=(
            "자연어로 아이템을 검색한다. 속성·가격·강화수치·레벨 조건을 "
            "질의에 그대로 담아도 된다(예: '3만원 이하 불속성 검'). "
            "아이템 id를 알아내는 용도로도 쓴다. "
            "반환되는 listing_price는 **판매자가 올린 등록가**이지 시세가 아니다 — "
            "시세나 가격 전망을 물었다면 여기서 얻은 item_id로 "
            "forecast_item_price를 이어서 호출할 것."
        )
    )
    async def search_items(
        tenant_code: str, query: str, size: int = 5
    ) -> list[dict[str, Any]]:
        result = await run_search(
            es=get_es_client(),
            llm_client=get_llm_client(),
            tenant_code=tenant_code,
            query=query,
            size=size,
        )
        return [_trim_item(doc) for doc in result["results"]]

    @server.tool(
        description=(
            "아이템의 향후 시세를 예측한다. 가격 전망·적정가·'오를까 내릴까' "
            "류의 질문에는 반드시 이 도구를 쓴다 — search_items가 주는 등록가는 "
            "시세가 아니다. 거래 이력이 부족하면 유사 아이템의 추세를 "
            "물려받아(Cold Start) 예측하며 그 경우 inherited_from에 출처가 담긴다."
        )
    )
    async def forecast_item_price(
        tenant_code: str, item_id: int, horizon_days: int = 7
    ) -> dict[str, Any]:
        result = await forecast_price(
            es=get_es_client(),
            tenant_code=tenant_code,
            item_id=item_id,
            horizon=horizon_days,
        )
        return {
            "item_id": result["item_id"],
            "name": result["name"],
            # 예측의 기준 가격. search_items의 listing_price와 **다른 기준**이라
            # 무엇을 기준으로 잡았는지 같이 알려줘야 모델이 둘을 섞지 않는다.
            "baseline_price": result["anchor_price"],
            "baseline_source": _baseline_source(result),
            "expected_change_pct": result["expected_change_pct"],
            "cold_start": result["cold_start"],
            "history_days": result["history_days"],
            # 일자별 전체가 아니라 시작/끝만 — 추세 판단에는 그걸로 충분하다.
            "forecast_first": result["forecast"][0],
            "forecast_last": result["forecast"][-1],
            "inherited_from": [
                {"name": source["name"], "weight": source["weight"]}
                for source in result["inherited_from"]
            ],
        }

    @server.tool(
        description=(
            "거래 1건이 이상거래인지 판정하고 근거를 피처 기여도로 설명한다. "
            "거래 id가 필요하다. **합성 데모 거래(id 1~26,702)만 조회한다** — "
            "실제 백엔드 거래는 아직 연동돼 있지 않으며 두 id 범위가 겹치므로, "
            "사용자가 자기 거래 번호를 말한 것이라면 그 점을 답변에 밝혀야 한다."
        )
    )
    async def detect_trade_anomaly(tenant_code: str, trade_id: int) -> dict[str, Any]:
        # id_space를 LLM이 고르게 두지 않는다. 이 도구가 볼 수 있는 평면은
        # 하나뿐이고, 모델이 추측하면 그게 곧 조용한 오답이 된다.
        result = detect_trade(tenant_code, trade_id, IdSpace.SYNTHETIC)
        return {
            "trade_id": result["trade_id"],
            "item_id": result["item_id"],
            "is_anomaly": result["is_anomaly"],
            "anomaly_score": result["anomaly_score"],
            "threshold": result["threshold"],
            "price_ratio": result["price_ratio"],
            "contributions": result["contributions"],
        }

    return server


def _trim_item(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": doc["item_id"],
        "name": doc["name"],
        "category": doc["category"],
        # 세부 종류를 같이 준다. 이름만 보고 모델이 활을 검이라고 단정한
        # 전례가 있다 — 종류를 명시하면 스스로 검증할 수 있다.
        "subcategory": doc["subcategory"],
        # 속성도 같은 이유로 준다. `"불속성 검"`에서 모델이 이름만 보고
        # 속성을 추측할 근거가 없었다. "무속성"은 값이 없는 게 아니라
        # 속성이 없다는 뜻이다.
        "element": doc["element"],
        # 그냥 price로 주면 모델이 forecast의 기준가와 같은 것으로 착각하고
        # 둘을 직접 비교해 잘못된 등락 결론을 낸다(실제로 관측됨). 이름으로
        # 기준을 못박는다.
        "listing_price": doc["price"],
        "enhancement_level": doc["enhancement_level"],
        "required_level": doc["required_level"],
        "sale_type": doc["sale_type"],
    }


def _baseline_source(result: dict[str, Any]) -> str:
    """예측 기준가가 어디서 왔는지 — 경로마다 다르다."""
    if not result["cold_start"]:
        return "최근 체결가"
    if result["history_days"] > 0:
        return f"관측된 거래 {result['history_days']}건의 평균"
    return "등록가(거래 이력 없음)"
