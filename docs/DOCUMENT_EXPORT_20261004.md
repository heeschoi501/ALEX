# ALEX 문서 생성·내보내기 — 2026-10-04 Codex

## 사용 방법

ALEX가 문제·보고서·설명을 답변한 뒤 오른쪽 **문서 저장 · 최신 답변**에서 엑셀/PDF/PPT/Word를 누른다. 모델을 다시 호출하지 않고 저장된 최신 assistant 답변을 파일로 변환한다. 다운로드 버튼은 앱의 기존 로컬 인증 토큰을 사용한다.

터미널과 웹에서 같은 명령을 쓴다.

```text
/export xlsx
/export pdf last 통계 시험지
/export pptx last 발표 자료
/export docx all 전체 대화
/document docx 시험 문제 | # 통계 시험
1. 평균과 중앙값의 차이를 설명하세요.
```

- `/export`만 쓰면 기존과 같이 **전체 세션을 Markdown**으로 저장한다.
- 형식을 지정하면 기본 범위는 `last`: 최신 assistant 답변. 최근 200개보다 오래된 답변도 검색한다.
- `all`은 현재 세션의 **전체 대화**를 날짜·역할과 함께 저장한다. 이전 페이지도 시간 순서대로 합친다. 대화 요약으로 원문을 대신하지 않는다.
- `/document 형식 제목 | 내용`은 직접 입력한 내용을 저장한다. 본문에는 줄바꿈·Markdown 표·코드 블록을 사용할 수 있다.
- 생성 파일은 사용 중인 데이터 폴더의 `exports/alex-document-날짜-시각-난수.확장자`에 저장한다. 기존 파일을 덮어쓰지 않는다.

## 출력 범위

| 형식 | 구현 내용 |
| --- | --- |
| XLSX | Markdown 표를 별도 워크시트로 변환. 나머지 본문은 본문 시트에 보존. 한글, 헤더, 줄바꿈, 셀 높이, 자동 필터, 첫 행 고정. 값은 문자열로 저장해 `01`, `=2+2`를 원문 그대로 유지하고 수식으로 실행하지 않음. |
| DOCX | A4, 한글 글꼴, 제목·본문·번호/글머리 텍스트·코드·실제 Word 표. 표 헤더 반복. |
| PPTX | 16:9, 편집 가능한 텍스트, 한글 글꼴, 절 제목별 분할, 긴 내용을 추가 슬라이드로 나눔, 페이지 번호. 표는 텍스트 행으로 표현. |
| PDF | A4, 제목·본문·코드·표·한글. 설치된 Edge/Chrome/Chromium의 별도 headless 프로필로 인쇄. GPU와 소프트웨어 GPU 래스터라이저를 끄고 앱/기존 브라우저 프로필을 사용하지 않음. |

입력은 기본 Markdown의 제목·문단·목록·코드·파이프 표만 해석한다. 복잡한 Markdown, 수식 조판, 그림 삽입, 차트, 템플릿 복제는 이 버전에 포함되지 않는다. 문서 생성은 답변의 정답 여부를 검증하거나 모델을 재학습하지 않는다. 문제지/정답지를 나누려면 각각의 내용을 생성/직접 입력해 따로 저장한다.

Office 출력은 Python 표준 라이브러리로 OOXML ZIP/XML을 작성하므로 Office 설치나 외부 Python 런타임 패키지가 필요 없다. PDF는 설치된 지원 브라우저가 필요하고, 없거나 60초 내 인쇄 실패하면 DOCX 대안을 안내한다. 외부 이미지/웹 콘텐츠는 불러오지 않으며 HTML은 모두 이스케이프한다.

제한: 본문 500000자, 제목 200자, 파일/다운로드 32MB, XLSX 100시트·표당 100열/20000행·셀 32767 UTF-16 단위, PPTX 200슬라이드. 제한 초과는 오류로 알리고 본문을 조용히 잘라 저장하지 않는다. Excel 행 높이는 최대 409.5pt이므로 아주 긴 셀은 셀 편집/수식 입력줄에서 전체 내용을 확인해야 한다.

## 평가 중·앱 재시작 없이 쓰는 방법

실행 중 ALEX는 새 Python 코드를 자동으로 불러오지 않는다. **Claude의 GPU 평가 동안 재시작하지 않았다.** 평가 완료 뒤 기존 정상 lifecycle 절차로 앱을 재시작하고 웹 페이지를 새로고침하면 명령/버튼이 활성화된다.

이미 준비된 UTF-8 텍스트 파일은 지금도 별도 CPU 명령으로 변환할 수 있다. 이 명령은 ALEX 대화나 Ollama를 시작하지 않는다.

```powershell
py -3.11 -X utf8 scripts/document_export.py --format xlsx --text-file 자료.md --title 문제은행
py -3.11 -X utf8 scripts/document_export.py --format pdf --text-file 자료.md --title 시험지
py -3.11 -X utf8 scripts/document_export.py --format pptx --text-file 자료.md --title 발표자료
py -3.11 -X utf8 scripts/document_export.py --format docx --text-file 자료.md --title 보고서
```

기본 출력 폴더는 `data/exports`; 별도 위치는 `--data-dir`로 데이터 폴더를 지정한다. 문서 생성은 승인된 데이터 폴더의 exports 아래만 쓰고, 웹 다운로드는 정해진 생성 파일 이름과 로컬 토큰을 모두 확인한다. 경로 탐색·임의 파일·심볼릭 링크·폴더 리다이렉션은 거부한다.

## 검증과 기록

- 구현/다운로드/명령/보존 검사: `tests/test_documents.py`. PDF 자동 단위 검사는 모델/실제 브라우저 없이 프로세스를 mock 처리하며 별도의 실제 PDF 인쇄 검사도 수행.
- 관련 기존 앱/웹/스토리지/링크 검사 포함: `logs/codex-document-integration-20261004.txt`.
- 실제 파일 생성·독립 Office 파일 판독·PDF 한글 텍스트/렌더링: `logs/codex-document-smoke-20261004.txt`, `logs/document-export-smoke-20261004/receipt.json`, 같은 폴더의 `pdf-preview.png`.
- 원본 백업: `logs/codex-backup-document-export-20261004/`.
- 시작부터 진행 기록: `logs/codex-document-export-20261004.md`.
- QA의 python-docx/python-pptx/openpyxl/pypdf/Poppler는 Codex 번들에서만 사용. ALEX 런타임 의존성에 추가하지 않았다.
- GPU/Ollama/모델·설정·평가 스크립트·얼린 평가 복사본을 변경하지 않았다. 무거운 전체 테스트 대신 영향 범위의 CPU 검사만 실행했다.
- 실제 Microsoft Office 앱에서의 열기/렌더링은 미검증. OOXML 구조/관계와 독립 판독기로 검사했고 PDF는 실제 출력물을 시각 확인했다.

형식 참고: [Microsoft PresentationML 구조](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document), [Microsoft 슬라이드 마스터](https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-slide-masters).
