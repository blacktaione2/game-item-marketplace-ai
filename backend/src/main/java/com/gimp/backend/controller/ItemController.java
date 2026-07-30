package com.gimp.backend.controller;

import com.gimp.backend.dto.item.ItemCreateRequest;
import com.gimp.backend.dto.item.ItemResponse;
import com.gimp.backend.dto.item.ItemUpdateRequest;
import com.gimp.backend.service.ItemService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * X-Tenant-Id / X-User-Id 헤더로 테넌트·행위자를 식별한다. 인증(JWT) 도입 전까지의 임시
 * 방편이며, 추후 JWT 클레임 추출로 교체될 자리라 헤더 파싱을 이 레이어에 고정해두었다.
 */
@RestController
@RequestMapping("/api/items")
@RequiredArgsConstructor
public class ItemController {

    private final ItemService itemService;

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ItemResponse create(
            @RequestHeader("X-Tenant-Id") Long tenantId,
            @RequestHeader("X-User-Id") Long sellerId,
            @Valid @RequestBody ItemCreateRequest request) {
        return itemService.create(tenantId, sellerId, request);
    }

    @GetMapping("/{itemId}")
    public ItemResponse get(@RequestHeader("X-Tenant-Id") Long tenantId, @PathVariable Long itemId) {
        return itemService.get(tenantId, itemId);
    }

    @GetMapping
    public Page<ItemResponse> list(@RequestHeader("X-Tenant-Id") Long tenantId, Pageable pageable) {
        return itemService.list(tenantId, pageable);
    }

    @PutMapping("/{itemId}")
    public ItemResponse update(
            @RequestHeader("X-Tenant-Id") Long tenantId,
            @RequestHeader("X-User-Id") Long requesterId,
            @PathVariable Long itemId,
            @Valid @RequestBody ItemUpdateRequest request) {
        return itemService.update(tenantId, itemId, requesterId, request);
    }

    @DeleteMapping("/{itemId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(
            @RequestHeader("X-Tenant-Id") Long tenantId,
            @RequestHeader("X-User-Id") Long requesterId,
            @PathVariable Long itemId) {
        itemService.delete(tenantId, itemId, requesterId);
    }
}
