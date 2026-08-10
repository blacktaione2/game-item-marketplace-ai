package com.gimp.backend.repository;

import static org.assertj.core.api.Assertions.assertThat;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;

/**
 * 저장소 쿼리가 <b>테넌트를 조건에 명시하는가.</b>
 *
 * <h2>왜 블랙박스 검사로는 안 되는가</h2>
 *
 * <p>user id 와 item id 는 <b>전역 유일</b>이라, {@code recipientId} 하나만으로도 테넌트가
 * 결정된다. 그래서 조건에서 테넌트를 빼도 <b>결과가 똑같다</b> — MockMvc 로 무엇을 단언하든
 * 두 판본이 구별되지 않는다. {@code MyDataScopeTest} 의 테넌트 검사들은 관측 가능한 성질(남의
 * 테넌트 것이 안 보인다)을 고정하지만, 그 성질이 <b>무엇에 기대어</b> 성립하는지는 못 본다.
 *
 * <p>지켜야 할 성질이 바로 그 "무엇에 기대는가" 다 — {@code TradeRepository.findMine} 이 이미
 * 적어뒀다: <i>"격리를 사용자-테넌트 관계에 의존시키면 그 관계가 바뀌는 날 조용히 샌다."</i>
 * 그건 쿼리를 들여다봐야만 보인다.
 *
 * <h2>열거하지 않는다</h2>
 *
 * <p>대상 저장소를 손으로 적지 않고 컨텍스트에서 받는다. 목록을 적으면 <b>다음에 새는 것이 그
 * 목록</b>이 된다 — 이 저장소가 이미 여러 번 겪은 모양이다(사례 28). 새 저장소를 만들면 이 파일을
 * 안 고쳐도 걸린다.
 *
 * <h2>이 검사의 깊이</h2>
 *
 * <p><b>선언이지 실행이 아니다.</b> 메서드 이름과 {@code @Query} 문자열만 본다 — 실제로 그
 * 조건이 SQL 로 내려가는지, 넘긴 값이 맞는 테넌트인지는 안 본다. 그쪽은
 * {@code MyDataScopeTest} 가 관측 가능한 범위에서 맡는다. 둘 다 필요하고, 어느 쪽도 혼자서는
 * 부족하다.
 */
@SpringBootTest
class TenantScopedQueryTest {

    /**
     * 테넌트를 안 걸어도 되는 메서드와 <b>그 사유</b>.
     *
     * <p>여기에 뭔가를 추가하는 것은 격리 정책에 예외를 하나 만든다는 뜻이므로 사유가 붙어야
     * 한다. "테스트를 통과시키려고" 추가하면 이 검사는 그 순간 장식이 된다.
     */
    private static final Map<String, String> EXEMPT = Map.of(
            "NotificationRepository#existsByRecipientIdAndTradeId",
            "격리가 아니라 유니크 인덱스 (recipient_id, trade_id) 를 그대로 비추는 멱등성 "
                    + "사전확인이다. 조건을 더하면 사전확인이 제약보다 좁아지고, 그 틈으로 들어온 "
                    + "재전달이 flush 에서 터진다.");

    @Autowired List<Repository<?, ?>> repositories;

    /** 이 메서드가 테넌트를 조건에 명시하는가 — <b>본 검사와 공허 방지가 이 식을 공유한다.</b> */
    static boolean namesTenant(Method method) {
        if (method.getName().toLowerCase().contains("tenant")) {
            return true;
        }
        Query query = method.getAnnotation(Query.class);
        return query != null && query.value().toLowerCase().contains("tenant");
    }

    /** 저장소 인터페이스에 <b>직접 선언된</b> 쿼리 메서드들. 상속받은 CRUD 는 대상이 아니다. */
    private List<Method> declaredQueryMethods() {
        List<Method> found = new ArrayList<>();
        for (Repository<?, ?> repository : repositories) {
            for (Class<?> type : repository.getClass().getInterfaces()) {
                if (!type.getPackageName().equals(getClass().getPackageName())) {
                    continue;
                }
                found.addAll(List.of(type.getDeclaredMethods()));
            }
        }
        return found;
    }

    private static String key(Method method) {
        return method.getDeclaringClass().getSimpleName() + "#" + method.getName();
    }

    private List<String> offenders(Predicate<Method> namesTenant) {
        return declaredQueryMethods().stream()
                .filter(method -> !namesTenant.test(method))
                .map(TenantScopedQueryTest::key)
                .filter(name -> !EXEMPT.containsKey(name))
                .sorted()
                .toList();
    }

    @Test
    void 검사할_쿼리_메서드가_실제로_있다() {
        // 0개를 세면 아래 검사는 공짜로 통과한다. 개수만 세면 엉뚱한 걸 세도 통과하므로
        // 아는 메서드가 잡히는지도 본다.
        List<String> all = declaredQueryMethods().stream().map(TenantScopedQueryTest::key).toList();
        assertThat(all).hasSizeGreaterThanOrEqualTo(7);
        assertThat(all).contains("TradeRepository#findMine", "ItemRepository#findByIdAndTenantId");
    }

    @Test
    void 모든_쿼리가_테넌트를_조건에_명시한다() {
        assertThat(offenders(TenantScopedQueryTest::namesTenant))
                .describedAs(
                        "테넌트를 조건에 안 넣은 쿼리입니다. id 가 전역 유일이라 지금은 결과가 "
                                + "같지만, 그건 격리가 id-테넌트 관계에 얹혀 있다는 뜻입니다. "
                                + "의도한 것이면 EXEMPT 에 사유와 함께 넣으세요.")
                .isEmpty();
    }

    @Test
    void 판정이_실제로_실패할_수_있다() {
        // **공허 방지 — 본 검사와 같은 식(offenders)을 실패 방향으로 돌린다.**
        // 반사실을 인자로 넘긴다(사례 36·44·48): 표본만 만들고 본 식에 안 먹이면
        // 판정 로직이 실패 방향으로 한 번도 안 도는 것과 같다.
        assertThat(offenders(method -> false))
                .describedAs("아무것도 테넌트를 안 건다고 해도 지목이 없으면 이 검사는 공허하다")
                .isNotEmpty();
    }

    @Test
    void 면제_목록이_살아_있다() {
        // 이름이 바뀌면 면제가 낡은 채로 남고, 낡은 면제는 다음에 같은 이름이 생겼을 때
        // 조용히 통과시킨다 (ADR-0051 과 같은 이유).
        List<String> all = declaredQueryMethods().stream().map(TenantScopedQueryTest::key).toList();
        assertThat(all).containsAll(EXEMPT.keySet());
        EXEMPT.forEach((name, reason) ->
                assertThat(reason).describedAs("%s 의 면제 사유", name).hasSizeGreaterThan(20));
    }

    @Test
    void 사유_없는_면제는_통과시키지_않는다() {
        // **반대 방향.** 면제 대상이 실제로 테넌트를 안 걸고 있어야 면제에 뜻이 있다 —
        // 이미 테넌트를 거는 메서드가 목록에 있으면 그건 낡은 면제다.
        List<Method> exempted = declaredQueryMethods().stream()
                .filter(method -> EXEMPT.containsKey(key(method)))
                .toList();
        assertThat(exempted).isNotEmpty();
        assertThat(exempted).noneMatch(TenantScopedQueryTest::namesTenant);
    }
}
