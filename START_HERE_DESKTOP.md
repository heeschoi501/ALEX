# ALEX 데스크톱 신규 설치 / 이어서 작업하기

이 이전본은 2026-09-25 노트북 상태의 개인용 스냅샷입니다.
수정된 코드와 대화·기억·경험 기록을 포함합니다. 모델과 Ollama 실행 파일은
데스크톱 첫 실행 시 내려받습니다. 노트북과 자동 동기화되지는 않습니다.

## 설치 순서

1. ZIP을 데스크톱으로 옮기고 **압축을 전부 해제**하십시오.
   내부 `jarvis` 폴더를 원하는 위치에 두십시오. 예: `C:\ALEX\jarvis`.
   신규 설치용이므로 기존 ALEX 폴더 위에 덮어쓰지 마십시오.
2. Windows 64비트 Python 3.11을 설치하십시오. 사용자별 기본 설치 경로가
   `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`인지 확인하십시오.
   공식 설치 파일: https://www.python.org/downloads/release/python-3119/
   페이지의 `Windows installer (64-bit)`를 사용하며 tkinter 구성요소를 포함하십시오.
   3.11 계열 검증 환경을 유지하기 위한 안내이며 최신 Python 버전이라는 뜻은 아닙니다.
3. `00_START_DESKTOP.cmd`를 두 번 클릭하십시오. 지정 Windows Python으로 릴리스를
   검증한 뒤 런처가 열립니다. 첫 설치의 다운로드 안내에서 **모델 다운로드 시작**을
   누르십시오. Ollama 런타임과 `gpt-oss:20b`를 준비합니다.
   현재 노트북 모델 파일 합계는 약 13.8GB이며 런타임 공간은 별도로 필요합니다.
   다운로드 중에는 인터넷 연결이 필요합니다. 모델 준비 후 일상 실행은 로컬입니다.
4. 브라우저가 열리면 `/status`를 입력하십시오. 모델은 general/coder 모두
   `gpt-oss:20b`여야 합니다. 문서상 대상 RTX 5080 장비라면 DESKTOP 감지 여부도
   확인하십시오. 다른 값이면 강제로 바꾸기 전에 환경부터 확인하십시오.
   같은 화면에 `web: on (/search, /web)` 이 보여야 합니다(ADR-051 웹 읽기).
   채팅에 `iverilog 최신 버전 검색해줘` 를 한 번 쳐서 출처가 붙은 답이 오는지 확인하십시오
   (명령 없이 부탁만으로 됩니다. `/search` 명령도 같습니다).
   노트북 실측은 모델이 떠 있을 때 5~10초였습니다(PERFORMANCE.md 25절).
5. **노트북에서도 이 데스크톱 ALEX 를 쓰려면**(집 밖 포함) `REMOTE_SETUP.md` 를 따르십시오.
   Tailscale 을 설치한 뒤 관리자 PowerShell 에서 `scripts\remote_setup_desktop.ps1` 을 한 번 실행합니다.
6. 다음부터는 `start_alex.vbs`로 실행하십시오. 원하는 실제 코드 폴더는
   화면의 **프로젝트 폴더 변경**으로 지정하십시오.
7. Python 코딩 실습은 `study\start_jupyterlab.cmd`를 더블클릭하십시오. 첫 실행 때
   별도 가상 환경에 JupyterLab을 설치합니다. 브라우저에서
   `ALEX_PYTHON_LAB.ipynb`를 열고 `study/JUPYTERLAB_GUIDE.md`를 따라 하십시오.

앱을 열지 않고 검증만 하려면 CMD에서 `00_START_DESKTOP.cmd --verify-only`를 실행합니다.
설치·다운로드 실패 시 오류 문구와 `logs/web-server.log`를 확인하십시오.
이 패키지에 Python, GPU 드라이버, 코딩 에이전트 자체는 들어 있지 않습니다.

## 노트북과 한곳에서 이어 쓰기

데스크톱을 기준 ALEX로 쓰려면 노트북에서 `connect_desktop.cmd`로 원격 접속하십시오.
노트북 브라우저의 `127.0.0.1:8766`도 데스크톱 ALEX의 대화·기억·모델을 사용합니다.
프로그램 코드 변경은 데스크톱의 `jarvis` 폴더에서만 진행하십시오. 노트북 복사본의
코드 변경은 자동 반영되지 않습니다. 자세한 절차와 오프라인 예외는 `REMOTE_SETUP.md`의
"두 PC의 업데이트를 한곳에 모으려면" 절에 있습니다. ZIP의 DB는 최초 이전본이며
설치 후 두 PC의 독립 DB를 병합하는 기능은 아닙니다.
나중에 미니 PC를 기준 ALEX로 옮기려면 원격 모델 연결이나 미니 PC 자체의
대체 모델을 먼저 검증하십시오. 현재 데스크톱 GPU를 원격 작업자로 쓰는 기능은
아직 없으므로 `REMOTE_SETUP.md`의 미래 구성 안내를 완성 기능으로 읽지 마십시오.

