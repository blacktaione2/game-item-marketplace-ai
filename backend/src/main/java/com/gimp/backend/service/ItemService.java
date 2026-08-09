package com.gimp.backend.service;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.ItemStatus;
import com.gimp.backend.domain.tenant.Tenant;
import com.gimp.backend.domain.user.User;
import com.gimp.backend.dto.item.ItemCreateRequest;
import com.gimp.backend.dto.item.ItemResponse;
import com.gimp.backend.dto.item.ItemUpdateRequest;
import com.gimp.backend.exception.InvalidTradeRequestException;
import com.gimp.backend.exception.ResourceNotFoundException;
import com.gimp.backend.repository.ItemRepository;
import com.gimp.backend.repository.TenantRepository;
import com.gimp.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class ItemService {

    private final ItemRepository itemRepository;
    private final UserRepository userRepository;
    private final TenantRepository tenantRepository;

    @Transactional
    public ItemResponse create(Long tenantId, Long sellerId, ItemCreateRequest request) {
        Tenant tenant = getTenant(tenantId);
        User seller = getUser(tenantId, sellerId);

        Item item = Item.builder()
                .tenant(tenant)
                .seller(seller)
                .name(request.name())
                .description(request.description())
                .saleType(request.saleType())
                .price(request.price())
                .stock(request.stock())
                .build();

        return ItemResponse.from(itemRepository.save(item));
    }

    public ItemResponse get(Long tenantId, Long itemId) {
        return ItemResponse.from(getItem(tenantId, itemId));
    }

    public Page<ItemResponse> list(Long tenantId, Pageable pageable) {
        // 논리 삭제(CLOSED)는 목록에서 뺀다 — ADR-0003 이 지시하고 놓쳤던 필터.
        return itemRepository
                .findAllByTenantIdAndStatusNot(tenantId, ItemStatus.CLOSED, pageable)
                .map(ItemResponse::from);
    }

    @Transactional
    public ItemResponse update(Long tenantId, Long itemId, Long requesterId, ItemUpdateRequest request) {
        Item item = getItem(tenantId, itemId);
        requireSeller(item, requesterId);

        item.updateInfo(request.name(), request.description(), request.price());
        return ItemResponse.from(item);
    }

    /** 거래 이력이 item_id FK로 물려 있어 물리 삭제 대신 CLOSED로 상태 전환한다. */
    @Transactional
    public void delete(Long tenantId, Long itemId, Long requesterId) {
        Item item = getItem(tenantId, itemId);
        requireSeller(item, requesterId);
        item.close();
    }

    private void requireSeller(Item item, Long requesterId) {
        if (!item.getSeller().getId().equals(requesterId)) {
            throw new InvalidTradeRequestException("아이템 등록자만 수정/삭제할 수 있습니다.");
        }
    }

    private Item getItem(Long tenantId, Long itemId) {
        return itemRepository
                .findByIdAndTenantId(itemId, tenantId)
                .orElseThrow(() -> new ResourceNotFoundException("아이템을 찾을 수 없습니다. id=" + itemId));
    }

    private User getUser(Long tenantId, Long userId) {
        return userRepository
                .findByIdAndTenantId(userId, tenantId)
                .orElseThrow(() -> new ResourceNotFoundException("유저를 찾을 수 없습니다. id=" + userId));
    }

    private Tenant getTenant(Long tenantId) {
        return tenantRepository
                .findById(tenantId)
                .orElseThrow(() -> new ResourceNotFoundException("테넌트를 찾을 수 없습니다. id=" + tenantId));
    }
}
