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
    private User me;
    private User other;
    private Long otherNotificationId;

    @BeforeEach
    void setUp() {
        tenant = tenantRepository.save(
                Tenant.builder().code("scope_t").name("스코프 테스트").build());
        me = newUser("scope_me");
        other = newUser("scope_other");
        User third = newUser("scope_third");

        Item mine = newItem(me, "내가 파는 검");
        Item theirs = newItem(other, "남이 파는 검");

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

    // --- 알림 읽음 처리 ------------------------------------------------------

    @Test
    void 모두_읽음은_내_알림만_바꾼다() throws Exception {
        mockMvc.perform(patch("/api/notifications/read")
                        .header("Authorization", "Bearer " + tokenFor(me)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.count").value(0));

        assertThat(notificationRepository.countByRecipientIdAndReadFalse(me.getId()))
                .isZero();
        // **이쪽이 진짜 단언이다.** 조건에서 수신자를 빼먹으면 여기서 걸린다.
        assertThat(notificationRepository.countByRecipientIdAndReadFalse(other.getId()))
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

    private User newUser(String username) {
        return userRepository.save(User.builder()
                .tenant(tenant)
                .username(username)
                .email(username + "@example.com")
                .passwordHash("(테스트 — 로그인하지 않는다)")
                .role(UserRole.USER)
                .build());
    }

    private Item newItem(User seller, String name) {
        return itemRepository.save(Item.builder()
                .tenant(tenant)
                .seller(seller)
                .name(name)
                .description("스코프 테스트용")
                .saleType(SaleType.FIXED_PRICE)
                .price(new BigDecimal("10000"))
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
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("gimp-backend")
                .issuedAt(now)
                .expiresAt(now.plus(1, ChronoUnit.HOURS))
                .subject(String.valueOf(user.getId()))
                .claim(Claims.TENANT_ID, tenant.getId())
                .claim(Claims.TENANT_CODE, tenant.getCode())
                .claim(Claims.ROLE, UserRole.USER.name())
                .build();
        return jwtEncoder
                .encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();
    }
}
