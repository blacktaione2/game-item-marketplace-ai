package com.gimp.backend.repository;

import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface TradeRepository extends JpaRepository<Trade, Long> {

    /**
     * 이 아이템의 진행 중인 입찰.
     *
     * <p><b>테넌트를 조건에 넣는다</b> — 아래 {@code findMine} 이 적어둔 정책과 같다. 호출부는
     * 이미 {@code findByIdAndTenantId} 로 아이템을 확인한 뒤라 {@code itemId} 만으로도 결과는
     * 같지만, 그건 격리가 <b>아이템-테넌트 관계</b>에 얹혀 있다는 뜻이다.
     */
    Optional<Trade> findByTenantIdAndItemIdAndStatus(
            Long tenantId, Long itemId, TradeStatus status);

    /**
     * 내가 관여한 거래. <b>구매자와 판매자 양쪽</b>을 본다 — 한 사람이 사기도 하고 팔기도 한다.
     *
     * <p><b>테넌트를 조건에 명시한다.</b> 사용자는 한 테넌트에만 속하므로 {@code userId} 만으로도
     * 결과는 같지만, 격리를 사용자-테넌트 관계에 의존시키면 그 관계가 바뀌는 날 조용히 샌다.
     * 테넌트는 토큰에서 오고(ADR-0023) 조건은 여기 남긴다.
     *
     * <p>{@code join fetch} 셋은 N+1 방지용이다. 목록이 아이템 이름과 상대방 이름을 보여주는데,
     * 지연 로딩으로 두면 거래 50건에 쿼리가 151번 나간다. 전부 {@code ManyToOne} 이라
     * {@code Pageable} 이 SQL 로 내려간다 — 컬렉션 fetch 였다면 메모리 페이징이 됐을 것이다.
     */
    @Query(
            """
            select t from Trade t
            join fetch t.item
            join fetch t.buyer
            join fetch t.seller
            where t.tenant.id = :tenantId
              and (t.buyer.id = :userId or t.seller.id = :userId)
            order by t.id desc
            """)
    List<Trade> findMine(
            @Param("tenantId") Long tenantId, @Param("userId") Long userId, Pageable pageable);
}
