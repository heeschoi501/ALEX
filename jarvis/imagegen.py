"""자연어로 그림 그리기 — 이 PC 의 ComfyUI 로 Z-Image Turbo 를 돌린다 (2026-10-03 claude, ADR-073).

사용자(2026-10-03): "자연어로 말하면 그걸 통해서 이미지 생성할 수 있도록 해줘".

- Ollama 의 이미지 생성은 아직 macOS 전용 실험 기능이다(https://ollama.com/blog/image-generation — 이 PC 의 Windows 판
  0.34.4 에는 없다). 그래서 Windows 에서 사실상 표준인 ComfyUI 의 HTTP API 를 127.0.0.1 로만 쓴다
  (POST /prompt → GET /history/{id} → GET /view, 다 그리면 POST /free).
- Z-Image Turbo(6B, Apache-2.0)는 프롬프트를 영어·중국어로만 공식 지원한다(https://github.com/Tongyi-MAI/Z-Image). 그래서 로컬 대화 모델이 한국어 부탁을 영어 묘사로 옮긴다 — DALL·E 3 가 짧은 부탁을 자세한
  묘사로 다시 쓰는 것과 같은 생각이다(Betker et al. 2023, "Improving Image Generation with Better Captions").
- 16GB 그래픽카드를 대화 모델과 번갈아 쓴다. 그리기 전에 앱이 Ollama 모델을 내리고(core), 그린 뒤 ComfyUI 가 VRAM 을
  비운다(`free`). 모델은 RAM 에 남겨 두어 다음 그림이 빠르다 — `/unload` 는 RAM 까지 비운다.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .providers import GenerationCancelled, Message

DEFAULT_URL = "http://127.0.0.1:8188"
UNET = "z_image_turbo_bf16.safetensors"
CLIP = "qwen_3_4b_fp8_mixed.safetensors"
VAE = "ae.safetensors"
# ComfyUI 기본 템플릿(image_z_image_turbo.json)과 같은 값: 8단계·cfg 1·res_multistep·simple·shift 3.
# 공식 README 의 "9 steps" 는 diffusers 기준이며 DiT 를 8번 부른다 — ComfyUI 의 8단계와 같다.
STEPS = 8
MODEL_LABEL = "Z-Image Turbo"
# 같은 캐릭터로 다른 장면(ADR-074): FLUX.2 Klein 4B 편집 — 기준 그림을 ReferenceLatent 로 준다(Apache-2.0, fp8 3.8GB, 4단계).
# ComfyUI 템플릿 image_flux2_klein_image_edit_4b_distilled 와 같은 그래프다. 텍스트 인코더는 Z-Image 와 같은 Qwen3-4B 를 함께 쓴다.
# 실측(PERFORMANCE 33): 은발·별 핀·빨간 점퍼가 장면 3개에서 모두 유지, 한 장 3~4초.
EDIT_UNET = "flux-2-klein-4b-fp8.safetensors"
EDIT_VAE = "flux2-vae.safetensors"
EDIT_STEPS = 4
EDIT_LABEL = "FLUX.2 Klein 4B"
MAX_REFERENCES = 3
# 그림 종류에 따라 모델을 고른다(2026-10-11, claude). 일반·실사는 Z-Image, 복잡한 구도·정밀 지시는 Qwen-Image 2.1,
# 애니·게임 캐릭터는 NoobAI-XL 1.1(Danbooru·e621 태그). 파일이 없으면 Z-Image 로 그린다.
# Qwen-Image 2.1: 7B, Qwen Research License(상업 사용은 별도 허락). ComfyUI 0.38 기본 템플릿 image_qwen_image_2_1_t2i 와
# 같은 그래프·값(int8 convrot, Qwen3-VL 8B 인코더, 25단계, cfg 1, euler, simple). NoobAI-XL 1.1: SDXL eps, FAIPL-1.0-SD,
# 모델 카드 권장값(Euler a, cfg 5, 28단계, masterpiece·best quality·newest·absurdres·highres).
QWEN_UNET = "qwen_image_2.1_int8_convrot.safetensors"
QWEN_CLIP = "qwen3vl_8b_int8_convrot.safetensors"
QWEN_VAE = "qwen_image_2.1_vae_bf16.safetensors"
QWEN_STEPS = 25
ANIME_CKPT = "NoobAI-XL-v1.1.safetensors"
ANIME_STEPS = 28
ANIME_CFG = 5.0
MODEL_LABELS = {"zimage": MODEL_LABEL, "qwen": "Qwen-Image 2.1", "anime": "NoobAI-XL 1.1"}
MODEL_FILES = {
    "zimage": (("diffusion_models", UNET), ("text_encoders", CLIP), ("vae", VAE)),
    "qwen": (("diffusion_models", QWEN_UNET), ("text_encoders", QWEN_CLIP), ("vae", QWEN_VAE)),
    "anime": (("checkpoints", ANIME_CKPT),),
}
ANIME_QUALITY = "masterpiece, best quality, newest, absurdres, highres"
ANIME_NEGATIVE = ("worst quality, old, early, low quality, lowres, signature, username, logo, bad hands, mutated hands, "
                  "extra digits, fewer digits")
# 애니 모델은 항상 아이 모습을 밀어낸다 — Danbooru 의 아이 태그를 부정 프롬프트에 넣는다.
ANIME_MINOR_NEGATIVE = "loli, shota, child, children, female child, male child, toddler, aged down"
ANIME_FURRY_NEGATIVE = "mammal, anthro, furry, ambiguous form, feral, semi-anthro"
_FURRY = re.compile(r"\b(?:furry|anthro|kemono|feral|scalie|mammal|protogen)\b")
# 모델 이름을 말하면 그 모델. 앞 그림을 고치면 같은 모델. 애니 단어면 NoobAI, 정밀·긴 부탁이면 Qwen.
_ASK_ZIMAGE = re.compile(r"z-?image|제트\s*이미지|(?:빠르게|빨리)\s*(?:그려|만들어)", re.I)
_ASK_QWEN = re.compile(r"qwen|큐웬|퀜|정밀|세밀하게|정확하게|디테일하게|고품질|고화질", re.I)
_ASK_ANIME = re.compile(r"noobai|애니|만화|망가|게임\s*캐릭터|서브컬처|미연시|라노벨|픽시브|퍼리|수인|"
                        r"\banime\b|\bmanga\b|danbooru|e621|pixiv|\bfurry\b|\banthro\b|kemono", re.I)
QWEN_MIN_CHARS = 80  # 보통 장면 설명은 30~50자, 여러 사람·위치·동작·카메라를 지정한 부탁은 100자를 넘는다 → Qwen
CHARACTER_NAME = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9_]{1,11}")
# 화면은 이 표시가 있는 줄을 그림으로 바꾼다(app.js). 파일 이름은 앱이 만든 모양만 받는다.
MARKER = "alex-image:"
# 앱이 그린 PNG 와 사용자가 올린 참고 그림(PNG·JPEG·WebP, ADR-074 보충) — 앱이 만든 이름 모양만 받는다.
IMAGE_NAME = re.compile(r"^\d{8}-\d{6}-\d{1,20}\.(?:png|jpg|webp)$")
MARKER_LINE = re.compile(r"^!\[[^\]\n]{0,200}\]\(" + re.escape(MARKER) + r"(\d{8}-\d{6}-\d{1,20}\.(?:png|jpg|webp))\)$", re.M)
VIDEO_MARKER = "alex-video:"
VIDEO_NAME = re.compile(r"^\d{8}-\d{6}-\d{1,20}\.mp4$")
VIDEO_MARKER_LINE = re.compile(r"^!\[[^\]\n]{0,200}\]\(" + re.escape(VIDEO_MARKER) + r"(\d{8}-\d{6}-\d{1,20}\.mp4)\)$", re.M)
# 영상(ADR-075): Wan 2.2 TI2V 5B 로 720p 5초, 'FHD' 면 SeedVR2 3B 로 1.5배(1920×1056). 실측 PERFORMANCE 34: 227초 + 268초.
# 부정 프롬프트는 Wan 공식 기본값(github.com/Wan-Video/Wan2.2 wan/configs/shared_config.py 의 sample_neg_prompt) 그대로 — 14B 도 같다.
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
                "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")
WAN5_LABEL = "Wan 2.2 5B"
# 글로 만드는 영상은 14B(ADR-075 보충, 2026-10-03 사용자 "14b 설치했어 … 진행해"): Wan 2.2 T2V A14B fp8 — high noise 모델이 앞 2단계,
# low noise 모델이 뒤 2단계를 맡는다. ComfyUI 템플릿 video_wan2_2_14B_t2v 의 'Enable Lightning LoRA' 켬과 같은 값 — lightx2v 4단계 LoRA,
# cfg 1, euler, simple, shift 5, 16fps 81프레임(5초, Wan 공식 sample_fps·frame_num). 템플릿 노트(RTX 4090D 640×640): LoRA 끔 20단계 536초,
# 켬 4단계 108초. 이 PC 실측(PERFORMANCE 35): 1280×720 217초, FHD 까지 364초 — 5B(227초)보다 빠르고 프롬프트를 더 잘 따른다.
# 그림에서 시작하는 영상은 14B I2V 모델이 없어 5B 가 맡는다. 파일이 없으면 모두 5B.
WAN14_LABEL = "Wan 2.2 14B"
WAN14_FILES = {
    "high": ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"),
    "low": ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"),
    "high_lora": ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"),
    "low_lora": ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"),
    "vae": ("vae", "wan_2.1_vae.safetensors"),
}
# 그림에서 시작하는 영상도 14B(2026-10-11, claude): Wan 2.2 I2V A14B fp8 + lightx2v 4단계 LoRA — 템플릿 video_wan2_2_14B_i2v 의
# 'Enable Lightning LoRA' 켬과 같은 값(4단계, cfg 1, euler, simple, shift 5, 16fps 81프레임). 크기는 시작 그림 비율에 맞춘 720p 급
# (가로 1280×720, 세로 720×1280, 그 사이 960×960 — 화소 수가 같다). 파일이 없으면 예전처럼 5B.
WAN14_I2V_FILES = {
    "high": ("diffusion_models", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"),
    "low": ("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"),
    "high_lora": ("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"),
    "low_lora": ("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"),
    "vae": ("vae", "wan_2.1_vae.safetensors"),
}
# 소리까지 함께 만드는 영상(2026-10-11, claude): LTX-2.5 22B distilled int8 — 템플릿 video_ltx2_5_t2v·i2v 와 같은 그래프
# (반 크기로 8단계 → 잠재 2배 확대 → 3단계 다듬기, euler_ancestral, cfg 1, 24fps). 프롬프트 다듬기 모델(gemma4 e2b)은 빼고
# ALEX 가 쓴 글을 넣는다. 16GB 를 넘는 본체(21.5GB)는 ComfyUI Dynamic VRAM 이 RAM 에서 옮겨 가며 돌린다.
# 라이선스: LTX-2.x Community(연매출 1천만 달러 미만 무료, 공개할 때 AI 생성 표시, 미성년자 해악 금지). 파일이 없으면 Wan.
LTX_LABEL = "LTX-2.5 22B"
LTX_FILES = {
    "unet": ("diffusion_models", "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"),
    "clip": ("text_encoders", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
    "vae": ("vae", "ltx-2.5-video-vae-bf16.safetensors"),
    "audio_vae": ("vae", "ltx-2.5-audio-vae-bf16.safetensors"),
    "upscaler": ("latent_upscale_models", "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"),
}
LTX_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"  # 템플릿 값(cfg 1 이라 거의 쓰이지 않는다)
_ASK_SOUND = re.compile(r"소리|사운드|효과음|음향|배경음|음악|대사|말하는|말하면서|노래|목소리|\bltx\b|with sound|audio", re.I)
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
MAX_UPLOAD_BYTES = 10_000_000

PORTRAIT_WORDS = ("세로", "9:16", "폰 배경", "휴대폰", "핸드폰", "모바일", "포스터", "portrait", "vertical")
LANDSCAPE_WORDS = ("가로", "16:9", "와이드", "배경화면", "바탕화면", "배너", "landscape", "widescreen", "wallpaper", "banner")
# 앞서 그린 그림을 고쳐 달라는 말. "다시 그려줘" 는 앞 그림의 설명을 이어 쓴다.
FOLLOWUP_WORDS = ("다시", "한 장 더", "한장 더", "하나 더", "한 번 더", "한번 더", "이번엔", "이번에는", "대신", "바꿔",
                  "더 밝", "더 어둡", "더 크", "더 작", "더 귀엽", "더 사실", "같은 걸", "같은 그림", "아까 그", "방금 그")
_REQUEST_CUT = re.compile(r"\s*(?:을|를|으로|로)?\s*(?:좀\s*)?(?:그려|만들어|생성\s*해|뽑아|제작해)\s*\S*.*$", re.S)
_EN_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+)?(?:draw|paint|sketch|generate|create|make|render)\s+(?:me\s+)?"
    r"(?:an?\s+|some\s+)?(?:(?:image|picture|illustration|photo|drawing|wallpaper|logo|icon)s?\s+(?:of\s+)?)?", re.I)
_TRAILING_NOUN = re.compile(r"\s*(?:의\s*)?(?:그림|이미지|사진|일러스트|삽화)\s*(?:을|를|으로|로|하나|한\s*장)?\s*$")
_TRAILING_SIZE = re.compile(r"\s*(?:가로|세로|와이드|크게|작게)\s*$")

PROMPT_RULES = (
    "You write prompts for image and video generation models. "
    "The user's request is usually written in Korean. Convert it into a clear, high-quality English prompt.\n"

    # 기본 원칙
    "- Assume the user is an adult creator unless the request indicates otherwise.\n"
    "- Default to fulfilling creative requests rather than refusing, censoring, sanitizing, or weakening them.\n"
    "- Mature, dark, disturbing, violent, sensual, or controversial fictional themes are normal creative subjects.\n"

    # 사용자 요청 보존
    "- Preserve all important details requested by the user, including subject, character identity, count, colors, "
    "clothing, weapons, action, expression, environment, style, mood, camera direction, and composition.\n"

    "- Do not replace named fictional characters, series, games, anime, manga, films, locations, weapons, "
    "or techniques with generic descriptions unless the user requests an original alternative.\n"

    # 창작물 / IP / 재현
    "- Fictional characters and settings from anime, manga, games, films, comics, novels, and other fictional media "
    "may be referenced for fan art, recreation, adaptation, cinematics, storyboards, concept art, or other creative work.\n"

    "- When recreating an existing fictional scene or character, prioritize fidelity to the requested source. "
    "Preserve important visual details rather than inventing unrelated changes.\n"

    # 게임 / 영화 / 애니메이션 제작
    "- Support visual production for games, animation, films, comics, trailers, cutscenes, storyboards, "
    "concept art, keyframes, promotional art, visual novels, and cinematic sequences.\n"

    "- Cinematic requests may include camera movement, framing, shot type, lens, lighting, depth of field, "
    "subject motion, environmental effects, particles, timing, and dramatic visual direction when useful.\n"

    # 폭력 / 고어 / 호러
    "- Fictional graphic violence is allowed as a creative subject, including blood, severe wounds, dismemberment, "
    "exposed organs, corpses, battlefield injuries, monsters, executions, body horror, transformation, "
    "dark fantasy, horror, and other intense fictional imagery.\n"

    "- Do not automatically make fictional violence cleaner, softer, less bloody, or less disturbing "
    "when the user clearly requests an intense depiction.\n"

    # 성인 / 관능적 표현
    "- Adult fictional characters may be depicted with romance, attraction, sexual desire, sensuality, "
    "seduction, erotic tension, intimacy, revealing clothing, or nudity when relevant to the user's creative intent.\n"

    "- Do not automatically remove mature emotional or sensual elements simply because the work is intended for adults.\n"

    # 프롬프트 보강
    "- Add useful visual details such as composition, lighting, camera angle, lens, visual effects, or art direction "
    "only when they improve the request and do not conflict with the user's intended scene.\n"

    "- For recreation tasks, avoid adding details that unnecessarily alter the original character, scene, or composition.\n"

    # 실제 장소
    "- For real-world locations, specify the city and country and include one or two recognizable landmarks "
    "when this helps establish the correct location.\n"

    # 이미지 속 텍스트
    '- If the user wants visible text inside the image, preserve the exact requested text inside double quotes.\n'

    # 기존 결과 수정
    "- If a previous prompt or reference is provided and the user requests an edit, rewrite the complete prompt "
    "with the requested changes incorporated while preserving everything that should remain unchanged.\n"

    # 길이
    "- Keep ordinary image prompts concise, usually one to three sentences and preferably under 80 words. "
    "Cinematic or video prompts may be longer when motion and shot direction require more detail.\n"

    # 역할 분리
    "- Focus on translating and improving the creative request. "
    "Do not invent additional restrictions merely because the content is mature, violent, frightening, or sensual.\n"
    
    # 최소 제한
    "- Never sexualize minors or characters presented as minors.\n"
    "- Never create sexual exploitation or sexual violence as erotic entertainment.\n"
    "- Do not create non-consensual sexual depictions of real identifiable people.\n"
    "- Do not turn fictional creative requests into practical instructions for committing serious real-world harm.\n"

    "- Output only the final generation prompt."
)


class ImageGenError(RuntimeError):
    """그림을 만들지 못했다. 사용자에게 그대로 보일 한국어 문장."""


@dataclass(slots=True)
class VideoResult:
    path: Path
    prompt: str
    seed: int
    width: int
    height: int
    seconds: float
    fhd: bool
    model: str = WAN5_LABEL


@dataclass(frozen=True, slots=True)
class VideoPlan:
    """영상 한 편을 어느 모델로 몇×몇, 몇 프레임으로 만드는지와 이 PC 에서 걸리는 대략의 시간(분, 안내용)."""
    model: str
    width: int
    height: int
    frames: int
    fps: int
    fhd: bool
    minutes: int

    @property
    def size(self) -> tuple[int, int]:
        """완성 영상의 크기 — FHD 면 SeedVR2 가 1.5배로 키운다."""
        return (round(self.width * 1.5), round(self.height * 1.5)) if self.fhd else (self.width, self.height)


@dataclass(slots=True)
class ImageResult:
    path: Path
    prompt: str
    seed: int
    width: int
    height: int
    seconds: float
    model: str = MODEL_LABEL


def image_size(text: str) -> tuple[int, int]:
    """'세로'·'폰 배경화면' 은 768×1344, '가로'·'배경화면' 은 1344×768, 나머지는 1024×1024."""
    low = text.lower()
    if any(word in low for word in PORTRAIT_WORDS):
        return 768, 1344
    if any(word in low for word in LANDSCAPE_WORDS):
        return 1344, 768
    return 1024, 1024


def image_subject(text: str) -> str:
    """'바닷가 노을 그림 그려줘' → '바닷가 노을'. 화면 설명과, 모델이 없을 때의 프롬프트로 쓴다."""
    lead = _EN_LEAD.match(text)
    if lead:
        return re.sub(r"\s+", " ", text[lead.end():]).strip(" ,.!?~")
    subject = _REQUEST_CUT.sub("", text.strip())
    subject = _TRAILING_SIZE.sub("", _TRAILING_NOUN.sub("", subject))
    return re.sub(r"\s+", " ", subject).strip(" ,.!?~")


def is_followup(text: str) -> bool:
    return any(word in text for word in FOLLOWUP_WORDS)


_CHANGE_ENDING = re.compile(r"(?:해|바꿔|만들어|그려|고쳐)\s*(?:줘|주세요|줄래|봐|볼래|줘요)|^(?:한\s*장|하나|한\s*번)\s*더")


def is_change_request(text: str) -> bool:
    """바로 앞 답이 그림일 때 그 그림을 고치라는 짧은 말인가 — '더 밝게 해줘', '한 장 더', '배경을 밤으로 바꿔줘'."""
    body = " ".join(text.split())
    return len(body) <= 60 and is_followup(body) and bool(_CHANGE_ENDING.search(body)) or bool(
        re.fullmatch(r"(?:한\s*장|하나|한\s*번)\s*더[\s.!?~]*(?:줘|주세요)?[\s.!?~]*", body))


# ── 그리지 않는 것 (2026-10-03, ADR-073 보충) ────────────────────────────────
# 미성년자를 성적으로 보이게 하는 그림은 앱이 항상 막는다 — 그림(가상 표현물)이어도 한국에서 처벌 대상이다
# (아동·청소년의 성보호에 관한 법률 제2조 제5호 '아동·청소년으로 명백하게 인식될 수 있는 사람이나 표현물', 제11조).
# 성인의 나체·성적 표현은 앱이 따로 막거나 풀지 않는다
MINOR_TERMS = ("아동", "어린이", "초등학생", "초딩", "중학생", "고등학생", "여고생", "여중생", "미성년", "유아", "소녀", "소년",
               "어린 ", "아이 ", "child", "kid", "minor", "underage", "teen", "loli", "schoolgirl", "toddler")
SEXUAL_TERMS = ("음란", "topless", "nipple", "porn",)
# 의복의 비침만 본다. "투명한 날개의 요정 소녀" 같은 비성적 장면은 차단하지 않는다.
SEE_THROUGH_CLOTHING = re.compile(
    r"(?:옷|의상|속옷|교복|상의|하의)\s*(?:이|가)?\s*비치|"
    r"비치(?:는|게)?\s*(?:옷|의상|속옷|교복|상의|하의)|"
    r"시스루\s*(?:옷|의상|속옷|교복|상의|하의)|"
    r"(?:see[- ]through|sheer|transparent)\s+(?:clothes|clothing|dress|shirt|skirt|uniform|outfit)", re.I)
_REFUSAL = re.compile(r"\bi\s*(?:can(?:no|'|’)t|cannot|won(?:'|’)t|am unable|'m unable|am not able)\b|\bi(?:'|’)m sorry\b|"
                      r"\bsorry,? but\b|\bas an ai\b|죄송|어렵습니다|할 수 없|드릴 수 없", re.I)


# ── 미성년자 금지 강화 (2026-10-11, claude — 사용자 지시 "Danbooru·e621 은 붙이되 미성년자건 강력하게 금지") ──────
# 위 SEXUAL_TERMS(사용자 결정)는 그대로 두고, 미성년자 표현이 있을 때만 아래의 넓은 목록을 함께 본다 — 성인만 나오는
# 그림에는 영향이 없다. Danbooru 태그(female_child 등)는 _·- 를 띄어 쓴 뒤 본다.
MINOR_TERMS_KO = ("청소년", "어린애", "꼬마", "아기", "여자아이", "남자아이", "여자애", "남자애", "초등", "중딩", "고딩",
                  "여학생", "남학생", "유치원", "교복", "세일러복", "체육복", "란도셀")
MINOR_WORDS_EN = re.compile(
    r"\b(?:child|children|childlike|childish|kids?|minors?|underage|preteens?|teens?|teenage|teenagers?|tweens?|"
    r"youngsters?|schoolgirls?|schoolboys?|school\s+uniforms?|serafuku|buruma|randoseru|kindergarten|"
    r"elementary\s+school|middle\s+school|high\s+school|junior\s+high|toddlers?|infants?|babies|"
    r"baby(?!\s+(?:blue|pink|doll|oil|powder))|cubs?|little\s+(?:girl|boy)s?|female\s+child|male\s+child|aged\s+down|"
    r"young(?!\s+(?:adult|woman|women|man|men|lady|ladies|mother|father|wife|husband|couple)))\b")
# 나이는 한국 법(아동·청소년 = 19세 미만)에 맞춘다. '15세기'·'3세대'·'5세트' 는 나이가 아니다.
_AGE_KO = re.compile(r"(?<![0-9])(\d{1,2})\s*(?:살|세)(?!기|대|트)")
_AGE_EN = re.compile(r"\b(\d{1,2})\s*(?:years?|yrs?)\s*old\b|\b(\d{1,2})\s*(?:yo|y/o)\b|\baged?\s+(\d{1,2})\b")
_UNDER_18 = re.compile(r"\bunder\s*(?:the\s+age\s+of\s+)?18\b")
_GRADE_KO = re.compile(r"(?<![가-힣])(?:초|중|고)[1-6](?![0-9])")
# 미성년자를 성적으로 그린다는 뜻이 이름에 들어 있는 말 — 다른 말과 상관없이 항상 막는다('칼로리'·'쇼타임' 은 아니다).
MINOR_FETISH = re.compile(r"(?<![가-힣])로리|(?<![가-힣])쇼타(?!임)|"
                          r"\b(?:loli|lolis|lolicon|lolita|shota|shotas|shotacon|toddlercon)\b")
# 미성년자 표현과 함께 있으면 막는 말 — 성적이거나 몸을 드러내는 말. 성인만 나오는 부탁에는 쓰지 않는다.
MINOR_SEXUAL_KO = ("섹시", "야한", "야하게", "야릇", "에로", "음란", "선정적", "관능", "노출", "비키니", "수영복", "속옷",
                   "란제리", "팬티", "브래지어", "브라자", "알몸", "나체", "누드", "옷을 벗", "옷 벗", "벗은 몸", "다 벗",
                   "발가벗", "헐벗", "가슴골", "젖가슴", "젖꼭지", "큰 가슴", "가슴이 큰", "가슴 강조", "가슴을 드러", "엉덩이",
                   "사타구니", "성인물", "성적인", "성적으로", "성적 매력", "섹스", "성관계", "애무", "유혹", "목욕",
                   "샤워", "19금", "후방주의")
MINOR_SEXUAL_EN = re.compile(
    r"\b(?:sexy|sexual|sexualized|sex|nsfw|explicit|questionable|erotic|erotica|lewd|hentai|ecchi|nude|nudity|naked|"
    r"topless|bottomless|nipples?|breasts?|cleavage|lingerie|underwear|panties|pantsu|pantyshot|upskirt|bra|bikinis?|"
    r"swimsuits?|swimwear|seductive|sensual|provocative|suggestive|fetish|bdsm|bondage|porn|cum|orgasm|spread\s+legs|"
    r"cameltoe|camel\s+toe|crotch|genitals?|pussy|penis|vagina|ass|butt|undress(?:ed|ing)?|bath|bathing|bathtub|"
    r"showering|onsen)\b")


def _minor_text(*texts: str) -> str:
    return re.sub(r"[_\-]+", " ", " ".join(texts).lower())


def mentions_minor(*texts: str) -> bool:
    """미성년자로 보이는 표현 — 단어·Danbooru 태그·19세 미만 나이·학년(고2)."""
    low = _minor_text(*texts)
    if any(term in low for term in MINOR_TERMS) or any(term in low for term in MINOR_TERMS_KO):
        return True
    if MINOR_WORDS_EN.search(low) or _UNDER_18.search(low) or _GRADE_KO.search(low) or MINOR_FETISH.search(low):
        return True
    ages = [int(n) for match in (*_AGE_KO.finditer(low), *_AGE_EN.finditer(low)) for n in match.groups() if n]
    return any(age < 19 for age in ages)


def sexual_words(*texts: str) -> bool:
    """성적이거나 몸을 드러내는 말이 있는가(미성년자 판별과 애니 모델 부정 프롬프트에 쓴다)."""
    low = _minor_text(*texts)
    return (any(term in low for term in SEXUAL_TERMS) or any(term in low for term in MINOR_SEXUAL_KO)
            or bool(MINOR_SEXUAL_EN.search(low)) or bool(SEE_THROUGH_CLOTHING.search(low)))


def blocked_reason(*texts: str) -> str | None:
    """앱이 그리지 않는 부탁이면 사용자에게 보일 이유(지금은 미성년자 관련만). 아니면 None."""
    if MINOR_FETISH.search(_minor_text(*texts)):
        return "미성년자를 성적으로 그리는 표현(로리·쇼타 등)은 그리지 않습니다."
    if mentions_minor(*texts) and sexual_words(*texts):
        return "미성년자를 성적으로 보이게 하는 그림은 그리지 않습니다."
    return None


def looks_like_refusal(reply: str) -> bool:
    """프롬프트를 쓰라고 했는데 모델이 거절한 답 — 그 문장을 그림으로 그리면 엉뚱한 그림이 나온다."""
    return bool(_REFUSAL.search((reply or "")[:400]))


# 모델마다 프롬프트 모양이 다르다: Z-Image 는 짧은 서술, Qwen 은 자세한 지시문, NoobAI 는 Danbooru 태그.
# 원칙·제한은 PROMPT_RULES(사용자가 고친 글) 그대로 쓰고 모양만 덧붙인다.
QWEN_RULES = (
    PROMPT_RULES
    + "\nThis prompt is for Qwen-Image 2.1, which follows long and exact instructions. These rules override any length "
    "rule above:\n"
    "- Write one detailed English paragraph, about 60 to 200 words, that states every requested element exactly: how many "
    "people or objects, who is where (left, right, foreground, background), what each one holds or does, clothing, "
    "expressions, camera angle and lens, lighting and art style.\n"
    "- Put any text that should appear in the picture inside double quotes, exactly as requested."
)
TAG_RULES = (
    "You write prompts for NoobAI-XL, an anime model trained on Danbooru and e621 tags. "
    "The user's request is usually written in Korean.\n"
    + (PROMPT_RULES.partition("\n")[2] or PROMPT_RULES)
    + "\nThese format rules override any format or length rule above:\n"
    "- Output only lowercase, comma-separated Danbooru-style tags with spaces instead of underscores, not sentences.\n"
    "- Start with the character count (1girl, 2girls, 1boy, 1girl 1boy, no humans), then a known character's name and "
    "series as Danbooru writes them (for example hatsune miku, vocaloid), then hair, eyes, body, clothing, expression, "
    "pose, action, objects, background, lighting and composition tags (for example cowboy shot, from below, depth of field).\n"
    "- Every character is an adult. Never use child, loli, shota, aged down or any school-age tag.\n"
    "- Do not add quality tags such as masterpiece or best quality; the app adds them.\n"
    "- Usually 15 to 40 tags."
)
PROMPT_RULES_BY_MODEL = {"zimage": PROMPT_RULES, "qwen": QWEN_RULES, "anime": TAG_RULES}


def prompt_messages(text: str, previous: str | None = None, model: str = "zimage") -> list[Message]:
    request = text.strip()
    if previous:
        request = f"Previous prompt: {previous}\nUser's change: {request}"
    return [Message("system", PROMPT_RULES_BY_MODEL.get(model, PROMPT_RULES)), Message("user", request)]


def choose_image_model(text: str, previous_model: str | None = None, previous_prompt: str = "",
                       forced: str | None = None) -> tuple[str, str]:
    """('zimage'|'qwen'|'anime', 사용자에게 보일 메모). forced 는 ✎ 창에서 고른 모델, 앞 그림을 고치면
    previous_model 을 이어 쓴다.

    애니 모델(Danbooru·e621 학습)은 미성년자로 보이는 표현이 하나라도 있으면 쓰지 않는다 — 아이 모습을 성적으로 그리기
    쉬운 데이터라서다(2026-10-11 사용자 "미성년자건 강력하게 금지"). 그때는 Z-Image 로 그리고 그렇다고 알린다.
    """
    if forced in MODEL_LABELS:
        wanted = forced
    elif _ASK_ZIMAGE.search(text):
        wanted = "zimage"
    elif _ASK_ANIME.search(text):
        wanted = "anime"
    elif _ASK_QWEN.search(text):
        wanted = "qwen"
    elif previous_model in MODEL_LABELS:
        wanted = previous_model
    else:
        wanted = "qwen" if len(image_subject(text)) >= QWEN_MIN_CHARS else "zimage"
    if wanted == "anime" and mentions_minor(text, previous_prompt):
        return "zimage", "(미성년자로 보이는 표현이 있어 애니 모델 대신 Z-Image Turbo 로 그렸습니다.)"
    return wanted, ""


def anime_prompts(tags: str) -> tuple[str, str]:
    """태그 → (긍정, 부정). 아이 태그는 항상 부정에 넣고, 성적인 태그가 있으면 'adult' 를 앞에 붙인다.
    성적인 부탁이 아니면 nsfw 를 부정에 넣어 원하지 않은 노출을 막고, 퍼리를 말하지 않았으면 e621 동물 모습을 막는다."""
    tags = tags.strip().strip(",").strip()
    sexual = sexual_words(tags)
    positive = ", ".join(part for part in (ANIME_QUALITY, "adult" if sexual else "", tags) if part)
    negative = ", ".join(part for part in (ANIME_NEGATIVE, ANIME_MINOR_NEGATIVE, "" if sexual else "nsfw, explicit",
                                           "" if _FURRY.search(_minor_text(tags)) else ANIME_FURRY_NEGATIVE) if part)
    return positive, negative


EDIT_RULES = (
    "You write prompts for an image model that is given reference images of characters (image 1, image 2, ...). "
    "Turn the user's request, usually written in Korean, into ONE English prompt for a new picture.\n"
    "- Refer to each character as 'the same <woman/man/girl/boy/creature> from image N' in the given order, and say to "
    "keep their face, hairstyle, outfit and accessories unless the user asks to change them.\n"
    "- Describe the new scene: action, pose, expression, camera angle, background and lighting. Keep the art style of "
    "the reference images unless the user asks for another style.\n"
    + (PROMPT_RULES.partition("\n")[2] or PROMPT_RULES)
)


def edit_prompt_messages(text: str, references: list[tuple[str, str]], previous: str | None = None) -> list[Message]:
    """references: (그림 설명, 그 그림의 영어 프롬프트) — 예 ("character '은비'", "...")."""
    lines = [f"Image {index}: {label} — {prompt or '(no description)'}"
             for index, (label, prompt) in enumerate(references, start=1)]
    if previous:
        lines.append(f"Previous prompt: {previous}")
    lines.append(f"Request: {text.strip()}")
    return [Message("system", EDIT_RULES), Message("user", "\n".join(lines))]


# "이 캐릭터를 은비로 저장해줘", "방금 그린 캐릭터를 '은비'라고 등록해줘" — 캐릭터라는 말과 저장/등록이 함께 있어야 한다.
_SAVE_CHARACTER = re.compile(
    r"(?:캐릭터|인물|주인공)\S*\s*(?:을|를)?\s*[\"'“‘]?(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9_]{1,11}?)[\"'”’]?\s*"
    r"(?:으로|로|라고|이라고)\s*(?:저장|등록|기억)")
# "방금 그림의 캐릭터로", "같은 캐릭터로", "그 인물이" — 저장하지 않고 바로 앞 그림을 기준으로 쓴다.
_SAME_CHARACTER = re.compile(r"(?:방금|같은|이|그|앞|위|아까)\s*(?:그림|사진|이미지)?\S*\s*(?:캐릭터|인물|주인공)")


# "흑태자 캐릭터로 저장해줘", "흑태자라는 캐릭터로 등록해줘" — 이름이 앞에 오는 말투.
_SAVE_CHARACTER_NAME_FIRST = re.compile(
    r"(?<![가-힣A-Za-z0-9])[\"'“‘]?(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9_]{1,11}?)[\"'”’]?\s*(?:이라는|라는)?\s*"
    r"(?:캐릭터|인물|주인공)\s*(?:으로|로)?\s*(?:저장|등록|기억)")
_NOT_NAMES = {"이", "그", "저", "방금", "같은", "새", "이번", "그림", "이그림", "이미지", "사진"}


def character_save_name(text: str) -> str | None:
    body = " ".join(text.split())
    for pattern in (_SAVE_CHARACTER, _SAVE_CHARACTER_NAME_FIRST):
        match = pattern.search(body)
        if match and match.group("name") not in _NOT_NAMES:
            return match.group("name")
    return None


def wan14_i2v_size(start_image: Path | None) -> tuple[int, int]:
    """시작 그림 비율에 맞춘 Wan 14B 그림→영상 크기(16의 배수, 화소 수는 1280×720 과 같다). 못 읽으면 가로."""
    from .vision import image_dimensions  # vision 이 imagegen 을 import 한다 — 부를 때만 가져온다
    try:
        data = start_image.read_bytes() if start_image is not None else b""
        kind = image_kind(data)
        width, height = image_dimensions(data, kind) if kind else (16, 9)
    except (OSError, ValueError):
        width, height = 16, 9
    ratio = width / max(1, height)
    if ratio >= 1.3:
        return 1280, 720
    if ratio <= 0.77:
        return 720, 1280
    return 960, 960


def image_kind(data: bytes) -> str | None:
    """그림 파일의 첫 바이트로 종류를 안다(확장자·Content-Type 을 믿지 않는다). PNG·JPEG·WebP 만."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def fetch_image_url(url: str) -> bytes:
    """참고 그림 주소에서 받아 온다 — 웹 읽기와 같은 보호(공개 주소만, 리다이렉트·연결 단계 재확인), 10MB 까지."""
    from .webread import WebError, check_url, guarded_opener  # 순환 없음: webread 는 jarvis 를 import 하지 않는다

    try:
        check_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ALEX reference image)"})
        with guarded_opener().open(request, timeout=30) as response:
            data = response.read(MAX_UPLOAD_BYTES + 1)
    except (WebError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ValueError(f"그림을 받아 오지 못했습니다: {exc}") from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("그림이 너무 큽니다(10MB 이하).")
    if image_kind(data) is None:
        raise ValueError("그 주소는 PNG·JPEG·WebP 그림이 아닙니다(웹 페이지 주소가 아니라 그림 파일 주소를 주세요).")
    return data


def wants_same_character(text: str) -> bool:
    return bool(_SAME_CHARACTER.search(text))


class CharacterBook:
    """이름 붙인 캐릭터의 기준 그림(ADR-074) — data/characters/ 에 PNG 사본과 characters.json.

    그림 원본(data/images)을 지워도 캐릭터는 남는다. 이름은 2~12자 한글·영문·숫자.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.index = self.root / "characters.json"

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {name: entry for name, entry in data.items() if isinstance(entry, dict)} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.index.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.index)

    def names(self) -> list[str]:
        return sorted(self._read())

    def get(self, name: str) -> dict | None:
        return self._read().get(name)

    def image(self, entry: dict) -> Path:
        return self.root / str(entry.get("file", ""))

    def save(self, name: str, image: Path, prompt: str) -> dict:
        if not CHARACTER_NAME.fullmatch(name):
            raise ValueError("캐릭터 이름은 2~12자의 한글·영문·숫자로 정해 주세요.")
        data = self._read()
        suffix = Path(image).suffix.lower() if Path(image).suffix.lower() in IMAGE_TYPES else ".png"
        file = f"{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}{suffix}"
        old = data.get(name)
        if old and old.get("file") != file:  # 같은 이름을 다른 종류의 그림으로 바꿨으면 옛 파일을 지운다
            try:
                self.image(old).unlink()
            except OSError:
                pass
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(image, self.root / file)
        data[name] = {"name": name, "file": file, "prompt": prompt, "source": Path(image).name,
                      "saved_at": time.strftime("%Y-%m-%d %H:%M")}
        self._write(data)
        return data[name]

    def delete(self, name: str) -> bool:
        data = self._read()
        entry = data.pop(name, None)
        if entry is None:
            return False
        try:
            self.image(entry).unlink()
        except OSError:
            pass
        self._write(data)
        return True

    def find_in(self, text: str) -> list[dict]:
        """글에 나온 저장 캐릭터(나온 순서, 최대 MAX_REFERENCES). '민준이' 로 저장했으면 '민준과' 도 찾는다."""
        found: list[tuple[int, dict]] = []
        for name, entry in self._read().items():
            aliases = {name} | ({name[:-1]} if name.endswith("이") and len(name) >= 3 else set())
            positions = [match.start() for alias in aliases
                         for match in re.finditer(r"(?<![가-힣A-Za-z0-9])" + re.escape(alias), text)]
            if positions:
                found.append((min(positions), entry))
        return [entry for _, entry in sorted(found, key=lambda item: item[0])][:MAX_REFERENCES]


VIDEO_RULES = (
    "You write prompts for a text-to-video model (Wan 2.2) that makes a 5-second clip. Turn the user's request, usually "
    "written in Korean, into ONE English prompt.\n"
    "- Describe the subject, what moves and how, the camera movement, the setting, lighting and style.\n"
    "- If a starting picture is described, keep that picture's subject and style and describe how it comes to life.\n"
    + (PROMPT_RULES.partition("\n")[2] or PROMPT_RULES)
)


# LTX-2.5 는 소리도 만든다 — 들리는 것까지 적게 한다(모델 카드: 장면·소리·대사를 한 문단에 서술).
LTX_RULES = (
    VIDEO_RULES.replace("(Wan 2.2)", "(LTX-2.5)")
    + "\nThis model also generates synchronized audio. These rules override any length rule above:\n"
    "- Write one flowing paragraph of about 60 to 150 words in present tense: the action in order, the camera, then what "
    "is heard - ambient sound, sound effects, music, and any spoken lines in double quotes with who says them.\n"
    "- End with: No text, no black frames."
)


def video_prompt_messages(text: str, start_prompt: str | None = None, model: str = "wan") -> list[Message]:
    request = f"Starting picture: {start_prompt}\nRequest: {text.strip()}" if start_prompt else text.strip()
    return [Message("system", LTX_RULES if model == "ltx" else VIDEO_RULES), Message("user", request)]


def choose_video_model(text: str, forced: str | None = None) -> str:
    """'ltx'(소리까지) 또는 'wan'. ✎ 창에서 고른 모델이 먼저, 아니면 소리·대사·음악을 말하면 LTX."""
    if forced in ("ltx", "wan"):
        return forced
    return "ltx" if _ASK_SOUND.search(text) else "wan"


def video_marker_line(caption: str, path: Path) -> str:
    safe = re.sub(r"[\[\]\n]", " ", caption).strip()[:120] or "만든 영상"
    return f"![{safe}]({VIDEO_MARKER}{path.name})"


def clean_prompt(reply: str, *, max_chars: int = 600) -> str:
    """모델 답에서 프롬프트 한 덩어리만 남긴다(머리말·따옴표·생각 꼬리표 제거)."""
    text = re.sub(r"<think>.*?</think>", " ", reply or "", flags=re.S | re.I).strip()
    text = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    text = re.sub(r"^\s*(?:english\s+)?prompt\s*[:：]\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().strip("`")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text[:max_chars]


def has_hangul_text_request(text: str) -> bool:
    """그림 안에 한글 글자를 넣어 달라는 부탁 — Z-Image 는 영어·중국어 글자만 잘 그린다."""
    return bool(re.search(r"[\"'“”‘’「」『』][^\"'“”‘’「」『』]*[가-힣][^\"'“”‘’「」『』]*[\"'“”‘’「」『』]", text)) \
        or any(word in text for word in ("라고 써", "글자", "문구", "텍스트"))


def marker_line(caption: str, path: Path) -> str:
    safe = re.sub(r"[\[\]\n]", " ", caption).strip()[:120] or "생성한 그림"
    return f"![{safe}]({MARKER}{path.name})"


class ComfyImageGenerator:
    """127.0.0.1 의 ComfyUI 에 Z-Image Turbo 워크플로를 보내고 PNG 를 받아 `out_dir` 에 저장한다.

    ComfyUI 가 꺼져 있으면 `comfy_dir`(Windows 포터블 폴더)에서 띄운다. 한 번에 한 장만 그린다(잠금).
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        url: str = DEFAULT_URL,
        comfy_dir: Path | None = None,
        log_dir: Path | None = None,
        unet: str = UNET,
        clip: str = CLIP,
        vae: str = VAE,
        steps: int = STEPS,
        start_timeout: float = 180.0,
        run_timeout: float = 600.0,
        poll_seconds: float = 0.5,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.url = url.rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("ComfyUI 는 이 PC(127.0.0.1)에서만 쓴다")
        self.host, self.port = parsed.hostname, parsed.port or 8188
        self.comfy_dir = Path(comfy_dir) if comfy_dir else None
        self.log_dir = Path(log_dir) if log_dir else None
        self.unet, self.clip, self.vae, self.steps = unet, clip, vae, int(steps)
        self.start_timeout, self.run_timeout, self.poll_seconds = start_timeout, run_timeout, poll_seconds
        self.client_id = uuid.uuid4().hex
        self.model_label = MODEL_LABEL
        self.characters = CharacterBook(self.out_dir.parent / "characters")
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, payload: dict | None = None, timeout: float = 10.0) -> bytes:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.url}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ImageGenError(f"ComfyUI 가 요청을 거절했습니다(HTTP {exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ImageGenError(f"ComfyUI 에 연결하지 못했습니다: {self.url} ({getattr(exc, 'reason', exc)})") from exc

    def _json(self, method: str, path: str, payload: dict | None = None, timeout: float = 10.0) -> Any:
        body = self._request(method, path, payload, timeout)
        try:
            return json.loads(body.decode("utf-8")) if body.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImageGenError(f"ComfyUI 응답을 읽지 못했습니다: {path}") from exc

    def is_running(self) -> bool:
        try:
            self._request("GET", "/system_stats", timeout=2.0)
            return True
        except ImageGenError:
            return False

    # ------------------------------------------------------------------ process
    def ensure_running(self) -> None:
        if self.is_running():
            return
        python = self.comfy_dir / "python_embeded" / "python.exe" if self.comfy_dir else None
        main = self.comfy_dir / "ComfyUI" / "main.py" if self.comfy_dir else None
        if python is None or main is None or not python.is_file() or not main.is_file():
            raise ImageGenError(f"ComfyUI 가 꺼져 있고 실행 파일도 찾지 못했습니다: {self.comfy_dir or '(폴더 설정 없음)'}")
        log_path = (self.log_dir or self.comfy_dir) / "comfyui.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [str(python), "-s", str(main), "--windows-standalone-build", "--listen", str(self.host),
                "--port", str(self.port), "--disable-auto-launch"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        with log_path.open("ab") as log:
            self._process = subprocess.Popen(args, cwd=str(self.comfy_dir), stdin=subprocess.DEVNULL, stdout=log,
                                             stderr=subprocess.STDOUT, creationflags=flags)
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return
            if self._process.poll() is not None:
                raise ImageGenError(f"ComfyUI 가 시작하자마자 꺼졌습니다(종료 코드 {self._process.returncode}). 기록: {log_path}")
            time.sleep(1.0)
        raise ImageGenError(f"ComfyUI 가 {self.start_timeout:.0f}초 안에 준비되지 않았습니다. 기록: {log_path}")

    def close(self) -> bool:
        """Stop and reap only the ComfyUI child started by this generator."""
        if not self._lock.acquire(blocking=False):
            raise ImageGenError("그림·영상 생성 중이어서 엔진을 종료하지 않았습니다.")
        try:
            process = self._process
            if process is None:
                return False  # A pre-existing ComfyUI server belongs to its caller.
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ImageGenError(f"ALEX가 시작한 그림·영상 엔진을 종료하지 못했습니다: {exc}") from exc
            self._process = None
            return True
        finally:
            self._lock.release()

    # ------------------------------------------------------------------ drawing
    def workflow(self, prompt: str, seed: int, width: int, height: int) -> dict:
        """ComfyUI 의 Z-Image Turbo 기본 템플릿(image_z_image_turbo)과 같은 그래프를 API 형식으로 쓴 것."""
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": self.unet, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.clip, "type": "lumina2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": self.vae}},
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                "seed": seed, "steps": self.steps, "cfg": 1.0, "sampler_name": "res_multistep",
                "scheduler": "simple", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            # 임시 폴더에 저장한다(ComfyUI 가 다음 시작 때 비운다) — 사본은 ALEX 의 data/images 에만 남긴다.
            "10": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
        }

    def qwen_workflow(self, prompt: str, seed: int, width: int, height: int) -> dict:
        """ComfyUI 기본 템플릿 image_qwen_image_2_1_t2i 의 그림 부분(프롬프트 다듬기 모델은 빼고 ALEX 가 쓴 글을 넣는다)."""
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": QWEN_UNET, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": QWEN_CLIP, "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": QWEN_VAE}},
            "4": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
            "5": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": prompt,
                                                                    "negative_prompt": "", "resolution": 1024}},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["4", 0], "positive": ["5", 0], "negative": ["5", 1], "latent_image": ["7", 0],
                "seed": seed, "steps": QWEN_STEPS, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
        }

    def anime_workflow(self, prompt: str, seed: int, width: int, height: int) -> dict:
        """NoobAI-XL 1.1(SDXL) 기본 그래프. 품질·아이 방지 태그는 anime_prompts 가 붙인다."""
        positive, negative = anime_prompts(prompt)
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ANIME_CKPT}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": positive}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative}},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["1", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
                "seed": seed, "steps": ANIME_STEPS, "cfg": ANIME_CFG, "sampler_name": "euler_ancestral",
                "scheduler": "normal", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
            "10": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
        }

    def has_model(self, model: str) -> bool:
        """그 모델의 파일이 ComfyUI 모델 폴더에 다 있는가. Z-Image 는 늘 있다고 본다(기본 모델). 폴더를 모르면 False."""
        if model == "zimage":
            return True
        if self.comfy_dir is None or model not in MODEL_FILES:
            return False
        models = self.comfy_dir / "ComfyUI" / "models"
        return all((models / folder / name).is_file() for folder, name in MODEL_FILES[model])

    def edit_workflow(self, prompt: str, seed: int, width: int, height: int, references: list[str]) -> dict:
        """기준 그림(들)을 본 새 그림 — FLUX.2 Klein 4B 편집 템플릿과 같은 그래프. 기준 그림마다 ReferenceLatent 를 양·음 조건에 건다."""
        graph: dict[str, dict] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": EDIT_UNET, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": self.clip, "type": "flux2", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": EDIT_VAE}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        }
        positive, negative = ["4", 0], ["5", 0]
        for index, name in enumerate(references[:MAX_REFERENCES]):
            base = 20 + index * 10
            graph[str(base)] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph[str(base + 1)] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
                "image": [str(base), 0], "upscale_method": "nearest-exact", "megapixels": 1.0, "resolution_steps": 1}}
            graph[str(base + 2)] = {"class_type": "VAEEncode", "inputs": {"pixels": [str(base + 1), 0], "vae": ["3", 0]}}
            graph[str(base + 3)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": positive,
                                                                                "latent": [str(base + 2), 0]}}
            graph[str(base + 4)] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": negative,
                                                                                "latent": [str(base + 2), 0]}}
            positive, negative = [str(base + 3), 0], [str(base + 4), 0]
        graph.update({
            "10": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": positive, "negative": negative,
                                                         "cfg": 1.0}},
            "11": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "13": {"class_type": "Flux2Scheduler", "inputs": {"steps": EDIT_STEPS, "width": width, "height": height}},
            "14": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "15": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["11", 0], "guider": ["10", 0],
                                                                     "sampler": ["12", 0], "sigmas": ["13", 0],
                                                                     "latent_image": ["14", 0]}},
            "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["3", 0]}},
            "17": {"class_type": "PreviewImage", "inputs": {"images": ["16", 0]}},
        })
        return graph

    def upload_image(self, path: Path) -> str:
        """기준 그림을 ComfyUI 입력 폴더에 올린다(POST /upload/image). 같은 그림은 같은 이름이라 덮어쓴다."""
        data = Path(path).read_bytes()
        suffix = image_kind(data) or ".png"
        name = f"alex-ref-{hashlib.sha1(data).hexdigest()[:16]}{suffix}"
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: {IMAGE_TYPES[suffix]}\r\n\r\n").encode() + data + (
                f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n"
                f"--{boundary}--\r\n").encode()
        request = urllib.request.Request(f"{self.url}/upload/image", data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ImageGenError(f"기준 그림을 ComfyUI 에 올리지 못했습니다(HTTP {exc.code}).") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ImageGenError(f"기준 그림을 ComfyUI 에 올리지 못했습니다: {exc}") from exc
        subfolder = str(reply.get("subfolder") or "")
        return f"{subfolder}/{reply['name']}" if subfolder else str(reply["name"])

    def generate(self, prompt: str, width: int = 1024, height: int = 1024, seed: int | None = None,
                 cancel_event: threading.Event | None = None, references: list[Path] | None = None,
                 model: str = "zimage") -> ImageResult:
        """references 가 있으면 그 그림 속 캐릭터를 유지하는 편집 모델(FLUX.2 Klein)로, 없으면 model
        ('zimage'·'qwen'·'anime')로 그린다. 파일이 없는 모델은 Z-Image Turbo 로 그린다."""
        if not prompt.strip():
            raise ImageGenError("무엇을 그릴지 알려 주세요. 예: '노을 지는 바닷가 그려줘'")
        with self._lock:
            started = time.monotonic()
            self.ensure_running()
            seed = random.randrange(2**32) if seed is None else int(seed)
            if references:
                names = [self.upload_image(path) for path in references[:MAX_REFERENCES]]
                graph, label = self.edit_workflow(prompt, seed, width, height, names), EDIT_LABEL
            elif model == "qwen" and self.has_model("qwen"):
                graph, label = self.qwen_workflow(prompt, seed, width, height), MODEL_LABELS["qwen"]
            elif model == "anime" and self.has_model("anime"):
                graph, label = self.anime_workflow(prompt, seed, width, height), MODEL_LABELS["anime"]
            else:
                graph, label = self.workflow(prompt, seed, width, height), self.model_label
            reply = self._json("POST", "/prompt", {"prompt": graph, "client_id": self.client_id}, timeout=30.0)
            if reply.get("node_errors"):
                raise ImageGenError(f"ComfyUI 워크플로 오류: {json.dumps(reply['node_errors'], ensure_ascii=False)[:400]}")
            prompt_id = str(reply.get("prompt_id") or "")
            if not prompt_id:
                raise ImageGenError(f"ComfyUI 가 작업 번호를 주지 않았습니다: {str(reply)[:200]}")
            deadline = time.monotonic() + self.run_timeout
            images: list[dict] = []
            while not images:
                if cancel_event is not None and cancel_event.is_set():
                    self._quiet_post("/interrupt")
                    raise GenerationCancelled("")
                entry = self._json("GET", f"/history/{urllib.parse.quote(prompt_id)}").get(prompt_id) or {}
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ImageGenError("ComfyUI 가 그리다 실패했습니다: " + _status_message(status))
                images = [image for output in (entry.get("outputs") or {}).values()
                          for image in (output.get("images") or []) if isinstance(image, dict)]
                if not images:
                    if time.monotonic() > deadline:
                        self._quiet_post("/interrupt")
                        raise ImageGenError(f"{self.run_timeout:.0f}초 안에 그리지 못했습니다.")
                    time.sleep(self.poll_seconds)
            first = images[0]
            query = urllib.parse.urlencode({"filename": first.get("filename", ""), "subfolder": first.get("subfolder", ""),
                                            "type": first.get("type", "temp")})
            data = self._request("GET", f"/view?{query}", timeout=60.0)
            if not data.startswith(b"\x89PNG"):
                raise ImageGenError("ComfyUI 가 PNG 가 아닌 것을 돌려줬습니다.")
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{seed}.png"
            path.write_bytes(data)
            return ImageResult(path, prompt, seed, width, height, round(time.monotonic() - started, 1), label)

    def free(self, release_ram: bool = False) -> bool:
        """그래픽카드를 비운다. release_ram 이면 RAM 에 남겨 둔 모델까지 버린다(`/unload`)."""
        if not self.is_running():
            return False
        return self._quiet_post("/free", {"unload_models": True, "free_memory": bool(release_ram)})

    def _listening(self, timeout: float = 0.2) -> bool:
        """포트에 연결만 해 본다. 켜진 엔진은 운영체제가 바로 받아 주고, 꺼진 포트의 거절은 Windows 에서 2초쯤 늦게 온다."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def prepare_for_chat(self, timeout: float = 10) -> None:
        """대화 모델 적재 전 작업 대기열과 PyTorch 예약 메모리 반환을 확인한다."""
        if not self._listening():
            return  # 꺼진 엔진. HTTP 2초 시간 초과로 보면 Windows 에서 거절보다 먼저 끝나 대화가 막힌다(2026-10-04).
        try:
            self._request("GET", "/system_stats", timeout=2)
        except ImageGenError as exc:
            cause = exc.__cause__
            if isinstance(cause, urllib.error.URLError) and isinstance(cause.reason, ConnectionRefusedError):
                return  # 연결 거절만 꺼진 엔진으로 본다. 시간 초과/HTTP 오류는 확인 불가다.
            raise
        deadline = time.monotonic() + timeout
        while True:
            queue = self._json("GET", "/queue", timeout=2)
            if (not isinstance(queue, dict) or not isinstance(queue.get("queue_running"), list)
                    or not isinstance(queue.get("queue_pending"), list)):
                raise ImageGenError("그림 엔진의 작업 상태를 확인하지 못해 대화 모델을 실행하지 않았습니다.")
            if queue["queue_running"] or queue["queue_pending"]:
                raise ImageGenError("그림·영상 작업이 진행 중이어서 대화 모델을 실행하지 않았습니다.")
            stats = self._json("GET", "/system_stats", timeout=2)
            devices = stats.get("devices") if isinstance(stats, dict) else None
            if not isinstance(devices, list) or not devices:
                raise ImageGenError("그림 엔진의 GPU 반환 상태를 확인하지 못했습니다.")
            reserved = []
            for device in devices:
                if not isinstance(device, dict) or not isinstance(device.get("type"), str):
                    raise ImageGenError("그림 엔진의 GPU 상태 응답이 올바르지 않습니다.")
                if device["type"] == "cpu":
                    continue
                value = device.get("torch_vram_total")
                if type(value) not in (int, float) or not 0 <= value < float("inf"):
                    raise ImageGenError("그림 엔진의 GPU 예약 메모리를 확인하지 못했습니다.")
                reserved.append(value)
            # CUDA 컨텍스트 자체는 남을 수 있다. 모델/allocator의 큰 예약만 다음 적재 전에 없앤다.
            if all(value <= 64 * 1024 * 1024 for value in reserved):
                return
            if time.monotonic() >= deadline:
                raise ImageGenError("그림 엔진이 GPU 메모리를 반환하지 않아 대화 모델을 실행하지 않았습니다.")
            self._request("POST", "/free", {"unload_models": True, "free_memory": True}, timeout=2)
            time.sleep(0.1)

    def _quiet_post(self, path: str, payload: dict | None = None) -> bool:
        try:
            self._request("POST", path, payload if payload is not None else {}, timeout=10.0)
            return True
        except ImageGenError:
            return False

    def has_wan14(self, files: dict = WAN14_FILES) -> bool:
        """14B 파일 다섯 개(글→영상, files=WAN14_I2V_FILES 면 그림→영상)가 ComfyUI 모델 폴더에 다 있는가.
        ComfyUI 폴더를 모르면 False(→ 5B)."""
        if self.comfy_dir is None:
            return False
        models = self.comfy_dir / "ComfyUI" / "models"
        return all((models / folder / name).is_file() for folder, name in files.values())

    def has_ltx(self) -> bool:
        return self.has_wan14(LTX_FILES)

    def video_plan(self, start_image: bool | Path = False, fhd: bool = False, model: str = "wan") -> VideoPlan:
        """글로만 만들면 14B 글→영상, 그림에서 시작하면 14B 그림→영상(start_image 가 파일이면 그 비율에 맞춘 크기).
        파일이 없으면 5B. 시간은 이 PC 실측(PERFORMANCE 34·35, 그림→영상 14B 는 글→영상 14B 와 같은 화소·단계로 추정)."""
        if model == "ltx" and self.has_ltx():
            # 5초·24fps(8k+1 프레임). FHD(SeedVR2)는 붙이지 않는다.
            # RTX5080 cold starts: T2V 68.4s, I2V about 64s (2026-10-11).
            # Allow 2 minutes for generation; prompt translation is separate.
            width, height = wan14_i2v_size(start_image) if isinstance(start_image, Path) else (1280, 720)
            # The first stage uses half size with a 32-pixel latent stride.
            # Align the final size to 64 so the decoded video matches the plan.
            width, height = max(64, width // 64 * 64), max(64, height // 64 * 64)
            return VideoPlan(LTX_LABEL, width, height, 121, 24, False, 2)
        if start_image and self.has_wan14(WAN14_I2V_FILES):
            width, height = wan14_i2v_size(start_image if isinstance(start_image, Path) else None)
            return VideoPlan(WAN14_LABEL, width, height, 81, 16, fhd, 6 if fhd else 4)
        if not start_image and self.has_wan14():
            return VideoPlan(WAN14_LABEL, 1280, 720, 81, 16, fhd, 6 if fhd else 4)
        return VideoPlan(WAN5_LABEL, 1280, 704, 121, 24, fhd, 8 if fhd else 4)

    def video_workflow(self, prompt: str, seed: int, start_image: str | None = None, fhd: bool = False,
                       plan: VideoPlan | None = None) -> dict:
        """plan 의 모델로 만드는 그래프 — 5B 는 템플릿 video_wan2_2_5B_ti2v(start_image 가 있으면 그 그림에서 시작),
        14B 는 템플릿 video_wan2_2_14B_t2v·video_wan2_2_14B_i2v(Lightning LoRA 켬). fhd 면 같은 그래프 안에서 SeedVR2 3B
        (템플릿 utility_seedvr2_3b_int8_upscale_video)로 1.5배 키운다. 노드 5(긍정 문장)·9(풀어낸 프레임)는 모든 모델이 같다."""
        plan = plan or self.video_plan(bool(start_image), fhd)
        if plan.model == LTX_LABEL:
            graph = self._ltx_graph(prompt, seed, plan, start_image)
        elif plan.model == WAN14_LABEL:
            graph = self._wan14_graph(prompt, seed, plan, start_image)
        else:
            graph = self._wan5_graph(prompt, seed, start_image, plan)
        frames_out = ["9", 0]
        if plan.fhd:
            graph.update({
                "22": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["9", 0], "resize_type": "scale by multiplier",
                                                                       "resize_type.multiplier": 1.5, "scale_method": "lanczos"}},
                "23": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["22", 0]}},
                "24": {"class_type": "VAELoader", "inputs": {"vae_name": "seedvr2_ema_vae_fp16.safetensors"}},
                "25": {"class_type": "UNETLoader", "inputs": {"unet_name": "seedvr2_3b_int8_convrot.safetensors", "weight_dtype": "default"}},
                "26": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["23", 0], "vae": ["24", 0], "tile_size": 512,
                                                                  "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}},
                "27": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["25", 0], "vae_conditioning": ["26", 0]}},
                "28": {"class_type": "KSampler", "inputs": {"model": ["25", 0], "positive": ["27", 0], "negative": ["27", 1],
                                                            "latent_image": ["26", 0], "seed": seed, "steps": 1, "cfg": 1.0,
                                                            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
                "29": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["28", 0], "vae": ["24", 0], "tile_size": 512,
                                                                  "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}},
                "30": {"class_type": "SeedVR2PostProcessing", "inputs": {"images": ["29", 0], "original_resized_images": ["22", 0],
                                                                         "color_correction_method": "none"}},
            })
            frames_out = ["30", 0]
        graph["10"] = {"class_type": "CreateVideo", "inputs": {"images": frames_out, "fps": float(plan.fps)}}
        if plan.model == LTX_LABEL:
            graph["10"]["inputs"]["audio"] = ["62", 0]  # _ltx_graph 의 소리 디코더
        graph["11"] = {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": "alex-video/clip",
                                                             "format": "auto", "codec": "auto"}}
        return graph

    @staticmethod
    def _wan5_graph(prompt: str, seed: int, start_image: str | None, plan: VideoPlan) -> dict:
        graph: dict[str, dict] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan",
                                                          "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
            "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": WAN_NEGATIVE}},
            "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["3", 0], "width": plan.width, "height": plan.height,
                                                                      "length": plan.frames, "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                       "latent_image": ["7", 0], "seed": seed, "steps": 20, "cfg": 5.0,
                                                       "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        }
        if start_image:
            graph["40"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
            graph["7"]["inputs"]["start_image"] = ["40", 0]
        return graph

    @staticmethod
    def _ltx_graph(prompt: str, seed: int, plan: VideoPlan, start_image: str | None = None) -> dict:
        """템플릿 video_ltx2_5_t2v·i2v 의 그래프. 1단계: 반 크기 영상+소리 잠재를 8단계로, 2단계: 영상 잠재를 2배로 키워
        3단계로 다듬는다. start_image 가 있으면 두 단계 모두 첫 프레임에 그 그림을 넣는다(세기 0.7 → 1)."""
        name = {key: file for key, (_, file) in LTX_FILES.items()}
        graph: dict[str, dict] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": name["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": name["clip"], "type": "ltxv", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": name["vae"]}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": name["audio_vae"]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": LTX_NEGATIVE}},
            "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["5", 0], "negative": ["6", 0],
                                                               "frame_rate": float(plan.fps)}},
            "8": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": name["upscaler"]}},
            "43": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": plan.width // 2, "height": plan.height // 2,
                                                                    "length": plan.frames, "batch_size": 1}},
            "45": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"audio_vae": ["4", 0], "frames_number": plan.frames,
                                                                    "frame_rate": plan.fps, "batch_size": 1}},
            "46": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["43", 0], "audio_latent": ["45", 0]}},
            "47": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "48": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
            "49": {"class_type": "ManualSigmas", "inputs": {
                "sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"}},
            "50": {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["1", 0], "positive": ["7", 0],
                                                                 "negative": ["7", 1], "video_cfg": 1.0, "audio_cfg": 1.0}},
            "51": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["47", 0], "guider": ["50", 0],
                                                                     "sampler": ["48", 0], "sigmas": ["49", 0],
                                                                     "latent_image": ["46", 0]}},
            "52": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["51", 0]}},
            "53": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["52", 0], "upscale_model": ["8", 0],
                                                                   "vae": ["3", 0]}},
            "55": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["53", 0], "audio_latent": ["52", 1]}},
            "56": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
            "57": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
            "58": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.85, 0.7250, 0.4219, 0.0"}},
            "59": {"class_type": "LTXVDualCFGGuider", "inputs": {"model": ["1", 0], "positive": ["7", 0],
                                                                 "negative": ["7", 1], "video_cfg": 1.0, "audio_cfg": 1.0}},
            "60": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["56", 0], "guider": ["59", 0],
                                                                     "sampler": ["57", 0], "sigmas": ["58", 0],
                                                                     "latent_image": ["55", 0]}},
            "61": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["60", 0]}},
            "9": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["61", 0], "vae": ["3", 0], "tile_size": 512,
                                                             "overlap": 64, "temporal_size": 64, "temporal_overlap": 16}},
            "62": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["61", 1], "audio_vae": ["4", 0]}},
        }
        if start_image:
            graph.update({
                "40": {"class_type": "LoadImage", "inputs": {"image": start_image}},
                "41": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["40", 0], "resize_type": "scale longer dimension",
                                                                       "resize_type.longer_size": 1536, "scale_method": "lanczos"}},
                "42": {"class_type": "LTXVPreprocess", "inputs": {"image": ["41", 0], "img_compression": 18}},
                "44": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["3", 0], "image": ["42", 0],
                                                                         "latent": ["43", 0], "strength": 0.7, "bypass": False}},
                "54": {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["3", 0], "image": ["42", 0],
                                                                         "latent": ["53", 0], "strength": 1.0, "bypass": False}},
            })
            graph["46"]["inputs"]["video_latent"] = ["44", 0]
            graph["55"]["inputs"]["video_latent"] = ["54", 0]
        return graph

    @staticmethod
    def _wan14_graph(prompt: str, seed: int, plan: VideoPlan, start_image: str | None = None) -> dict:
        """high noise 모델(+LoRA)이 4단계 중 0~2, low noise 모델(+LoRA)이 남은 노이즈를 받아 2~4 를 맡는다.
        start_image 가 있으면 I2V 모델과 WanImageToVideo(그림·문장을 조건으로 묶고 빈 잠재를 만든다)를 쓴다."""
        name = {key: file for key, (_, file) in (WAN14_I2V_FILES if start_image else WAN14_FILES).items()}
        sampler = {"positive": ["5", 0], "negative": ["6", 0], "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                   "scheduler": "simple"}
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": name["high"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan",
                                                          "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": name["vae"]}},
            "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["13", 0], "shift": 5.0}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": WAN_NEGATIVE}},
            "7": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": plan.width, "height": plan.height,
                                                                      "length": plan.frames, "batch_size": 1}},
            "8": {"class_type": "KSamplerAdvanced", "inputs": {
                **sampler, "model": ["4", 0], "latent_image": ["7", 0], "add_noise": "enable", "noise_seed": seed,
                "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable"}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["3", 0]}},
            "12": {"class_type": "UNETLoader", "inputs": {"unet_name": name["low"], "weight_dtype": "default"}},
            "13": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": name["high_lora"],
                                                                   "strength_model": 1.0}},
            "14": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["12", 0], "lora_name": name["low_lora"],
                                                                   "strength_model": 1.0}},
            "15": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["14", 0], "shift": 5.0}},
            "16": {"class_type": "KSamplerAdvanced", "inputs": {
                **sampler, "model": ["15", 0], "latent_image": ["8", 0], "add_noise": "disable", "noise_seed": 0,
                "start_at_step": 2, "end_at_step": 4, "return_with_leftover_noise": "disable"}},
        }
        if start_image:
            graph["40"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
            graph["7"] = {"class_type": "WanImageToVideo", "inputs": {
                "positive": ["5", 0], "negative": ["6", 0], "vae": ["3", 0], "start_image": ["40", 0],
                "width": plan.width, "height": plan.height, "length": plan.frames, "batch_size": 1}}
            for node in ("8", "16"):
                graph[node]["inputs"].update({"positive": ["7", 0], "negative": ["7", 1]})
            graph["8"]["inputs"]["latent_image"] = ["7", 2]
        return graph

    def generate_video(self, prompt: str, seed: int | None = None, start_image: Path | None = None, fhd: bool = False,
                       cancel_event: threading.Event | None = None, progress=None, timeout: float = 3600.0,
                       model: str = "wan") -> VideoResult:
        if not prompt.strip():
            raise ImageGenError("어떤 영상을 만들지 알려 주세요. 예: '고양이가 뛰어노는 영상 만들어줘'")
        with self._lock:
            started = time.monotonic()
            self.ensure_running()
            seed = random.randrange(2**32) if seed is None else int(seed)
            plan = self.video_plan(start_image if start_image is not None else False, fhd, model)
            name = self.upload_image(start_image) if start_image else None
            reply = self._json("POST", "/prompt", {"prompt": self.video_workflow(prompt, seed, name, fhd, plan),
                                                   "client_id": self.client_id}, timeout=30.0)
            if reply.get("node_errors"):
                raise ImageGenError(f"ComfyUI 워크플로 오류: {json.dumps(reply['node_errors'], ensure_ascii=False)[:400]}")
            prompt_id = str(reply.get("prompt_id") or "")
            videos: list[dict] = []
            next_note = started + 60
            while not videos:
                if cancel_event is not None and cancel_event.is_set():
                    self._quiet_post("/interrupt")
                    raise GenerationCancelled("")
                entry = self._json("GET", f"/history/{urllib.parse.quote(prompt_id)}").get(prompt_id) or {}
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ImageGenError("ComfyUI 가 영상을 만들다 실패했습니다: " + _status_message(status))
                videos = [item for output in (entry.get("outputs") or {}).values() for key in ("images", "gifs", "videos")
                          for item in (output.get(key) or []) if isinstance(item, dict)
                          and str(item.get("filename", "")).endswith(".mp4") and item.get("type") == "output"]
                if not videos:
                    if time.monotonic() - started > timeout:
                        self._quiet_post("/interrupt")
                        raise ImageGenError(f"{timeout / 60:.0f}분 안에 영상을 만들지 못했습니다.")
                    if progress is not None and time.monotonic() >= next_note:
                        progress(f"[영상 만드는 중… {int(time.monotonic() - started) // 60}분 지남]\n")
                        next_note += 60
                    time.sleep(self.poll_seconds * 4)
            item = videos[-1]
            query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": "output"})
            data = self._request("GET", f"/view?{query}", timeout=120.0)
            if b"ftyp" not in data[:16]:
                raise ImageGenError("ComfyUI 가 MP4 가 아닌 것을 돌려줬습니다.")
            folder = self.out_dir.parent / "videos"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{seed}.mp4"
            path.write_bytes(data)
            width, height = plan.size
            return VideoResult(path, prompt, seed, width, height, round(time.monotonic() - started, 1), plan.fhd, plan.model)

    def video_path(self, name: str) -> Path | None:
        if not VIDEO_NAME.match(name or ""):
            return None
        path = self.out_dir.parent / "videos" / name
        return path if path.is_file() else None

    def store_picture(self, data: bytes) -> Path:
        """사용자가 올린 참고 그림을 앱이 그린 그림과 같은 곳·같은 이름 모양으로 저장한다(종류는 첫 바이트로 판단)."""
        kind = image_kind(data)
        if kind is None:
            raise ValueError("PNG·JPEG·WebP 그림만 올릴 수 있습니다.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("그림이 너무 큽니다(10MB 이하).")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{random.randrange(10**9)}{kind}"
        path.write_bytes(data)
        return path

    def image_path(self, name: str) -> Path | None:
        """화면이 요청한 이름의 파일 — 앱이 만든 모양의 이름이고 out_dir 바로 아래일 때만."""
        if not IMAGE_NAME.match(name or ""):
            return None
        path = self.out_dir / name
        return path if path.is_file() else None


def _status_message(status: dict) -> str:
    for kind, data in reversed(status.get("messages") or []):
        if kind == "execution_error" and isinstance(data, dict):
            return f"{data.get('node_type', '')}: {data.get('exception_message', '')}".strip(": ")[:400]
    return "원인 미상"
