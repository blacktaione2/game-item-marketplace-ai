package com.gimp.backend.controller;

import com.gimp.backend.dto.trade.TradeHistoryResponse;
import com.gimp.backend.repository.TradeRepository;
import com.gimp.backend.security.Actor;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 내 거래 내역.
 *
 * <p><b>{@link TradeController} 와 나눈 이유는 경로다.</b> 그쪽은
 * {@code /api/items/{itemId}} 아래에 있다 — 체결은 항상 특정 아이템에 대한 행위이기
 * 때문이다. 내역은 아이템이 아니라 <b>사람</b>에 매달리므로 같은 경로 아래 둘 수 없다.
 *
 * <p><b>조회자는 토큰에서만 온다.</b> {@code ?userId=} 를 받으면 남의 거래 내역을 읽을 수
 * 있다 — {@link NotificationController} 와 같은 이유이고, 신원을 요청이 주장하게 두지
 * 않는다는 ADR-0023 의 원칙이다.
 */
@RestController
@RequestMapping("/api/trades")
@RequiredArgsConstructor
public class TradeHistoryController {

    /**
     * 페이지네이션을 두지 않고 상한만 둔다.
     *
     * <p>데모 계정의 거래는 수십 건이라 더 필요할 일이 없고, 무한 스크롤이나 커서를 붙이면
     * 화면과 API 양쪽에 상태가 는다. <b>상한이 없으면</b> 부하테스트로 수만 건이 쌓인 계정에서
     * 이 화면이 응답을 통째로 밀어 넣는다는 게 진짜 이유다.
     */
    private static final int MAX_ITEMS = 50;

    private final TradeRepository tradeRepository;

    @GetMapping
    public List<TradeHistoryResponse> mine(Actor actor) {
        return tradeRepository
                .findMine(actor.tenantId(), actor.userId(), PageRequest.of(0, MAX_ITEMS))
                .stream()
                .map(trade -> TradeHistoryResponse.from(trade, actor.userId()))
                .toList();
    }
}
