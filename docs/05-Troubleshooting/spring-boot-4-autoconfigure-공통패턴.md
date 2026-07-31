---
status: 진행중
created: 2026-07-28
tags: [backend, spring-boot, autoconfigure]
---

# Spring Boot 4.x autoconfigure 관련 공통 패턴

> Phase 1~2에서 겪은 두 건(Redisson, RestClient.Builder)이 표면적으로는
> 다른 증상이지만 같은 원인 축("Boot 4.x가 autoconfigure 모듈을 재구성하면서
> 3.x 시절 코드/서드파티 라이브러리가 기대하던 클래스·빈이 사라짐")에서
> 나왔다. 개별 사례로 흩어두면 다음에 또 같은 유형의 문제를 만났을 때
> "또 이건가?"를 못 알아챌 것 같아서 하나로 묶는다. 앞으로 비슷한 문제가
> 나오면 이 문서에 사례를 추가한다.
>
> 배경: 이 프로젝트는 Spring Initializr가 이미 3.x 지원을 완전히 중단한
> 시점(`compatibility range >=4.0.0`)에 시작해서 계획과 달리 Spring Boot
> 4.x(Spring Framework 7)로 스캐폴딩했다 (`CLAUDE.md` 참고). 즉 처음부터
> "3.x를 전제로 한 지식/라이브러리와 실제 4.x 클래스패스가 어긋날 수 있다"는
> 조건에서 출발한 프로젝트다.

## 사례 1: redisson-spring-boot-starter가 컨텍스트 로딩에 실패함

### 문제

`org.redisson:redisson-spring-boot-starter:3.50.0`을 추가하고
`./gradlew build`(컨텍스트 로딩 테스트)를 돌리면 애플리케이션 컨텍스트
초기화 자체가 실패했다.

```
java.lang.IllegalStateException: Failed to load ApplicationContext ...
Caused by: java.lang.IllegalArgumentException:
    Could not find class [org.springframework.boot.autoconfigure.data.redis.RedisProperties]
Caused by: java.lang.ClassNotFoundException:
    org.springframework.boot.autoconfigure.data.redis.RedisProperties
```

`RedissonClient` 빈은 `RedissonConfig`에서 이미 수동으로 구성해뒀고 이
스타터의 자동 설정 기능은 애초에 쓰지도 않았는데, 클래스패스에 올라와
있는 것만으로 부팅이 막혔다.

### 원인

`redisson-spring-boot-starter`의 `@AutoConfiguration` 클래스가
`@EnableConfigurationProperties(RedisProperties.class)`로
`org.springframework.boot.autoconfigure.data.redis.RedisProperties`를
참조하는데, Boot 4.x에서 autoconfigure 모듈이 재구성되면서 그 패키지
경로의 클래스가 사라졌다. 스타터 3.50.0은 Boot 3.x 클래스패스를 전제로
빌드된 상태라 Boot 4.x와 바이너리 비호환이고, 스타터가 클래스패스에
있으면 오토컨피그 등록 단계(빈을 실제로 쓰기도 전, 리플렉션으로
애노테이션 값을 읽는 시점)에서 바로 터진다.

### 해결

스타터 대신 순수 클라이언트 라이브러리만 의존성에 추가.

```diff
- implementation 'org.redisson:redisson-spring-boot-starter:3.50.0'
+ implementation 'org.redisson:redisson:3.50.0'
```

`RedissonConfig`는 원래도 `spring.data.redis.*` 프로퍼티를 `@Value`로
읽어 `Config().useSingleServer()...`로 직접 구성한 `RedissonClient` 빈을
등록하는 방식이라 스타터의 오토컨피그에 의존하지 않았으므로 그대로 유지.

## 사례 2: RestClient.Builder 빈이 오토컨피그되지 않음

### 문제

Spring Boot ↔ FastAPI 헬스체크용으로 `AiServerClient`를 만들면서
`RestClient.Builder`를 생성자에 `@Value`와 같이 주입받게 했더니 부팅이
실패했다.

```
Parameter 0 of constructor in com.gimp.backend.client.AiServerClient
required a bean of type 'org.springframework.web.client.RestClient$Builder'
that could not be found.
```

Boot 3.x에서는 `spring-boot-starter-web`(또는 webmvc)만 있으면
`RestClientAutoConfiguration`이 `RestClient.Builder` 빈을 자동 등록해주는
게 익숙한 동작이었는데, 이 프로젝트의 의존성 조합(`data-jpa`,
`validation`, `webmvc`, `redisson`, lombok)에서는 그 빈이 없었다.

### 원인

Boot 4.x가 autoconfigure를 여러 모듈로 재구성하면서(같은 계열의 변화로
`spring-boot-starter-web` → `spring-boot-starter-webmvc` 이름 변경, 테스트
스타터가 `-test` 접미사 모듈로 분리된 것도 이미 겪음), `RestClient.Builder`
오토컨피그가 이 프로젝트가 받은 의존성 세트만으로는 활성화되지 않는
것으로 보인다. 정확히 어느 모듈이 빠졌는지까지는 추적하지 않았다 — Boot
4.1이 아직 생태계 전반에서 성숙한 버전이 아니라 문서/사례가 적다는 점도
고려해 "필요한 빈이 이번에도 없을 수 있다"는 결론만 내리고 다음 방법으로
우회했다.

### 해결

DI로 받는 대신 정적 팩토리로 직접 생성.

```diff
- public AiServerClient(RestClient.Builder builder, @Value("${ai-server.base-url}") String baseUrl) {
-     ...
-     this.restClient = builder.baseUrl(baseUrl).requestFactory(requestFactory).build();
+ public AiServerClient(@Value("${ai-server.base-url}") String baseUrl) {
+     ...
+     this.restClient = RestClient.builder().baseUrl(baseUrl).requestFactory(requestFactory).build();
  }
```

## 사례 3: `@AutoConfigureMockMvc`의 패키지가 옮겨감 (2026-08-01)

### 문제

인증 라운드(ADR-0023)에서 백엔드 첫 행동 테스트를 쓰면서 Boot 3.x 관례대로
임포트했더니 컴파일이 실패했다.

```
error: package org.springframework.boot.test.autoconfigure.web.servlet does not exist
```

### 원인

Boot 4에서 webmvc 테스트 지원이 별도 모듈(`spring-boot-webmvc-test`)로
쪼개지면서 패키지도 함께 옮겨졌다. 의존성은 이미 있었고(`spring-boot-starter-webmvc-test`),
**클래스가 없어진 게 아니라 자리가 바뀐 것**이다.

```
org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc  (3.x)
org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc       (4.x)
```

### 해결

임포트만 교체. 확인은 추측하지 말고 jar를 직접 열어보는 게 빠르다.

```bash
unzip -l ~/.gradle/caches/.../spring-boot-webmvc-test-4.1.0.jar | grep AutoConfigureMockMvc
```

### 앞의 둘과 다른 점

사례 1·2는 **런타임**에만 드러났지만 이건 **컴파일에서 잡힌다.** 같은 축
(Boot 4 재배치)이라도 테스트 코드 쪽은 즉시 실패하므로 훨씬 싸다 — 다만
"의존성이 있으니 클래스도 그 자리에 있겠지"라는 가정은 똑같이 틀렸다.

## 공통 원인

세 사례 모두 "Boot 3.x 시절에 당연히 있던 autoconfigure 클래스/빈이 Boot
4.x에서 이름이 바뀌었거나, 모듈이 쪼개졌거나, 아예 없어졌다"는 같은
축에서 나왔다(사례 3은 테스트 지원 클래스의 패키지 이동이라 컴파일에서
잡혔다). 하나는 서드파티 스타터가 구버전 클래스를 참조해서 터진
것(라이브러리 쪽이 아직 Boot 4를 못 따라감)이고, 다른 하나는 Boot
자신의 오토컨피그가 이 프로젝트의 의존성 조합에서는 활성화되지 않은
것(우리 쪽 의존성 구성이 그 오토컨피그의 활성화 조건을 충족 못 시킴)이라
정확한 트리거는 다르지만, 증상의 성격은 같다: **컴파일은 되는데
런타임(컨텍스트 로딩/DI)에서만 드러난다.**

## 체크리스트 — 새 라이브러리(특히 spring-boot-*-starter류)를 추가할 때

- [ ] 그 라이브러리가 Spring Boot 4.x 호환을 공식적으로 명시하고
      있는가? (README/release notes/호환성 매트릭스 확인 — "Boot 3.x
      대상"이라고만 쓰여 있으면 일단 의심)
- [ ] `-starter` 아티팩트라면, 그 스타터가 제공하는 오토컨피그 기능
      (자동 빈 등록, actuator 헬스 인디케이터 등)을 실제로 쓸 계획인가?
      아니라면 코어 라이브러리만 받고 설정은 직접 코드(`@Bean`/생성자)로
      명시한다 — 오토컨피그 클래스가 클래스패스에 존재하기만 해도
      실패할 수 있다(사례 1).
- [ ] Boot가 기본 제공하던 빌더/클라이언트 빈(`RestClient.Builder`,
      `RestTemplateBuilder`, `WebClient.Builder` 등)에 의존하기 전에,
      실제로 그 빈이 지금 의존성 조합에서 등록되는지 한 번은 부팅해서
      확인한다. 안 되면 정적 팩토리로 직접 생성(사례 2).
- [ ] 새 의존성을 추가한 직후엔 반드시 `./gradlew build`(또는
      `bootRun`) 한 번을 돌린다 — 컴파일 성공은 이런 문제를 잡아주지
      않는다. 실패는 항상 런타임 컨텍스트 로딩 단계에서만 드러난다.
- [ ] 에러가 나면 스택트레이스 최상위(`Failed to load ApplicationContext`
      류)만 보고 우리 설정을 의심하지 말고, `Caused by` 체인 끝까지
      내려가서 진짜 원인(`ClassNotFoundException`,
      `NoSuchBeanDefinitionException` 등)을 확인한다.
- [ ] 해결하고 나면 이 문서에 사례를 추가한다.
