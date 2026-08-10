package com.gimp.backend.client;

import com.gimp.backend.dto.health.AiHealthResponse;
import java.util.Optional;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/** FastAPI AI 서버(ai/) 호출 클라이언트. 지금은 헬스체크 왕복 확인용 메서드만 있다. */
@Slf4j
@Component
public class AiServerClient {

    private final RestClient restClient;

    // Boot 4.1 doesn't autoconfigure a RestClient.Builder bean out of this project's
    // dependency set, so build via the static factory instead of relying on DI for it.
    public AiServerClient(@Value("${ai-server.base-url}") String baseUrl) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(2000);
        requestFactory.setReadTimeout(3000);

        this.restClient = RestClient.builder().baseUrl(baseUrl).requestFactory(requestFactory).build();
    }

    public Optional<AiHealthResponse> checkHealth() {
        try {
            return Optional.ofNullable(
                    restClient.get().uri("/health").retrieve().body(AiHealthResponse.class));
        } catch (RestClientException e) {
            // **조용히 넘기지 않는다** (ADR-0053). 호출자는 `Optional.empty()` 를
            // "AI 서버 DOWN" 으로 표시하므로 결과는 보이지만 **이유는 사라진다** —
            // 연결 거부·타임아웃·5xx·DNS 가 전부 같은 빈 값이 된다. 이 저장소는
            // 배포에서 설정이 안 먹은 걸 여러 번 겪었고, 그때 필요한 것이 정확히
            // 이 구분이다. 형제 fail-open 인 {@code TradeEventPublisher} 는
            // 카운터와 로그를 둘 다 남긴다 — 여기만 규칙 밖이었다.
            //
            // 폴링되지 않는다: 컨테이너 헬스체크는 {@code /actuator/health} 를 쓰고,
            // 이 경로는 배포 검증과 수동 확인에서만 불린다. 로그 폭주 위험이 없다.
            log.warn("AI 서버 헬스체크 실패 — DOWN 으로 표시한다", e);
            return Optional.empty();
        }
    }
}
