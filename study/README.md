# Project JARVIS — Python Study Track

JARVIS를 별도 교재로 공부하기보다 **실제 코드를 작은 순서로 읽고 수정**하기 위한 폴더다.

JupyterLab에서 직접 코드를 실행하려면 [JUPYTERLAB_GUIDE.md](JUPYTERLAB_GUIDE.md)를
읽고 `start_jupyterlab.cmd`를 실행한다. `ALEX_PYTHON_LAB.ipynb`는 기초 4문제와
실제 라우터 관찰, 각 문제의 실행 검사를 담고 있다. 미완성 함수는 사용자가 직접 푼다.

## 1. 먼저 풀기

`practice.py`
- dict/count
- if
- Min-Max normalization
- 문자열 parsing
- Regex/validation

## 2. v0.1.3 문제

`practice_v013.py`
- Hardware Profile 분기
- `pathlib.Path`와 traversal boundary
- Regex redaction
- Counter/statistics
- Context budget
- file cache invalidation
- byte 단위 formatting
- 순서 보존 중복 제거

정답은 일부러 넣지 않았다.


## 2-1. ALEX 구현 실습 (2026-09-25)

[ALEX_IMPLEMENTATION_LAB.ipynb](ALEX_IMPLEMENTATION_LAB.ipynb) — 그날 ALEX 에 들어간 기능을 작은 함수로 직접
만들어 보는 14문제(★ 기초 → ★★★ 어려움). 데스크톱 전용 FA 설정, 빈 답 원인, 배운 것 고르기, "틀렸어" 알아채기,
개인 정보 검색어 거르기, 검색 캐시, 도구 호출 루프, 코드 지문, 설계 이유 개념 확인. 검사는 노트북 안에 있고
정답은 없습니다. 힌트: [ALEX_IMPLEMENTATION_LAB_HINTS.md](ALEX_IMPLEMENTATION_LAB_HINTS.md).

## 3. 자동 채점

정답 구현은 들어 있지 않지만 예상 동작을 확인하는 채점기는 제공한다.

```bash
python study/check_practice.py --set basic
python study/check_practice.py --set v013
python study/check_practice.py --set all
```

처음에는 `NotImplementedError` 때문에 FAIL이 나오는 것이 정상이다. 문제를 하나씩 구현하면서 PASS 수를 늘린다.

## 4. 본체 읽기 순서

```text
jarvis/router.py       # if/tuple/dataclass/Enum
jarvis/config.py       # dataclass/env/validation
jarvis/storage.py      # SQLite/contextmanager
jarvis/providers.py    # class/inheritance/HTTP/JSON
jarvis/tools.py        # pathlib/file safety/search/cache
jarvis/core.py         # 객체 조립/context/fallback
jarvis/app.py          # CLI/전체 프로그램 연결
jarvis/web.py          # 작은 HTTP server
```

## 5. 공부 방식

기능 하나를 고를 때:

```text
관련 테스트 읽기
 -> 직접 예상 적기
 -> 작은 변경
 -> 해당 테스트 실행
 -> python scripts/verify_release.py
 -> CHANGELOG/TODO에 한 줄 기록
```

AI에게 곧바로 완성 코드를 받기보다 먼저 힌트만 받고 15~30분 직접 구현하는 방식을 권장한다.

자세한 개념 설명과 과제는 루트의 `STUDY_GUIDE.md`를 따른다.
