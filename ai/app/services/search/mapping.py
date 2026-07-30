"""게임사(테넌트)별 인덱스 매핑 정의.

인덱스는 테넌트당 하나(`items-{tenant_code}`)로 물리 분리하지 않고 논리
분리한다 — 계획서의 index-per-tenant 전략. 테넌트마다 shard/replica를
독립적으로 줄 수 있도록 생성 시 인자로 받는다.
"""

from typing import Any

# nori 형태소 분석기 기반 한국어 분석기.
# decompound_mode=mixed: 복합어를 원형과 구성요소 둘 다 색인해서
# "롱소드"와 "소드" 양쪽 질의에 걸리게 한다.
_ANALYSIS = {
    "tokenizer": {
        "korean_nori_tokenizer": {
            "type": "nori_tokenizer",
            "decompound_mode": "mixed",
        }
    },
    "analyzer": {
        "korean": {
            "type": "custom",
            "tokenizer": "korean_nori_tokenizer",
            "filter": ["nori_part_of_speech", "lowercase"],
        }
    },
}


def index_name(prefix: str, tenant_code: str) -> str:
    return f"{prefix}-{tenant_code}"


def build_index_body(
    embedding_dims: int,
    number_of_shards: int = 1,
    number_of_replicas: int = 0,
) -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": number_of_shards,
            "number_of_replicas": number_of_replicas,
            "analysis": _ANALYSIS,
        },
        "mappings": {
            "properties": {
                "item_id": {"type": "long"},
                # 인덱스가 이미 테넌트별로 분리돼 있어도 tenant_id를 문서에
                # 남긴다 — 색인 사고(다른 테넌트 문서 혼입) 검증용.
                "tenant_id": {"type": "long"},
                "name": {
                    "type": "text",
                    "analyzer": "korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "description": {"type": "text", "analyzer": "korean"},
                "category": {"type": "keyword"},
                # 세부 종류(검/활/지팡이/갑옷/반지…). category가 `무기`까지만
                # 구분해서 "검 찾아줘"에 활이 섞이던 문제를 하드 필터로 막는다.
                # 임계값이 없다는 게 이 필드의 요점이다 — 리랭커 점수 하한은
                # LLM 재작성 노이즈 때문에 캘리브레이션이 불가능했다(ADR-0014).
                "subcategory": {"type": "keyword"},
                # 속성(화염/냉기/번개/암흑/신성/무속성). subcategory와 같은
                # 이유로 필요하다 — `"불속성 검"`이 종류만 맞고 속성은 안 맞는
                # 결과를 돌려주고 있었다. "속성 없음"은 null이 아니라 `무속성`
                # 이라는 값이다(필터의 null은 "필터 안 걸기"라는 다른 뜻).
                "element": {"type": "keyword"},
                "sale_type": {"type": "keyword"},
                "status": {"type": "keyword"},
                "price": {"type": "double"},
                # "+8 vs +9"처럼 BM25로는 구분이 잘 안 되는 축은 숫자 필드로
                # 따로 빼서 필터로 정확히 거른다.
                "enhancement_level": {"type": "integer"},
                # 착용 요구 레벨. "100렙 이상" 같은 조건을 필터로 거르기 위한
                # 필드 — 설명문에만 있으면 BM25가 숫자를 제대로 못 거른다.
                # 계정/재화처럼 레벨 개념이 없는 아이템은 0.
                "required_level": {"type": "integer"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": embedding_dims,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        },
    }
