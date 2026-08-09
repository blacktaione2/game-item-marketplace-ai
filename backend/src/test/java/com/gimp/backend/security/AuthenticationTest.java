package com.gimp.backend.security;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.UserRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
// Boot 4에서 패키지가 재배치됐다. 3.x의
// org.springframework.boot.test.autoconfigure.web.servlet 는 더 이상 없다 —
// docs/05-Troubleshooting/spring-boot-4-autoconfigure-공통패턴.md 참고.
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

/**
 * 인증·인가 행동 테스트 (ADR-0023).
 *
 * <p><b>이 프로젝트 백엔드에 행동을 단언하는 테스트가 처음 생긴 자리다.</b> 그전까지는 Initializr 기본
 * {@code contextLoads()} 한 건뿐이었고, ADR-0021에 "초록 뱃지를 백엔드가 검증됐다는 뜻으로 읽으면 안 된다"고
 * 적어야 했다.
 *
 * <p><b>시드 데이터에 의존하지 않는다.</b> CI는 빈 DB로 뜨므로({@code ddl-auto: update}) 필요한 행을 직접
 * 만든다. {@code seed-demo.sql}에 기대면 로컬에서만 통과하는 테스트가 된다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "rate-limit.enabled=false")
@Transactional
class AuthenticationTest {

    @Autowired MockMvc mockMvc;
    @Autowired JwtEncoder jwtEncoder;
    @Autowired TenantRepository tenantRepository;
    @Autowired UserRepository userRepository;
    @Autowired ItemRepository itemRepository;

    private Long tenantAId;
    private Long tenantBId;
    private Long sellerId;
    private Long itemInTenantA;

    @BeforeEach
    void setUp() {
        Tenant tenantA = tenantRepository.save(
                Tenant.builder().code("test_a").name("테스트 테넌트 A").build());
        Tenant tenantB = tenantRepository.save(
                Tenant.builder().code("test_b").name("테스트 테넌트 B").build());
        User seller = userRepository.save(User.builder()
                .tenant(tenantA)
                .username("seller_test")
                .email("seller_test@example.com")
                .passwordHash("(데모 — 비밀번호 인증 없음)")
                .role(UserRole.USER)
                .build());
        Item item = itemRepository.save(Item.builder()
                .tenant(tenantA)
                .seller(seller)
                .name("테스트 검")
                .description("인증 테스트용")
                .saleType(SaleType.FIXED_PRICE)
                .price(new BigDecimal("10000"))
                .stock(5)
                .build());

        tenantAId = tenantA.getId();
        tenantBId = tenantB.getId();
        sellerId = seller.getId();
        itemInTenantA = item.getId();
    }

    private String token(Long tenantId, String tenantCode, Long userId, UserRole role, long ttlSeconds) {
        Instant now = Instant.now();
        Instant expiresAt = now.plus(ttlSeconds, ChronoUnit.SECONDS);
        // 만료 토큰(ttl 음수)을 만들 때 iat이 exp보다 뒤면 Nimbus가 인코딩 단계에서
        // 거부한다. 과거에 발급돼 이미 만료된 토큰을 흉내내야 하므로 iat을 exp 앞에 둔다.
        Instant issuedAt = now.minus(10, ChronoUnit.SECONDS);
        if (!expiresAt.isAfter(issuedAt)) {
            issuedAt = expiresAt.minus(60, ChronoUnit.SECONDS);
        }
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("gimp-backend")
                .issuedAt(issuedAt)
                .expiresAt(expiresAt)
                .subject(String.valueOf(userId))
                .claim(Claims.TENANT_ID, tenantId)
                .claim(Claims.TENANT_CODE, tenantCode)
                .claim(Claims.ROLE, role.name())
                .build();
        return jwtEncoder
                .encode(JwtEncoderParameters.from(
                        JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();
    }

    private String validToken() {
        return token(tenantAId, "test_a", sellerId, UserRole.USER, 3600);
    }

    // --- 인증 ---------------------------------------------------------------

    @Test
    void 토큰이_없으면_401() throws Exception {
        mockMvc.perform(get("/api/items/" + itemInTenantA)).andExpect(status().isUnauthorized());
    }

    @Test
    void 토큰이_쓰레기값이면_401() throws Exception {
        mockMvc.perform(get("/api/items/" + itemInTenantA).header("Authorization", "Bearer not-a-jwt"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void 만료된_토큰은_401() throws Exception {
        String expired = token(tenantAId, "test_a", sellerId, UserRole.USER, -60);
        mockMvc.perform(get("/api/items/" + itemInTenantA).header("Authorization", "Bearer " + expired))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void 정상_토큰이면_조회된다() throws Exception {
        mockMvc.perform(get("/api/items/" + itemInTenantA).header("Authorization", "Bearer " + validToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("테스트 검"));
    }

    // --- 테넌트 격리 ---------------------------------------------------------

    @Test
    void 다른_테넌트의_아이템은_보이지_않는다() throws Exception {
        String otherTenant = token(tenantBId, "test_b", sellerId, UserRole.USER, 3600);
        mockMvc.perform(get("/api/items/" + itemInTenantA).header("Authorization", "Bearer " + otherTenant))
                .andExpect(status().isNotFound());
    }

    @Test
    void 헤더로_테넌트를_바꿔치기할_수_없다() throws Exception {
        // 예전에는 이 헤더가 곧 진실이었다. 이제는 무시돼야 한다 —
        // 토큰은 테넌트 A인데 헤더로 B를 주장해도 A의 아이템이 보인다.
        mockMvc.perform(get("/api/items/" + itemInTenantA)
                        .header("Authorization", "Bearer " + validToken())
                        .header("X-Tenant-Id", String.valueOf(tenantBId)))
                .andExpect(status().isOk());
    }

    // --- permitAll ----------------------------------------------------------

    @Test
    void 부하_하네스가_읽는_메트릭은_잠기지_않는다() throws Exception {
        // load/snapshot_metrics.py 가 실행 전후로 스크레이프한다. 잠그면 에러가
        // 아니라 **빈 차분**이 나와서 조용히 잘못된 측정이 된다.
        mockMvc.perform(get("/actuator/prometheus")).andExpect(status().isOk());
    }

    @Test
    void 로그인_경로는_인증_없이_열려_있다() throws Exception {
        // 토큰을 받으러 오는 길이라 잠글 수 없다. 자격증명이 틀리면 401 이지
        // 403 이 아니다 — 403 은 "인증은 됐는데 권한이 없다"는 뜻이다.
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content("{\"tenantCode\": \"nexon\", \"username\": \"없는사람\","
                                + " \"password\": \"아무거나\"}"))
                .andExpect(status().isUnauthorized());
    }

    /**
     * 인증 이전 경로에서 나는 오류가 <b>401 로 가려지지 않는다</b> (ADR-0034).
     *
     * <p>{@code /error} 가 {@code PUBLIC_PATHS} 에 없으면 Boot 의 에러 디스패치가 보안
     * 체인에 다시 걸려 <b>400 도 500 도 전부 401</b> 이 된다. 그 상태에서 실제 결함 두 건이
     * "비밀번호가 틀렸다"로 보였다 — 중복 아이디와 짧은 {@code JWT_SECRET}.
     */
    @Test
    void 잘못된_요청은_401이_아니라_400이다() throws Exception {
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content("{\"username\":"))
                .andExpect(status().isBadRequest());
    }

    // --- 검증기 두 벌이 갈라지지 않는가 -------------------------------------

    /**
     * {@code Claims} 가 선언한 계약 — <b>"이 파일을 고치면 파이썬 쪽 {@code _REQUIRED_CLAIMS} 도
     * 같이 고쳐야 한다"</b> — 이 실제로 지켜지는지 본다.
     *
     * <p>오래 한쪽만 지키고 있었다. 파이썬은 {@code iss} 와 필수 클레임 6개를 검증하는데,
     * 여기는 {@code NimbusJwtDecoder} 기본값이라 <b>만료만</b> 봤다. 서명 키가 있어야 하므로
     * 악용 경로는 없지만, {@code role} 이 빠진 토큰이 파이썬에서는 401 이고 여기서는
     * {@code UserRole.valueOf(null)} 로 <b>500</b> 이었다.
     */
    private String tokenWithoutClaim(String omitted) {
        Instant now = Instant.now();
        JwtClaimsSet.Builder claims = JwtClaimsSet.builder()
                .issuer("gimp-backend")
                .issuedAt(now)
                .expiresAt(now.plus(3600, ChronoUnit.SECONDS));
        if (!"sub".equals(omitted)) {
            claims.subject(String.valueOf(sellerId));
        }
        if (!Claims.TENANT_ID.equals(omitted)) {
            claims.claim(Claims.TENANT_ID, tenantAId);
        }
        if (!Claims.TENANT_CODE.equals(omitted)) {
            claims.claim(Claims.TENANT_CODE, "test_a");
        }
        if (!Claims.ROLE.equals(omitted)) {
            claims.claim(Claims.ROLE, UserRole.USER.name());
        }
        return jwtEncoder
                .encode(JwtEncoderParameters.from(
                        JwsHeader.with(MacAlgorithm.HS256).build(), claims.build()))
                .getTokenValue();
    }

    @Test
    void 클레임이_빠진_토큰은_500이_아니라_401이다() throws Exception {
        // 넷을 다 돈다 — 하나만 보면 나머지 셋이 빠져도 통과한다.
        for (String omitted : new String[] {"sub", Claims.TENANT_ID, Claims.TENANT_CODE, Claims.ROLE}) {
            mockMvc.perform(get("/api/items/" + itemInTenantA)
                            .header("Authorization", "Bearer " + tokenWithoutClaim(omitted)))
                    .andExpect(status().isUnauthorized());
        }
    }

    @Test
    void 발급자가_다른_토큰은_401이다() throws Exception {
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("someone-else")
                .issuedAt(now)
                .expiresAt(now.plus(3600, ChronoUnit.SECONDS))
                .subject(String.valueOf(sellerId))
                .claim(Claims.TENANT_ID, tenantAId)
                .claim(Claims.TENANT_CODE, "test_a")
                .claim(Claims.ROLE, UserRole.USER.name())
                .build();
        String token = jwtEncoder
                .encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();

        mockMvc.perform(get("/api/items/" + itemInTenantA).header("Authorization", "Bearer " + token))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void 대조_모든_클레임이_있으면_통과한다() throws Exception {
        // **위 둘의 공허 방지.** 검증기를 "전부 거절"로 만들어도 그 둘은 통과한다.
        mockMvc.perform(get("/api/items/" + itemInTenantA)
                        .header("Authorization", "Bearer " + tokenWithoutClaim("(없음)")))
                .andExpect(status().isOk());
    }

    @Test
    void demo_token_경로는_사라졌다() throws Exception {
        // ADR-0031 이 제거했다. **경로가 없어졌다는 것 자체가 판정**이라 되살아나면 실패한다.
        //
        // **유효한 토큰을 들고 간다.** 인증 없이 가면 보안 계층이 먼저 401 을 내는데,
        // 그건 "핸들러가 없다"가 아니라 "인증하고 오라"는 뜻이라 증거가 약하다 —
        // 누군가 이 엔드포인트를 인증 뒤에 되살려도 똑같이 401 이 나온다.
        // 토큰을 들고 가면 보안을 통과하므로, 404 는 **핸들러가 없다는 직접 증거**다.
        mockMvc.perform(post("/api/auth/demo-token")
                        .header("Authorization", "Bearer " + validToken())
                        .contentType("application/json")
                        .content("{\"userId\": 1}"))
                .andExpect(status().isNotFound());
    }
}
