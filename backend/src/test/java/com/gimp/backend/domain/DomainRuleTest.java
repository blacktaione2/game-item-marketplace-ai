package com.gimp.backend.domain;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.ItemStatus;
import com.gimp.backend.domain.item.SaleType;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.TradeRepository;
import com.gimp.backend.repository.UserRepository;
import com.gimp.backend.security.Claims;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
// Boot 4에서 패키지가 재배치됐다 —
// docs/05-Troubleshooting/spring-boot-4-autoconfigure-공통패턴.md 참고.
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

/**
 * 거래 도메인 규칙 테스트 — 권한 / 상태 전이 / 유효성.
 *
 * <p><b>부하테스트가 덮는 것과 겹치지 않는다.</b> ADR-0020은 오버셀 0건을 <b>동시성</b>에서
 * 검증했지만, "경매 아이템을 즉시구매로 살 수 있는가" 같은 규칙은 한 명만 요청해도 성립해야
 * 하는 것이라 부하로는 확인되지 않는다. 그동안 수동 curl로만 검증하던 영역이다.
 *
 * <p><b>시드 데이터에 의존하지 않는다.</b> CI는 빈 DB로 뜨므로 필요한 행을 직접 만든다
 * ({@code AuthenticationTest}와 같은 이유).
 *
 * <p><b>거절이 전부 409인 것은 현재 동작을 고정한 것이지 옳다는 뜻이 아니다.</b>
 * {@code InvalidTradeRequestException}이 한 개라 "권한 없음"과 "재고 부족"이 같은 코드로 나간다.
 * 권한 위반은 403이 맞지만 <b>이번 라운드에서 바꾸지 않았다</b> — 응답 코드 변경은 프론트와
 * k6 스크립트에 동시에 영향을 주므로 별도 건이다. 로드맵에 등록했다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class DomainRuleTest {

    @Autowired MockMvc mockMvc;
    @Autowired JwtEncoder jwtEncoder;
    @Autowired TenantRepository tenantRepository;
    @Autowired UserRepository userRepository;
    @Autowired ItemRepository itemRepository;
    @Autowired TradeRepository tradeRepository;

    private Tenant tenant;
    private User seller;
    private User buyer;
    private User otherBuyer;

    private Item fixedPriceItem; // stock 5, 10,000원
    private Item auctionItem; // 시작가 10,000원

    @BeforeEach
    void setUp() {
        tenant = tenantRepository.save(
                Tenant.builder().code("rule_t").name("규칙 테스트 테넌트").build());
        seller = saveUser("rule_seller");
        buyer = saveUser("rule_buyer");
        otherBuyer = saveUser("rule_other");
        fixedPriceItem = saveItem("정가 아이템", SaleType.FIXED_PRICE, 5);
        auctionItem = saveItem("경매 아이템", SaleType.AUCTION, 1);
    }

    // --- 아이템 권한과 상태 전이 -------------------------------------------

    @Nested
    class 아이템 {

        @Test
        void 등록자가_아니면_수정할_수_없다() throws Exception {
            mockMvc.perform(put("/api/items/" + fixedPriceItem.getId())
                            .header("Authorization", bearer(buyer))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"가로챈 이름\",\"price\":1}"))
                    .andExpect(status().isConflict());
        }

        @Test
        void 등록자는_수정할_수_있다() throws Exception {
            mockMvc.perform(put("/api/items/" + fixedPriceItem.getId())
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"바뀐 이름\",\"price\":12000}"))
                    .andExpect(status().isOk());

            assertThat(reload(fixedPriceItem).getName()).isEqualTo("바뀐 이름");
        }

        @Test
        void 등록자가_아니면_삭제할_수_없다() throws Exception {
            mockMvc.perform(delete("/api/items/" + fixedPriceItem.getId())
                            .header("Authorization", bearer(buyer)))
                    .andExpect(status().isConflict());
        }

        /**
         * 삭제가 물리 삭제였다면 거래 이력의 item_id FK가 끊어진다. 그래서 CLOSED 전환이고,
         * 이건 구현 세부가 아니라 <b>지켜야 할 규칙</b>이라 단언한다.
         */
        @Test
        void 삭제는_행을_지우지_않고_CLOSED로_바꾼다() throws Exception {
            mockMvc.perform(delete("/api/items/" + fixedPriceItem.getId())
                            .header("Authorization", bearer(seller)))
                    .andExpect(status().isNoContent());

            Item closed = reload(fixedPriceItem);
            assertThat(closed).isNotNull();
            assertThat(closed.getStatus()).isEqualTo(ItemStatus.CLOSED);
        }

        @Test
        void 가격이_0이하면_등록되지_않는다() throws Exception {
            mockMvc.perform(post("/api/items")
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"공짜 검\",\"saleType\":\"FIXED_PRICE\","
                                    + "\"price\":0,\"stock\":1}"))
                    .andExpect(status().isBadRequest());
        }

        @Test
        void 이름이_비면_등록되지_않는다() throws Exception {
            mockMvc.perform(post("/api/items")
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"  \",\"saleType\":\"FIXED_PRICE\","
                                    + "\"price\":1000,\"stock\":1}"))
                    .andExpect(status().isBadRequest());
        }

        /**
         * 컬럼이 {@code varchar(200)} 인데 DTO 에 {@code @Size} 가 없어서 201자가 검증을
         * 통과하고 <b>INSERT 시점에 터져 500</b> 이 나왔다 (ADR-0035, 실측 300자 → 500).
         *
         * <p><b>경계로 잰다.</b> "길다"만 확인하면 상한을 하나 어긋나게 잡아도 통과한다.
         */
        @Test
        void 이름이_200자를_넘으면_400이다() throws Exception {
            mockMvc.perform(post("/api/items")
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"" + "A".repeat(201) + "\",\"saleType\":\"FIXED_PRICE\","
                                    + "\"price\":1000,\"stock\":1}"))
                    .andExpect(status().isBadRequest());
        }

        @Test
        void 이름이_정확히_200자면_등록된다() throws Exception {
            mockMvc.perform(post("/api/items")
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"" + "A".repeat(200) + "\",\"saleType\":\"FIXED_PRICE\","
                                    + "\"price\":1000,\"stock\":1}"))
                    .andExpect(status().isCreated());
        }

        /** 등록만 막고 수정을 열어두면 같은 결함이 경로 하나로 남는다. */
        @Test
        void 수정에서도_이름_길이가_막힌다() throws Exception {
            mockMvc.perform(put("/api/items/" + fixedPriceItem.getId())
                            .header("Authorization", bearer(seller))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"name\":\"" + "A".repeat(201) + "\",\"price\":1000}"))
                    .andExpect(status().isBadRequest());
        }
    }

    // --- 구매 ---------------------------------------------------------------

    @Nested
    class 구매 {

        @Test
        void 정상_구매는_재고를_줄이고_체결된다() throws Exception {
            mockMvc.perform(purchase(fixedPriceItem, buyer, 2)).andExpect(status().isCreated());

            assertThat(reload(fixedPriceItem).getStock()).isEqualTo(3);
            assertThat(tradesOf(fixedPriceItem))
                    .singleElement()
                    .extracting(Trade::getStatus)
                    .isEqualTo(TradeStatus.COMPLETED);
        }

        @Test
        void 경매_아이템은_즉시구매할_수_없다() throws Exception {
            mockMvc.perform(purchase(auctionItem, buyer, 1)).andExpect(status().isConflict());
        }

        @Test
        void 본인이_등록한_아이템은_구매할_수_없다() throws Exception {
            mockMvc.perform(purchase(fixedPriceItem, seller, 1)).andExpect(status().isConflict());
        }

        @Test
        void 재고보다_많이_살_수_없다() throws Exception {
            mockMvc.perform(purchase(fixedPriceItem, buyer, 6)).andExpect(status().isConflict());

            assertThat(reload(fixedPriceItem).getStock()).isEqualTo(5); // 거절 시 재고는 그대로
        }

        @Test
        void 수량이_0이하면_거절된다() throws Exception {
            mockMvc.perform(purchase(fixedPriceItem, buyer, 0)).andExpect(status().isBadRequest());
        }

        /** 상태 전이 — 재고가 0이 되면 SOLD_OUT이 되고, 그 뒤로는 판매중이 아니다. */
        @Test
        void 재고를_다_사면_SOLD_OUT이_되고_추가_구매가_막힌다() throws Exception {
            mockMvc.perform(purchase(fixedPriceItem, buyer, 5)).andExpect(status().isCreated());
            assertThat(reload(fixedPriceItem).getStatus()).isEqualTo(ItemStatus.SOLD_OUT);

            mockMvc.perform(purchase(fixedPriceItem, otherBuyer, 1)).andExpect(status().isConflict());
        }

        @Test
        void 삭제된_아이템은_구매할_수_없다() throws Exception {
            fixedPriceItem.close();
            itemRepository.flush();

            mockMvc.perform(purchase(fixedPriceItem, buyer, 1)).andExpect(status().isConflict());
        }
    }

    // --- 입찰 ---------------------------------------------------------------

    @Nested
    class 입찰 {

        @Test
        void 정가_아이템에는_입찰할_수_없다() throws Exception {
            mockMvc.perform(bid(fixedPriceItem, buyer, "20000")).andExpect(status().isConflict());
        }

        @Test
        void 본인이_등록한_아이템에는_입찰할_수_없다() throws Exception {
            mockMvc.perform(bid(auctionItem, seller, "20000")).andExpect(status().isConflict());
        }

        @Test
        void 시작가_이하는_입찰할_수_없다() throws Exception {
            // 시작가와 같은 금액도 거절된다 — 조건이 `<= 0`이라 동률은 갱신이 아니다.
            mockMvc.perform(bid(auctionItem, buyer, "10000")).andExpect(status().isConflict());
        }

        @Test
        void 첫_입찰은_현재가를_갱신한다() throws Exception {
            mockMvc.perform(bid(auctionItem, buyer, "11000")).andExpect(status().isCreated());

            assertThat(reload(auctionItem).minimumAcceptableBid())
                    .isEqualByComparingTo(new BigDecimal("11000"));
        }

        @Test
        void 현재_최고가_이하로는_재입찰할_수_없다() throws Exception {
            mockMvc.perform(bid(auctionItem, buyer, "11000")).andExpect(status().isCreated());

            mockMvc.perform(bid(auctionItem, otherBuyer, "10500")).andExpect(status().isConflict());
        }

        /**
         * 상태 전이 — 더 높은 입찰이 들어오면 <b>이전 입찰이 OUTBID로 바뀐다.</b> ACTIVE가 둘이면
         * 낙찰 대상이 둘이 되므로, 이 전이가 이 도메인에서 가장 조용히 깨질 수 있는 규칙이다.
         */
        @Test
        void 더_높은_입찰이_들어오면_이전_입찰은_OUTBID가_된다() throws Exception {
            mockMvc.perform(bid(auctionItem, buyer, "11000")).andExpect(status().isCreated());
            mockMvc.perform(bid(auctionItem, otherBuyer, "12000")).andExpect(status().isCreated());

            List<Trade> trades = tradesOf(auctionItem);
            assertThat(trades).hasSize(2);
            assertThat(trades)
                    .filteredOn(t -> t.getStatus() == TradeStatus.ACTIVE)
                    .singleElement()
                    .extracting(t -> t.getBuyer().getId())
                    .isEqualTo(otherBuyer.getId());
            assertThat(trades)
                    .filteredOn(t -> t.getStatus() == TradeStatus.OUTBID)
                    .singleElement()
                    .extracting(t -> t.getBuyer().getId())
                    .isEqualTo(buyer.getId());
        }
    }

    // --- 헬퍼 ---------------------------------------------------------------

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder purchase(
            Item item, User actor, int quantity) {
        return post("/api/items/" + item.getId() + "/purchase")
                .header("Authorization", bearer(actor))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"quantity\":" + quantity + "}");
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder bid(
            Item item, User actor, String price) {
        return post("/api/items/" + item.getId() + "/bids")
                .header("Authorization", bearer(actor))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"bidPrice\":" + price + "}");
    }

    private List<Trade> tradesOf(Item item) {
        return tradeRepository.findAll().stream()
                .filter(t -> t.getItem().getId().equals(item.getId()))
                .toList();
    }

    private Item reload(Item item) {
        itemRepository.flush();
        return itemRepository.findById(item.getId()).orElseThrow();
    }

    private User saveUser(String username) {
        return userRepository.save(User.builder()
                .tenant(tenant)
                .username(username)
                .email(username + "@example.com")
                .passwordHash("(데모 — 비밀번호 인증 없음)")
                .role(UserRole.USER)
                .build());
    }

    private Item saveItem(String name, SaleType saleType, int stock) {
        return itemRepository.save(Item.builder()
                .tenant(tenant)
                .seller(seller)
                .name(name)
                .description("도메인 규칙 테스트용")
                .saleType(saleType)
                .price(new BigDecimal("10000"))
                .stock(stock)
                .build());
    }

    private String bearer(User actor) {
        Instant now = Instant.now();
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("gimp-backend")
                .issuedAt(now)
                .expiresAt(now.plus(1, ChronoUnit.HOURS))
                .subject(String.valueOf(actor.getId()))
                .claim(Claims.TENANT_ID, tenant.getId())
                .claim(Claims.TENANT_CODE, tenant.getCode())
                .claim(Claims.ROLE, actor.getRole().name())
                .build();
        return "Bearer "
                + jwtEncoder
                        .encode(JwtEncoderParameters.from(
                                JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                        .getTokenValue();
    }
}
