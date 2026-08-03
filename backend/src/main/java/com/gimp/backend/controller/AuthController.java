package com.gimp.backend.controller;

import com.gimp.backend.dto.auth.LoginRequest;
import com.gimp.backend.dto.auth.LoginResponse;
import com.gimp.backend.service.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 로그인 (ADR-0031).
 *
 * <p><b>{@code /demo-token} 은 제거됐다.</b> 그건 비밀번호를 확인하지 않아 userId 만 알면
 * 누구나 그 사용자의 토큰을 받을 수 있었고, 공개 배포에서는 성립하지 않는 전제였다.
 * 경로가 사라졌다는 것 자체를 테스트가 404 로 고정한다.
 *
 * <p>회원가입 경로는 <b>일부러 없다</b> — 계정은 시드로 고정이다. 사유는 {@code AuthService}.
 */
@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/login")
    public LoginResponse login(@Valid @RequestBody LoginRequest request) {
        return authService.login(request.username(), request.password());
    }
}
