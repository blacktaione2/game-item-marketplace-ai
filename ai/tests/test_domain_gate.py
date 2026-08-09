"""도메인 밖 질의 거절 — ADR-0039.

`"삼성전자 주식 어때?"` 가 시세 예측 분기를 그대로 타고 **"삼성전자 주식의 최근
거래가는 약 26,090원"** 이라고 답했다. 숫자는 `게임 머니 1000만 골드` 의 진짜
예측값이었고 주어만 거짓이었다.

**게이트의 판정 자체(LLM)는 여기서 재지 않는다** — 그건
`scripts/evaluate_domain_gate.py` 가 오거부율·미검출률로 잰다. 여기서 고정하는
것은 판정이 났을 때 **배선이 그대로 따라가는가**다: 검색을 건너뛰는가, 응답이
어떤 모양인가, 캐시가 저장을 막는가.
"""

import asyncio
import inspect
import json

from app.core.metrics import _STAGE_BY_KEY, _outcome
from app.services.assistant.pipeline import _no_results, _out_of_domain, _search_cost
from app.services.cache.policy import is_cacheable
from app.services.router.intents import Intent
from app.services.search import domain_gate
from app.services.search.domain_gate import _parse as parse_verdict
from app.services.search.pipeline import search
from app.services.search.query_understanding import _PROMPT as UNDERSTAND_PROMPT


class _StubLLM:
    """두 프롬프트가 한 클라이언트를 공유한다 — 어느 쪽인지 보고 답을 고른다."""

    def __init__(self, verdict: str, understanding: dict | None = None):
        self.verdict = verdict
        self.understanding = understanding or {"rewritten_query": "질의", "filters": {}}
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("다음은 사용자가 게임 아이템 거래소에"):
            return self.verdict
        return json.dumps(self.understanding, ensure_ascii=False)


class TestVerdictParsing:
    """판정은 한 단어다. **실패는 통과다.**

    게이트가 죽었을 때 서비스가 "아무것도 답하지 않는" 상태로 넘어가는 쪽이
    훨씬 나쁘다 — `understand_query` 가 파싱 실패에 원본 질의로 폴백하는 것과
    같은 방향이다.
    """

    def test_no_rejects(self):
        assert parse_verdict("NO") is False
        assert parse_verdict(" no \n") is False
        assert parse_verdict("아니오") is False

    def test_yes_passes(self):
        assert parse_verdict("YES") is True
        assert parse_verdict("yes") is True

    def test_unexpected_output_passes(self):
        """예상 밖 출력은 거절이 아니라 통과다."""
        assert parse_verdict("잘 모르겠습니다") is True
        assert parse_verdict("") is True

    def test_a_bare_substring_search_would_have_flipped_this(self):
        """**`"NO" in text` 로 쓰면 안 된다.**

        모델이 설명을 붙이면 긍정 답변 안에 `NO` 가 들어갈 수 있다. 앞을 본다.
        """
        answer = "YES, 이 문장은 NO 라고 볼 수 없습니다"
        assert "NO" in answer
        assert parse_verdict(answer) is True

    def test_llm_failure_passes(self):
        class _Broken:
            async def complete(self, prompt: str) -> str:
                raise RuntimeError("업스트림 장애")

        # **두 번째 값이 "게이트가 조용히 열렸다"는 신호다.** 위 모듈 독스트링이
        # 그 대가를 인정하며 "로그와 메트릭이 맡는다"고 적어뒀는데, 있던 메트릭은
        # `outcome="out_of_domain"` 뿐이라 게이트가 **닫힌** 횟수만 셌다.
        assert asyncio.run(domain_gate.judge_in_domain(_Broken(), "검 찾아줘")) == (True, True)

    def test_대조_정상_판정은_내려앉지_않았다고_말한다(self):
        class _Ok:
            async def complete(self, prompt: str) -> str:
                return "YES"

        assert asyncio.run(domain_gate.judge_in_domain(_Ok(), "검 찾아줘")) == (True, False)


