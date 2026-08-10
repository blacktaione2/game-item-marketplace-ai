package com.gimp.backend.config;

import com.gimp.backend.domain.common.StoredTime;
import java.time.LocalDateTime;
import java.util.Optional;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.auditing.DateTimeProvider;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;

/**
 * {@code createdAt} / {@code updatedAt} 을 채우는 설정.
 *
 * <p><b>기록 시간대를 명시한다.</b> 기본 제공자는 {@code LocalDateTime.now()} 를 쓰는데, 그건
 * <b>JVM 기본 시간대</b>에 기대는 값이다. 컨테이너는 UTC(실측)이고 개발기(Windows)는 KST 라
 * <b>같은 코드가 두 환경에서 다른 뜻의 값을 썼다.</b> 컬럼이
 * {@code timestamp without time zone} 이라 어느 쪽인지 나중에 알아낼 방법도 없다.
 *
 * <p>그래서 쓰는 쪽이 {@link StoredTime#ZONE} 을 직접 지정한다. 읽는 쪽
 * ({@code StoredTime.toInstant}) 도 같은 상수를 쓰므로 <b>둘이 갈릴 수 없다.</b>
 *
 * <h2>JVM 기본 시간대를 바꾸지 않은 이유</h2>
 *
 * <p>{@code main} 에서 {@code TimeZone.setDefault} 를 부르는 방법도 있고 실제로 그렇게 짰다가
 * 물렀다. <b>테스트는 {@code main} 을 실행하지 않는다.</b> 그러면 검증이 운영과 다른 시간대에서
 * 도는데, 그건 지금 고치는 결함과 <b>같은 계열</b>이다 — 두 환경이 다른 가정 위에서 도는 것.
 *
 * <p>여기서 지정하면 컨텍스트를 띄우는 모든 곳(운영·테스트)이 같은 경로를 지난다. 전역 상태를
 * 바꾸지 않는다는 점도 낫다.
 */
@Configuration
@EnableJpaAuditing(dateTimeProviderRef = "storedTimeProvider")
public class JpaAuditingConfig {

    @Bean
    public DateTimeProvider storedTimeProvider() {
        return () -> Optional.of(LocalDateTime.now(StoredTime.ZONE));
    }
}
