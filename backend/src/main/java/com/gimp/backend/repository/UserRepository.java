package com.gimp.backend.repository;

import com.gimp.backend.domain.user.User;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserRepository extends JpaRepository<User, Long> {

    Optional<User> findByIdAndTenantId(Long id, Long tenantId);

    /**
     * 로그인용 (ADR-0031).
     *
     * <p>테넌트를 조건에 넣지 않는다 — 로그인 시점에는 <b>아직 테넌트를 모른다.</b>
     * 그게 토큰에 실릴 값이고, 요청이 주장하게 두면 예전 헤더 방식과 같아진다.
     * 데모 계정이 한 테넌트뿐이라 username 은 전역 유일하다.
     */
    Optional<User> findByUsername(String username);
}
