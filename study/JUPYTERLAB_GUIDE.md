# ALEX Python 코딩 실습 — JupyterLab

이 실습은 ALEX가 코드를 얼마나 잘 만드는지 **실행 결과로 확인**하면서 Python 기초를 익히기 위한 것입니다. `STUDY_GUIDE.md`의 14·15번 문제는 사용자 풀이로 남겨 두었으므로 여기에는 정답이나 풀이를 싣지 않았습니다.

## 시작하기 (Windows)

1. [start_jupyterlab.cmd](start_jupyterlab.cmd)를 더블클릭하십시오. 첫 실행은 프로젝트의 `.venv-alex-study`에 JupyterLab을 설치하므로 인터넷과 몇 분이 필요합니다. ALEX 앱·모델과 별도의 학습 환경입니다.
2. 브라우저에서 `ALEX_PYTHON_LAB.ipynb`를 열고 **Python 3 (ipykernel)**을 선택하십시오.
3. 셀을 `Shift+Enter`로 한 칸씩 실행하십시오. `TODO` 셀을 아직 풀지 않았다면 검사에 `FAIL`이 나오는 것이 정상입니다.
4. 실습 코드는 노트북의 함수만 수정하십시오. 본체 파일과 저장된 대화는 이 실습에서 바꾸지 않습니다.

첫 실행 이후에는 같은 파일을 다시 더블클릭하면 됩니다. 끝낼 때 명령 창에서 `Ctrl+C`를 누르십시오. JupyterLab 설치·실행 명령은 [Project Jupyter의 공식 안내](https://jupyter.org/install)를 따릅니다.

## ALEX와 같이 공부하는 방법

한 문제를 먼저 직접 10~15분 풀고 검사 셀을 실행하십시오. 막히면 ALEX에 `이 함수의 실패 원인을 설명하고 힌트 하나만 주세요. 완성 코드는 아직 주지 마세요`처럼 묻고, 받은 제안을 직접 수정해 다시 실행하십시오. 이후에는 `이 코드의 경계 조건 2개를 더 제안해 주세요`라고 물어 검사를 확장하십시오. **ALEX의 코드도 실행·검사해야 합니다.** 과거의 자동 채점 만점은 새로 붙여 넣은 코드의 정확성을 보장하지 않습니다.

노트북 1~3번을 푼 뒤 같은 개념을 [practice.py](practice.py)에 옮기고, 프로젝트 루트에서 지정 Windows Python으로 다음을 실행하십시오.

```bat
"%LOCALAPPDATA%\Programs\Python\Python311\python.exe" study\check_practice.py --set basic
"%LOCALAPPDATA%\Programs\Python\Python311\python.exe" scripts\verify_release.py
```

`practice.py`에는 별도 문제도 있으므로 `basic` 전체가 바로 통과하지 않아도 됩니다. 실제 기능 변경 때는 반드시 릴리스 검증을 다시 실행하십시오. JupyterLab은 파일을 자동 저장할 수 있으므로 실험을 시작하기 전 노트북 사본을 만들어도 좋습니다.

## 다른 PC로 옮길 때

노트북과 시작 파일은 데스크톱 이전 ZIP의 `study` 폴더에 포함됩니다. 가상 환경은 PC마다 다시 만듭니다. Windows Python 3.11을 설치하고 `study\start_jupyterlab.cmd`를 실행하십시오. Python 경로가 표준 설치 위치와 다르면 시작 파일의 `ALEX_PY` 한 줄을 해당 Windows Python 경로로 바꾸십시오.
