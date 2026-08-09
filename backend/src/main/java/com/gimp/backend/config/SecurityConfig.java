package com.gimp.backend.config;

import com.gimp.backend.security.Claims;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import java.nio.charset.StandardCharsets;
import java.util.Objects;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtClaimNames;
import org.springframework.security.oauth2.jwt.JwtClaimValidator;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
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
     *   <li>{@code /error} — <b>Boot 의 에러 디스패치다. 없으면 모든 오류가 401 이 된다</b>
     * </ul>
     *
     * <h2>{@code /error} 가 왜 여기 있어야 하는가 (ADR-0034)</h2>
     *
     * Spring Security 6+ 는 <b>ERROR 디스패치도 필터링한다.</b> 그래서 예외가 나면 Boot 가
     * {@code /error} 로 포워드하고, 그 경로가 여기 없으면 {@code anyRequest().authenticated()}
     * 에 걸려 <b>401</b> 이 나간다 — 원래 400 이나 500 이어야 할 응답이 전부 그렇게 된다.
     *
     * <p>실측으로 확인한 증상 셋이 전부 같은 원인이었다.
     *
     * <ul>
     *   <li>깨진 JSON 으로 로그인 → 400 이어야 하는데 <b>401</b>
     *   <li>두 테넌트에 같은 아이디가 있을 때 → 500 이어야 하는데 <b>401</b>
     *   <li>{@code JWT_SECRET} 이 256비트 미만일 때 → 500 이어야 하는데 <b>401</b>
     * </ul>
     *
     * <p>뒤의 둘은 진짜 결함인데 <b>"비밀번호가 틀렸다"로 보였다.</b> 즉 이 한 줄의 부재가
     * 다른 결함들을 가리고 있었다. 대조군: 유효 토큰으로 없는 경로를 치면 정상적으로 404 다.
     *
     * <p>본문이 새지는 않는다 — {@code server.error.include-stacktrace} 기본값이
     * {@code never} 라 {@code {timestamp, status, error, path}} 만 나간다.
     */
    private static final String[] PUBLIC_PATHS = {
        "/api/auth/login", "/api/health", "/actuator/health", "/actuator/prometheus", "/error"
    };

    private final SecretKey secretKey;
    private final String issuer;

    /** HS256 최소 키 길이. Nimbus 가 서명 시점에 요구하는 값이다. */
    private static final int MIN_SECRET_BYTES = 32;

    public SecurityConfig(@Value("${jwt.secret}") String secret, @Value("${jwt.issuer}") String issuer) {
        this.issuer = issuer;
        byte[] keyBytes = secret.getBytes(StandardCharsets.UTF_8);

        // **여기서 직접 재야 한다.** 예전 주석은 "짧으면 여기서 즉시 실패한다"고 적어뒀는데
        // 사실이 아니었다 — `SecretKeySpec` 은 길이를 검증하지 않고(빈 배열만 거부한다),
        // Nimbus 의 `KeyLengthException` 은 **첫 로그인 시점**에야 난다.
        //
        // 그 조합이 만든 상태가 나쁘다: 5바이트 시크릿으로도 컨테이너가 **healthy** 로 뜨고,
        // 헬스체크도 통과하고, 로그인만 실패한다. 게다가 그 실패가 401 로 보여서(ADR-0034의
        // /error 건) **"비밀번호가 틀렸다"로 읽힌다.** 어디에도 시크릿을 가리키는 신호가 없다.
        //
        // `SecretGuard` 에 두지 않는 이유: 그건 `prod` 전용이고 "저장소 기본값인가"만 본다.
        // 짧은 키는 **모든 프로파일에서** 고장이므로 검사도 모든 프로파일에 있어야 한다.
        if (keyBytes.length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "JWT_SECRET 이 너무 짧습니다: " + keyBytes.length + "바이트 (HS256 은 최소 "
                            + MIN_SECRET_BYTES + "바이트). 조용히 약한 키로 도는 것보다 기동이 안 되는 쪽이 낫다.");
        }
        this.secretKey = new SecretKeySpec(keyBytes, "HmacSHA256");
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

    /**
     * 토큰 검증기.
     *
     * <p><b>기본 검증기는 만료만 본다.</b> {@code NimbusJwtDecoder.withSecretKey(...).build()} 가
     * 다는 것은 {@code JwtTimestampValidator} 하나뿐이라, 발급자도 필수 클레임도 검사하지 않았다.
     *
     * <h2>왜 문제인가 — 계약이 한쪽만 지켜지고 있었다</h2>
     *
     * {@link com.gimp.backend.security.Claims} 는 <b>"이 파일을 고치면 파이썬 쪽
     * {@code _REQUIRED_CLAIMS} 도 같이 고쳐야 한다"</b> 고 적어뒀다. 게이트웨이를 두지 않기로 해서
     * 검증기가 두 벌이 됐고(ADR-0023), 두 벌이 갈라지지 않게 하려고 쓴 문장이다. 그런데 실제로는
     * 파이썬만 지키고 있었다:
     *
     * <table><caption>수정 전</caption>
     *   <tr><th></th><th>iss</th><th>필수 클레임</th><th>만료</th></tr>
     *   <tr><td>{@code ai/app/core/auth.py}</td><td>O</td><td>O (6개)</td><td>O</td></tr>
     *   <tr><td>여기</td><td><b>X</b></td><td><b>X</b></td><td>O</td></tr>
     * </table>
     *
     * <p>서명 키가 있어야 하므로 <b>악용 경로는 없다.</b> 고치는 이유는 실패 모드다 — {@code role}
     * 이 빠진 토큰이면 파이썬은 401 을 내는데 자바는 {@code Actor.from()} 의
     * {@code UserRole.valueOf(null)} 에서 NPE 가 나 <b>500</b> 이 된다. "토큰이 잘못됐다" 가
     * "서버가 고장났다" 로 보이는 것은 ADR-0034 가 {@code /error} 로 겪은 것과 같은 종류다.
     *
     * <p>필수 클레임을 {@code Objects::nonNull} 로만 보는 이유: 값의 형태(테넌트가 존재하는가 등)는
     * 여기서 판단할 일이 아니다. 이 계층이 답할 질문은 <b>"이 토큰이 우리가 발급한 모양인가"</b> 하나다.
     */
    @Bean
    public JwtDecoder jwtDecoder() {
        NimbusJwtDecoder decoder =
                NimbusJwtDecoder.withSecretKey(secretKey).macAlgorithm(MacAlgorithm.HS256).build();
        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
                // 만료 + 발급자. AuthService 가 싣는 값과 같아야 한다.
                JwtValidators.createDefaultWithIssuer(issuer),
                // exp 는 **있을 때만** 검사되므로(TimestampValidator) 존재 자체를 따로 요구한다.
                new JwtClaimValidator<Object>(JwtClaimNames.EXP, Objects::nonNull),
                new JwtClaimValidator<Object>(JwtClaimNames.SUB, Objects::nonNull),
                new JwtClaimValidator<Object>(Claims.TENANT_ID, Objects::nonNull),
                new JwtClaimValidator<Object>(Claims.TENANT_CODE, Objects::nonNull),
                new JwtClaimValidator<Object>(Claims.ROLE, Objects::nonNull)));
        return decoder;
    }

    @Bean
    public JwtEncoder jwtEncoder() {
        return new NimbusJwtEncoder(new ImmutableSecret<>(secretKey));
    }
}
