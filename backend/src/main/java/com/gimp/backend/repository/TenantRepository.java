package com.gimp.backend.repository;

import com.gimp.backend.domain.tenant.Tenant;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TenantRepository extends JpaRepository<Tenant, Long> {}
