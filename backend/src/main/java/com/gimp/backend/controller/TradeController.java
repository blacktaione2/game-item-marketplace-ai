package com.gimp.backend.controller;

import com.gimp.backend.dto.trade.BidRequest;
import com.gimp.backend.dto.trade.PurchaseRequest;
import com.gimp.backend.dto.trade.TradeResponse;
import com.gimp.backend.service.TradeService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/items/{itemId}")
@RequiredArgsConstructor
public class TradeController {

    private final TradeService tradeService;

    @PostMapping("/purchase")
    @ResponseStatus(HttpStatus.CREATED)
    public TradeResponse purchase(
            @RequestHeader("X-Tenant-Id") Long tenantId,
            @RequestHeader("X-User-Id") Long buyerId,
            @PathVariable Long itemId,
            @Valid @RequestBody PurchaseRequest request) {
        return tradeService.purchase(tenantId, itemId, buyerId, request.quantity());
    }

    @PostMapping("/bids")
    @ResponseStatus(HttpStatus.CREATED)
    public TradeResponse bid(
            @RequestHeader("X-Tenant-Id") Long tenantId,
            @RequestHeader("X-User-Id") Long bidderId,
            @PathVariable Long itemId,
            @Valid @RequestBody BidRequest request) {
        return tradeService.bid(tenantId, itemId, bidderId, request.bidPrice());
    }
}
