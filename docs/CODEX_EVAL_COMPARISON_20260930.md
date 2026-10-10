# 완료된 교훈 off/on 평가 비교 — 2026-09-30, Codex

## 결론

27B의 쉬운 test 16개를 모두 대응시킨 결과, **교훈 off/on 모두 엄격 성공 15/16이며 같은 한 과제가 계속 실패했다.** 성공/실패가 서로 다른 과제에서 뒤바뀐 경우는 없다. 이 실험에서는 교훈을 켜서 성공한 과제가 추가되지 않았다.

과제별 시간 합은 2,128.2초에서 2,235.9초로 107.7초(+5.06%) 늘었다. 단일 실행이고 소스·설정이 완전히 같은 조건임을 증명할 메타데이터가 없으므로, 교훈 때문에 느려졌다고 인과적으로 단정하지 않는다. 현재 어려운 과제 11개 평가의 결과는 이 보고서에 포함하지 않는다.

## 입력과 읽기 전용 검증

| 입력 | 완료·용도 | SHA-256 |
|---|---|---|
| `logs/coding-agent-eval-mut-test-qwen3.8_27b.json` | off, 16행. 종료 txt의 15/16·wrote 확인 | `1d01a1d12cb66df5430e027c7871f781673dc36c01223421bf1813efbb8b0588` |
| `logs/coding-agent-eval-mut-test-insights-qwen3.8_27b.json` | on, 16행. 종료 txt의 15/16·wrote 확인 | `db539ccc49db7f2abc9ba93c95e36f935702e0bd6e5d2a5c85bc97c0178d40fc` |
| `logs/probe/mutation_tasks.json` | 과제 ID·baseline_id·변이 정의 | `1150d3a90af83f8a96651f2b060eb51d9658dd25d6c4d209bca87e1c7cfa83dd` |
| `logs/coding-insights-train.json` | on에서 사용한 교훈 8개 | `05f496faae2e11f9f8d1be7634d05d91a19d287811f9d82354b0fd67a0c39669` |

표준 라이브러리로 파일 안정성과 지문을 확인하고 JSON을 집계했다. 모델·ALEX 테스트·별도 복사본 패치를 실행하지 않았다. 원본 결과와 현재 실험 입력을 변경하지 않았다.

재사용할 수 있는 대응·수치 자료는 [codex-paired-test-20260930.json](../logs/probe/codex-paired-test-20260930.json)에 저장했다. 이 파일에는 16개 대응, 입력 지문, 중복 ID 해결 근거, 전후 상태·시간·회차가 들어 있다. 저장 후 JSON을 다시 읽어 원래 계산 결과와 일치함을 확인했다.

## 앞서 미확인으로 남겼던 ID 충돌 두 건 해결

baseline JSON의 2·3번째 행은 둘 다 `mut_device_change_constant_25`다. 둘 다 선택·전체 검사를 통과하고 `restored_original=true`이며, round_diff 한 개가 해당 변이의 정확한 역변이다.

| baseline 행(1부터) | 제거된 변이 줄의 차이 | 복구된 줄 | 현재 과제 ID |
|---|---|---|---|
| 2 | `b"\xff" * 6 + raw * 15` | `b"\xff" * 6 + raw * 16` | `mut_device_change_constant_25_eaad` |
| 3 | `b"\xff" * 5 + raw * 16` | `b"\xff" * 6 + raw * 16` | `mut_device_change_constant_25_ac3d` |

과제 파일의 replace/find와 각 diff의 삭제/추가 줄을 대조해 각각 단 하나의 후보와 일치함을 확인했다. **행 번호는 위 지문의 파일에 대한 위치 참조일 뿐, 임의 순서 매칭 근거가 아니다.** 14개는 고유 baseline_id, 2개는 이 diff 근거로 대응했으며 baseline/on 모두 16행을 한 번씩 사용했다. 따라서 이전 분석의 ‘14개 확정·2개 미확인’은 이 문서에서 해소됐다. 과거 문서는 당시 관측 기록으로 보존한다.

## 엄격 성적과 시간

엄격 성공 = `bug_detected is True` + `acceptance_passed is True` + 정수 `suite_exit == 0` + 명시적 `new_failures == []`.

| 항목 | off | on |
|---|---:|---:|
| 계획/관측/감지된 과제 | 16/16/16 | 16/16/16 |
| 엄격 성공 | 15/16 (93.75%) | 15/16 (93.75%) |
| 선택 검사 미해결 | 1 | 1 |
| 성공 중 원본 동일 복구 | 11 | 12 |
| seconds 합 | 2,128.2 | 2,235.9 |
| seconds 중앙값 | 119.45 | 122.05 |
| rounds 분포 | 2회 14개 / 3회 1개 / 4회 1개 | 같음 |

짝별 시간 차이의 중앙값은 +3.7초다. 13개는 on이 더 길고 3개는 더 짧았다. 이 수치는 과제별 기록 시간이며 모델 로딩·대기·전체 파이프라인의 벽시계나 전력량을 나타내지 않는다. 두 실행의 원본 동일 복구 수 차이도 독립적인 정답 검증이나 기능 개선의 증거로 단정하지 않는다.

