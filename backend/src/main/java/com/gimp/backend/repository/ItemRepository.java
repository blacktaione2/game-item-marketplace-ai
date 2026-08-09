package com.gimp.backend.repository;

import com.gimp.backend.domain.item.Item;
import com.gimp.backend.domain.item.ItemStatus;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ItemRepository extends JpaRepository<Item, Long> {

    Optional<Item> findByIdAndTenantId(Long id, Long tenantId);

    /**
     * 목록에서 <b>논리 삭제된 아이템을 뺀다</b> (ADR-0003, ADR-0047).
     *
     * <p>ADR-0003 은 {@code CLOSED} 를 물리 삭제 대신 쓰기로 정하면서 <i>"검색 결과에서
     * 제외하는 필터를 반드시 넣어야 한다 — 이 ADR을 참고해서 놓치지 않을 것"</i> 이라고
     * 적었는데, <b>넣지 않았다.</b> 그래서 판매자가 지운 아이템이 목록 화면(ADR-0037)에
     * 계속 떴다. 거래는 막혀 있었으므로(구매·입찰 모두 {@code isOnSale()} 검사) 살 수는
     * 없었고, <b>목록에만 남아 클릭하면 거절되는</b> 상태였다.
     *
     * <p><b>{@code SOLD_OUT} 은 빼지 않는다.</b> 그건 재고가 0인 것이지 삭제가 아니고,
     * 화면이 품절로 보여주는 것이 맞다 — ADR-0003 이 두 상태를 구분한 이유가 그것이다.
     */
    Page<Item> findAllByTenantIdAndStatusNot(
            Long tenantId, ItemStatus status, Pageable pageable);
}
