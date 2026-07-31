package com.gimp.backend.controller;

import com.gimp.backend.dto.item.ItemCreateRequest;
import com.gimp.backend.dto.item.ItemResponse;
import com.gimp.backend.dto.item.ItemUpdateRequest;
import com.gimp.backend.security.Actor;
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
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * 테넌트·행위자는 검증된 JWT 클레임에서 온다({@link Actor}). 예전에는 X-Tenant-Id / X-User-Id 헤더였는데,
 * 헤더는 누구나 아무 값이나 보낼 수 있어서 테넌트 격리가 성립하지 않았다(ADR-0023).
 *
 * <p>서비스 계층 시그니처는 그대로다 — 바뀐 것은 tenantId가 <b>어디서 오는가</b>뿐이다.
 */
@RestController
@RequestMapping("/api/items")
@RequiredArgsConstructor
public class ItemController {

    private final ItemService itemService;

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ItemResponse create(Actor actor, @Valid @RequestBody ItemCreateRequest request) {
        return itemService.create(actor.tenantId(), actor.userId(), request);
    }

    @GetMapping("/{itemId}")
    public ItemResponse get(Actor actor, @PathVariable Long itemId) {
        return itemService.get(actor.tenantId(), itemId);
    }

    @GetMapping
    public Page<ItemResponse> list(Actor actor, Pageable pageable) {
        return itemService.list(actor.tenantId(), pageable);
    }

    @PutMapping("/{itemId}")
    public ItemResponse update(
            Actor actor, @PathVariable Long itemId, @Valid @RequestBody ItemUpdateRequest request) {
        return itemService.update(actor.tenantId(), itemId, actor.userId(), request);
    }

    @DeleteMapping("/{itemId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(Actor actor, @PathVariable Long itemId) {
        itemService.delete(actor.tenantId(), itemId, actor.userId());
    }
}
