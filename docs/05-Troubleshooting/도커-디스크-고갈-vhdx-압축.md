---
status: 해결됨
created: 2026-08-04
tags: [docker, wsl2, windows, disk, diagnosis]
---

# 호스트 디스크가 차면 컨테이너는 "read-only file system"이라고 말한다

> ADR-0032의 arm64 교차 빌드가 디스크를 채웠고, 그 뒤로 **Docker 데몬이 아예
> 죽어 있었다.** 진단이 오래 걸린 이유는 에러 메시지가 원인을 가리키지 않기
> 때문이다 — 게스트가 보고한 것은 "읽기 전용 파일시스템"이고, 실제 원인은
> **호스트의 여유 공간**이었다.
>
> 정리 결과는 **호스트 여유 7.91 → 24.96 GB**다. 그런데 그 17 GB 중
> **prune 이 회수한 것은 1.3 GB뿐**이고 나머지는 전혀 다른 단계에서 나왔다.
> 이 문서의 요점이 거기에 있다.

## 문제

세 가지 증상이 따로 나타났고, 처음에는 서로 무관해 보였다.

| 증상 | 언제 |
|---|---|
| `Write` 도구가 ADR-0032 작성에 실패 (ENOSPC) | 문서 작업 중 |
| `buildx` 빌드가 `E: Unable to mkstemp … (30: Read-only file system)` | arm64 빌드 중 |
| **`docker ps` / `docker system df` 가 영원히 무응답** | 이후 전부 |

세 번째가 특히 나쁘다. **디스크를 진단하려고 `docker system df` 를 치는데 그게
바로 안 되는 명령이다.**

## 발생 원인

`dockerd.log` 는 아예 없었고, `%LOCALAPPDATA%\Docker\log\vm\init.log` 에 답이 있었다.

```json
{"error":"error writing log entry: write /var/lib/docker/containers/…-json.log:
          read-only file system", ...}
{"component":"containerd","msg":"goroutine 8 gp=0xc0002ca540 m=nil [GC worker (idle)]:"}
```

**containerd 가 goroutine 덤프를 남기며 죽었다.** 그래서 데몬이 안 뜬다.

원인 사슬은 이렇다.

```
호스트 C: 여유 소진 (arm64 빌드 캐시 19.35 GB)
  → docker_data.vhdx 가 더 이상 커질 수 없음
  → 게스트에서는 ENOSPC 로 보임
  → ext4 가 errors=remount-ro 정책대로 읽기 전용 전환
  → containerd 크래시 → 데몬 사망
```

**게스트의 에러 메시지 어디에도 "호스트 디스크"라는 말이 없다.** "읽기 전용"은
권한 문제처럼 읽히고, 실제로 처음엔 Docker Desktop 손상을 의심했다.

## 해결 방법

### 1. 데몬 되살리기 — "시작 요청"과 "떴다"는 다르다

`Docker Desktop.exe` 를 다시 실행했는데 **5분을 기다려도 데몬이 안 붙었다.**
확인해 보니 distro 는 `Stopped` 이고 `init.log` 의 타임스탬프가 **40분 전에
멈춰 있었다** — VM 이 기동 시도조차 하지 않은 것이다.

기존 Docker Desktop 프로세스(GUI·backend·buildx)가 살아 있으면 새 실행은 그
인스턴스를 깨우려 할 뿐이고, 그 인스턴스가 이미 망가져 있으면 아무 일도 안 난다.

```powershell
Get-Process -Name "Docker Desktop","com.docker.backend","com.docker.build",
                  "docker","docker-buildx","docker-sandbox" | Stop-Process -Force
wsl --shutdown
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```

이렇게 하니 **15초 만에** 붙었다. 앞의 5분은 기다림이 부족했던 게 아니라
**아무것도 기동되지 않은 5분**이었다.

> 판별법: 기다리는 동안 `init.log` 의 `LastWriteTime` 이 갱신되는지 본다.
> 안 갱신되면 기다려도 소용없다.

### 2. prune — 안에서 지운다

```
TYPE            TOTAL   ACTIVE   SIZE      RECLAIMABLE
Images          13      6        22.62GB   4.452GB (19%)
Build Cache     160     0        19.35GB   15.14GB      ← 범인
```

```powershell
docker builder prune -af     # 19.35 GB
docker container prune -f
docker volume prune -f       # 익명 볼륨만. 명명 볼륨은 안 지운다
```

**호스트 여유는 7.91 → 9.21 GB, 겨우 1.3 GB 늘었다.**

### 3. compact — 파일을 줄인다 (여기가 진짜다)

```
게스트 파일시스템 사용량:  9.9 GB
vhdx 파일 크기:           27.70 GB   ← 17.8 GB 가 죽은 공간
```

vhdx 는 **커지기만 하고 줄지 않는다.** 안에서 지운 19 GB 는 파일에 그대로 남는다.

Windows 11 Home 에는 `Optimize-VHD`(Hyper-V 모듈)가 **없다.** `diskpart` 를 쓰고,
**관리자 권한이 필요**하며, vhdx 가 사용 중이면 안 되므로 Docker 를 완전히 내린다.

```
select vdisk file="C:\Users\<user>\AppData\Local\Docker\wsl\disk\docker_data.vhdx"
attach vdisk readonly
compact vdisk
detach vdisk
```

