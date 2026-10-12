# ALEX 검증 재개 — 2026-10-12

## 반영 내용

- 로그인 만료 또는 계정 중지 후 로컬 owner로 전환되는 문제를 수정했다.
- 동시 로그인 실패 집계와 임의 로그인 이름에 의한 잠금 해제를 방지했다.
- 자료 업로드 응답 전에 모델 잠금을 해제하고, 프로젝트 대화 생성 중 중복 전송을 막았다.
- Python 3.10 unittest 검사 집계와 선택적 pygame 글꼴 초기화를 보완했다.
- 공개 압축본에서 개인 설정, 실행 데이터, 비공개 인계 파일과 브라우저 산출물을 제외한다.
- 동일한 18개 Python 과제용 비교 도구와 선택적 브라우저 검사 도구를 추가했다.

## 확인한 결과

소스 검증과 배포 압축본 검증은 구분한다. 임시 데이터와 합성 모델 응답을 사용했다.

- 기존 소스 검사: Python 3.10, 3.11, 3.12에서 전체 릴리스 검사 PASS.
- 별도 작업 사본의 ZIP 및 tar.gz 구성 감사: PASS.
- ZIP 압축 해제 후 전체 검사: 1,342개 중 1,338 PASS, 4 SKIP, 0 FAIL. 릴리스 검사 PASS.
- 압축본에서 건너뛴 4개는 Windows 전용 검사 3개와 선택적 PDF 환경 검사 1개다.
- HTTPS 브라우저 검사: 데스크톱, 모바일 너비, 프로젝트 자료 분리, 로그인, 권한, PWA PASS. JavaScript 오류 0개.
- 모델 비교 채점기 회귀 검사: 7개 PASS. 모델 성능 점수는 미측정이다.

첫 압축본 검사에서는 이미지 검사 완료 요약이 없어 실패로 기록됐다. 해당 이미지 검사 77개와 압축본 전체 검사 재실행은 통과했지만, 초기 종료 원인은 확정하지 않았다.

## 재현

```bash
python scripts/verify_release.py --test-workers 8
python scripts/package_release.py
python scripts/compare_python.py --dry-run
node scripts/verify_web_ui.cjs
```

브라우저 검사는 별도 Node, Playwright, Chromium, openssl 환경이 필요하다. 모델 비교 도구의 --execute는 실제 호출 및 생성 코드 실행을 허용하며, OpenAI 호출에는 API 자격 증명이 필요하다. 실제 GPU, 외부 모델, 사용자 데이터 및 실제 모바일 설치를 검증한 결과는 없다.
