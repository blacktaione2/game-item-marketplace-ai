package com.gimp.backend.controller;

import com.gimp.backend.dto.auth.DemoTokenRequest;
import com.gimp.backend.dto.auth.DemoTokenResponse;
import com.gimp.backend.service.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 데모 사용자 전환용 토큰 발급.
 *
 * <p><b>경로 이름이 곧 경고다.</b> {@code /login}이 아니라 {@code /demo-token}인 이유는 이것이 인증이 아니기
 * 때문이다 — 비밀번호를 확인하지 않으므로 <b>userId만 알면 누구나 그 사용자의 토큰을 받는다.</b> 이 서버를
 * 외부에 노출하면 안 되는 이유가 여전히 유효하고, README에도 같은 경고가 있다.
 *
 * <p>발급이 데모라고 해서 검증도 데모인 것은 아니다. 발급된 토큰은 서명·만료·클레임이 전부 실제로 검증된다
 * (ADR-0023).
 */
@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/demo-token")
    public DemoTokenResponse demoToken(@Valid @RequestBody DemoTokenRequest request) {
        return authService.issueDemoToken(request.userId());
    }
}
