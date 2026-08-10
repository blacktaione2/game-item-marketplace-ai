package com.gimp.backend.domain.common;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;

/**
 * 저장된 시각을 <b>시간대가 붙은 값</b>으로 바꾼다.
 *
 * <h2>왜 필요한가</h2>
 *
 * <p>{@code created_at} 은 DB 에서 {@code timestamp without time zone}, 엔티티에서
 * {@link LocalDateTime} 이다. <b>둘 다 시간대를 안 들고 다닌다.</b> 그래서 응답 JSON 도
 * {@code "2026-08-10T21:06:12"} 처럼 오프셋 없이 나갔고, <b>받는 쪽은 변환할 근거가 없어</b>
 * 문자열을 그대로 잘라 화면에 뿌렸다. 배포본은 UTC 로 돌았으므로 한국에서 보면 9시간 어긋난
 * 시각이 그대로 보였다 — 값이 틀린 게 아니라 <b>뜻이 전달되지 않은 것</b>이다.
 *
 * <p>고친 방식은 단계마다 가정을 하나씩만 두고, 그 가정을 각각 한 곳에 적는 것이다.
 *
 * <table border="1">
 *   <caption>시각이 지나는 세 단계</caption>
 *   <tr><th>단계</th><th>무엇을 정하나</th><th>어디에 적혀 있나</th></tr>
 *   <tr><td>저장</td><td>기준 시간대는 UTC</td><td>{@code JpaAuditingConfig} 의 제공자</td></tr>
 *   <tr><td>전송</td><td>오프셋을 붙여 보낸다</td><td>이 클래스를 거치는 DTO 들</td></tr>
 *   <tr><td>표시</td><td>보는 사람의 시간대로</td><td>프론트 {@code formatDateTime()}</td></tr>
 * </table>
 *
 * <p><b>{@link #ZONE} 은 상수 하나다.</b> 쓰는 쪽(감사 제공자)과 읽는 쪽({@link #toInstant})이
 * 같은 값을 가져다 쓰므로, "쓸 때 쓴 시간대"와 "읽을 때 가정한 시간대"가 <b>구조적으로 갈릴 수
 * 없다.</b> 두 곳에 각각 {@code UTC} 라고 적었다면 그게 다음에 어긋날 자리가 된다.
 */
public final class StoredTime {

    /** 저장 기준 시간대. <b>이 값 하나가 저장·해석 양쪽을 정한다.</b> */
    public static final ZoneOffset ZONE = ZoneOffset.UTC;

    private StoredTime() {}

    /** 저장된 벽시계 시각 → 시간대가 붙은 시점. {@code null} 은 그대로 통과시킨다. */
    public static Instant toInstant(LocalDateTime storedAt) {
        return storedAt == null ? null : storedAt.toInstant(ZONE);
    }
}
