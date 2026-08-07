package com.gimp.backend.dto.health;

/**
 * ai/app/routers/health.py의 GET /health 응답 중 <b>이 백엔드가 쓰는 부분</b>.
 *
 * <p>AI 쪽 응답에는 필드가 더 있다({@code llm_fallback} 등, ADR-0042). 여기서 굳이
 * 받지 않는 이유는 백엔드가 프록시 헬스체크에만 쓰기 때문이고, Boot의 Jackson은
 * 모르는 필드를 기본으로 무시하므로 그쪽이 늘어나도 이 레코드는 안 깨진다.
 *
 * <p>다만 <b>"일치해야 한다"는 예전 주석은 틀렸다</b> — 부분집합이다. 그렇게 적어두면
 * AI 쪽에 필드를 더할 때 여기도 고쳐야 하는 줄 알고 백엔드까지 재빌드하게 된다.
 */
public record AiHealthResponse(String status, String service) {}
