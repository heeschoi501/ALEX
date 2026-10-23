"""ALEX 가 자기 기능을 스스로 안다(2026-10-23).

사용자가 "파일 읽기 쓰기 같은 것도 가능하지?" 라고 물었을 때 ALEX 가 '웹 검색뿐' 에 가깝게 답했다 — 대화 모델은 지시문과 받은 도구만
알고, 명령(/read·/edit-file …)과 화면(코딩 세션 …)은 몰랐다. 손으로 쓴 안내문 대신 **실제 명령 목록(/help)과 화면(index.html 의
버튼·메뉴 설명)에서 기능 줄을 뽑아 두고**, 기능을 묻는 질문일 때만 맞는 줄을 골라 지시문에 붙인다. 새 기능이 생기면 저절로 들어간다.
줄 고르기: 질문 낱말의 어간(끝말을 뗀 것 — '읽기'·'읽고' 가 만나게)이 줄에 들어 있으면 맞은 것으로 보고, 여러 줄에 흔한 낱말은
가볍게(idf) 센다. '쓰기' 는 저장·수정·고치기, '읽기' 는 열기·보기도 같이 본다.
"""
from __future__ import annotations

import html
from pathlib import Path
import math
import re

# 기능을 묻는 말(할 수 있어?·가능해?·어떻게 해?·기능·명령 …)
ASKING = re.compile(r"수\s*있|할수있|있어\?|있나\?|가능|권한|되나|돼\?|되니|되요|돼요|되죠|지원|기능|어떻게\s*(해|하|써|쓰|켜|열)|방법|명령|할\s*줄|쓸\s*수|없어\?|못\s*해")
# 무엇을 할 수 있는지 통째로 묻는 말
OVERVIEW = re.compile(r"(뭘|무엇을|뭐|무슨|어떤)\s*(것|거|일|기능)?\s*(을|를)?\s*(할\s*수|가능|해\s*줄|기능)|기능\s*(목록|소개|알려)")
ENDINGS = sorted(("하기", "해줘", "하지", "하는", "해요", "할", "한", "기", "고", "어", "아", "는", "을", "를", "도", "은",
                  "이", "가", "요", "지", "나", "니", "에", "서", "로", "으로", "까지", "에서", "같은"), key=len, reverse=True)
STOP = {"현재", "지금", "혹시", "같은", "같은것", "같은거", "가능", "가능하", "할수", "있어", "있나", "있니", "있", "되나", "되", "너는",
        "네가", "니가", "알렉스", "기능", "방법", "어떻게", "이런", "저런", "그런", "정도", "그리고", "것", "거", "이것", "그것", "뭐",
        "무엇", "할", "해", "수", "좀", "다", "또", "그럼", "근데", "그래서", "이", "그", "저", "는", "을", "를", "에", "도",
        "의", "와", "과", "잘", "더", "안", "못", "왜", "뭘", "내", "제", "나", "너", "네", "거기", "여기"}
SYNONYMS = {"파일": ("파일", "file", "경로"), "쓰": ("쓰", "저장", "수정", "고치", "고쳐", "만들"), "읽": ("읽", "열", "보기"), "고치": ("고치", "고쳐", "수정"),
            "만들": ("만들", "생성"), "찾": ("찾", "검색"), "지우": ("지우", "삭제"), "그리": ("그리", "그림"), "번역": ("번역", "통역")}


def _stems(question: str) -> list[str]:
    stems = []
    for word in re.findall(r"[가-힣]+|[0-9a-z_/-]{2,}", question.lower()):  # 한 글자 낱말(웹·앱·폰)도
        stem = word
        for ending in ENDINGS:
            if stem.endswith(ending) and len(stem) > len(ending):
                stem = stem[: -len(ending)]
                break
        if stem not in STOP and word not in STOP and stem not in stems:
            stems.append(stem)
    return stems


