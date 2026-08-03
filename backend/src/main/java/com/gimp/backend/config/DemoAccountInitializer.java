package com.gimp.backend.config;

import com.gimp.backend.domain.user.User;
import com.gimp.backend.domain.user.UserRole;
import com.gimp.backend.repository.UserRepository;
import java.util.List;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 시드 계정의 비밀번호를 기동 시 주입한다 (ADR-0031).
 *
 * <h2>왜 SQL 이 아니라 기동 시점인가</h2>
 *
 * {@code seed-demo.sql} 은 {@code scripts/export_demo_sql.py} 가 만드는 <b>자동 생성
 * 파일이고 저장소에 커밋된다.</b> 거기에 해시를 박으면 비밀번호가 소스에 남는다.
 * 환경변수로 받아 기동 시 넣으면 저장소에는 아무것도 안 남는다.
 *
 * <h2>왜 비밀번호가 둘인가</h2>
 *
 * 하나면 <b>데모 비밀번호를 아는 사람이 곧바로 GM 이 된다.</b> 이상거래 큐가 GM 전용인
 * 것(ADR-0023 의 역할 인가)이 그 순간 무의미해진다. 그래서 ADMIN 계정만 별도 비밀번호를
 * 쓴다 — 공개 배포에서 README 에 데모 비밀번호를 적어도 GM 은 열리지 않는다.
 *
 * <h2>실패 방향</h2>
 *
 * 값이 비어 있으면 <b>아무것도 하지 않는다.</b> 시드의 자리표시자는 BCrypt 형식이
 * 아니라(29자, 정상은 60자) 어떤 비밀번호와도 매칭되지 않으므로, 설정을 빠뜨린 배포는
 * <b>아무도 로그인하지 못하는 쪽</b>으로 실패한다. 조용히 열리는 것보다 낫다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DemoAccountInitializer implements ApplicationRunner {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    @Value("${demo.password:}")
    private String demoPassword;

    @Value("${demo.admin-password:}")
    private String adminPassword;

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        if (demoPassword.isBlank() && adminPassword.isBlank()) {
            log.warn("데모 계정 비밀번호가 설정되지 않았습니다 — 로그인이 불가능합니다. "
                    + "DEMO_PASSWORD / ADMIN_PASSWORD 를 설정하세요.");
            return;
        }

        List<User> users = userRepository.findAll();
        int updated = 0;
        for (User user : users) {
            String raw = user.getRole() == UserRole.ADMIN ? adminPassword : demoPassword;
            if (raw.isBlank()) {
                continue;
            }
            user.changePassword(passwordEncoder.encode(raw));
            updated++;
        }
        // **비밀번호를 로그에 남기지 않는다.** 개수만 적는다.
        log.info("데모 계정 비밀번호 설정 완료: {}건 (ADMIN 은 별도 비밀번호)", updated);
    }
}
