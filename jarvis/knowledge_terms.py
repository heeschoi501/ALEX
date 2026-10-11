"""로컬 지식 검색의 한영 용어 사전(2026-10-11, claude — Codex 검토 K01).

한 줄이 한 개념이다. 질문에 그 개념의 어느 표현이 나오면 다른 표현으로도 찾는다(래치 → latch). 영어 교재를 한국어로
묻거나 그 반대일 때 쓴다. 코드 식별자·수식은 바꾸지 않는다 — 사전에 있는 낱말만 더해서 찾는다.
용어를 고치면 TERMS_VERSION 을 올린다(검색 결과가 바뀌기 때문).
"""
from __future__ import annotations

TERMS_VERSION = "2026-10-11.1"

# 디지털 설계·반도체 기초. 한국어 표현은 띄어쓰기 없이도 찾는다("비차단할당").
TERM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("래치", "latch"),
    ("플립플롭", "플롭", "flip flop", "flipflop", "flop", "dff"),
    ("레지스터", "register"),
    ("비차단 할당", "논블로킹 할당", "논블로킹", "nonblocking assignment", "nonblocking", "non blocking"),
    ("차단 할당", "블로킹 할당", "blocking assignment"),
    ("조합 회로", "조합 논리", "조합회로", "combinational logic", "combinational", "combinatorial"),
    ("순차 회로", "순차 논리", "순차회로", "sequential logic", "sequential"),
    ("상태 기계", "유한 상태 기계", "상태 머신", "finite state machine", "state machine", "fsm"),
    ("클럭", "클록", "clock", "clk"),
    ("상승 엣지", "상승 에지", "라이징 엣지", "rising edge", "posedge"),
    ("하강 엣지", "하강 에지", "폴링 엣지", "falling edge", "negedge"),
    ("동기 리셋", "synchronous reset"),
    ("비동기 리셋", "asynchronous reset"),
    ("리셋", "reset"),
    ("준안정", "준안정성", "메타스테빌리티", "metastability", "metastable"),
    ("동기화기", "싱크로나이저", "synchronizer"),
    ("클럭 도메인 교차", "클럭 도메인 크로싱", "clock domain crossing", "cdc"),
    ("셋업 시간", "셋업 타임", "setup time"),
    ("홀드 시간", "홀드 타임", "hold time"),
    ("타이밍 위반", "timing violation"),
    ("테스트벤치", "testbench", "test bench"),
    ("시뮬레이션", "simulation"),
    ("논리 합성", "합성", "synthesis"),
    ("검증", "verification"),
    ("어서션", "assertion"),
    ("커버리지", "coverage"),
    ("린트", "lint"),
    ("파이프라인", "pipeline"),
    ("핸드셰이크", "handshake"),
    ("역압", "backpressure"),
    ("버퍼", "buffer"),
    ("선입선출", "피포", "fifo"),
    ("멀티플렉서", "먹스", "multiplexer", "mux"),
    ("디코더", "decoder"),
    ("인코더", "encoder"),
    ("가산기", "덧셈기", "adder"),
    ("카운터", "계수기", "counter"),
    ("시프트 레지스터", "shift register"),
    ("비트 폭", "비트폭", "bit width"),
    ("부호 있는", "부호있는", "signed"),
    ("부호 없는", "부호없는", "unsigned"),
    ("오버플로", "오버플로우", "overflow"),
    ("자리올림", "캐리", "carry"),
    ("트랜지스터", "transistor"),
    ("모스펫", "mosfet"),
    ("인버터", "반전기", "inverter"),
    ("누설 전류", "leakage current", "leakage"),
    ("동적 전력", "dynamic power"),
    ("클럭 게이팅", "clock gating"),
    ("물리 설계", "physical design"),
    ("배선", "routing"),
    ("메모리", "memory"),
    ("인스턴스", "instance"),
    ("파라미터", "parameter"),
)
