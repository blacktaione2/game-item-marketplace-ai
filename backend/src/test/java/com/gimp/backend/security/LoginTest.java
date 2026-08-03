package com.gimp.backend.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.UserRepository;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 비밀번호 로그인 (ADR-0031).
 *
 * <h2>비밀번호가 둘이고, 검증도 양방향이다</h2>
 *
 * 일반 계정과 GM 계정의 비밀번호를 나눈 이유는 <b>데모 비밀번호를 아는 사람이 곧바로
 * GM 이 되지 않게</b> 하기 위해서다. 그런데 그걸 한 방향으로만 확인하면
 * ("일반 비밀번호로 GM 로그인 실패") <b>초기화가 반대로 잘못된 경우를 못 잡는다</b> —
 * ADMIN 비밀번호가 일반 계정에도 걸리는 실수는 그 방향으로는 안 걸린다.
 *
 * <p>그래서 <b>양방향</b>으로 본다. 그리고 역방향은 일반 계정 <b>전부</b>를 돈다 —
 * 초기화가 한 계정에만 잘못 적용되는 것이 실제 실수 형태이기 때문이다.
 *
 * <h2>시드 데이터에 의존하지 않는다</h2>
 *
 * CI 는 빈 DB 로 뜨므로 계정을 직접 만든다. 처음에는 시드 계정에 기댔다가 DB 를
 * 재생성한 순간 <b>ADMIN 계정이 없어</b> 두 건이 실패했다 — 다행히 "GM 계정이 없으면
 * 이 검사가 공허해진다"는 가드를 넣어둬서 조용히 통과하지 않았다.
 *
 * <p>대신 {@link DemoAccountInitializer} 를 <b>직접 호출한다.</b> 그래야 이 테스트가
 * 로그인만이 아니라 <b>초기화가 역할별로 올바른 비밀번호를 넣는지</b>까지 검증한다 —
 * 양방향 검사의 대상이 바로 그 초기화다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
    "demo.password=demo-pw-for-test",
    "demo.admin-password=admin-pw-for-test",
    // **한도를 끈다.** 이 테스트가 재는 것은 비밀번호 로직이지 한도가 아닌데,
    // 로그인 한도가 IP당 30회/분이라 양방향 검증(계정 전부를 도는)이 걸린다.
    // 실제로 429 를 받아 5건이 실패했다. 한도 자체는 load/verify-auth.sh 가 본다.
    "rate-limit.enabled=false"
})
class LoginTest {

    private static final String DEMO_PW = "demo-pw-for-test";
    private static final String ADMIN_PW = "admin-pw-for-test";

    @Autowired MockMvc mockMvc;
    @Autowired UserRepository userRepository;
    @Autowired TenantRepository tenantRepository;
    @Autowired com.gimp.backend.config.DemoAccountInitializer initializer;

    private String suffix;
    private String tenantCode;

    @BeforeEach
    void seedAccounts() {
        suffix = String.valueOf(System.nanoTime());
        tenantCode = "login_" + suffix;
        Tenant tenant = tenantRepository.save(
                Tenant.builder().code(tenantCode).name("로그인 테스트").build());
        // 시드와 같은 자리표시자(BCrypt 형식이 아님)로 만든다 — 초기화기가
        // 이걸 실제 해시로 바꾸는지가 검증 대상이다.
        saveUser(tenant, "u1_" + suffix, UserRole.USER);
        saveUser(tenant, "u2_" + suffix, UserRole.USER);
        saveUser(tenant, "admin_" + suffix, UserRole.ADMIN);

        // **기동 시가 아니라 여기서 부른다.** 테스트가 만든 계정은 기동 이후에
        // 생기므로 자동 실행분에는 잡히지 않는다.
        initializer.run(null);
    }

    private void saveUser(Tenant tenant, String username, UserRole role) {
        userRepository.save(User.builder()
                .tenant(tenant)
                .username(username)
                .email(username + "@example.com")
                .passwordHash("$2a$10$demoDemoDemoDemoDemoDe")
                .role(role)
                .build());
    }

    private String body(String username, String password) {
        return body(tenantCode, username, password);
    }

    /** 테넌트를 명시하는 형태 (ADR-0034). 자격증명은 테넌트 + 아이디 + 비밀번호 셋이다. */
    private String body(String tenant, String username, String password) {
        return "{\"tenantCode\":\"%s\",\"username\":\"%s\",\"password\":\"%s\"}"
                .formatted(tenant, username, password);
    }

    /** 이 테스트가 만든 계정만 본다 — 시드 계정이 섞이면 무엇을 쟀는지 흐려진다. */
    private List<User> usersOf(UserRole role) {
        return userRepository.findAll().stream()
                .filter(u -> u.getUsername().endsWith(suffix))
                .filter(u -> u.getRole() == role)
                .toList();
    }

    // --- 기본 동작 -----------------------------------------------------------

    @Test
    void 올바른_비밀번호면_토큰을_준다() throws Exception {
        User user = usersOf(UserRole.USER).stream().findFirst().orElseThrow();

        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body(user.getUsername(), DEMO_PW)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").isNotEmpty())
                .andExpect(jsonPath("$.role").value("USER"));
    }

