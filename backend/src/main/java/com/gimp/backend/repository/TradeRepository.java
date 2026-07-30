package com.gimp.backend.repository;

import com.gimp.backend.domain.trade.Trade;
import com.gimp.backend.domain.trade.TradeStatus;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TradeRepository extends JpaRepository<Trade, Long> {

    Optional<Trade> findByItemIdAndStatus(Long itemId, TradeStatus status);
}
