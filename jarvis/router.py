"""Rule-based request router.

Study focus:
- tuple 상수와 반복문
- 정규식 기초
- 규칙의 "순서"가 곧 우선순위라는 점

설계 원칙(ADR-004): LLM Router 를 쓰지 않는다. 왜 그렇게 분류했는지
`RouteDecision.reason` 과 `matched` 로 항상 설명할 수 있어야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import ipaddress
import re
import urllib.parse


class Destination(str, Enum):
    TOOL_SYSTEM_INFO = "tool/system_info"
    TOOL_FILE_SEARCH = "tool/file_search"
    TOOL_WEB_SEARCH = "tool/web_search"
    TOOL_WEB_READ = "tool/web_read"
    TOOL_IMAGE_GEN = "tool/image_gen"
    TOOL_VIDEO_GEN = "tool/video_gen"
    SESSION_RESUME = "session/resume"
    LOCAL_GENERAL = "local_general"
    LOCAL_CODER = "local_coder"
    CLOUD_CODER = "cloud_coder"
    CLOUD_REASONING = "cloud_reasoning"


@dataclass(slots=True)
class RouteDecision:
    destination: Destination
    reason: str
    matched: str = ""


CODE_WORDS = (
    "python", "파이썬", "pandas", "numpy", "sql", "regex", "정규식",
    "코드", "함수", "버그", "에러", "예외", "traceback", "스크립트",
    "클래스", "리스트", "딕셔너리", "컴파일", "디버깅", "테스트 코드",
)
COMPLEX_CODE_WORDS = (
    "전체 구조", "리팩터링", "리팩토링", "대규모", "코드베이스", "3만 줄",
    "30000", "아키텍처", "마이그레이션", "전면 재설계", "모듈 전체", "설계 리뷰",
)
REASONING_WORDS = (
    "복잡하게 추론", "깊게 분석", "심층 분석", "증명", "고난도 추론",
    "수학적으로 증명", "논문 수준", "논리적으로 증명", "정밀하게 분석",
)
SYSTEM_INFO_WORDS = (
    "시스템 ram", "시스템 램", "gpu 알려", "ram과 gpu", "하드웨어 정보",
    "시스템 정보", "내 컴퓨터 사양", "하드웨어 스펙", "vram", "그래픽카드 뭐",
    "프로파일 알려", "지금 프로필",
)
FILE_SEARCH_WORDS = (
    "파일 찾아", "파일 검색", "파일 어디", "파일명 검색", "어느 폴더",
    "파일 목록", "find file",
)
RESUME_WORDS = (
    "지난 세션", "이전 세션", "세션 이어", "이어서 해", "이어서 하자",
    "아까 하던", "어제 하던", "하던 거 계속", "하던거 계속", "resume",
)
# 위 세 지름길(시스템 정보·파일 검색·세션 이어가기)은 모델 없이 답을 대신한다. 짧은 직접 요청일 때만 탄다.
# 2026-09-26 사용자가 실습 노트북 문제를 붙여 넣었는데 코드 속 예시 문자열 '현재 GPU 알려줘' 하나로
# 시스템 정보 표만 나왔다. 긴 글은 모델이 읽는다 — 모델도 환경 문장으로 이 기계의 사양을 안다.
# 골든 코퍼스의 지름길 문장은 19자 이하다.
# 알아서 검색(ADR-053)도 이 길이까지만 한다(ADR-062 결정 2, 2026-09-26): 긴 글에 '최신'·'가격' 이 섞여 있으면 글 전체가
# 검색어가 되어 붙여 넣은 내용이 검색엔진으로 나갔다. 긴 글은 모델이 읽고, 필요하면 모델이 짧은 검색어로 찾는다(ADR-056).
# 부탁 말(검색해줘·찾아봐)과 주소는 길이와 상관없이 그대로다 — 사용자가 밖으로 보내라고 한 것이다.
SHORTCUT_MAX_CHARS = 120
# ── 웹 (ADR-051) ─────────────────────────────────────────────────────────
# 2026-09-25 사용자: "명령어를 입력해야 한다는데.. 그냥 채팅에서 검색해달라고 하면 바로 되도록".
# 요청을 알아보는 방법이 /search 뿐이던 것을 자연어로 넓혔다. 같은 날 데스크톱에서 "알아서 검색" 을
# 더했다(ADR-053, 아래 AUTO_SEARCH_WORDS) — 최신 정보가 있어야 맞는 질문이면 부탁 말이 없어도 나간다.
# 나갈지는 이 규칙이 정한다. 모델이 스스로 나가기로 정하는 경로(도구 호출)는 여전히 없다.
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
# 이것만으로 검색이다. "검색해줄 수 있어?" 는 한국어에서 대개 정중한 부탁이다.
WEB_SEARCH_VERBS = ("검색해", "검색 해", "검색좀", "검색 좀", "검색하고", "검색해서",
                    "구글링", "서치해", "search for", "google it")
WEB_PLACES = ("인터넷에서", "인터넷으로", "웹에서", "온라인에서", "구글에서", "네이버에서")
FRESH_WORDS = ("최신", "최근", "요즘", "시세", "가격", "뉴스", "출시", "릴리스", "릴리즈")
FETCH_VERBS = ("찾아", "알아봐", "알아 봐", "조사해")
# "인터넷에서 찾아줄 수 있어?" 는 능력을 묻는 것이다. 장소/신선도 규칙에만 적용한다.
CAPABILITY_MARKERS = ("수 있어", "수 있나", "수 있니", "수 있을까", "가능해", "가능한가",
                      "가능하니", "되나", "되니", "기능")
# 이 기계 안에서 찾으라는 말이다. "코드에서 버그 찾아줘" 가 웹으로 가면 안 된다.
# "최근 에러 로그 찾아줘" 는 이 기계의 디버깅 이야기다. 라이브러리 이름(pandas 등)은 막지 않는다 —
# "최신 pandas 버전 찾아줘" 는 검색해야 답이 맞는다(처음엔 코드 단어 전체로 막아서 놓쳤다).
DEBUG_WORDS = ("에러", "오류", "예외", "traceback", "버그", "로그", "디버깅", "커밋", "스택")
# 이 기계를 가리키는 말. "RTX 5080 VRAM 용량 좀 찾아봐" 는 웹이고 "내 VRAM 알려줘" 는 이 기계다.
SELF_MACHINE = ("내 ", "이 컴퓨터", "이 pc", "이 기계", "이 노트북", "이 데스크톱", "지금", "현재",
                "시스템", "잡혔", "우리")
LOCAL_CONTEXT = ("프로젝트에서", "프로젝트 안", "코드에서", "파일에서", "폴더에서", "저장소에서",
                 "로컬에서", "내 컴퓨터", "대화에서", "기억에서", "메모에서",
                 # 대화 속 무언가를 가리킨다. 검색어가 아니다.
                 "아까", "방금 말한", "위에서 말한", "앞에서 말한")
# ── 알아서 검색 (ADR-053) ────────────────────────────────────────────────
# 2026-09-25 데스크톱 사용자: "26년 현재기준 rtx5090 가격은?", "상하이 여행 호텔 추천해줘" 에 ALEX 는
# 2024년 지식으로 답하거나 "/search 를 쓰라" 고 했다 — "그 명령어 없어도 너가 알아서 검색할 수 있도록
# 설정해뒀는데?". 부탁 말(검색해줘·찾아봐)이 없어도 **최신·바깥 정보가 있어야 답이 맞는 질문**이면
# 검색한다. 모델이 도구 호출로 정하게 하지 않았다 — 모델마다 다르게 굴고, 이 규칙은 골든 코퍼스로 고정된다.
AUTO_SEARCH_WORDS = (
    "가격", "시세", "환율", "주가", "최저가", "요금", "날씨", "뉴스", "동향", "트렌드",
    "현재기준", "현재 기준", "지금 기준", "오늘 기준",
    "최신", "출시일", "출시됐", "출시했", "출시 예정", "언제 나왔", "언제 나와", "발매", "신제품",
)
# 바깥 세상의 장소·물건 추천. "변수 이름 추천해줘" 는 모델이 답한다.
REAL_WORLD_TOPICS = ("여행", "호텔", "숙소", "맛집", "식당", "카페", "관광", "항공권", "항공편",
                     "비행기표", "쇼핑", "제품", "가성비", "살만한", "노트북", "모니터", "그래픽카드",
                     "키보드", "마우스")
# 모델의 학습 시점(gpt-oss 는 2024-06) 뒤의 해를 못박은 질문. 해만 있고 묻는 말이 없으면("26년 계획
# 세워줘") 검색하지 않는다.
RECENT_YEAR = re.compile(r"(?<!\d)(?:20)?(2[5-9])\s*년")
YEAR_QUESTION_WORDS = ("누가", "누구", "언제", "어디", "몇", "얼마", "결과", "우승", "수상", "뭐야", "뭐였")
# ALEX 자신에 대한 질문이다. "너의 최신 학습은 언제야?" 가 검색되면 안 된다(사용자가 실제로 물었다).
ABOUT_ALEX = ("너의", "너는", "너가", "네가", "니가", "넌 ", "너한테", "alex", "학습")
# 코드를 쓰라는 말. 라이브러리 이름만으로는 막지 않는다 — "최신 pandas 버전은?" 은 검색해야 맞는다.
CODE_TASK = ("코드", "함수", "스크립트", "클래스", "짜줘", "작성해", "구현", "고쳐", "리팩터", "리팩토링")
# 앞 대화를 가리킨다. "이 가격이면 괜찮아?" 로 검색하면 검색어가 뜻이 없다.
CONTEXT_REFERENCES = ("이 가격", "그 가격", "이거", "그거")
_WEB_REQUEST_NOISE = re.compile(
    r"(인터넷|웹|온라인|구글|네이버)\s*(에서|으로|로)\s*"
    r"|(좀\s*)?(검색|서치|구글링|조사)\s*(좀\s*)?(해\s*)?"
    r"(줘|주세요|줄래|줄\s*수\s*있어|줄\s*수\s*있나|봐|볼래|보자|서|주라|하고)?"
    r"|(좀\s*)?(찾아|알아)\s*(봐|줘|주세요|볼래|줄래|보자|주라)?\s*(줘|주세요|줄래)?"
    r"|\s(좀|부탁해|부탁)(?=\s|$)"
)


def extract_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    return match.group(0).rstrip(".,;:!?") if match else None


def web_search_query(text: str) -> str:
    """검색엔진에 넘길 말. 부탁하는 말투만 걷어낸다 — 조사는 검색엔진이 알아서 다룬다."""
    query = _WEB_REQUEST_NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", query).strip(" ,.?!？~")


_SEARCH_REQUEST_END = re.compile(
    r"검색\s*(?:해\s*)?(?:줘|주세요|줄래|봐|해)|찾아\s*(?:봐|줘|주세요|줄래)",
    re.IGNORECASE,
)


def explicit_web_queries(text: str) -> list[str]:
    """Split separate search requests before an optional URL into distinct queries.

    A URL followed by '여기서 ...' asks about that page. Treating the whole message as
    one query makes the search engine look up the user's instructions instead.
    """
    prefix = URL_PATTERN.split(text, maxsplit=1)[0]
    queries: list[str] = []
    start = 0
    for match in _SEARCH_REQUEST_END.finditer(prefix):
        request = prefix[start:match.end()]
        start = match.end()
        # A message can also contain a local-file request and a URL. Reading the
        # URL must not send the local filename or code question to a search engine.
        if _contains_any(request.lower(), LOCAL_CONTEXT + FILE_SEARCH_WORDS):
            continue
        if _contains_any(request.lower(), DEBUG_WORDS) and not _contains_any(request.lower(), WEB_PLACES):
            continue
        query = web_search_query(request)
        if query:
            queries.append(query)
    return queries


def _is_private_url(url: str) -> bool:
    """localhost·사설 IP 를 가리키는 주소는 웹으로 보내지 않는다. 막힐 게 뻔하고,
    "이 주소 왜 안 열려?" 같은 질문에는 차단 문구보다 모델의 답이 필요하다."""
    from .webread import is_public_address  # 순환 없음: webread 는 jarvis 를 import 하지 않는다

    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False  # 이름은 여기서 풀지 않는다. 실제 차단은 webread 가 연결 단계에서 한다.
    return not is_public_address(host)


def _web_route(normalized: str, auto: bool = True) -> RouteDecision | None:
    url = extract_url(normalized)
    if url and not _is_private_url(url):
        return RouteDecision(Destination.TOOL_WEB_READ, "url in message", url)
    if _contains_any(normalized, LOCAL_CONTEXT):
        return None
    hit = _find_any(normalized, WEB_SEARCH_VERBS)
    if hit:
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "explicit web search request", hit)
    if _contains_any(normalized, CAPABILITY_MARKERS):
        return None
    fetch = _find_any(normalized, FETCH_VERBS)
    if not fetch:
        return _auto_search(normalized) if auto else None
    place = _find_any(normalized, WEB_PLACES)
    if place:
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "web place + fetch request", place)
    fresh = _find_any(normalized, FRESH_WORDS)
    if fresh and not _contains_any(normalized, DEBUG_WORDS):
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "fresh-info + fetch request", fresh)
    # "○○ 찾아봐/찾아줘" 는 대개 찾아보라는 뜻이다. 코드·디버깅 일은 이 기계 안의 일이라 뺀다.
    # "알아봐" 는 "왜 안 되는지 알아봐줘" 처럼 디버깅에도 써서 장소·신선도 단어가 있을 때만 받는다.
    if "찾아" in normalized and not _contains_any(normalized, CODE_WORDS + DEBUG_WORDS):
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "find request", "찾아")
    return _auto_search(normalized) if auto else None


def _auto_search(normalized: str) -> RouteDecision | None:
    """부탁 말이 없어도 최신·바깥 정보가 있어야 맞는 질문이면 검색한다(ADR-053).

    능력 질문·이 기계 안의 일·주소는 앞 단계에서 이미 걸렀다. 여기서는 ALEX 자신, 코드 작성, 디버깅,
    앞 대화 가리키기, 세션 이어가기를 뺀다 — 그런 말에 검색 결과를 붙이면 답이 틀어진다.
    """
    if _contains_any(normalized, ABOUT_ALEX + CODE_TASK + DEBUG_WORDS + CONTEXT_REFERENCES + RESUME_WORDS):
        return None
    hit = _find_any(normalized, AUTO_SEARCH_WORDS)
    if hit:
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "needs current information (auto)", hit)
    if "추천" in normalized:
        topic = _find_any(normalized, REAL_WORLD_TOPICS)
        if topic:
            return RouteDecision(Destination.TOOL_WEB_SEARCH, "real-world recommendation (auto)", topic)
    year = RECENT_YEAR.search(normalized)
    if year and _contains_any(normalized, YEAR_QUESTION_WORDS):
        return RouteDecision(Destination.TOOL_WEB_SEARCH, "asks about a year after training (auto)",
                             year.group(0))
    return None


def _web_intent(normalized: str) -> bool:
    return _contains_any(normalized, WEB_SEARCH_VERBS + ("찾아",))


# ── 그림 그리기 (ADR-073) ───────────────────────────────────────────────
# 2026-10-03 사용자: "자연어로 말하면 그걸 통해서 이미지 생성할 수 있도록 해줘". 웹 검색과 같은 방식이다 — 모델의
# 도구 호출이 아니라 골든 코퍼스로 고정되는 규칙이 정한다. 부탁 말이 문장 끝에 있어야 한다("…그려줘", "…이미지
# 만들어줘", "draw a cat"). "그림 그릴 수 있어?" 처럼 능력을 묻는 말과 그래프·도표·코드는 모델이 답한다.
IMAGE_NOUNS = ("그림", "이미지", "사진", "일러스트", "삽화", "포스터", "로고", "아이콘", "배경화면", "바탕화면",
               "썸네일", "초상화", "풍경화", "캐리커처", "image", "picture", "illustration", "photo", "wallpaper",
               "logo", "icon", "drawing")
NOT_PICTURES = ("그래프", "차트", "도표", "다이어그램", "흐름도", "순서도", "구조도", "플로우차트", "마인드맵",
                "회로도", "설계도", "도식", "산점도", "히스토그램", "ascii", "mermaid", "plot", "graph", "chart",
                "diagram", "flowchart", "how to", "how do")
IMAGE_CODE_WORDS = ("코드", "함수", "스크립트", "프로그램", "파이썬", "python", "matplotlib", "seaborn", "plotly",
                    "pillow", "opencv", "html", "css", "svg", "캔버스", "canvas")
IMAGE_MAX_CHARS = 500
_IMAGE_ENDINGS = r"(?:줘|주세요|줄래|줄\s*수|봐|볼래|보자|주라|줘요|주실|주시겠|달라|다오)"
_DRAW_REQUEST = re.compile(r"그려\s*" + _IMAGE_ENDINGS)
_MAKE_REQUEST = re.compile(r"(?:만들어|생성\s*해|뽑아|제작해)\s*" + _IMAGE_ENDINGS)
_EN_IMAGE_REQUEST = re.compile(
    r"\b(?:draw|paint|sketch)\s+(?:me\s+)?\w"
    r"|\b(?:generate|create|make|render)\s+(?:me\s+)?(?:an?\s+|some\s+)?(?:image|picture|illustration|photo|drawing|"
    r"wallpaper|logo|icon)s?\b")
# 부탁 말 뒤에 이 정도만 남아야 한다("그려줘 귀엽게", "그려 주세요!"). 더 길면 다른 일이 이어지는 글이다.
IMAGE_TAIL_MAX_CHARS = 15


# ── 영상 만들기 (ADR-075, 2026-10-03 사용자 "영상 못 만든다고 그러는데?") ─────────────────
VIDEO_WORDS = ("영상", "동영상", "비디오", "애니메이션", "움짤", "클립", "video", "animation", "clip")
VIDEO_NOT = ("편집", "자막", "요약", "다운로드", "유튜브", "youtube", "찾아", "검색", "녹화", "재생", "변환", "압축")
_VIDEO_MAKE = re.compile(r"(?:만들어|생성\s*해|뽑아|제작해)\s*" + _IMAGE_ENDINGS)
_ANIMATE = re.compile(r"움직이게\s*(?:해|만들어)\s*" + _IMAGE_ENDINGS)
_EN_VIDEO = re.compile(r"\b(?:make|generate|create|render)\s+(?:me\s+)?(?:an?\s+)?(?:short\s+)?(?:video|animation|clip)\b")


def video_request(normalized: str) -> str | None:
    """영상을 만들어 달라는 부탁이면 일치한 말. "○○ 영상 만들어줘", "방금 그림 움직이게 해줘", "make a video of …"."""
    if len(normalized) > IMAGE_MAX_CHARS or "```" in normalized:
        return None
    if _contains_any(normalized, IMAGE_CODE_WORDS + VIDEO_NOT):
        return None
    match = _ANIMATE.search(normalized)
    if match:
        return match.group(0)
    matches = list(_VIDEO_MAKE.finditer(normalized))
    if matches:
        match = matches[-1]
        if (_contains_any(normalized[:match.start()], VIDEO_WORDS)
                and len(normalized[match.end():].strip()) <= IMAGE_TAIL_MAX_CHARS):
            return match.group(0)
    match = _EN_VIDEO.search(normalized)
    return match.group(0) if match and match.start() <= 40 else None


def image_request(normalized: str) -> str | None:
    """그림을 그려 달라는 부탁이면 일치한 말을 돌려준다. normalized 는 소문자·공백 정리된 글."""
    if len(normalized) > IMAGE_MAX_CHARS or "```" in normalized:
        return None
    if _contains_any(normalized, NOT_PICTURES + IMAGE_CODE_WORDS):
        return None
    for pattern, needs_noun in ((_DRAW_REQUEST, False), (_MAKE_REQUEST, True)):
        matches = list(pattern.finditer(normalized))
        if not matches:
            continue
        match = matches[-1]
        if needs_noun and not _contains_any(normalized[:match.start()], IMAGE_NOUNS):
            continue
        if len(normalized[match.end():].strip()) <= IMAGE_TAIL_MAX_CHARS:
            return match.group(0)
    match = _EN_IMAGE_REQUEST.search(normalized)
    if match and match.start() <= 40:
        return match.group(0)
    return None


# "계속해서 설명해줘" 같은 문장은 세션 재개가 아니다.
RESUME_EXCLUSIONS = ("계속해서", "계속 해서", "이어서 설명", "이어서 써", "이어서 작성")


def _find_any(text: str, words: tuple[str, ...]) -> str | None:
    """일치한 첫 키워드를 돌려준다. 없으면 None."""
    for word in words:
        if word in text:
            return word
    return None


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return _find_any(text, words) is not None


def route_request(text: str) -> RouteDecision:
    """투명한 규칙으로 요청을 분류한다.

    규칙 순서가 중요하다. 구체적인 의도를 넓은 코드 키워드보다 먼저 확인한다.
    """
    normalized = re.sub(r"\s+", " ", text.strip().lower())

    if not normalized:
        return RouteDecision(Destination.LOCAL_GENERAL, "empty input fell back to default")
    shortcut = len(normalized) <= SHORTCUT_MAX_CHARS

    hit = _find_any(normalized, SYSTEM_INFO_WORDS) if shortcut else None
    # 2026-09-25 실측: "RTX 5080 VRAM 용량 좀 찾아봐" 가 'vram' 한 단어로 여기 걸려 이 노트북의
    # RTX 4070 8GB 를 보여줬다. 찾아보라는 말이 있고 이 기계를 가리키지 않으면 웹이다.
    if hit and (_contains_any(normalized, SELF_MACHINE) or not _web_intent(normalized)):
        return RouteDecision(Destination.TOOL_SYSTEM_INFO, "system hardware keywords detected", hit)

    hit = _find_any(normalized, FILE_SEARCH_WORDS) if shortcut else None
    if hit:
        return RouteDecision(Destination.TOOL_FILE_SEARCH, "file name search keywords detected", hit)

    hit = video_request(normalized)
    if hit:
        return RouteDecision(Destination.TOOL_VIDEO_GEN, "video generation request", hit)

    hit = image_request(normalized)
    if hit:
        return RouteDecision(Destination.TOOL_IMAGE_GEN, "image generation request", hit)

    web = _web_route(normalized, auto=shortcut)
    if web is not None:
        return web

    hit = _find_any(normalized, RESUME_WORDS) if shortcut else None
    if hit and not _contains_any(normalized, RESUME_EXCLUSIONS):
        return RouteDecision(Destination.SESSION_RESUME, "session resume keywords detected", hit)

    hit = _find_any(normalized, COMPLEX_CODE_WORDS)
    if hit and _contains_any(normalized, CODE_WORDS + ("프로젝트", "저장소", "repo")):
        return RouteDecision(Destination.CLOUD_CODER, "large/complex code task detected", hit)

    hit = _find_any(normalized, REASONING_WORDS)
    if hit:
        return RouteDecision(Destination.CLOUD_REASONING, "explicit hard reasoning request detected", hit)

    hit = _find_any(normalized, CODE_WORDS)
    if hit:
        return RouteDecision(Destination.LOCAL_CODER, "simple coding keywords detected", hit)

    return RouteDecision(Destination.LOCAL_GENERAL, "default conversational route")


def explain(decision: RouteDecision) -> str:
    """로그와 CLI 에서 같은 문장을 쓰기 위한 헬퍼."""
    if decision.matched:
        return f"{decision.destination.value} ({decision.reason}; matched={decision.matched!r})"
    return f"{decision.destination.value} ({decision.reason})"
