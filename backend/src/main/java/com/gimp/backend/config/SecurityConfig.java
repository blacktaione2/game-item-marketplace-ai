package com.gimp.backend.config;

import com.nimbusds.jose.jwk.source.ImmutableSecret;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.web.SecurityFilterChain;

/**
 * JWT 검증 설정 (ADR-0023).
 *
 * <p>대칭키(HMAC-SHA256)를 쓴다. 발급자가 이 서버 하나뿐이라 공개키 배포(JWK 엔드포인트)가 필요 없고, AI
 * 서버는 같은 비밀키로 검증만 한다.
 *
 * <p><b>비밀키는 백엔드와 {@code ai/.env} 양쪽에 같은 값이 있어야 한다.</b> {@code OPENAI_API_KEY}가 AI
 * 쪽에만 있는 것과 다르다. 값이 다르면 발급은 성공하는데 AI 서버만 401을 내므로 증상이 헷갈린다.
 */
@Configuration
public class SecurityConfig {

    /**
     * 인증 없이 열어두는 경로. <b>각각 열어둔 이유가 있고, 이유 없는 항목은 없다.</b>
     *
     * <ul>
     *   <li>{@code /actuator/prometheus} — 부하 하네스({@code load/snapshot_metrics.py})가 실행 전후로
     *       스크레이프한다. 잠그면 측정이 조용히 깨진다(에러가 아니라 빈 차분이 나온다)
     *   <li>{@code /actuator/health}, {@code /api/health} — 헬스체크. docker-compose와 수동 확인용
     *   <li>{@code /api/auth/login} — 토큰을 받으러 오는 길이라 잠글 수 없다
     * </ul>
     */
    private static final String[] PUBLIC_PATHS = {
        "/api/auth/login", "/api/health", "/actuator/health", "/actuator/prometheus"
    };

    private final SecretKey secretKey;

    public SecurityConfig(@Value("${jwt.secret}") String secret) {
        // HS256은 키가 256비트 이상이어야 한다. 짧으면 여기서 즉시 실패하는 게 맞다 —
        // 조용히 약한 키로 도는 것보다 기동이 안 되는 쪽이 낫다.
        this.secretKey = new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256");
    }

    /**
     * 비밀번호 해시 (ADR-0031). BCrypt 는 솔트를 해시 문자열 안에 담으므로 별도 컬럼이 없다.
     *
     * <p>시드의 자리표시자(`$2a$10$demoDemo...`)는 <b>29자로 BCrypt 형식이 아니다</b> —
     * 60자가 정상이다. 그래서 어떤 비밀번호와도 매칭되지 않고, 초기화가 실패하면
     * 아무도 로그인하지 못한다(조용히 열리는 쪽이 아니라 조용히 닫히는 쪽으로 실패한다).
     */
    @Bean
    org.springframework.security.crypto.password.PasswordEncoder passwordEncoder() {
        return new org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
                // 토큰 기반이라 세션도 CSRF 토큰도 없다. 쿠키를 쓰지 않으므로 CSRF 공격면이 없다.
                .csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth.requestMatchers(PUBLIC_PATHS)
                        .permitAll()
                        .anyRequest()
                        .authenticated())
                .oauth2ResourceServer(oauth2 -> oauth2.jwt(jwt -> jwt.decoder(jwtDecoder())))
                .build();
    }

    @Bean
    public JwtDecoder jwtDecoder() {
        return NimbusJwtDecoder.withSecretKey(secretKey)
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
    }

    @Bean
    public JwtEncoder jwtEncoder() {
        return new NimbusJwtEncoder(new ImmutableSecret<>(secretKey));
    }
}
