package com.gimp.backend.repository;

import com.gimp.backend.domain.item.Item;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ItemRepository extends JpaRepository<Item, Long> {

    Optional<Item> findByIdAndTenantId(Long id, Long tenantId);

    Page<Item> findAllByTenantId(Long tenantId, Pageable pageable);
}