| 현재 과제 ID | off → on 상태 | off 초 | on 초 | 차이 초 |
|---|---|---:|---:|---:|
| `mut_core_remove_assignment_598_da39` | 미해결 → 미해결 | 154.2 | 180.2 | +26.0 |
| `mut_device_change_constant_25_eaad` | 성공 → 성공 | 104.4 | 109.6 | +5.2 |
| `mut_device_change_constant_25_ac3d` | 성공 → 성공 | 105.1 | 105.9 | +0.8 |
| `mut_excerpt_change_boolean_153_433c` | 성공 → 성공 | 303.2 | 340.3 | +37.1 |
| `mut_excerpt_remove_assignment_189_da39` | 성공 → 성공 | 110.6 | 112.4 | +1.8 |
| `mut_lessons_remove_assignment_141_da39` | 성공 → 성공 | 107.4 | 120.0 | +12.6 |
| `mut_lifecycle_change_operator_34_7c2b` | 성공 → 성공 | 138.4 | 138.6 | +0.2 |
| `mut_portable_remove_assignment_33_da39` | 성공 → 성공 | 105.5 | 116.0 | +10.5 |
| `mut_portable_change_operator_114_6b06` | 성공 → 성공 | 135.6 | 124.1 | −11.5 |
| `mut_portable_remove_assignment_45_da39` | 성공 → 성공 | 98.8 | 107.2 | +8.4 |
| `mut_providers_remove_assignment_648_6c4c` | 성공 → 성공 | 112.5 | 114.7 | +2.2 |
| `mut_providers_remove_assignment_83_da39` | 성공 → 성공 | 109.5 | 111.7 | +2.2 |
| `mut_remote_change_operator_117_fe3b` | 성공 → 성공 | 148.2 | 148.1 | −0.1 |
| `mut_storage_swap_operands_563_6f0d` | 성공 → 성공 | 132.6 | 147.2 | +14.6 |
| `mut_webread_swap_operands_455_0459` | 성공 → 성공 | 135.8 | 127.9 | −7.9 |
| `mut_webread_remove_conditional_441_da39` | 성공 → 성공 | 126.4 | 132.0 | +5.6 |

전이 수는 성공 유지 15, 미해결 유지 1, 새 성공 0, 새 실패 0이다. ‘전체 15/16이 같음’보다 구체적인 결론은 **동일한 성공 집합을 유지했다**는 것이다.

## 남은 실패의 실제 행동과 후속 설계

실패한 `ask_file_local` 과제는 다음 원본 줄을 삭제한 변이다.

```python
content = self.file_read_tool.run(path=relative_path)
```

off/on 모두 첫 시도에 `self.file_read_tool.read(relative_path)`를 추가하고, 다음에는 `read_file(relative_path)`, 마지막에는 다시 `read(relative_path)`로 바꿨다. 두 실행 모두 네 차례 검사 후 미해결이다. 교훈을 켜도 실제 원본 API인 `run(path=...)`를 복구하지 못했다.

이 관측은 ‘대입 누락을 인식한다’와 ‘해당 도구의 올바른 호출 계약을 찾는다’가 다른 문제임을 보여 준다. 후속 설계에서는 기존 CodeT 후보 생성에 **수신 객체의 실제 클래스·호출 시그니처·프로젝트 내부 사용 예**를 제한된 읽기 문맥으로 제공하는 방안을 검토할 수 있다. 잘못된 API 이름만 순환하는 후보 여러 개는 후보 수를 늘려도 독립적인 해결 가설이 되기 어렵다.

이는 설계 제안이며 효과를 검증한 구현이 아니다. 이 과제는 이미 결과와 정답을 본 진단용 사례이므로, 같은 문제에 맞춘 보완 뒤 그 재성공만으로 일반 성능이 높아졌다고 주장하지 않는다. 별도 보류 과제에서 확인해야 한다. 현재 실험 중에는 도구·프롬프트·교훈을 바꾸지 않는다.

## 해석의 범위와 다음 작업

- 두 실행은 소스 변경이 있었던 시간대를 사이에 두고 기존 live-copy harness로 수행됐다. v2의 고정 스냅샷·manifest는 이후 적용할 변경이며 과거 결과의 조건을 소급 증명하지 않는다.
- 16개는 관련 함수에서 만든 작은 변이 집합이다. 범용 코딩·다중 파일 기능 개발·Claude/Codex 대체 능력으로 바로 환산할 수 없다.
- 엄격 15/16에서 추가 성공은 한 문제만 가능하므로 쉬운 집합의 성공률은 포화에 가깝다. 현재 hard 결과와 구분하여 보고하되, hard 평가가 끝나기 전에 최종 성적을 내지 않는다.
- 생성 교훈의 잘못된 일반화 검토는 [완료 train·교훈 분석](CODEX_TRAIN_INSIGHTS_ANALYSIS_20260930.md)에 있다. 교훈 내용의 오류와 이번 성적은 구분한다. 현재 결과는 ‘교훈이 항상 무효하다’가 아니라 ‘이 교훈 버전과 쉬운 test에서는 추가 해결을 관측하지 못했다’는 근거다.
- 실험 종료 뒤 Claude가 최종 보고할 때 이 대응표를 사용하고, v2 보완은 [3차 리뷰](CODEX_REVIEW3_20260930.md)의 남은 세 항목을 확인한다. 이번 작업에서는 새로운 모델 실행·벤치마크·코드 수정을 시작하지 않았다.