    @Test
    void 틀린_비밀번호면_401() throws Exception {
        User user = usersOf(UserRole.USER).stream().findFirst().orElseThrow();

        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body(user.getUsername(), "틀린비밀번호")))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void 없는_사용자도_401이다() throws Exception {
        // 404 로 갈리면 "이 아이디가 존재하는가"를 밖에서 알 수 있다(사용자 열거).
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body("존재하지않는계정", DEMO_PW)))
                .andExpect(status().isUnauthorized());
    }

    // --- 비밀번호 분리 — 양방향 ---------------------------------------------

    @Test
    void 일반_비밀번호로는_GM_계정에_로그인할_수_없다() throws Exception {
        List<User> admins = usersOf(UserRole.ADMIN);
        assertThat(admins).isNotEmpty();  // GM 계정이 없으면 이 검사가 공허해진다

        for (User admin : admins) {
            mockMvc.perform(post("/api/auth/login")
                            .contentType("application/json")
                            .content(body(admin.getUsername(), DEMO_PW)))
                    .andExpect(status().isUnauthorized());
        }
    }

    /**
     * <b>역방향.</b> 초기화가 반대로 잘못돼 ADMIN 비밀번호가 일반 계정에도 걸리면
     * 위 테스트로는 안 잡힌다. 그리고 <b>일반 계정 전부</b>를 도는 이유는 한 계정에만
     * 잘못 적용되는 것이 실제 실수 형태이기 때문이다.
     */
    @Test
    void GM_비밀번호로는_일반_계정에_로그인할_수_없다() throws Exception {
        List<User> users = usersOf(UserRole.USER);
        assertThat(users).isNotEmpty();

        for (User user : users) {
            mockMvc.perform(post("/api/auth/login")
                            .contentType("application/json")
                            .content(body(user.getUsername(), ADMIN_PW)))
                    .andExpect(status().isUnauthorized());
        }
    }

    @Test
    void GM_계정은_GM_비밀번호로_로그인되고_ADMIN_역할을_받는다() throws Exception {
        User admin = usersOf(UserRole.ADMIN).stream().findFirst().orElseThrow();

        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body(admin.getUsername(), ADMIN_PW)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.role").value("ADMIN"));
    }

    // --- 테넌트가 자격증명의 일부다 (ADR-0034) ---------------------------------

    /**
     * <b>이 프로젝트의 간판이 멀티테넌시인데, 로그인이 그 모델과 어긋나 있었다.</b>
     *
     * <p>{@code users} 의 유니크 제약은 {@code (tenant_id, username)} 인데 조회는
     * {@code findByUsername} 하나였다. 두 테넌트가 같은 아이디를 가지면
     * {@code NonUniqueResultException} 이 나고, 그게 401 로 나가 <b>"비밀번호가 틀렸다"로
     * 보였다</b> — 즉 해당 계정은 영구히 로그인 불가인데 원인은 어디에도 안 보였다.
     *
     * <p>시드 테넌트가 하나뿐이라 <b>발현되지 않는 잠복 결함</b>이었다. 그래서 이 테스트는
     * 두 번째 테넌트를 실제로 만든다 — 없으면 무엇도 증명하지 못한다.
     */
    @Test
    void 서로_다른_테넌트의_같은_아이디는_각자_로그인된다() throws Exception {
        String sharedName = "shared_" + suffix;
        User first = usersOf(UserRole.USER).stream().findFirst().orElseThrow();
        Tenant other = tenantRepository.save(
                Tenant.builder().code("login_other_" + suffix).name("두 번째 테넌트").build());
        saveUser(tenantRepository.findById(first.getTenant().getId()).orElseThrow(),
                sharedName, UserRole.USER);
        saveUser(other, sharedName, UserRole.ADMIN);
        initializer.run(null);

        // 같은 아이디가 두 테넌트에 실제로 존재하는지 먼저 확인한다 —
        // 하나뿐이면 아래 단언은 아무것도 증명하지 않는다.
        assertThat(userRepository.findAll().stream()
                        .filter(u -> sharedName.equals(u.getUsername()))
                        .count())
                .isEqualTo(2);

        // 각 테넌트의 계정이 **자기 역할로** 열린다. 역할이 갈리므로 어느 쪽이 열렸는지
        // 응답만 보고 구분할 수 있다 — 둘 다 USER 면 엉뚱한 쪽이 열려도 통과한다.
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body(tenantCode, sharedName, DEMO_PW)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.role").value("USER"));

        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body(other.getCode(), sharedName, ADMIN_PW)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.role").value("ADMIN"));
    }

    @Test
    void 없는_테넌트도_401이다() throws Exception {
        User user = usersOf(UserRole.USER).stream().findFirst().orElseThrow();

        // 404 나 400 으로 갈리면 어떤 테넌트가 존재하는지 밖에서 알 수 있다.
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content(body("no_such_tenant", user.getUsername(), DEMO_PW)))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void 테넌트를_빼면_400이다() throws Exception {
        User user = usersOf(UserRole.USER).stream().findFirst().orElseThrow();

        // **401 이 아니라 400 이어야 한다.** 자격증명이 틀린 게 아니라 요청이 불완전하다.
        // 예전에는 이런 검증 실패조차 401 로 나갔다 — /error 가 보안 체인에 걸려서다(F1).
        mockMvc.perform(post("/api/auth/login")
                        .contentType("application/json")
                        .content("{\"username\":\"%s\",\"password\":\"%s\"}"
                                .formatted(user.getUsername(), DEMO_PW)))
                .andExpect(status().isBadRequest());
    }
}
