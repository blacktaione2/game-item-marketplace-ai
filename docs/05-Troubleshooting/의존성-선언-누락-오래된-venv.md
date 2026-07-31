---
status: 해결됨
created: 2026-07-31
tags: [ci, dependencies, repo-hygiene]
---

# requirements.txt에 없는 패키지로 여섯 달을 돌았다

> CI를 처음 붙인 날(ADR-0021) 첫 실행이 빨간불이었다. 원인은 CI 설정이 아니라
> 저장소였다. **`mcp`가 `requirements.txt`에 없었다.** 로컬에서는 78건이 전부
> 통과하고 있었고, 그 사실은 이 결함에 대해 아무 정보도 주지 않았다.
>
> 진단 과정은 별도 문서로 분리했다 — CI 로그를 읽을 수 없어서 컨테이너로
> 재현해야 했다: [[ci-로그-접근-불가-컨테이너-재현]].

## 문제

`ai` 워크플로 첫 실행에서 테스트 모듈 2개가 **수집 단계**에서 실패했다.

```
app/services/mcp/session.py:25: in <module>
    from mcp import Client
E   ModuleNotFoundError: No module named 'mcp'
```

로컬(윈도우)에서는 재현되지 않았다. `models/`도 `.env`도 없는 새 클론에서도
78건이 통과했다.

## 발생 원인

Phase 6에서 MCP를 붙일 때 `pip install mcp`를 손으로 실행하고
`requirements.txt`에 **선언하지 않았다.** 그 venv가 계속 살아 있었으므로 이후
모든 작업이 선언 없이 정상 동작했다.

**오래 산 개발 환경은 빠진 선언을 감춘다.** 설치는 한 번 하고 잊지만 선언은
매번 필요한데, 잊어도 로컬에서는 아무 일도 일어나지 않는다. 되먹임이 없다.

### 두 번째 결함 — 이건 CI도 못 잡는다

진단하다 `redis`도 빠져 있는 걸 찾았다. 그런데 이쪽은 **CI가 잡지 못한다.**

```python
# app/services/cache/dependencies.py
@lru_cache
def get_redis_client():
    # 임포트를 함수 안에 둔다 — 캐시를 끈 환경에서 redis 패키지를 강제하지
    # 않기 위해서다.
    from redis import asyncio as aioredis
```

지연 임포트라 **모듈 수집이 통과한다.** 테스트는 전부 초록이고, 서버는 첫
요청에서 죽는다. 시맨틱 캐시는 FAQ 외 전 의도의 기본 경로(ADR-0012)이므로
"캐시를 끈 환경"은 사실상 없다 — 주석이 상정한 선택성이 실제로는 없었다.

**CI의 보증 범위에 정확한 경계가 있다: 모듈 최상위 임포트만이다.**

## 해결 방법

둘 다 `requirements.txt`에 선언했다.

```
mcp>=2.0,<3
redis>=5.0
```

`from mcp import Client`는 2.x API다(1.x는 `ClientSession`을 노출한다). 그래서
`>=2.0`이 필요하고, 상한은 다음 메이저에서 또 바뀔 것을 예상해 걸었다.

검증은 로컬에서 하면 안 된다 — 문제를 감춘 바로 그 환경이다. 추적된 파일만
있는 새 클론을 리눅스 컨테이너에 올려 전체 설치 후 78건 통과를 확인했다.

## 재발 방지

### 지연 임포트 목록을 알고 있어야 한다

CI가 못 보는 곳이므로 손으로 세어둔다.

```bash
grep -rnE "^[[:space:]]+(import|from) [a-z_0-9]+" app/ --include=*.py
```

2026-07-31 기준 **6곳, 5개 패키지**다.

| 위치 | 패키지 | 선언 상태 |
|---|---|---|
| `cache/dependencies.py` | `redis` | **빠져 있었음 → 추가함** |
| `forecast/predictor.py`, `router/classifier.py` | `torch` | `sentence-transformers` 경유 |
| `router/classifier.py`, `search/reranker.py` | `transformers` | `sentence-transformers` 경유 |
| `search/embedding.py` | `sentence-transformers` | 직접 선언됨 |
| `search/reranker.py` | `optimum` | 직접 선언됨 |

`torch`와 `transformers`는 **간접 선언**이다. 지금은 맞지만
`sentence-transformers`가 의존성을 바꾸면 조용히 깨진다. 직접 선언으로 바꿀지는
별도 판단 — 지금 고치지 않았다.

### 규칙

- **import를 추가하는 편집에서 선언도 같이 한다.** 나중에 하면 안 한다
- **로컬 테스트 통과는 선언이 완전하다는 증거가 아니다.** 새 환경만이 증거다
- 지연 임포트를 새로 만들면 그게 **CI 사각지대**임을 알고 만든다. 위 표에
  한 줄 추가한다

## 배운 점

`mcp`는 CI가 첫날에 잡았다. 여기서 만족하고 넘어갔으면 `redis`는 남았을
것이다 — 초록불이 "선언이 완전하다"는 뜻이 아니라 **"최상위 임포트가
완전하다"**는 뜻임을 구분하지 않았다면.

자동 검사를 붙였을 때 물어야 하는 건 "무엇을 잡았나"가 아니라 **"무엇을 못
잡나"**다. 그 경계를 모르면 초록불이 오히려 위험해진다 — 이 저장소에는 이미
같은 형태의 교훈이 있다: 리랭커 점수 하한이 안전해 보였던 건 분할 하나에서만
확인했기 때문이었다(ADR-0018).

일반 규칙 한 줄은 `CLAUDE.md` Gotchas에도 남겼다. 이 문서는 진단 서사이고,
거기 한 줄은 다음 작업에서 눈에 띄어야 하는 원칙이다.
