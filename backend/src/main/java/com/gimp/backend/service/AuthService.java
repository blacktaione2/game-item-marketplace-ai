package com.gimp.backend.service;

import com.gimp.backend.domain.user.User;
import com.gimp.backend.dto.auth.DemoTokenResponse;
import com.gimp.backend.exception.ResourceNotFoundException;
import com.gimp.backend.repository.UserRepository;
import com.gimp.backend.security.Claims;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 데모 토큰 발급 (ADR-0023).
 *
 * <p><b>이것은 로그인이 아니다.</b> 비밀번호를 확인하지 않으므로 userId를 아는 사람은 누구나 그 사용자의 토큰을
 * 받을 수 있다. 포트폴리오 데모라 회원가입·비밀번호 관리를 범위에서 뺐고, 대신 <b>검증 쪽은 전부 진짜다</b> —
 * 서명, 만료, 클레임, 401/403.
 *
 * <p>클레임 값은 <b>요청이 아니라 DB에서</b> 읽는다. 요청 본문의 테넌트·역할 주장을 그대로 실으면 헤더를 믿던
 * 예전과 달라지는 게 없다.
 */
@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserRepository userRepository;
    private final JwtEncoder jwtEncoder;

    @Value("${jwt.issuer}")
    private String issuer;

    @Value("${jwt.ttl-seconds}")
    private long ttlSeconds;

    @Transactional(readOnly = true)
    public DemoTokenResponse issueDemoToken(Long userId) {
        User user = userRepository
                .findById(userId)
                .orElseThrow(() -> new ResourceNotFoundException("사용자를 찾을 수 없습니다: " + userId));

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
                .encode(JwtEncoderParameters.from(JwsHeader.with(org.springframework.security.oauth2.jose.jws.MacAlgorithm.HS256).build(), claims))
                .getTokenValue();

        return new DemoTokenResponse(
                token, ttlSeconds, user.getId(), user.getUsername(), user.getRole().name());
    }
}
