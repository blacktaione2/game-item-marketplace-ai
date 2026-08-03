package com.gimp.backend.config;

import jakarta.annotation.PostConstruct;
import java.util.ArrayList;
import java.util.List;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

/**
 * 기본 시크릿으로는 운영 프로파일이 뜨지 않는다 (ADR-0031).
 *
 * <h2>왜 경고가 아니라 기동 거부인가</h2>
 *
 * 저장소에 기본값이 그대로 있다 — {@code gimp_local_pw},
 * {@code gimp_local_dev_secret_change_me_32b}. 로컬 개발에는 그게 편하고, 그래서
 * <b>바꾸는 것을 잊기 쉽다.</b> 경고 로그는 기동 로그에 묻힌다.
 *
 * <p>{@code loadtest} 프로파일을 별도 파일로 뺀 것과 같은 발상이다(ADR-0024) —
 * <b>구조적으로 잊을 수 없게</b> 만든다. 다만 그쪽은 "완화된 값이 파일에 남지 않게"였고
 * 이쪽은 "위험한 값으로는 뜨지 않게"다.
 *
 * <h2>{@code prod} 프로파일에서만 동작한다</h2>
 *
 * 로컬·CI·테스트는 기본값으로 돌아야 한다. 공개 배포만
 * {@code SPRING_PROFILES_ACTIVE=prod} 로 띄우고, 그때만 이 검사가 산다.
 *
 * <p><b>이 클래스가 있다는 것 자체는 방어가 아니다.</b> {@code prod} 로 띄우지 않으면
 * 아무 일도 하지 않는다 — 배포 절차 문서가 그 프로파일을 명시해야 실질 방어가 된다.
 */
@Slf4j
@Configuration
@Profile("prod")
public class SecretGuard {

    /** 저장소에 커밋된 기본값들. 여기 있는 값으로는 {@code prod} 가 뜨지 않는다. */
    private static final List<String> FORBIDDEN = List.of(
            "gimp_local_pw", "gimp_local_dev_secret_change_me_32b");

    @Value("${jwt.secret}")
    private String jwtSecret;

    @Value("${spring.datasource.password}")
    private String dbPassword;

    @Value("${spring.data.redis.password}")
    private String redisPassword;

    @Value("${spring.rabbitmq.password}")
    private String rabbitPassword;

    @Value("${demo.password:}")
    private String demoPassword;

    @Value("${demo.admin-password:}")
    private String adminPassword;

    @PostConstruct
    void verify() {
        List<String> problems = new ArrayList<>();

        check(problems, "JWT_SECRET", jwtSecret);
        check(problems, "DB_PASSWORD", dbPassword);
        check(problems, "REDIS_PASSWORD", redisPassword);
        check(problems, "RABBITMQ_PASSWORD", rabbitPassword);

        // 데모 비밀번호는 기본값이 없다 — 비어 있으면 아무도 로그인 못 한다.
        // prod 에서 그 상태로 뜨는 것은 배포가 아니라 사고다.
        if (demoPassword.isBlank()) {
            problems.add("DEMO_PASSWORD 가 비어 있습니다 (로그인 불가)");
        }
        if (adminPassword.isBlank()) {
            problems.add("ADMIN_PASSWORD 가 비어 있습니다 (GM 로그인 불가)");
        }
        // 둘이 같으면 비밀번호를 나눈 의미가 없다 — 데모 비밀번호를 아는 사람이
        // 곧바로 GM 이 된다. 이건 "설정은 했는데 잘못한" 경우라 값 비교가 필요하다.
        if (!demoPassword.isBlank() && demoPassword.equals(adminPassword)) {
            problems.add("DEMO_PASSWORD 와 ADMIN_PASSWORD 가 같습니다 (역할 분리 무효)");
        }

        if (!problems.isEmpty()) {
            // **값은 로그에 남기지 않는다.** 어떤 항목이 문제인지만 말한다.
            throw new IllegalStateException(
                    "운영 프로파일(prod)을 기본 시크릿으로 기동할 수 없습니다: " + problems);
        }
        log.info("시크릿 검사 통과 — 기본값이 남아 있지 않습니다.");
    }

    private void check(List<String> problems, String name, String value) {
        if (value != null && FORBIDDEN.contains(value)) {
            problems.add(name + " 가 저장소의 기본값 그대로입니다");
        }
    }
}
