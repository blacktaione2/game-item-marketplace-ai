package com.gimp.backend.dto.auth;

public record DemoTokenResponse(
        String token, long expiresIn, Long userId, String username, String role) {}