class TestExtractionPromptStaysClean:
    """**판정을 추출 프롬프트에 다시 얹지 않는다** (ADR-0039).

    한 번 얹어봤고 측정으로 기각됐다 — 재작성 토큰집합 일치도가 대조군 대비
    -0.24 떨어졌고 문구를 고쳐도 그대로였다. 되돌아가기 쉬운 변경이라 고정한다.
    """

    def test_no_domain_field_in_the_understanding_prompt(self):
        assert "in_domain" not in UNDERSTAND_PROMPT

    def test_the_gate_has_its_own_prompt(self):
        assert domain_gate._PROMPT != UNDERSTAND_PROMPT


class TestSearchShortCircuits:
    def test_out_of_domain_skips_embedding_es_and_rerank(self):
        """도메인 밖이면 검색 자체를 하지 않는다.

        `es=None` 을 넘기는 것이 이 테스트의 핵심이다 — ES 를 건드리면 터진다.
        임베딩도 마찬가지로, 부르면 466MB 모델을 올리느라 이 테스트가 느려진다.
        """
        llm = _StubLLM("NO")
        result = asyncio.run(
            search(es=None, llm_client=llm, tenant_code="nexon", query="삼성전자 주식 어때?")
        )

        assert result["in_domain"] is False
        assert result["results"] == []

    def test_both_calls_go_out_even_when_rejected(self):
        """**병렬이라 판정이 났을 때는 이미 둘 다 나간 뒤다.**

        `llm_calls` 가 2인 근거이고, "판정을 먼저 하면 하나 아낀다"를 택하지
        않았다는 기록이기도 하다 — 그 대가는 모든 요청의 지연이다.
        """
        llm = _StubLLM("NO")
        asyncio.run(
            search(es=None, llm_client=llm, tenant_code="nexon", query="오늘 날씨 어때")
        )
        assert len(llm.prompts) == 2

    def test_the_two_stages_are_timed_separately(self):
        """`gather` 로 묶어놓고 합쳐 재면 어느 쪽이 느려졌는지 알 수 없다.

        그리고 **뒤 단계 계측 키의 부재**가 단축이 실제로 걸렸다는 증거다
        (캐시 적중 시 `cache_encode` 가 없어야 하는 것과 같은 규칙).
        """
        llm = _StubLLM("NO")
        result = asyncio.run(
            search(es=None, llm_client=llm, tenant_code="nexon", query="오늘 날씨 어때")
        )
        assert set(result["timings"]) == {"query_understanding_ms", "domain_gate_ms"}

    def test_the_new_stage_is_registered_as_a_metric(self):
        """새 단계는 `_STAGE_BY_KEY` 에 한 줄 — 없으면 조용히 버려진다."""
        assert _STAGE_BY_KEY["domain_gate_ms"] == "domain_gate"

    def test_in_domain_flag_is_present_on_the_normal_path_too(self):
        """상류가 `result["in_domain"]` 을 무조건 읽는다 — 없으면 KeyError 다.

        정상 경로에서 이 키가 빠지면 **모든 검색이 죽는다.** 그 경로는 ES 가
        필요해서 여기서 끝까지 돌릴 수 없으므로, 반환 지점에 키가 있다는 것만
        소스에서 확인한다.
        """
        source = inspect.getsource(search)
        assert source.count('"in_domain"') == 2


