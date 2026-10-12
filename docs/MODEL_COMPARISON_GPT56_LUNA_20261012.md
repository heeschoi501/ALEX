# ALEX 로컬 모델과 GPT-5.6 Luna 비교: 실행 준비, 실측 미실행

요청한 모델 ID는 정확히 **gpt-5.6-luna**다. 다른 Luna나 Sol로 대체하지 않는다.
공식 문서: https://developers.openai.com/api/docs/models/gpt-5.6-luna

현재 작업 환경에는 OpenAI API 자격 증명과 사용자 PC의 로컬 모델 연결이 없다. **두 모델의 실제 점수·시간 비교 결과는 없다.** 아래는 실행 가능한 비교 도구와 그 채점기 검증이다.

## 비교 방법

scripts/compare_python.py가 기존 grade_python.py의 기본 6·어려움 6·전문가 6 과제를 같은 입력과 검사로 평가한다.

- 같은 한국어 요구사항과 중립 system 지시를 사용한다. 정답·검사 코드는 모델 입력에 넣지 않는다.
- 과제마다 독립된 1회 생성, 기본 출력 상한 8,192토큰·생성 시간 제한 180초·코드 실행 15초.
- API와 로컬 서버의 추론·토큰 한도 의미가 같다고 가정하지 않는다. OpenAI는 API 기본 추론 설정, Strata는 think 켬·medium, Ollama는 think 켬이다.
- 과제·검사 전체의 SHA-256을 기록한다. 서로 다른 버전의 답안을 섞지 않는다.
- 실행은 임시 폴더·별도 Python 프로세스·기존 I/O 가드로 한다. API 키 등 부모 환경의 자격 증명은 생성 코드에 전달하지 않는다. Python 가드는 OS sandbox와는 다르다.
- 성공은 실제 검사 완료로 판정한다. 출력에 가짜 성공 문구를 넣거나 정상 종료만 해서는 통과하지 않는다.
- 생성 오류·누락 답안을 기록하며, 전부 미생성이면 measurement_status=unmeasured다. API 접속 실패를 모델의 코딩 점수로 해석하지 않는다.

기존 과제를 재사용하는 역사 평가다. 새 독립 평가나 SWE-bench 결과가 아니다. 함수 코딩의 동작 검사를 측정하며, 요구사항의 의도·모든 계산량 제약이나 ALEX /dev-auto와 Work 에이전트의 전체 성능을 보증하지 않는다.

## 여기서 완료한 검증

채점기 회귀 7개 PASS: 프롬프트에 검사 코드 없음, 올바른 코드 통과, 오답·가짜 성공 출력 거절, sys.exit(0)·os._exit(0) 거절, 네트워크 차단, 부모 API 키 미상속.
--dry-run의 18개 과제 내보내기도 확인했다. 이 결과는 채점기 검증이며 모델 성능 점수가 아니다.

## PC에서 같은 조건으로 실행

GitHub 코드를 받은 뒤 ALEX 폴더에서 실행한다. OpenAI API 키는 PC의 OPENAI_API_KEY 환경변수에 설정돼 있어야 한다. 키를 답안 파일이나 보고서에 넣지 않는다.

~~~bash
python scripts/compare_python.py --dry-run
python scripts/compare_python.py --provider openai --model gpt-5.6-luna --execute --runs 3 --out logs/model-compare/luna56.json
python scripts/compare_python.py --provider strata --model qwen3.8-flash-next --execute --runs 3 --out logs/model-compare/alex-strata.json
~~~

로컬 모델 ID는 PC의 실제 Strata 설정과 일치해야 한다. Ollama라면 --provider ollama --model 실제태그를 사용한다. --host는 로컬 서버 주소를 지정하는 옵션이다.

실행 시 기본 ALEX 설정·모델 선택·대화 DB를 변경하지 않는다. 결과는 logs/에 저장하며 공개 배포에서 제외한다. 반복 3회는 모델당 54회 생성이므로, 먼저 --runs 1로 연결과 출력 조건을 확인할 수 있다.

## 모델 답안을 따로 받아 채점

모델 연결 없이 동일 프롬프트에 대한 답안 JSON을 가져와 평가할 수도 있다.

~~~json
{
  "suite_sha256": "--dry-run에 표시된 실제 해시",
  "model": "gpt-5.6-luna",
  "answers": [
    {"task": "merge_ranges", "run": 1, "reply": "모델이 실제로 반환한 코드"}
  ]
}
~~~

~~~bash
python scripts/compare_python.py --answers 답안.json --execute --out logs/model-compare/imported.json
~~~

제공되지 않은 과제는 누락으로 기록한다. 저장 답안의 모델 이름은 제공자가 기록한 값이며 도구가 실제 호출 모델을 검증한 값은 아니다. 기능 성공·실패, 생성 오류, 생성 시간, 전체 과제 수와 실패 사례를 함께 비교해야 한다.
