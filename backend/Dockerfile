# 백엔드 이미지 — 멀티스테이지(빌드 JDK → 실행 JRE).
#
# JDK를 실행 이미지에 남기지 않는 이유는 크기다. 배포 대상이 4 OCPU / 24GB를
# **다른 프로젝트와 공유**하므로 이미지 하나가 가져가는 몫을 줄여둔다.
#
# 베이스는 eclipse-temurin — amd64/arm64 둘 다 공식 제공한다. 배포 대상이
# Oracle Cloud ARM이라 arm64가 없는 이미지는 후보가 될 수 없다.

FROM eclipse-temurin:21-jdk AS build
WORKDIR /build

# 의존성 해석을 소스 복사보다 **먼저** 한다. 소스만 바뀐 재빌드에서 이 레이어가
# 캐시되어 다시 받지 않는다.
COPY gradle gradle
COPY gradlew build.gradle settings.gradle ./
RUN chmod +x gradlew && ./gradlew dependencies --no-daemon

COPY src src
# 테스트는 CI가 돈다. 여기서 또 돌리면 Postgres·Redis가 빌드 시점에 필요해지는데,
# 이미지 빌드에 DB를 띄우는 건 잘못된 결합이다.
RUN ./gradlew bootJar --no-daemon -x test

FROM eclipse-temurin:21-jre
WORKDIR /app

# curl 은 헬스체크용이다. 포트가 열렸는지가 아니라 **애플리케이션이 준비됐는지**를
# 물어야 하기 때문에 필요하다 — 이 저장소에는 "포트가 응답한다"와 "내가 띄운
# 프로세스가 응답한다"가 다르다는 걸 두 번 겪은 기록이 있다.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 루트로 돌리지 않는다. 컨테이너 탈출이 곧 호스트 루트가 되는 걸 막는 최소 조치다.
RUN useradd --system --create-home --shell /usr/sbin/nologin app
COPY --from=build /build/build/libs/*-SNAPSHOT.jar app.jar
USER app

EXPOSE 8080
# 컨테이너 메모리 상한을 JVM이 인식하게 둔다(21은 기본으로 cgroup을 읽는다).
# 힙을 고정하지 않는 이유: 공유 박스라 상한이 배포마다 달라진다.
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=60", "-jar", "app.jar"]
