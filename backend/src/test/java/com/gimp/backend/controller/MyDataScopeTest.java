package com.gimp.backend.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.notification.Notification;
import com.gimp.backend.domain.notification.NotificationType;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.trade.TradeType;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.NotificationRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.TradeRepository;
import com.gimp.backend.repository.UserRepository;
import com.gimp.backend.security.Claims;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

/**
 * "내 것"만 나오는가 — 거래 내역과 알림 읽음 처리.
 *
 * <p>두 엔드포인트의 공통점은 <b>대상을 요청이 고르지 않는다</b>는 것이다. 조회자도 수신자도
 * 토큰에서 오므로 남의 것을 지목할 방법이 애초에 없어야 한다. 그 "없어야 한다"를 여기서 단언한다.
 *
 * <p><b>다른 사용자를 반드시 같이 만든다.</b> 혼자만 있는 DB 에서는 "내 것만 나온다"와 "전부
 * 나온다"가 <b>같은 결과</b>라, 격리를 하나도 안 해도 통과한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = "rate-limit.enabled=false")
@Transactional
class MyDataScopeTest {

    @Autowired MockMvc mockMvc;
    @Autowired JwtEncoder jwtEncoder;
    @Autowired TenantRepository tenantRepository;
    @Autowired UserRepository userRepository;
    @Autowired ItemRepository itemRepository;
    @Autowired TradeRepository tradeRepository;
    @Autowired NotificationRepository notificationRepository;

    private Tenant tenant;
    private Tenant otherTenant;
    private User me;
    private User other;
    private User stranger;
    private Long otherNotificationId;
    private Long strangerNotificationId;

    @BeforeEach
    void setUp() {
        tenant = tenantRepository.save(
                Tenant.builder().code("scope_t").name("스코프 테스트").build());
        // **두 번째 테넌트를 반드시 같이 만든다** (ADR-0057). 위 클래스 주석이 사용자
        // 축에 대해 적은 것과 같은 이유이고, 그 논리가 테넌트 축으로 안 건너와 있었다 —
        // 테넌트가 하나뿐이면 "내 테넌트만 나온다"와 "전부 나온다"가 같은 결과라,
        // 조건에서 테넌트를 빼도 전부 통과한다.
        otherTenant = tenantRepository.save(
                Tenant.builder().code("scope_t2").name("다른 테넌트").build());

        me = newUser(tenant, "scope_me");
        other = newUser(tenant, "scope_other");
        User third = newUser(tenant, "scope_third");
        stranger = newUser(otherTenant, "scope_stranger");

        // 가격을 다르게 둔다 — 목록 정렬 검사가 이걸 근거로 삼는다.
        Item mine = newItem(tenant, me, "내가 파는 검", "90000");
        Item theirs = newItem(tenant, other, "남이 파는 검", "10000");
        // 가격을 양 끝 바깥에 둔다 — 정렬 검사가 첫 행을 보므로, 새면 그 검사가 먼저 깨진다.
        newItem(otherTenant, stranger, "다른 테넌트 검", "999000");

        // 내가 산 것 / 내가 판 것 / 나와 무관한 것 — 셋을 다 둔다.
        newTrade(theirs, me, other, TradeType.PURCHASE, "10000");
        newTrade(mine, other, me, TradeType.BID, "20000");
        newTrade(theirs, third, other, TradeType.PURCHASE, "30000");

        notificationRepository.save(Notification.builder()
                .tenant(tenant)
                .recipient(me)
                .tradeId(1001L)
                .type(NotificationType.PURCHASE_COMPLETED)
                .message("내 알림")
                .build());
        Notification theirNotification = notificationRepository.save(Notification.builder()
                .tenant(tenant)
                .recipient(other)
                .tradeId(1002L)
                .type(NotificationType.ITEM_SOLD)
                .message("남의 알림")
                .build());
        otherNotificationId = theirNotification.getId();

        Notification strangerNotification = notificationRepository.save(Notification.builder()
                .tenant(otherTenant)
                .recipient(stranger)
                .tradeId(1003L)
                .type(NotificationType.ITEM_SOLD)
                .message("다른 테넌트 알림")
                .build());
        strangerNotificationId = strangerNotification.getId();
    }

    // --- 테넌트 경계 --------------------------------------------------------

    @Test
    void 다른_테넌트의_매물은_목록에_안_나온다() throws Exception {
        mockMvc.perform(get("/api/items?page=0&size=20&sort=price,desc")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalElements").value(2))
                // 목록은 `Page` 라 배열이 `content` 아래다 — 위 정렬 검사와 같은 경로를 쓴다.
                .andExpect(jsonPath("$.content[?(@.name == '다른 테넌트 검')]").isEmpty());
    }

    @Test
    void 다른_테넌트의_알림은_안_보이고_읽음_처리에도_안_걸린다() throws Exception {
        mockMvc.perform(get("/api/notifications")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].message").value("내 알림"));

        mockMvc.perform(patch("/api/notifications/read")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk());

        // **이쪽이 진짜 단언이다.** 조건에서 테넌트를 빼도 recipientId 때문에 위 두 줄은
        // 통과하지만, 그건 격리가 사용자-테넌트 관계에 얹혀 있다는 뜻이다.
        assertThat(notificationRepository.findById(strangerNotificationId))
                .get()
                .extracting(Notification::isRead)
                .isEqualTo(false);
    }

    @Test
    void 다른_테넌트의_사용자는_자기_알림을_본다() throws Exception {
        // **반대 방향.** 위 검사만 있으면 "아무것도 안 보여준다"도 통과한다.
        mockMvc.perform(get("/api/notifications")
                        .header("Authorization", "Bearer " + tokenFor(stranger)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].message").value("다른 테넌트 알림"));
    }

    // --- 거래 내역 ----------------------------------------------------------

    @Test
    void 토큰이_없으면_401() throws Exception {
        mockMvc.perform(get("/api/trades")).andExpect(status().isUnauthorized());
    }

    @Test
    void 내가_관여한_거래만_나온다() throws Exception {
        mockMvc.perform(get("/api/trades").header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                // 셋 중 둘. 나와 무관한 거래는 빠진다.
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[?(@.counterpartyUsername == 'scope_third')]").isEmpty());
    }

    @Test
    void 거래_일시가_오프셋을_달고_나간다() throws Exception {
        // **이게 화면의 9시간 오차를 만들었다.** 예전에는 `LocalDateTime` 이라
        // `"2026-08-10T21:06:12"` 처럼 오프셋 없이 나갔고, 받는 쪽은 변환할 근거가 없어
        // 문자열을 그대로 잘라 뿌렸다 — 배포본이 UTC 라 한국에서 9시간 어긋나 보였다.
        //
        // Jackson 설정에 기대는 부분이라(Boot 기본값이 ISO-8601) **직렬화 결과를 직접 본다.**
        // 타입만 확인하면 설정이 바뀌었을 때 못 잡는다.
        mockMvc.perform(get("/api/trades").header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].createdAt").value(org.hamcrest.Matchers.endsWith("Z")));
    }

    @Test
    void 산_것과_판_것이_side_로_갈린다() throws Exception {
        mockMvc.perform(get("/api/trades").header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                // 한 목록에 양방향이 섞여 나오므로, 방향이 없으면 읽을 수 없다.
                .andExpect(jsonPath("$[?(@.side == 'BUY')].itemName").value("남이 파는 검"))
                .andExpect(jsonPath("$[?(@.side == 'SELL')].itemName").value("내가 파는 검"));
    }

    @Test
    void 상대방_이름이_id_대신_실린다() throws Exception {
        // `buyerId: 3` 은 화면에서 아무 뜻이 없다 — 이 엔드포인트가 TradeResponse 를
        // 그대로 쓰지 않는 이유가 이것이다.
        mockMvc.perform(get("/api/trades").header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(jsonPath("$[?(@.side == 'BUY')].counterpartyUsername")
                        .value("scope_other"));
    }

    // --- 매물 목록 ----------------------------------------------------------

    @Test
    void 매물_목록은_content_와_totalElements_를_낸다() throws Exception {
        // **프론트가 이 이름들에 직접 의존한다**(`Page<T>` 타입). Spring 의
        // `PageImpl` 직렬화 형태는 프레임워크가 정하는 것이라 Boot 판올림에서
        // 바뀔 수 있고, 바뀌면 화면이 조용히 빈 표가 된다 — 여기서 고정한다.
        mockMvc.perform(get("/api/items?page=0&size=20&sort=price,desc")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content").isArray())
                .andExpect(jsonPath("$.totalElements").isNumber())
                .andExpect(jsonPath("$.totalPages").isNumber())
                .andExpect(jsonPath("$.number").value(0))
                .andExpect(jsonPath("$.size").value(20));
    }

    @Test
    void 매물_목록은_정렬을_실제로_적용한다() throws Exception {
        // 정렬 파라미터가 무시돼도 200 이 나오므로, 형태만 보는 검사는 이걸 놓친다.
        //
        // **이름이 아니라 가격으로 정렬한다.** 첫 판본은 `sort=name,asc` 로 썼다가
        // 틀렸는데, 이유가 둘이다 — 한글에서 `남`(ㅏ) 이 `내`(ㅐ) 보다 앞이라는 걸
        // 잘못 알았고, 애초에 **한글 정렬 순서는 DB 로케일이 정한다.** 판정 근거를
        // 환경에 맡기면 여기서 통과하고 배포에서 갈릴 수 있다. 숫자는 안 갈린다.
        mockMvc.perform(get("/api/items?page=0&size=20&sort=price,desc")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[0].price").value(90000))
                .andExpect(jsonPath("$.content[1].price").value(10000));

        mockMvc.perform(get("/api/items?page=0&size=20&sort=price,asc")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(jsonPath("$.content[0].price").value(10000));
    }

    // --- 알림 읽음 처리 ------------------------------------------------------

    @Test
    void 모두_읽음은_내_알림만_바꾼다() throws Exception {
        mockMvc.perform(patch("/api/notifications/read")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(0));

        assertThat(notificationRepository.countByTenantIdAndRecipientIdAndReadFalse(tenant.getId(), me.getId()))
                .isZero();
        // **이쪽이 진짜 단언이다.** 조건에서 수신자를 빼먹으면 여기서 걸린다.
        assertThat(notificationRepository.countByTenantIdAndRecipientIdAndReadFalse(tenant.getId(), other.getId()))
                .isEqualTo(1);
        assertThat(notificationRepository.findById(otherNotificationId))
                .get()
                .extracting(Notification::isRead)
                .isEqualTo(false);
    }

    @Test
    void 읽음_처리에도_토큰이_필요하다() throws Exception {
        mockMvc.perform(patch("/api/notifications/read")).andExpect(status().isUnauthorized());
    }

    // --- 헬퍼 ---------------------------------------------------------------

    private User newUser(Tenant owner, String username) {
        return userRepository.save(User.builder()
                .tenant(owner)
                .username(username)
                .email(username + "@example.com")
                .passwordHash("(테스트 — 로그인하지 않는다)")
                .role(UserRole.USER)
                .build());
    }

    private Item newItem(Tenant owner, User seller, String name, String price) {
        return itemRepository.save(Item.builder()
                .tenant(owner)
                .seller(seller)
                .name(name)
                .description("스코프 테스트용")
                .saleType(SaleType.FIXED_PRICE)
                .price(new BigDecimal(price))
                .stock(5)
                .build());
    }

    private void newTrade(Item item, User buyer, User seller, TradeType type, String price) {
        tradeRepository.save(Trade.builder()
                .tenant(tenant)
                .item(item)
                .buyer(buyer)
                .seller(seller)
                .tradeType(type)
                .price(new BigDecimal(price))
                .quantity(1)
                .status(TradeStatus.COMPLETED)
                .build());
    }

    private String tokenFor(User user) {
        // **테넌트를 사용자에게서 읽는다.** 필드를 그대로 쓰면 두 번째 테넌트의 사용자에게
        // 첫 번째 테넌트의 클레임을 발급하게 되고, 그건 격리를 시험하는 게 아니라
        // 위조 토큰을 시험하는 것이 된다.
        Tenant owner = user.getTenant();
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("gimp-backend")
                .issuedAt(now)
                .expiresAt(now.plus(1, ChronoUnit.HOURS))
                .subject(String.valueOf(user.getId()))
                .claim(Claims.TENANT_ID, owner.getId())
                .claim(Claims.TENANT_CODE, owner.getCode())
                .claim(Claims.ROLE, UserRole.USER.name())
                .build();
        return jwtEncoder
                .encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();
    }
}
