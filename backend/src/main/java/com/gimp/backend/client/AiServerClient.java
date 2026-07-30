package com.gimp.backend.client;

import com.gimp.backend.dto.health.AiHealthResponse;
import java.util.Optional;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/** FastAPI AI 서버(ai/) 호출 클라이언트. 지금은 헬스체크 왕복 확인용 메서드만 있다. */
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
            return Optional.empty();
        }
    }
}
