package com.gimp.backend.dto.health;

/** ai/app/routers/health.py의 GET /health 응답 스키마와 일치해야 한다. */
public record AiHealthResponse(String status, String service) {}