## 보존한 기록과 설정

- SQLite online backup으로 대화·세션·기억·할 일·경험·검토 상태를 복제했습니다.
- 경험 id, split, content_hash는 그대로 보존했습니다. 새로 승인한 경험은 없습니다.
- `transfer/portable-state.json`은 기억·할 일·경험의 보조 내보내기입니다.
  신규 설치 DB에 이미 들어 있으므로 다시 import할 필요는 없습니다.
- 원래 노트북 작업 폴더를 자동으로 열지 않도록 **복사본에서만** 작업 폴더 상태를
  초기화합니다. 대화 내용 안의 과거 경로는 기록이므로 변경하지 않습니다.
- 설정 초기값은 think=True, ctx=8192, output=6144, timeout=360, num_gpu=0입니다.
  이는 노트북에서 가져온 값이며 **데스크톱 최적값으로 검증하지 않았습니다**.
  데스크톱 전용 세 한도는 0(전역값 상속)으로 유지합니다. 실측 전에 임의로 올리지 마십시오.
- 최근 테스트, 측정 JSON과 인수 문서가 포함됩니다. 이전 모델 평가를 다시 할 필요는 없습니다.
- 이 ZIP을 만든 후 노트북에 새로 쌓인 대화는 이 스냅샷에 포함되지 않습니다.

## 데스크톱의 다음 에이전트에게 전달할 내용

아래 내용을 데스크톱의 코딩 에이전트에게 전달하고 프로젝트 경로만 바꾸십시오.

> 프로젝트: C:\ALEX\jarvis
> START_HERE_DESKTOP.md, GPT_NEXT.md 마지막 2026-09-25 절과 A~F를 읽으십시오.
> 존댓말로 답하십시오. A/C는 닫혔으므로 손대지 마십시오.
> D/E는 사용자가 직접 풀 문제이므로 보류하십시오. 경험을 대신 승인하지 마십시오.
> 먼저 Windows Python311로 verify_release.py를 실행하고 /status로 실효 모델과
> thinking/ctx/output/timeout을 읽으십시오. 측정은 겹쳐 실행하지 마십시오.
> 다음은 데스크톱 하드웨어 확인과 gpt-oss:20b 계산기 n=10(B)입니다.
> 웹 읽기(/search, /web, 채팅의 "검색해줘", ADR-051)가 2026-09-25 에 들어갔습니다. 데스크톱은 문맥 예산이
> 24000자(노트북 6000자)라 웹 자료가 더 많이 들어갑니다. 같은 세 질문으로 시간을 재서
> PERFORMANCE.md 25절 표에 데스크톱 열을 추가하십시오. 로컬 전용(local_only)은 클라우드
> LLM 경계이지 인터넷 금지가 아닙니다. 결제·주문은 범위 밖입니다.
> 사용자는 데스크톱에서 더 큰 모델(gpt-oss:120b, 65.37GB)로 가기로 했습니다. 측정으로 근거를
> 만든 뒤 바꾸십시오. 순서는 CLAUDE_NEXT.md 0절(GPT_NEXT.md 마지막 절도 같습니다). 20b 가
> 틀리는 과제 - 오답이 쌓인 대화에서 바로잡기(20b 0/3) - 로 가르십시오:
>   python scripts/replay_session.py replay --model gpt-oss:120b -n 3
> HARDWARE_VALIDATION.md B절의 과거 qwen 모델 예시는 현재 모델 선택이 아닙니다.
> 모델은 ADR-047과 현재 설정의 gpt-oss:20b를 기준으로 확인하십시오.
> .cmd 파일에 한글을 넣지 마십시오. 잘못된 판단을 사용자가 정정하면 F에 따라
> dry-run 후 raw 경험으로 남기고 승인하거나 split을 지정하지 마십시오.
> 코딩 실습은 study/ALEX_PYTHON_LAB.ipynb와 study/JUPYTERLAB_GUIDE.md에 있습니다.
> 노트북/가상 환경의 실행 결과로 실제 코드 도움 품질을 확인하십시오.

**클로드 코드로 이어가시면** 위 대신 `CLAUDE_START_PROMPT.md` 의 상자 안 내용을 붙여넣으십시오.
데스크톱의 클로드는 노트북 클로드의 기억이 없으므로 `CLAUDE_NEXT.md` 가 그 기억을 대신합니다.

별도 `C:\Users\<user>\agent-board`는 ALEX 앱이 아니므로 이 ZIP에 설치하지 않았습니다.
현재 프로젝트 인계 메시지는 `transfer/BOARD_GPT_PEEK.txt`로 동봉했습니다.
데스크톱에 보드가 이미 있다면 반드시 `read --as gpt --peek`로 읽으십시오.
없다면 이 텍스트와 GPT_NEXT.md로 시작하고 인계 결과를 문서에 남기십시오.
