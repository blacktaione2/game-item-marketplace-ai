package com.gimp.backend.repository;

import com.gimp.backend.domain.user.User;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserRepository extends JpaRepository<User, Long> {

    Optional<User> findByIdAndTenantId(Long id, Long tenantId);

    /**
     * 로그인용 (ADR-0034).
     *
     * <p><b>테넌트가 조건에 들어간다.</b> {@code users} 의 유니크 제약이
     * {@code (tenant_id, username)} 이므로 <b>username 만으로는 행이 하나로 좁혀지지
     * 않는다.</b>
     *
     * <p>이전 버전은 {@code findByUsername} 하나였고 javadoc 에 "데모 계정이 한
     * 테넌트뿐이라 username 은 전역 유일하다"고 적혀 있었다 — <b>스키마가 보장하지 않는
     * 가정</b>이었다. 실제로 두 번째 테넌트에 같은 아이디를 넣자
     * {@code NonUniqueResultException} 이 났고, 그 예외가 401 로 나가서
     * <b>"비밀번호가 틀렸다"로 보였다</b>(ADR-0034).
     *
     * <p>{@code Tenant.code} 는 전역 유니크라 조회 키로 성립한다.
     */
    Optional<User> findByTenant_CodeAndUsername(String tenantCode, String username);
}
