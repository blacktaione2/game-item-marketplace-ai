package com.gimp.backend.service;

import com.gimp.backend.domain.user.User;
import com.gimp.backend.dto.auth.LoginResponse;
import com.gimp.backend.repository.UserRepository;
import com.gimp.backend.security.Claims;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 비밀번호 로그인 (ADR-0031).
 *
 * <p>ADR-0023 의 {@code demo-token} 을 <b>대체한다.</b> 그건 비밀번호를 확인하지 않아
 * userId 만 알면 누구나 그 사용자의 토큰을 받을 수 있었고, 그래서 "외부에 노출하지 말 것"이
 * 계속 붙어 다녔다. 공개 배포를 하기로 하면서 그 전제가 성립하지 않게 됐다.
 *
 * <p><b>회원가입은 없다.</b> 계정은 시드로 고정이다. 가입을 열면 이메일 인증·비밀번호
 * 재설정·계정 복구가 따라오고, 공개 배포에서는 스팸 계정 방어라는 다른 문제가 열린다 —
 * 이 프로젝트의 서사(AI 파이프라인·멀티테넌시·동시성)와 무관한 범용 웹 기능이다.
 *
 * <p>클레임 값은 예전처럼 <b>요청이 아니라 DB 에서</b> 읽는다.
 */
@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserRepository userRepository;
    private final JwtEncoder jwtEncoder;
    private final PasswordEncoder passwordEncoder;

    @Value("${jwt.issuer}")
    private String issuer;

    @Value("${jwt.ttl-seconds}")
    private long ttlSeconds;

    /**
     * 자격증명은 <b>테넌트 + 아이디 + 비밀번호</b> 세 쪽이다 (ADR-0034).
     *
     * <p>아이디만으로 찾던 이전 버전은 유니크 제약이 {@code (tenant_id, username)} 인 것과
     * 어긋났다 — 두 테넌트가 같은 아이디를 가지면 {@code NonUniqueResultException} 이 났고,
     * 그게 401 로 나가 <b>"비밀번호가 틀렸다"로 보였다.</b>
     */
    @Transactional(readOnly = true)
    public LoginResponse login(String tenantCode, String username, String rawPassword) {
        User user = userRepository
                .findByTenant_CodeAndUsername(tenantCode, username)
                .orElse(null);

        // **사용자가 없을 때도 같은 예외를 던진다.** 메시지나 상태 코드가 갈리면
        // "이 아이디가 존재하는가"를 밖에서 알 수 있게 된다(사용자 열거).
        // 없는 **테넌트**도 마찬가지다 — 갈리면 테넌트 목록이 새어 나간다.
        //
        // 존재하지 않는 사용자에도 해시 비교 비용을 치르지 않으므로 응답 시간으로는
        // 여전히 구분될 수 있다. 계정이 5개 고정이라 열거의 값어치가 없어 여기까지 둔다.
        if (user == null || !passwordEncoder.matches(rawPassword, user.getPasswordHash())) {
            throw new BadCredentialsException("아이디 또는 비밀번호가 올바르지 않습니다.");
        }

        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer(issuer)
                .issuedAt(now)
                .expiresAt(now.plus(ttlSeconds, ChronoUnit.SECONDS))
                .subject(String.valueOf(user.getId()))
                .claim(Claims.TENANT_ID, user.getTenant().getId())
                .claim(Claims.TENANT_CODE, user.getTenant().getCode())
                .claim(Claims.ROLE, user.getRole().name())
                .build();

        String token = jwtEncoder
                .encode(JwtEncoderParameters.from(
                        JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();

        return new LoginResponse(
                token, ttlSeconds, user.getId(), user.getUsername(), user.getRole().name());
    }
}
