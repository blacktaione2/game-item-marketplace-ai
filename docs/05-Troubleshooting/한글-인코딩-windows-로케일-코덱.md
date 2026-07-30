---
status: 진행중
created: 2026-07-29
tags: [environment, windows, encoding, korean]
---

# 한글 인코딩 — Windows 로케일 코덱(cp949) vs UTF-8

> Phase 2~5에서 겪은 네 건이 표면적으로는 전혀 다른 증상(pip 설치 실패, HTTP
> 400, 콘솔 출력 깨짐)이지만 같은 원인 축에서 나왔다: **Windows의 기본 로케일
> 코덱이 cp949인데 파일·페이로드·표준출력은 UTF-8을 전제한다.** 개별 사례로
> 흩어두면 다음에 또 만났을 때 "또 이건가?"를 못 알아챈다. 앞으로 비슷한
> 문제가 나오면 이 문서에 사례를 추가한다.
>
> 배경: 개발 환경이 Windows 11이고 셸이 PowerShell / Git Bash다. 반면 코드,
> 문서, 테스트 데이터가 전부 한국어라 UTF-8을 벗어나는 지점마다 걸린다.
> 이 프로젝트는 한국어 게임 아이템 도메인이라 이 마찰이 계속 발생한다.

## 사례 1: requirements.txt에 한글 주석을 넣으면 pip 설치가 실패한다

### 문제

`ai/requirements.txt`에 의존성 핀의 이유를 한글 주석으로 적었더니 설치가
파일 파싱 단계에서 죽었다.

```
UnicodeDecodeError: 'cp949' codec can't decode byte ...
```

### 원인

pip은 requirements 파일을 **시스템 로케일 코덱**으로 읽는다. 한국어 Windows
에서는 그게 cp949이고, UTF-8로 저장된 한글 바이트열을 cp949로 해석하려다
터진다.

### 해결

이 파일은 **ASCII만 유지**한다. 핀의 이유 같은 설명은 영어로 적는다.
`CLAUDE.md` Gotchas에도 못 박아뒀다.

## 사례 2: curl `-d`로 한글 JSON을 보내면 서버가 400을 뱉는다

### 문제

검색 API를 한글 질의로 테스트하려고 Git Bash에서 curl을 쐈더니 서버가 JSON
파싱 자체를 거부했다.

```
Invalid UTF-8 start byte 0xb7
```

### 원인

Git Bash가 명령줄 인자를 curl에 넘기기 전에 **ANSI 코드페이지(cp949)로
변환**한다. 그래서 실제 네트워크로 나가는 바이트는 UTF-8이 아니라 cp949이고,
서버의 JSON 파서는 그걸 잘못된 UTF-8로 판정한다.

`0xb7`은 UTF-8에서 시작 바이트로 올 수 없는 값이라 에러 메시지가 원인을
꽤 정확히 가리키고 있었는데, 처음에는 서버 설정 문제로 의심해서 시간을 썼다.

### 해결

페이로드를 **UTF-8 파일로 먼저 저장하고** 파일을 참조시킨다.

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @payload.json
```

`--data-binary`여야 한다. `-d`는 개행을 제거하는 등 내용을 손대므로 바이너리
동등성이 깨질 수 있다.

## 사례 3: heredoc으로 그 파일을 만들면 다시 깨진다

### 문제

사례 2의 해결책대로 파일을 만들려고 셸 heredoc을 썼는데 같은 에러가 났다.

```bash
cat > payload.json <<'EOF'
{"query": "불속성 검"}
EOF
```

### 원인

파일을 만드는 주체가 여전히 Git Bash다. heredoc 본문도 똑같이 코드페이지
변환을 거치므로, 저장된 파일이 이미 cp949다. 셸을 우회하지 않으면 셸 문제를
피할 수 없다.

### 해결

**에디터나 파일 쓰기 도구로 직접 UTF-8 파일을 만든다.** 셸을 경유하지 않는
경로여야 한다. 실무적으로는 한글이 들어가는 테스트 페이로드를 아예 스크립트
파일로 만들어두고 `python script.py`로 실행하는 편이 반복 작업에 낫다 —
이 프로젝트의 API 검증 스크립트들이 그렇게 되어 있다.

## 사례 4: Python `print()`의 한글이 콘솔에서 깨진다

### 문제

Phase 5에서 아이템 목록·학습 결과·피처 분포를 확인할 때마다 출력이 이렇게
나왔다.

```
   1 ����   +9 ��ȭ �ռҵ�   45000