HEADER = ("ALEX 의 실제 기능 — 이 PC 의 명령 목록(/help)과 화면에서 자동으로 뽑은 것이다. 여기 있는 것은 할 수 있다. "
          "대화 중 모델이 스스로 부르는 도구(웹 검색 등)와, 사용자가 쓰는 명령·화면은 다르다 — 둘을 구분해서 어떻게 하면 되는지 알려라. "
          "여기 없다고 '못 한다' 고 단정하지 말고 /help 를 권하라.")


def help_lines(help_text: str) -> list[str]:
    """'  /명령 인자   설명' 줄들 → '명령 /명령 인자 — 설명'. 더 들여 쓴 다음 줄은 앞 줄에 붙인다."""
    out: list[str] = []
    for raw in help_text.splitlines():
        line = raw.rstrip()
        if re.match(r"^\s{2}/\S", line):
            parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            out.append(f"명령 {parts[0]}" + (f" — {parts[1]}" if len(parts) > 1 else ""))
        elif out and re.match(r"^\s{4,}\S", line):
            out[-1] += " " + line.strip()
    return out


def ui_lines(page: str) -> list[str]:
    """화면의 버튼·메뉴·고르기 — 보이는 이름(또는 aria-label)과 설명(title)."""
    out, seen = [], set()   # 같은 이름(입력창·메뉴 둘 다 있는 '코딩 세션' 등)은 한 번만
    for match in re.finditer(r"<(button|label|a)\b([^>]*)>(.*?)</\1>", page, re.S):
        attrs, inner = match.group(2), match.group(3)
        title = re.search(r'\btitle="([^"]+)"', attrs)
        label = re.search(r'\baria-label="([^"]+)"', attrs)
        text = re.sub(r"<[^>]+>", " ", inner)
        name = " ".join(html.unescape(text).split()) or (html.unescape(label.group(1)) if label else "")
        if not title or not name or len(name) > 40:
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(f"화면 '{name[:30]}' — {html.unescape(title.group(1))}")
    return out


class SelfKnowledge:
    def __init__(self, help_text: str, page: Path | str | None = None, extra: tuple[str, ...] = ()) -> None:
        text = Path(page).read_text(encoding="utf-8") if isinstance(page, Path) and page.is_file() else (page or "")
        self.lines = help_lines(help_text) + ui_lines(str(text)) + list(extra)
        self._lower = [line.lower() for line in self.lines]

    def match(self, question: str, limit: int = 10) -> list[str]:
        total = len(self.lines) or 1
        weights = {}
        for stem in _stems(question):
            forms = SYNONYMS.get(stem, (stem,))
            hits = [i for i, line in enumerate(self._lower) if any(form in line for form in forms)]
            if not hits or (total >= 20 and len(hits) > total * 0.5):   # 목록이 길 때 절반 넘게 나오는 낱말은 고르는 데 도움이 안 된다
                continue
            weights[stem] = (math.log((total + 1) / len(hits)), set(hits))
        scores: dict[int, float] = {}
        for weight, hits in weights.values():
            for i in hits:
                scores[i] = scores.get(i, 0.0) + weight
        best = max(scores.values(), default=0.0)
        ranked = sorted((i for i, score in scores.items() if score >= best * 0.5), key=lambda i: (-scores[i], i))
        return [self.lines[i] for i in ranked[:limit]]

    def block(self, question: str, limit: int = 10, overview_chars: int = 2400) -> str:
        """기능을 묻는 질문이면 지시문에 붙일 글(아니면 ''). 통째로 물으면 명령·화면 이름 목록."""
        question = str(question or "")
        if OVERVIEW.search(question):
            names = [line.split(" — ", 1)[0] for line in self.lines]
            return f"{HEADER}\n" + ", ".join(names)[:overview_chars]
        if not ASKING.search(question):
            return ""
        lines = self.match(question, limit)
        return f"{HEADER}\n" + "\n".join(f"- {line}" for line in lines) if lines else ""
