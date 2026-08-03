package com.gimp.backend.controller;

import com.gimp.backend.dto.notification.NotificationResponse;
import com.gimp.backend.repository.NotificationRepository;
import com.gimp.backend.security.Actor;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 알림 조회 (ADR-0030).
 *
 * <p><b>수신자는 토큰에서 온다.</b> 쿼리 파라미터로 받으면 남의 알림을 읽을 수 있다 —
 * 신원을 요청이 주장하게 두지 않는다는 ADR-0023 의 원칙이 여기에도 적용된다.
 *
 * <p>알림이 DB 에만 쌓이면 "동작한다"를 사람이 확인할 수 없어서 이 엔드포인트를 뒀다.
 * 자동 검증은 판정선 2·3·4·7 이 하고, 이건 데모용이다.
 */
@RestController
@RequestMapping("/api/notifications")
@RequiredArgsConstructor
public class NotificationController {

    private static final int MAX_ITEMS = 20;

    private final NotificationRepository notificationRepository;

    @GetMapping
    public List<NotificationResponse> list(Actor actor) {
        return notificationRepository
                .findByRecipientIdOrderByIdDesc(actor.userId(), PageRequest.of(0, MAX_ITEMS))
                .stream()
                .map(NotificationResponse::from)
                .toList();
    }

    @GetMapping("/unread-count")
    public UnreadCount unreadCount(Actor actor) {
        return new UnreadCount(notificationRepository.countByRecipientIdAndReadFalse(actor.userId()));
    }

    public record UnreadCount(long count) {}
}
