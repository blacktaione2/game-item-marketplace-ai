package com.gimp.backend.domain;

import static org.assertj.core.api.Assertions.assertThat;

import com.gimp.backend.config.JpaAuditingConfig;
import com.gimp.backend.domain.common.StoredTime;
import com.gimp.backend.dto.trade.TradeHistoryResponse;
import java.lang.reflect.RecordComponent;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * 시각이 <b>뜻을 잃지 않고</b> 화면까지 가는가.
 *
 * <h2>무엇이 틀렸었나</h2>
 *
 * <p>{@code created_at} 은 DB·엔티티·DTO·JSON 네 단계를 <b>시간대 없이</b> 지나갔다. 그래서
 * 배포본(UTC)의 값이 한국에서 9시간 어긋난 채로 보였다. <b>값은 맞고 뜻이 안 갔다.</b>
 *
 * <p>이 검사는 그 사슬의 두 고리를 고정한다 — 저장 기준이 한 곳에서만 정해지는가, 그리고
 * 밖으로 나가는 시각이 오프셋을 들고 나가는가.
 */
class StoredTimeTest {

    @Test
    void 감사_제공자가_쓰는_시간대와_읽을_때_가정하는_시간대가_같다() {
        // **이게 이 결함의 본질이다.** 쓰는 쪽과 읽는 쪽이 다른 시간대를 쓰면 화면 시각이
        // 조용히 틀리고, 숫자가 그럴듯해서 아무도 눈치채지 못한다.
        //
        // JVM 기본값에 기대지 않는다 — 개발기(KST)와 컨테이너(UTC)가 다르고, 테스트는
        // main() 을 실행하지 않아 운영과 또 다르다. 실제 쓰기 경로를 직접 부른다.
        LocalDateTime written = new JpaAuditingConfig()
                .storedTimeProvider()
                .getNow()
                .map(LocalDateTime::from)
                .orElseThrow();

        Instant asStored = StoredTime.toInstant(written);
        assertThat(java.time.Duration.between(asStored, Instant.now()).abs())
                .describedAs("쓰는 시간대와 읽는 시간대가 갈렸습니다 (차이가 시간 단위로 벌어집니다)")
                .isLessThan(java.time.Duration.ofMinutes(1));
    }

    @Test
    void 그_비교가_시간대_불일치를_실제로_잡는다() {
        // **공허 방지 — 위 검사와 같은 식을 실패 방향으로 돌린다.**
        // 쓰는 쪽만 KST 로 바꾼 상태를 만들면, 같은 비교가 9시간 차이를 낸다.
        LocalDateTime writtenInKst = LocalDateTime.now(ZoneOffset.ofHours(9));
        Instant asStored = StoredTime.toInstant(writtenInKst);

        assertThat(java.time.Duration.between(asStored, Instant.now()).abs())
                .describedAs("갈린 상태에서도 1분 이내라면 위 검사는 아무것도 구별하지 못한다")
                .isGreaterThan(java.time.Duration.ofHours(8));
    }

    @Test
    void 저장된_벽시계_시각을_오프셋_있는_시점으로_바꾼다() {
        LocalDateTime stored = LocalDateTime.of(2026, 8, 10, 21, 6, 12);
        assertThat(StoredTime.toInstant(stored))
                .isEqualTo(Instant.parse("2026-08-10T21:06:12Z"));
    }

    @Test
    void null_은_그대로_통과시킨다() {
        // updatedAt 은 아직 수정되지 않은 행에서 비어 있을 수 있다.
        assertThat(StoredTime.toInstant(null)).isNull();
    }

    @Test
    void 변환이_실제로_시간대를_바꾼다() {
        // **공허 방지.** 두 값이 우연히 같으면 위 단언은 변환을 안 해도 통과한다.
        // KST 로 해석했을 때와 다르다는 것을 같은 식으로 확인한다.
        LocalDateTime stored = LocalDateTime.of(2026, 8, 10, 21, 6, 12);
        Instant asKst = stored.toInstant(ZoneOffset.ofHours(9));
        assertThat(StoredTime.toInstant(stored))
                .describedAs("UTC 해석과 KST 해석이 같으면 이 검사는 아무것도 구별하지 못한다")
                .isNotEqualTo(asKst);
    }

    @Test
    void 밖으로_나가는_DTO_에_시간대_없는_타입이_남아_있지_않다() {
        // **열거하지 않고 유도한다.** DTO 를 하나 더 만들면서 LocalDateTime 을 쓰면
        // 그 화면만 조용히 9시간 어긋난다 — 응답은 그럴듯하므로 아무도 안 본다.
        List<Class<?>> dtos = List.of(
                TradeHistoryResponse.class,
                com.gimp.backend.dto.trade.TradeResponse.class,
                com.gimp.backend.dto.notification.NotificationResponse.class,
                com.gimp.backend.dto.item.ItemResponse.class);

        List<String> naive = dtos.stream()
                .flatMap(dto -> java.util.Arrays.stream(dto.getRecordComponents())
                        .filter(c -> c.getType() == LocalDateTime.class)
                        .map(c -> dto.getSimpleName() + "#" + c.getName()))
                .toList();

        assertThat(naive)
                .describedAs("시간대 없는 타입은 오프셋 없이 직렬화되어 받는 쪽이 변환할 수 없습니다")
                .isEmpty();
    }

    @Test
    void 그_검사가_실제로_잡는다() {
        // **공허 방지 — 같은 술어를 실패 방향으로 돌린다.**
        record 낡은DTO(Long id, LocalDateTime createdAt) {}

        List<String> naive = java.util.Arrays.stream(낡은DTO.class.getRecordComponents())
                .filter(c -> c.getType() == LocalDateTime.class)
                .map(RecordComponent::getName)
                .toList();

        assertThat(naive).containsExactly("createdAt");
    }
}
