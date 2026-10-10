# ALEX 구현 실습 — 힌트

[ALEX_IMPLEMENTATION_LAB.ipynb](ALEX_IMPLEMENTATION_LAB.ipynb) 의 힌트입니다. **정답 코드는 없습니다.**
먼저 10~15분 직접 풀어 본 뒤에 보세요. 다 풀었으면 노트북 맨 아래 표의 실제 ALEX 코드와 비교해 보세요.

| 문제 | 힌트 |
|---|---|
| 1. 프로필별 환경 ★ | `dict(base_env)` 로 **복사**부터 하면 원본이 안 바뀝니다. 복사 → 고정 키 넣기 → 데스크톱일 때만 한 키 더. |
| 2. 필요한 겹침 수 ★ | 안쪽부터: `min(n, 10)` → `0.5 *` → `math.ceil(...)` → `max(2, ...)`. |
| 3. 빈 답의 원인 ★ | 위에서부터 걸러 내세요: `done_reason` → `isinstance(prompt_tokens, int)` → `num_ctx` 가 0 이 아닌가 → 합이 넘는가. |
| 4. 출처 번호 ★ | `for number, (_, known) in enumerate(sources, start=1):` 로 돌며 같은 주소면 그 번호, 끝까지 없으면 `append` 후 `len(sources)`. |
| 5. 두 글자 조각 ★★ | `range(len(word) - 1)` 과 슬라이스 `word[i:i + 2]`. 한글 판별은 `'가' <= word[0] <= '힣'`. |
| 6. 뜻 있는 단어 ★★ | `{어간: 조각목록}` 사전에 `setdefault` 를 쓰면 처음 것만 남습니다. `list(사전.values())` 는 넣은 순서를 지킵니다. |
| 7. 바로잡는 말 ★★ | 부분 문자열은 `in` 으로, "끝나는지" 만 `re.search(r'잖아요?[.?!…~]*$', body)`. **규칙 순서**가 중요합니다 — 자기 의심(`'내가 틀'`)을 먼저 거르세요. |
| 8. 개인 정보 검색어 ★★ | 정규식 하나에 `|` 로 네 모양을 묶을 수 있습니다. 메일 `[\w.+-]+@[\w-]+\.[\w.]+`, 긴 숫자 `\d{9,}`. 역슬래시가 있으니 `r'...'` raw 문자열을 쓰세요. |
| 9. 단어가 나왔나 ★★ | 어간 검사를 먼저. 그다음 불용어를 뺀 조각 목록에서 `terms` 에 있는 개수를 세고, 나눗셈 없이 `2 * 개수 >= 전체 개수` 로 "절반 이상" 을 판단하세요. |
| 10. 배운 것 고르기 ★★★ | 함수 안에 작은 함수 `usable(x)`, `expired(x)` 를 두면 읽기 쉽습니다. 만료는 `now - x['created_at'] > timedelta(days=90)`. 정렬 키는 `(score, created_at)`, `reverse=True`. |
| 11. 검색 캐시 ★★★ | `__init__` 에서 `self.cache = {}`, `self.blocked_until = 0.0`. `try: ... except Blocked: 기록; raise` 로 적고 다시 던집니다. 복사본은 `list(results)`. |
| 12. 도구 호출 루프 ★★★ | 한 차례의 순서: 모델 부르기 → `calls` 가 없으면 `return` → 마지막 차례면 `break` → `runner` 결과를 붙이기. 반복 밖에서 `content` 확인. |
| 13. 코드 지문 ★★ | `sorted(files)` 는 경로(키)를 정렬합니다. 파일마다 `update` 를 두 번. |
| 14. 개념 확인 ★ | q1 ADR-054 · q2 ADR-057 · q3 ADR-057 결정 4 · q4 PERFORMANCE 24 · q5 PERFORMANCE 26-1 · q6 PERFORMANCE 26 의 속도 표를 읽어 보세요. |

## ALEX 와 같이 풀기

막히면 ALEX 에 이렇게 물어보세요: `이 함수의 실패 원인을 설명하고 힌트 하나만 주세요. 완성 코드는 아직 주지 마세요`.
다 푼 뒤에는 `이 함수의 경계 조건 2개를 더 제안해 주세요` 라고 물어 검사를 늘려 보세요. ALEX 가 준 코드도 검사 셀로 확인하세요.