```

### 원인

Python의 `sys.stdout` 인코딩이 **콘솔 코드페이지**를 따라간다. UTF-8 문자열을
cp949 콘솔로 내보내면서 표현 불가능한 바이트가 깨진다.

### 해결

두 가지가 있고, **어느 쪽을 쓸지는 목적에 따라 다르다.**

```bash
# (a) 표준출력 인코딩을 강제하고 파일로 받는다
PYTHONIOENCODING=utf-8 python -m scripts.train_forecast > out.txt 2>&1
```

```python
# (b) 애초에 stdout을 거치지 않고 UTF-8 파일로 직접 쓴다
with io.open(path, "w", encoding="utf-8") as f:
    f.write(...)
```

그 다음 파일을 UTF-8로 읽으면 정상이다.

### 이 사례의 핵심 — 데이터는 멀쩡했다

**사례 2·3과 달리 여기서는 데이터가 손상되지 않았다.** 콘솔 표시만 깨졌고,
같은 문자열을 UTF-8 파일로 쓰면 완벽하게 정상이었다. 즉 고칠 대상이 데이터가
아니라 출력 경로였다.

## 사례 5: 자식 프로세스의 한글 에러 메시지를 utf-8로 읽으면 죽는다 — 그리고 그게 코퍼스를 손상시켰다

### 문제

`element` 임포트 가드가 실제로 발동하는지 확인하려고, 코퍼스 파일을 일부러
망가뜨린 뒤 자식 프로세스로 임포트해 에러를 확인하는 스크립트를 썼다.

```python
proc = subprocess.run([sys.executable, "-c", "import app.corpus"],
                      capture_output=True, text=True, encoding="utf-8")
last = proc.stderr.strip().splitlines()[-1]   # 여기서 죽었다
```

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb0 in position 370
```

**증상은 인코딩 에러 하나였지만 피해는 그게 아니었다.** 스크립트가 그 지점에서
죽는 바람에 **파일을 원복하지 못했고, 코퍼스가 `element` 한 줄이 빠진 채로
남았다.** 검증하려던 대상을 검증 도구가 망가뜨린 것이다.

### 원인

두 개가 겹쳤다.

1. **자식 프로세스의 stderr가 콘솔 코드페이지(cp949)로 나온다.** Python이
   `ValueError("element가 없는 아이템이…")`를 트레이스백으로 찍을 때 그 인코딩을
   따른다. 부모가 `encoding="utf-8"`로 읽겠다고 선언하면 그 바이트열이 유효한
   UTF-8이 아니라서 터진다. 사례 4와 같은 경계인데 **방향이 반대**다 — 사례 4는
   내가 쓰는 쪽, 여기는 내가 읽는 쪽이다.
2. **원복이 `finally`에 없었다.** 예외가 나면 원복 코드에 도달하지 못한다.
   1번 없이도 언젠가 터질 결함이었고, 1번이 그걸 당겼을 뿐이다.

### 해결

```python
# stderr는 bytes로 받아서 cp949로 푼다 (errors="replace"로 최악에도 안 죽게)
proc = subprocess.run([...], capture_output=True)   # text=True 안 씀
stderr = proc.stderr.decode("cp949", errors="replace")

# 원복은 반드시 finally
try:
    for label, mutate in CASES:
        PATH.write_text(mutate(original), encoding="utf-8")
        ...
finally:
    PATH.write_text(original, encoding="utf-8")
```

`ai/scripts/`가 아니라 스크래치패드에 두고 돌렸다 — 1회용 검증 스크립트다.

### 배운 점

**파일을 일부러 망가뜨리는 검증은 그 자체가 위험한 작업이다.** 가드가 진짜
발동하는지 보려면 충돌을 주입하는 것 말고 방법이 없는데(그래서 이 프로젝트는
계속 그렇게 한다), 주입 스크립트가 죽으면 손상이 남는다. 원복을 `finally`에
두는 건 선택이 아니고, 끝나고 나서 **대상 파일이 실제로 원복됐는지 따로
확인**해야 한다.

이번에는 손상이 바로 드러났다 — `element` 줄 수가 18이 아니라 17이었다.
그런데 그걸 확인해본 이유가 "인코딩 에러가 났으니 원복도 못 했겠다"였고,
에러가 안 났으면 그냥 넘어갔을 것이다.