```powershell
docker desktop stop
Get-Process -Name "Docker Desktop","com.docker.*" | Stop-Process -Force
wsl --shutdown
Start-Process powershell -ArgumentList "-Command","diskpart /s compact.txt" -Verb RunAs
```

**27.68 → 11.97 GB. 호스트 여유 9.21 → 24.96 GB.**

압축 후 이미지 12종·명명 볼륨 7종이 **전부 그대로**였고, 인프라 4종을 다시 띄워
`healthy` 와 데이터(ES 42건, Postgres users 46 / items 14, 모델 볼륨 5종 649 MB)를
확인했다. 압축은 비파괴 연산이지만 **확인은 별개다.**

## 배운 점

### `fstrim` 의 보고는 회수 가능량을 예측하지 못한다

압축 전에 게스트에서 trim 을 돌렸다.

```
/mnt/docker-desktop-disk: 0 B (0 bytes) trimmed
```

**0 바이트.** 여기서 "회수할 게 없구나"로 읽으면 15.7 GB 를 놓친다. 블록 장치는
discard 를 지원하고 있었고(`discard_max_bytes = 4294966784`), 그럼에도 `compact`
는 15.7 GB 를 회수했다.

`fstrim` 이 보고하는 것은 **이번 호출이 새로 내려보낸 discard 양**이지 vhdx 안의
미사용 블록 총량이 아니다. **회수량을 알고 싶으면 압축을 실행하는 수밖에 없다.**

### "정리했다"와 "공간이 생겼다"는 다른 문장이다

이번 건의 핵심이다. `docker system df` 가 19.35 GB 를 회수했다고 보고했고 그건
사실이지만, **호스트 여유는 1.3 GB 만 늘었다.** 도구가 보고하는 회수량은 그
도구가 관리하는 계층의 것이고, 그 아래 계층(vhdx 파일, 그 아래 NTFS)은 별도로
줄여야 한다.

> **일반화: 계층이 있는 저장소에서 "지웠다"는 최상위 계층의 진술이다.**
> 실제로 확보됐는지는 **가장 바깥에서 재야 한다** — 여기서는 `Get-PSDrive C`.
> 이 프로젝트가 계속 지켜온 규칙("합계만 재고 원인을 지목하지 않는다",
> "렌더링된 출력 위에 검사를 만들지 않는다")과 같은 계열이다.

### 자원 고갈은 자기 이름으로 보고되지 않는다

한 층 아래의 고갈이 한 층 위에서는 전혀 다른 이름을 단다.

| 실제 원인 | 관측된 증상 |
|---|---|
| 호스트 디스크 고갈 | 게스트 `read-only file system` |
| 같은 것 | `apt-get` 의 `Unable to mkstemp` |
| 같은 것 | containerd goroutine 덤프 |
| 같은 것 | **`docker` CLI 무응답** |

**진단 도구가 피해자면 그 도구로는 진단할 수 없다.** 컨테이너 계층이 이상하면
호스트에서 먼저 재는 것이 순서다 — `Get-PSDrive`, vhdx 파일 크기, `init.log`.
이번에는 `docker system df` 로 시작했다가 그게 멈춰서 시간을 썼다.

### 남은 것

| 항목 | 판단 |
|---|---|
| `chromadb/chroma:1.5.9` (826 MB) | **다른 프로젝트 것.** 여유가 25 GB 라 건드리지 않았다 |
| `elasticsearch:8.13.4` 원본 (1.88 GB) | nori 이미지의 베이스. 레이어를 공유하므로 지워도 회수량이 적다 |
| grafana + prometheus (1.05 GB) | `--profile observability` 가 쓴다 |
| **arm64 교차 빌드** | 캐시가 이 사달의 원인이다. **다시 한다면 `docker builder prune -af` 를 먼저** |

### 정리한 김에 원래 막혀 있던 일을 끝냈다

디스크 때문에 중단됐던 **AI 이미지 arm64 교차 빌드**(ADR-0032의 미완료 항목)를
그대로 다시 돌려 **완주했다** — pip 계층 1041.5s, exit 0.

이번에는 셋을 다르게 했다.

1. **`--output type=cacheonly`** — 이미지를 만들지 않는다. 알고 싶은 것은 "빌드가
   되는가"뿐인데 지난번엔 3GB 이미지까지 x86 디스크에 쓰고 있었다
2. **빌드 전에 더 싼 질문을 먼저 물었다** — 진짜 위험은 "빌드가 되는가"가 아니라
   **"의존성에 aarch64 휠이 있는가"** 였다. torch 인덱스를 직접 조회해
   `cp311 manylinux_2_28_aarch64` 가 2.7.0부터 있음을 먼저 확인했다(요청 한 번).
   없었다면 17분을 안 써도 됐다
3. **돌면서 디스크를 감시했다** — 6GB 미만이면 알리도록

빌드 후 캐시는 2.067GB였고 바로 `prune` 했다. **vhdx 는 11.97 → 13.85GB로 늘어난
채 남는다** — 재압축은 하지 않았다. 여유가 23GB라 2GB를 위해 Docker 를 다시 내릴
이유가 없다. **압축은 공짜가 아니다**(전체 중지 + 관리자 권한 + 수 분).
