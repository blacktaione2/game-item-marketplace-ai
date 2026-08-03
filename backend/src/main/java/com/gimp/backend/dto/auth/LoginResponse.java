package com.gimp.backend.dto.auth;

public record LoginResponse(
        String token, long expiresIn, Long userId, String username, String role) {}