**이 교훈은 인코딩과 무관하다.** cp949는 스크립트를 죽인 계기였을 뿐이고, 원복이
`finally`에 없다는 결함은 어떤 예외에도 똑같이 터진다. 이 프로젝트는 가드가
진짜 발동하는지 보려고 충돌 주입을 계속 하므로(코퍼스 disjoint,
`HARD_FILTER_FIELDS`, id 공간) 재발 조건이 상시 존재한다. 그래서 일반 규칙은
**`CLAUDE.md`의 Gotchas에 따로 올렸다** — 이 문서 제목으로는 찾아지지 않는다.

## 공통 원인과 판별법

다섯 사례 모두 **"UTF-8을 전제한 데이터가 cp949를 기본값으로 쓰는 경계를
지나갈 때"** 발생한다. 경계는 이렇게 나뉜다.

| 경계 | 기본값 | 강제 방법 |
|---|---|---|
| pip의 requirements 파싱 | 시스템 로케일 | 없음 → ASCII만 쓴다 |
| Git Bash 명령줄 인자 | ANSI 코드페이지 | 없음 → 파일로 우회 |
| Git Bash heredoc | ANSI 코드페이지 | 없음 → 셸 밖에서 파일 생성 |
| Python `sys.stdout` | 콘솔 코드페이지 | `PYTHONIOENCODING=utf-8` |
| **자식 프로세스 stderr/stdout** | **콘솔 코드페이지** | **bytes로 받아 `cp949`로 디코드** |
| PowerShell `Set-Content`/`Add-Content` | 시스템 ANSI | `-Encoding utf8` |

셸 파이프도 같은 경계다. `curl ... | python -c "json.load(sys.stdin)"`에서
stdin이 cp949로 해석돼 서로게이트가 섞였다 — 파이프를 없애고 Python 안에서
직접 호출하는 편이 낫다.

### 가장 먼저 할 판별: 데이터가 깨진 건가, 표시가 깨진 건가

이걸 먼저 가르지 않으면 엉뚱한 것을 고친다.

- **표시만 깨짐** — 같은 값을 UTF-8 파일로 써서 읽어보면 정상이다.
  고칠 대상은 출력 경로이지 데이터가 아니다. (사례 4)
- **데이터가 깨짐** — 파일로 써도 깨져 있거나, 수신 측이 바이트 단위로
  거부한다(`Invalid UTF-8 start byte`). 생성 경로를 바꿔야 한다. (사례 2·3)

## 배운 점

**한국어 프로젝트를 Windows에서 개발하면 인코딩 경계가 계속 나타나고, 증상은
매번 다르게 보인다.** pip 실패, HTTP 400, 콘솔 깨짐은 표면적으로 아무 관련이
없어 보이지만 전부 같은 원인이다. 증상으로 검색하지 말고 **"이 데이터가 방금
어떤 경계를 지났는가"** 를 먼저 물어야 빨리 잡힌다.

그리고 사례 3이 특히 교훈적이다. 사례 2의 해결책("파일로 우회")을 적용하면서
파일을 **문제의 원인인 그 셸로** 만들었기 때문에 해결이 되지 않았다. 우회
경로가 우회하려던 대상을 다시 지나가지 않는지 확인해야 한다.

### 체크리스트

한글이 포함된 데이터를 다룰 때:

- [ ] 이 파일을 읽는 도구가 인코딩을 명시적으로 받는가, 로케일에 맡기는가?
      로케일에 맡긴다면 ASCII로만 쓴다(예: `requirements.txt`).
- [ ] 셸 명령줄로 한글을 넘기고 있지는 않은가? 넘긴다면 파일로 우회한다.
- [ ] 그 우회용 파일을 **셸 밖에서** 만들었는가?
- [ ] Python 스크립트의 한글 출력을 콘솔로 보고 있는가? 파일로 받거나
      `PYTHONIOENCODING=utf-8`을 준다.
- [ ] **자식 프로세스의 출력을 읽는가? `text=True, encoding="utf-8"`은
      쓰지 않는다** — bytes로 받아 `cp949`로 디코드한다.
- [ ] PowerShell로 파일을 쓴다면 `-Encoding utf8`을 줬는가?
- [ ] **깨진 게 데이터인지 표시인지 먼저 확인했는가?**

사례 5의 나머지 절반(**파일을 조작하는 검증 스크립트는 원복을 `finally`에 두고
끝나고 원복 여부를 확인한다**)은 인코딩과 무관한 일반 규칙이라 이 체크리스트에
두지 않았다 — `CLAUDE.md`의 Gotchas에 있다. 이 문서에서만 읽히면 다음에
가드 검증 스크립트를 짤 때 이 제목의 문서를 열어볼 이유가 없다.
