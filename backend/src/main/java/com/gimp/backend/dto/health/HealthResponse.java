package com.gimp.backend.dto.health;

public record HealthResponse(String backend, String aiServerStatus, AiHealthResponse aiServer) {}
