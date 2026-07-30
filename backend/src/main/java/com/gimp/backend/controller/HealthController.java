package com.gimp.backend.controller;

import com.gimp.backend.client.AiServerClient;
import com.gimp.backend.dto.health.AiHealthResponse;
import com.gimp.backend.dto.health.HealthResponse;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Spring Boot(8080) ↔ FastAPI(8000) 간 통신 확인용. Phase 2 범위. */
@RestController
@RequestMapping("/api/health")
@RequiredArgsConstructor
public class HealthController {

    private final AiServerClient aiServerClient;

    @GetMapping
    public HealthResponse health() {
        Optional<AiHealthResponse> aiHealth = aiServerClient.checkHealth();
        return new HealthResponse("UP", aiHealth.isPresent() ? "UP" : "DOWN", aiHealth.orElse(null));
    }
}
