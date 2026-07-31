package com.gimp.backend.controller;

import com.gimp.backend.dto.trade.BidRequest;
import com.gimp.backend.dto.trade.PurchaseRequest;
import com.gimp.backend.dto.trade.TradeResponse;
import com.gimp.backend.security.Actor;
import com.gimp.backend.service.TradeService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 구매자·입찰자는 검증된 토큰에서 온다 — 예전의 X-User-Id 헤더는 자칭이었다(ADR-0023). */
@RestController
@RequestMapping("/api/items/{itemId}")
@RequiredArgsConstructor
public class TradeController {

    private final TradeService tradeService;

    @PostMapping("/purchase")
    @ResponseStatus(HttpStatus.CREATED)
    public TradeResponse purchase(
            Actor actor, @PathVariable Long itemId, @Valid @RequestBody PurchaseRequest request) {
        return tradeService.purchase(actor.tenantId(), itemId, actor.userId(), request.quantity());
    }

    @PostMapping("/bids")
    @ResponseStatus(HttpStatus.CREATED)
    public TradeResponse bid(
            Actor actor, @PathVariable Long itemId, @Valid @RequestBody BidRequest request) {
        return tradeService.bid(actor.tenantId(), itemId, actor.userId(), request.bidPrice());
    }
}