class TestOutOfDomainPayload:
    def test_shape(self):
        payload = _out_of_domain()
        assert payload["out_of_domain"] is True
        assert payload["results"] == []

    def test_costs_two_llm_calls_not_zero(self):
        """판정과 질의이해가 **병렬로 함께 나갔다.**

        도메인 밖이라는 걸 알았을 때는 이미 둘 다 돈 뒤다. 지연은 하나치인데
        비용은 둘이라는 것이 이 설계의 대가이고, 그 대가를 숫자로 남긴다.
        """
        cost = _search_cost({"llm_calls": 2, "timings": {}})
        assert cost["llm_calls"] == 2
        # **그 값은 이제 여기서 안 만든다.** 두 헬퍼가 각자 상수 2를 적어두면
        # 장애 때 거짓이 되고, 같은 사실의 출처가 둘이 된다.
        assert "llm_calls" not in _out_of_domain()
        assert "llm_calls" not in _no_results({"subcategory": "검"})

    def test_내려앉으면_그만큼_뺀다(self):
        """프로바이더가 죽으면 두 호출은 **성사되지 않는다.**

        시세(3→2)·이상거래(1→0)는 이미 빼고 있었고 검색만 2로 박혀 있었다.
        `degraded` 도 같이 서야 한다 — 검색은 500 도 안 나고 응답도 그럴듯해서
        **다른 신호가 아예 없는** 분기다.
        """
        both_down = _search_cost({"llm_calls": 0, "timings": {}, "degraded": True})
        assert both_down["llm_calls"] == 0
        assert both_down["degraded"] is True
        # 정상 응답에는 `degraded` 를 아예 안 싣는다(다른 분기와 같은 모양).
        assert "degraded" not in _search_cost({"llm_calls": 2, "timings": {}})

    def test_cannot_echo_the_query_because_it_never_sees_it(self):
        """**되풀이가 구조적으로 불가능하다.**

        "삼성전자 주식은 다루지 않습니다" 처럼 대상을 받아 적으면, 게이트가
        틀렸을 때(도메인 안을 거절했을 때) 그 문장이 오히려 설득력을 갖는다.
        인자를 받지 않으면 프롬프트 한 줄이 아니라 시그니처가 그걸 보장한다.
        """
        assert inspect.signature(_out_of_domain).parameters == {}

    def test_says_what_it_does_handle(self):
        answer = _out_of_domain()["answer"]
        assert "아이템" in answer and "재화" in answer

    def test_is_not_the_same_verdict_as_no_results(self):
        """둘을 한 플래그로 합치면 화면도 캐시도 구분할 수 없다.

        `"3만원 이하 불속성 검"` 은 조건을 완화하면 결과가 나올 수 있고,
        `"삼성전자 주식"` 은 완화할 조건이 없다.
        """
        out = _out_of_domain()
        empty = _no_results({"subcategory": "검"})
        assert "no_results" not in out
        assert "out_of_domain" not in empty
        assert out["answer"] != empty["answer"]


class TestMetricsOutcome:
    """게이트가 **운영에서 보여야** 한다.

    오거부율은 배포 전에 평가셋으로 한 번 쟀을 뿐이고, 실제 질의 분포는 그것과
    다르다. 이 카운터가 늘면 게이트가 멀쩡한 질의를 막고 있다는 신호다.
    """

    def test_out_of_domain_is_its_own_outcome(self):
        assert _outcome({"out_of_domain": True}) == "out_of_domain"

    def test_it_does_not_get_folded_into_no_results(self):
        """둘을 한 값으로 세면 "게이트가 막았다"와 "매물이 없다"가 섞인다."""
        assert _outcome({"no_results": True}) == "no_results"
        assert _outcome({}) == "ok"


class TestCacheVeto:
    def test_out_of_domain_is_never_stored(self):
        """판정 근거가 비결정적이라, 질의 문자열로 굳히면 오거부가 TTL 내내 산다."""
        for intent in (Intent.ITEM_SEARCH, Intent.PRICE_FORECAST, Intent.COMPOUND):
            assert is_cacheable(intent, _out_of_domain()) is False

    def test_the_veto_is_its_own_and_not_no_results(self):
        """플래그 하나에 기대면 다른 하나를 지웠을 때 조용히 캐시된다."""
        assert is_cacheable(Intent.ITEM_SEARCH, {"out_of_domain": True}) is False
        assert is_cacheable(Intent.ITEM_SEARCH, {"no_results": True}) is False

    def test_ordinary_responses_are_still_cacheable(self):
        """거부 조건이 넓어져서 전부 막아버리면 캐시가 죽는다 — 그건 조용하다."""
        assert is_cacheable(Intent.ITEM_SEARCH, {"answer": "3건 찾았습니다."}) is True
