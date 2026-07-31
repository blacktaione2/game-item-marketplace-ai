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
    void 토큰_발급_경로는_인증_없이_열려_있다() throws Exception {
        // 토큰을 받으러 오는 길이라 잠글 수 없다. 없는 사용자면 401이 아니라 404여야
        // 한다 — 401이면 "인증하고 다시 오라"는 순환이 된다.
        mockMvc.perform(post("/api/auth/demo-token")
                        .contentType("application/json")
                        .content("{\"userId\": 999999}"))
                .andExpect(status().isNotFound());
    }

    // --- 발급 ---------------------------------------------------------------

    @Test
    void 클레임은_요청이_아니라_DB에서_온다() throws Exception {
        mockMvc.perform(post("/api/auth/demo-token")
                        .contentType("application/json")
                        .content("{\"userId\": " + sellerId + "}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").isNotEmpty())
                .andExpect(jsonPath("$.username").value("seller_test"))
                .andExpect(jsonPath("$.role").value("USER"));
    }
}
