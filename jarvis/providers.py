"""LLM provider abstraction, mock providers and the first real local provider.

Study focus:
- 추상 클래스(ABC)와 contract
- 예외 정의와 예외 변환
- 표준 라이브러리만으로 HTTP 호출하기 (urllib)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import time
import threading
from typing import Callable, Iterator, Sequence
import urllib.error
import urllib.request
from .http_control import interrupt_on_cancel


class ProviderError(RuntimeError):
    """Provider 호출이 실패했을 때 Core가 잡을 수 있는 단일 예외."""


class ContextExhausted(ProviderError):
    """본문을 한 글자도 쓰기 전에 한도에 닿았다 — 대개 긴 대화가 문맥 창을 채우고 남은 자리를 생각이 다 썼다.

    2026-09-25 데스크톱: 프롬프트 7830 토큰 / 문맥 창 8192 에서 qwen3.8:27b 가 두 번 다 빈 답을 냈고,
    앱은 "모델이 없다, Ollama 서버와 모델 tag 를 확인하라" 고 안내했다. 원인과 다른 말이었다.
    """


class ThinkingTooLong(ContextExhausted):
    """생각이 답 쓸 자리까지 먹기 전에 끊었다. 받는 쪽은 ContextExhausted 처럼 생각 없이 다시 부른다.

    2026-10-05 테트리스 대화: 대화 8,658토큰 + 생각 7,722토큰으로 16K 문맥이 차서 답이 없었다(Q3 61초, Q4 면 7분).
    한도까지 다 생각하게 두면 시간만 쓰고 결국 빈 답이다 — 답 자리를 남기는 선에서 미리 끊는다.
    """


class ToolsUnsupported(ProviderError):
    """모델이 도구 호출을 모른다(Ollama 400 "does not support tools"). 도구 없이 다시 부른다."""


class GenerationCancelled(ProviderError):
    """Explicit local cancellation, never a reason to try another provider."""

    def __init__(self, partial: str = "") -> None:
        self.partial = partial
        super().__init__("응답 생성을 중단했습니다.")


# 한 답에서 모델의 도구 호출을 실행해 주는 차례 수(ADR-056). 그다음 차례에도 도구를 부르면 끊는다.
# 검색 횟수 자체는 앱의 runner 가 따로 막는다(한 질문에 두 번) — 세 번째 차례는 "그만 찾고 답하라" 를 받는다.
MAX_TOOL_ROUNDS = 3


@dataclass(slots=True)
class Message:
    role: str
    content: str
    # 도구 호출 루프(ADR-056)에서만 쓴다: 모델이 부른 도구(assistant), 그 결과를 돌려주는 tool 메시지의 도구 이름.
    tool_calls: list | None = None
    tool_name: str = ""


class BaseLLMProvider(ABC):
    """Small provider contract. 모든 Provider는 이 계약만 지키면 된다."""

    name = "base"

    @abstractmethod
    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        raise NotImplementedError

    def generate_stream(self, messages: Sequence[Message], **kwargs: object) -> Iterator[str]:
        """Optional streaming contract. Providers without native streaming yield one full chunk."""
        yield self.generate(messages, **kwargs)

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError


class MockLLMProvider(BaseLLMProvider):
    """Offline provider used to validate architecture before real model hookup."""

    def __init__(self, name: str, prefix: str, healthy: bool = True) -> None:
        self.name = name
        self.prefix = prefix
        self._healthy = healthy

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        latest = messages[-1].content if messages else ""
        return f"[{self.prefix}] 요청을 받았습니다: {latest}"

    def health_check(self) -> bool:
        return self._healthy


class CloudStubProvider(MockLLMProvider):
    """Cloud placeholder. 실제 외부 서비스를 호출하지 않는다."""

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        latest = messages[-1].content if messages else ""
        return (
            f"[{self.prefix}] 이 요청은 클라우드 모델 대상입니다. "
            f"아직 실제 전송하지 않습니다: {latest}"
        )


class FailingProvider(BaseLLMProvider):
    """테스트/데모용. 항상 실패하는 Provider."""

    def __init__(self, name: str = "failing") -> None:
        self.name = name

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        raise ProviderError(f"{self.name} 은(는) 현재 사용할 수 없습니다.")

    def health_check(self) -> bool:
        return False


class OllamaProvider(BaseLLMProvider):
    """OpenAI-compatible 대신 Ollama native /api/chat 를 사용하는 Local Provider.

    - 표준 라이브러리(urllib)만 사용한다. requests 의존성을 만들지 않는다.
    - 모델 이름은 config 에서 주입받는다. 코드에 박지 않는다.
    - num_ctx 는 Hardware Profile 에 따라 달라진다. (Laptop 작게 / Desktop 크게)
    """

    def __init__(
        self,
        model: str,
        host: str = "http://127.0.0.1:11434",
        timeout: int = 120,
        num_ctx: int = 4096,
        name: str | None = None,
        health_ttl: float = 30.0,
        think: bool = False,
        num_predict: int = 1024,
        num_gpu: int = 0,
        keep_alive: str = "10m",
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.name = name or f"ollama:{model}"
        self.health_ttl = max(0.0, float(health_ttl))
        self.think = bool(think)
        self.num_predict = max(1, int(num_predict))
        # 0 이면 Ollama 자동 배치. 실측으로 자동이 VRAM 을 남기는 것이 확인되면 직접 올린다.
        self.num_gpu = max(0, int(num_gpu))
        self.keep_alive = str(keep_alive).strip() or "10m"
        self._last_health_at = 0.0
        self._last_health = False
        self.last_metrics: dict = {}
        # 마지막 스트림에서 모델이 부른 도구들(ADR-056). 내용과 따로 모은다.
        self.last_tool_calls: list[dict] = []
        # 한 번 "도구를 모른다" 고 답한 모델에는 다시 도구를 주지 않는다.
        self._tools_unsupported = False
        self._thinking_unsupported = False
        self.cancel_event: threading.Event | None = None
        self.before_generate: Callable[[], None] | None = None

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise GenerationCancelled()

    def _post_json(self, path: str, payload: dict) -> dict:
        if path == "/api/chat" and self.before_generate is not None:
            self.before_generate()
        request = urllib.request.Request(
            url=f"{self.host}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            if self._disable_unsupported_thinking(exc.code, detail, payload):
                retry = dict(payload)
                retry.pop("think", None)
                return self._post_json(path, retry)
            raise ProviderError(f"Ollama HTTP {exc.code} 오류: {self.host}{path}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Ollama 서버에 연결할 수 없습니다: {self.host} ({exc.reason})") from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderError(f"Ollama 응답 시간 초과 또는 네트워크 오류: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama 응답을 JSON 으로 해석하지 못했습니다.") from exc

    @staticmethod
    def _http_error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read(8192).decode("utf-8", "replace")
        except OSError:
            return ""
        finally:
            exc.close()

    def _disable_unsupported_thinking(self, status: int, detail: str, payload: dict) -> bool:
        # Only a definitive pre-generation rejection permits this single retry.
        # Keep other models' thinking settings and ordinary HTTP failures intact.
        if (status == 400 and "think" in payload and not self._thinking_unsupported
                and "does not support thinking" in detail):
            self._thinking_unsupported = True
            return True
        return False

    @staticmethod
    def _wire(message: Message) -> dict:
        item = {"role": message.role, "content": message.content}
        if message.tool_calls:
            item["tool_calls"] = message.tool_calls
        if message.tool_name:
            item["tool_name"] = message.tool_name
        return item

    def _thinking_budget(self, messages: Sequence[Message]) -> int:
        """답 자리(출력 한도의 1/3, 최소 1,024토큰)를 남기는 생각 상한. 프롬프트는 글자 2.5자를 1토큰으로 넉넉히 어림한다
        (2026-10-05 실측: 대화 24,000자 = 8,658토큰, 2.8자/토큰)."""
        reserve = max(1024, self.num_predict // 3)
        budget = self.num_predict - reserve
        if self.num_ctx:
            prompt = sum(len(m.content) for m in messages) * 2 // 5
            budget = min(budget, self.num_ctx - prompt - reserve)
        return max(1024, budget)

    def _chat_payload(self, messages: Sequence[Message], *, stream: bool, tools: list | None = None) -> dict:
        if not messages:
            raise ProviderError("보낼 메시지가 비어 있습니다.")
        payload = {
            "model": self.model,
            "messages": [self._wire(m) for m in messages],
            "stream": stream,
            "think": self.think,
            "keep_alive": self.keep_alive,
            "options": self._options(),
        }
        if tools:
            payload["tools"] = tools
        if self._thinking_unsupported:
            payload.pop("think", None)
        return payload

    def generate_stream(self, messages: Sequence[Message], **kwargs: object) -> Iterator[str]:
        """Yield Ollama `/api/chat` NDJSON chunks while preserving the same provider contract."""
        self._check_cancelled()
        if self.before_generate is not None:
            self.before_generate()
        self.last_metrics = {}
        self.last_tool_calls = []
        started = time.monotonic()
        first = None
        completed = False
        tools = kwargs.get("tools")
        payload = self._chat_payload(messages, stream=True, tools=tools if isinstance(tools, list) else None)
        budget = self._thinking_budget(messages) if payload.get("think") else 0
        thought = 0
        request = urllib.request.Request(
            url=f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response, interrupt_on_cancel(response, self.cancel_event):
                for raw_line in response:
                    self._check_cancelled()
                    if not raw_line.strip():
                        continue
                    try:
                        data = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ProviderError("Ollama streaming 응답을 해석하지 못했습니다.") from exc
                    if not isinstance(data, dict):
                        raise ProviderError("Ollama streaming 응답 형식 오류")
                    if data.get("error"):
                        raise ProviderError(f"Ollama 오류: {data['error']}")
                    message = data.get("message") or {}
                    if not isinstance(message, dict):
                        raise ProviderError("Ollama message 형식 오류")
                    calls = message.get("tool_calls")
                    if calls:
                        if not isinstance(calls, list):
                            raise ProviderError("Ollama tool_calls 형식 오류")
                        self.last_tool_calls.extend(c for c in calls if isinstance(c, dict))
                    thinking = message.get("thinking")
                    if budget and first is None and isinstance(thinking, str) and thinking:
                        thought += max(1, len(thinking) // 4)  # 조각 하나가 대개 1토큰, 영어 4자 ≈ 1토큰
                        if thought > budget:
                            raise ThinkingTooLong(
                                f"생각이 약 {budget}토큰을 넘어 답 쓸 자리를 지키려고 끊었습니다(문맥 창 {self.num_ctx}, "
                                f"출력 한도 {self.num_predict}).")
                    content = message.get("content")
                    if content:
                        if not isinstance(content, str):
                            raise ProviderError("Ollama content 형식 오류")
                        if first is None:
                            first = time.monotonic() - started
                        yield str(content)
                    if data.get("done") is True:
                        completed = True
                        duration = data.get("eval_duration", 0)
                        count = data.get("eval_count", 0)
                        self.last_metrics = {
                            "first_token_seconds": round(first, 3) if first is not None else None,
                            "total_seconds": round(time.monotonic() - started, 3),
                            "output_tokens": count,
                            "tokens_per_second": round(count / (duration / 1e9), 2) if isinstance(duration, (int, float)) and duration > 0 and isinstance(count, (int, float)) else None,
                            "done_reason": data.get("done_reason", "stop"),
                            "prompt_tokens": data.get("prompt_eval_count"),
                        }
                        break
                self._check_cancelled()
                if not completed:
                    raise ProviderError("Ollama 응답 연결이 완료 전에 끊겼습니다. 다시 시도해 주세요.")
        except urllib.error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            if self._disable_unsupported_thinking(exc.code, detail, payload):
                yield from self.generate_stream(messages, **kwargs)
                return
            if payload.get("tools") and exc.code == 400:
                if "does not support tools" in detail:
                    raise ToolsUnsupported(f"{self.model} 은(는) 도구 호출을 지원하지 않습니다.") from exc
            raise ProviderError(f"Ollama HTTP {exc.code} 오류: {self.host}/api/chat") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Ollama 서버에 연결할 수 없습니다: {self.host} ({exc.reason})") from exc
        except (TimeoutError, OSError) as exc:
            self._check_cancelled()
            raise ProviderError(f"Ollama 응답 시간 초과 또는 네트워크 오류: {exc}") from exc

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        callback = kwargs.get("on_chunk")
        tools = kwargs.get("tools")
        runner = kwargs.get("tool_runner")
        if isinstance(tools, list) and tools and callable(runner) and not self._tools_unsupported:
            return self._generate_with_tools(messages, tools, runner, callback)
        if callable(callback) or self.cancel_event is not None:
            return self._final_content(self._collect_stream(messages, callback))

        payload = self._chat_payload(messages, stream=False)
        data = self._post_json("/api/chat", payload)
        self.last_metrics = ({"done_reason": data.get("done_reason", "stop"),
                              "prompt_tokens": data.get("prompt_eval_count")} if isinstance(data, dict) else {})
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise ProviderError("Ollama 응답 형식 오류")
        content = (data.get("message") or {}).get("content")
        if not content:
            self._raise_if_exhausted()
            raise ProviderError(f"Ollama 응답에 content 가 없습니다: {str(data)[:200]}")
        return str(content).strip()

    def _collect_stream(self, messages: Sequence[Message], callback: object, tools: list | None = None) -> str:
        """스트림을 모아 돌려주면서 조각을 화면(callback)에 흘린다. 중단되면 받은 만큼 GenerationCancelled 로."""
        parts: list[str] = []
        callback_active = True
        try:
            for chunk in self.generate_stream(messages, tools=tools):
                parts.append(chunk)
                if callback_active and callable(callback):
                    try:
                        callback(chunk)
                    except Exception:
                        callback_active = False
        except GenerationCancelled as exc:
            raise GenerationCancelled("".join(parts)) from exc
        return "".join(parts)

    def _final_content(self, content: str) -> str:
        content = content.strip()
        if not content:
            self._raise_if_exhausted()
            raise ProviderError("Ollama streaming 응답에 content 가 없습니다.")
        return content

    def _generate_with_tools(self, messages: Sequence[Message], tools: list, runner, callback: object) -> str:
        """모델이 도구를 부르면 앱(runner)이 실행하고, 결과를 tool 메시지로 돌려준 뒤 다시 묻는다(ADR-056).

        도구는 매 차례 계속 준다. 처음에는 마지막 차례에 도구를 빼서 답을 받으려 했는데, gpt-oss 는 도구를 뺀
        차례에도 도구를 부르며 본문을 비웠다(2026-09-25, `logs/probe/tool_final_round_probe.py`). 도구를 둔 채로는
        답했다. 그래서 검색 횟수는 runner 가 "그만 찾고 답하라" 로 막고, 여기서는 차례 수만 MAX_TOOL_ROUNDS 로 끊는다.
        도구를 모르는 모델이면 이 provider 로는 이후 도구를 주지 않는다.
        """
        conversation = list(messages)
        content = ""
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            try:
                content = self._collect_stream(conversation, callback, tools=tools)
            except ToolsUnsupported:
                self._tools_unsupported = True
                return self._final_content(self._collect_stream(conversation, callback))
            calls = list(self.last_tool_calls)
            if not calls:
                return self._final_content(content)
            if round_number == MAX_TOOL_ROUNDS:
                break
            conversation.append(Message("assistant", content, tool_calls=calls))
            for call in calls:
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(function.get("name", ""))
                arguments = function.get("arguments")
                if isinstance(arguments, str):  # 인자를 JSON 문자열로 주는 모델도 있다
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                result = runner(name, arguments if isinstance(arguments, dict) else {})
                conversation.append(Message("tool", str(result), tool_name=name))
        if content.strip():
            return content.strip()
        raise ProviderError("모델이 검색만 되풀이하고 답을 쓰지 않았습니다.")

    def _raise_if_exhausted(self) -> None:
        """빈 답이 한도 때문이면 무엇이 찼는지 말한다(ContextExhausted)."""
        metrics = self.last_metrics if isinstance(self.last_metrics, dict) else {}
        if metrics.get("done_reason") != "length":
            return
        prompt = metrics.get("prompt_tokens")
        if isinstance(prompt, int) and self.num_ctx and prompt + self.num_predict > self.num_ctx:
            raise ContextExhausted(
                f"답을 쓰기 전에 문맥 창이 찼습니다 — 대화 {prompt}토큰이 문맥 창 {self.num_ctx} 의 대부분을 "
                f"차지해, 남은 {max(0, self.num_ctx - prompt)}토큰을 생각(thinking)에 다 썼습니다.")
        raise ContextExhausted(
            f"답을 쓰기 전에 출력 한도 {self.num_predict}토큰을 생각(thinking)에 다 썼습니다.")

    def _fetch_installed_models(self) -> set[str]:
        request = urllib.request.Request(f"{self.host}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return set()
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return set()
        return {str(item.get("name", "")) for item in data["models"] if isinstance(item, dict) and item.get("name")}

    def _fetch_loaded_models(self) -> set[str]:
        """현재 메모리에 로드된(실행 중인) 모델 태그 목록을 반환합니다."""
        request = urllib.request.Request(f"{self.host}/api/ps", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return set()
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return set()
        return {str(item.get("name") or item.get("model") or "") for item in data["models"] if isinstance(item, dict) and (item.get("name") or item.get("model"))}


    def _requested_model_is_installed(self, installed: set[str]) -> bool:
        """요청한 tag 를 정확히 확인한다.

        v0.1.1 은 ``qwen2.5:7b`` 를 요청해도 ``qwen2.5:14b`` 가 있으면
        같은 base name 이라는 이유로 healthy 로 판단했다. 모델 크기/quant 가 다른 것은
        실제로 다른 runtime 자원이므로 colon 이 있는 요청은 exact match 만 허용한다.
        tag 를 아예 생략한 경우만 ``:latest`` 를 편의상 허용한다.
        """
        if ":" in self.model:
            return self.model in installed
        return self.model in installed or f"{self.model}:latest" in installed

    def _options(self) -> dict:
        """Ollama 에 보낼 옵션. num_gpu 는 0 이면 아예 보내지 않아 자동 배치를 유지한다."""
        options = {"num_ctx": self.num_ctx, "num_predict": self.num_predict}
        if self.num_gpu:
            options["num_gpu"] = self.num_gpu
        return options

    def placement(self) -> dict | None:
        """지금 로드된 이 모델이 GPU/RAM 에 어떻게 나뉘어 있는지 실측으로 읽는다.

        추정하지 않는다. Ollama 의 ``/api/ps`` 가 ``size``(전체)와 ``size_vram``(GPU 몫)을
        준다. 차이가 RAM 으로 넘어간 양이다. 모델이 로드돼 있지 않으면 ``None`` 이다.

        이게 필요한 이유: 2026-09-13 에 모델을 9b 로 바꾸면서 num_ctx 8192 가 그대로 남아
        층의 12% 가 RAM 으로 넘어갔고, **아무 경고도 없이 벽시계가 6.6배 느려졌다.**
        `ollama ps` 를 직접 치지 않으면 알 수 없었다.
        """
        request = urllib.request.Request(f"{self.host}/api/ps", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return None
        for item in data["models"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "")
            if name != self.model and not (":" not in self.model and name.startswith(f"{self.model}:")):
                continue
            total = int(item.get("size") or 0)
            vram = int(item.get("size_vram") or 0)
            if total <= 0:
                return None
            return {"model": name, "total_bytes": total, "vram_bytes": vram,
                    "ram_bytes": max(0, total - vram)}
        return None

    def health_check(self) -> bool:
        """서버 + 정확한 모델 tag 상태를 짧게 cache 해서 매 요청의 /api/tags 호출을 줄인다."""
        now = time.monotonic()
        if self.health_ttl > 0 and (now - self._last_health_at) < self.health_ttl:
            return self._last_health

        installed = self._fetch_installed_models()
        result = bool(installed) and self._requested_model_is_installed(installed)
        self._last_health = result
        self._last_health_at = now
        return result



def unload_ollama_models(host: str, timeout: float = 15) -> list[str]:
    """Ollama 에 올라간 모델을 지금 모두 내려 그래픽카드를 비운다(keep_alive 0) — `ollama stop` 과 같은 동작.

    2026-09-30 사용자: "로컬ai를 돌리면서 게임하거나 그런건 안된다는거네" — 앱은 마지막 질문 10분 뒤에야 모델을 내린다.
    참고: Ollama FAQ(https://docs.ollama.com/faq) 'keep_alive 0 이면 바로 내린다'. 다음 질문 때 다시 올라온다(수십 초).
    """
    def call(path: str, payload: dict | None) -> dict:
        request = urllib.request.Request(f"{host}{path}", data=None if payload is None else json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="GET" if payload is None else "POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        return json.loads(body.decode("utf-8")) if body.strip() else {}

    try:
        names = _loaded_names(call("/api/ps", None))
        for name in names:
            call("/api/generate", {"model": name, "keep_alive": 0})
    except urllib.error.URLError as exc:
        raise ProviderError(f"Ollama 서버에 연결할 수 없습니다: {host} ({exc.reason})") from exc
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"모델을 내리지 못했습니다: {exc}") from exc
    return names


def _loaded_names(ps: dict) -> list[str]:
    # 비정상 응답을 빈 모델 목록으로 해석하면 GPU를 반환하지 못해도 다음 엔진을 켜게 된다.
    if not isinstance(ps, dict) or ps.get("error") or not isinstance(ps.get("models"), list):
        raise ProviderError("Ollama 모델 상태 응답이 올바르지 않아 GPU 반환을 확인할 수 없습니다.")
    names = []
    for item in ps["models"]:
        name = (item.get("name") or item.get("model")) if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ProviderError("Ollama 모델 상태에 이름이 없는 항목이 있습니다.")
        names.append(name)
    return names


def loaded_ollama_models(host: str, timeout: float = 10) -> list[str]:
    """지금 Ollama 에 올라가 있는 모델 이름(/api/ps). 내린 뒤 확인용 — 답을 만드는 중이면 끝난 뒤에야 내려간다."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=timeout) as response:
            body = response.read()
        return _loaded_names(json.loads(body.decode("utf-8")) if body.strip() else {})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"Ollama 상태를 확인하지 못했습니다: {exc}") from exc


class StrataProvider(OllamaProvider):
    """이 PC 의 Strata 서버로 Qwen3.8-Flash-Next(125B MoE)를 쓰는 Local Provider (2026-10-10, ADR-086).

    Strata(github.com/Niko1221/Strata)는 전문가 가중치를 RAM 에 두고 자주 쓰는 것만 그래픽카드에 올려 16GB 로 125B 를 돌린다.
    API 는 OpenAI 호환 `/v1/chat/completions`(SSE 스트림, 생각은 `reasoning_content`). 도구 호출 루프·생각 상한·중단·빈 답 판정은
    OllamaProvider 의 것을 그대로 쓰고, 통신 형식만 바꾼다. 주소는 이 PC(loopback)만 — config 가 검사한다.
    `num_gpu`·`keep_alive` 는 Strata 가 스스로 정하므로 쓰지 않는다(Ollama 와 같은 생성자 모양을 지키려고 받기만 한다).
    """

    def __init__(self, model: str, host: str = "http://127.0.0.1:8080", timeout: int = 120, num_ctx: int = 65536,
                 name: str | None = None, health_ttl: float = 30.0, think: bool = False, num_predict: int = 1024,
                 num_gpu: int = 0, keep_alive: str = "10m", reasoning_effort: str = "medium") -> None:
        super().__init__(model, host=host, timeout=timeout, num_ctx=num_ctx, name=name or f"strata:{model}",
                         health_ttl=health_ttl, think=think, num_predict=num_predict, num_gpu=num_gpu,
                         keep_alive=keep_alive)
        self.reasoning_effort = reasoning_effort

    # ── 통신 형식 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _wire_all(messages: Sequence[Message]) -> list[dict]:
        """ALEX 메시지 → OpenAI 형식. 도구 결과(tool)에는 바로 앞 assistant 가 부른 같은 이름 도구의 id 를 붙인다."""
        wired: list[dict] = []
        pending: list[tuple[str, str]] = []  # (도구 이름, id) — 아직 결과가 안 온 호출
        for message in messages:
            item: dict = {"role": message.role, "content": message.content}
            if message.tool_calls:
                calls = []
                for index, call in enumerate(message.tool_calls):
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    arguments = function.get("arguments", {})
                    call_id = str(call.get("id") or f"call_{len(wired)}_{index}")
                    calls.append({"id": call_id, "type": "function",
                                  "function": {"name": str(function.get("name", "")),
                                               "arguments": arguments if isinstance(arguments, str)
                                               else json.dumps(arguments, ensure_ascii=False)}})
                    pending.append((str(function.get("name", "")), call_id))
                item["tool_calls"] = calls
            if message.role == "tool":
                match = next((p for p in pending if p[0] == message.tool_name), pending[0] if pending else None)
                if match is not None:
                    pending.remove(match)
                    item["tool_call_id"] = match[1]
                if message.tool_name:
                    item["name"] = message.tool_name
            wired.append(item)
        return wired

    def _chat_payload(self, messages: Sequence[Message], *, stream: bool, tools: list | None = None) -> dict:
        if not messages:
            raise ProviderError("보낼 메시지가 비어 있습니다.")
        payload = {
            "model": self.model,
            "messages": self._wire_all(messages),
            "stream": stream,
            "max_tokens": self.num_predict,
            "reasoning_effort": self.reasoning_effort if self.think else "none",
        }
        if tools:
            payload["tools"] = tools
        return payload

    def _request(self, path: str, payload: dict | None, timeout: float) -> urllib.request.Request:
        return urllib.request.Request(
            url=f"{self.host}{path}", data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="GET" if payload is None else "POST")

    def _strata_http_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        detail = self._http_error_detail(exc)
        try:
            message = str((json.loads(detail).get("error") or {}).get("message") or "")
        except (ValueError, AttributeError):
            message = ""
        if exc.code == 400 and "context" in message:
            return ContextExhausted(f"대화가 Strata 문맥 창({self.num_ctx}토큰)을 넘었습니다. 새 대화로 시작해 주세요. ({message[:200]})")
        if exc.code == 503:
            return ProviderError(f"Strata 가 지금 그래픽카드를 쓰지 못합니다(다른 프로그램이 사용 중일 수 있음): {message[:200]}")
        return ProviderError(f"Strata HTTP {exc.code} 오류: {message[:200] or self.host}")

    def generate_stream(self, messages: Sequence[Message], **kwargs: object) -> Iterator[str]:
        """`/v1/chat/completions` SSE 를 읽어 답 조각을 낸다. 생각·도구 호출은 OllamaProvider 와 같은 규칙으로 다룬다."""
        self._check_cancelled()
        if self.before_generate is not None:
            self.before_generate()
        self.last_metrics = {}
        self.last_tool_calls = []
        started = time.monotonic()
        first = None
        completed = False
        tools = kwargs.get("tools")
        payload = self._chat_payload(messages, stream=True, tools=tools if isinstance(tools, list) else None)
        budget = self._thinking_budget(messages) if self.think else 0
        thought = 0
        calls: dict[int, dict] = {}
        request = self._request("/v1/chat/completions", payload, self.timeout)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response, interrupt_on_cancel(response, self.cancel_event):
                for raw_line in response:
                    self._check_cancelled()
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue  # 빈 줄과 ': keep-alive' 주석(프롬프트를 읽는 동안)
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise ProviderError("Strata streaming 응답을 해석하지 못했습니다.") from exc
                    if not isinstance(data, dict):
                        raise ProviderError("Strata streaming 응답 형식 오류")
                    if data.get("error"):
                        error = data["error"]
                        raise ProviderError(f"Strata 오류: {error.get('message') if isinstance(error, dict) else error}")
                    choices = data.get("choices") or [{}]
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    for piece in delta.get("tool_calls") or []:
                        if not isinstance(piece, dict):
                            continue
                        slot = calls.setdefault(int(piece.get("index") or 0), {"id": "", "name": "", "arguments": ""})
                        function = piece.get("function") if isinstance(piece.get("function"), dict) else {}
                        slot["id"] = piece.get("id") or slot["id"]
                        slot["name"] = function.get("name") or slot["name"]
                        slot["arguments"] += function.get("arguments") or ""
                    thinking = delta.get("reasoning_content")
                    if budget and first is None and isinstance(thinking, str) and thinking:
                        thought += max(1, len(thinking) // 4)
                        if thought > budget:
                            raise ThinkingTooLong(
                                f"생각이 약 {budget}토큰을 넘어 답 쓸 자리를 지키려고 끊었습니다(문맥 창 {self.num_ctx}, "
                                f"출력 한도 {self.num_predict}).")
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        if first is None:
                            first = time.monotonic() - started
                        yield content
                    finish = choice.get("finish_reason")
                    if finish:
                        completed = True
                        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                        total = time.monotonic() - started
                        count = usage.get("completion_tokens")
                        writing = total - (first or 0.0)
                        self.last_metrics = {
                            "first_token_seconds": round(first, 3) if first is not None else None,
                            "total_seconds": round(total, 3),
                            "output_tokens": count,
                            "tokens_per_second": round(count / writing, 2) if isinstance(count, int) and writing > 0 else None,
                            "done_reason": "length" if finish == "length" else "stop",
                            "prompt_tokens": usage.get("prompt_tokens"),
                        }
                self._check_cancelled()
                if not completed:
                    raise ProviderError("Strata 응답 연결이 완료 전에 끊겼습니다. 다시 시도해 주세요.")
        except urllib.error.HTTPError as exc:
            raise self._strata_http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Strata 서버에 연결할 수 없습니다: {self.host} ({exc.reason}) — Strata 가 켜져 있는지 확인하세요.") from exc
        except (TimeoutError, OSError) as exc:
            self._check_cancelled()
            raise ProviderError(f"Strata 응답 시간 초과 또는 네트워크 오류: {exc}") from exc
        for index in sorted(calls):
            call = calls[index]
            try:
                arguments = json.loads(call["arguments"]) if call["arguments"].strip() else {}
            except json.JSONDecodeError:
                arguments = {}
            self.last_tool_calls.append({"id": call["id"], "function": {"name": call["name"], "arguments": arguments}})

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        callback = kwargs.get("on_chunk")
        tools = kwargs.get("tools")
        runner = kwargs.get("tool_runner")
        if isinstance(tools, list) and tools and callable(runner) and not self._tools_unsupported:
            return self._generate_with_tools(messages, tools, runner, callback)
        return self._final_content(self._collect_stream(messages, callback))

    # ── 상태·그래픽카드 ─────────────────────────────────────────────────────────
    def _health(self, timeout: float = 5) -> dict | None:
        try:
            with urllib.request.urlopen(self._request("/health", None, timeout), timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None
        return data if isinstance(data, dict) and data.get("service") == "strata" else None

    def health_check(self) -> bool:
        """서버가 Strata 로 답하면 건강하다 — 모델이 내려가 있어도 다음 요청 때 다시 올린다."""
        now = time.monotonic()
        if self.health_ttl > 0 and (now - self._last_health_at) < self.health_ttl:
            return self._last_health
        self._last_health = self._health() is not None
        self._last_health_at = now
        return self._last_health

    def _fetch_installed_models(self) -> set[str]:
        return {self.model} if self._health() is not None else set()

    def _fetch_loaded_models(self) -> set[str]:
        health = self._health()
        return {self.model} if health is not None and health.get("loaded") else set()

    def placement(self) -> dict | None:
        return None  # Strata 는 전문가를 그래픽카드·RAM·SSD 에 스스로 나눈다 — Ollama 의 size_vram 같은 값이 없다.

    def loaded_models(self, timeout: float = 10) -> list[str]:
        health = self._health(timeout)
        if health is None or health.get("error") or type(health.get("loaded")) is not bool:
            raise ProviderError(f"Strata 상태를 확인하지 못했습니다: {self.host}")
        return [self.model] if health["loaded"] else []

    def unload(self, timeout: float = 15) -> list[str]:
        """그래픽카드를 지금 돌려준다(`POST /unload`). 다음 요청 때 Strata 가 다시 올린다(첫 답이 늦어진다)."""
        before = self.loaded_models(timeout)
        try:
            with urllib.request.urlopen(self._request("/unload", {}, timeout), timeout=timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise ProviderError("Strata 가 답을 만드는 중이라 지금은 내릴 수 없습니다. 끝난 뒤 다시 시도하세요.") from exc
            raise self._strata_http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Strata 모델을 내리지 못했습니다: {exc}") from exc
        return before


def unload_local_models(provider: OllamaProvider, timeout: float = 15) -> list[str]:
    """로컬 provider 의 서버(Ollama 또는 Strata)에 올라간 모델을 내린다."""
    if isinstance(provider, StrataProvider):
        return provider.unload(timeout)
    return unload_ollama_models(provider.host, timeout=timeout)


def loaded_local_models(provider: OllamaProvider, timeout: float = 10) -> list[str]:
    if isinstance(provider, StrataProvider):
        return provider.loaded_models(timeout)
    return loaded_ollama_models(provider.host, timeout=timeout)


class JsonHTTPProvider(BaseLLMProvider):
    """Small stdlib-only helper for opt-in Cloud provider adapters."""

    def __init__(self, name: str, api_key: str, model: str, timeout: int = 120) -> None:
        self.name = name
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = max(1, int(timeout))

    def health_check(self) -> bool:
        # Cloud startup must not make a paid/network call. Configuration readiness only.
        return bool(self.api_key and self.model)

    def _request_json(
        self,
        url: str,
        payload: dict,
        headers: dict[str, str],
    ) -> dict:
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Cloud error bodies may echo request fragments. Never copy them into
            # user-visible errors or local logs; status code is enough for Alpha.
            raise ProviderError(f"{self.name} HTTP {exc.code} 오류") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.name} 연결 실패: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderError(f"{self.name} 응답 시간 초과/네트워크 오류: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.name} 응답 JSON 해석 실패") from exc

    @staticmethod
    def _system_and_dialog(messages: Sequence[Message]) -> tuple[str, list[Message]]:
        systems = [m.content for m in messages if m.role == "system"]
        dialog = [m for m in messages if m.role in {"user", "assistant"}]
        return "\n\n".join(systems), dialog


class OpenAIResponsesProvider(JsonHTTPProvider):
    """Opt-in OpenAI Responses API adapter. Default ALEX cloud mode remains Stub."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: int = 120,
        max_output_tokens: int = 2048,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        super().__init__(f"openai:{model}", api_key, model, timeout)
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max(1, int(max_output_tokens))

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        system, dialog = self._system_and_dialog(messages)
        payload: dict[str, object] = {
            "model": self.model,
            "input": [{"role": m.role, "content": m.content} for m in dialog],
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if system:
            payload["instructions"] = system
        data = self._request_json(
            f"{self.base_url}/responses",
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
        )
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return str(data["output_text"]).strip()
        parts: list[str] = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(str(content["text"]))
        result = "".join(parts).strip()
        if not result:
            raise ProviderError("OpenAI Responses 응답에서 text를 찾지 못했습니다.")
        return result


class AnthropicMessagesProvider(JsonHTTPProvider):
    """Opt-in Anthropic Messages API adapter."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: int = 120,
        max_output_tokens: int = 2048,
        base_url: str = "https://api.anthropic.com/v1",
        api_version: str = "2023-06-01",
    ) -> None:
        super().__init__(f"anthropic:{model}", api_key, model, timeout)
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.api_version = api_version

    @staticmethod
    def _merge_dialog(dialog: list[Message]) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        for message in dialog:
            role = "assistant" if message.role == "assistant" else "user"
            if merged and merged[-1]["role"] == role:
                merged[-1]["content"] += "\n\n" + message.content
            else:
                merged.append({"role": role, "content": message.content})
        return merged

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        system, dialog = self._system_and_dialog(messages)
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": self._merge_dialog(dialog),
        }
        if system:
            payload["system"] = system
        data = self._request_json(
            f"{self.base_url}/messages",
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": self.api_version,
            },
        )
        parts = [
            str(block.get("text", ""))
            for block in data.get("content", []) or []
            if block.get("type") == "text" and block.get("text")
        ]
        result = "".join(parts).strip()
        if not result:
            raise ProviderError("Anthropic 응답에서 text를 찾지 못했습니다.")
        return result


class GeminiGenerateContentProvider(JsonHTTPProvider):
    """Opt-in Gemini generateContent REST adapter."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: int = 120,
        max_output_tokens: int = 2048,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        super().__init__(f"gemini:{model}", api_key, model, timeout)
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max(1, int(max_output_tokens))

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        from urllib.parse import quote

        system, dialog = self._system_and_dialog(messages)
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in dialog
        ]
        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self.max_output_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        data = self._request_json(
            f"{self.base_url}/models/{quote(self.model, safe='')}:generateContent",
            payload,
            {"x-goog-api-key": self.api_key},
        )
        parts: list[str] = []
        for candidate in data.get("candidates", []) or []:
            for part in (candidate.get("content") or {}).get("parts", []) or []:
                if part.get("text"):
                    parts.append(str(part["text"]))
            if parts:
                break
        result = "".join(parts).strip()
        if not result:
            raise ProviderError("Gemini 응답에서 text를 찾지 못했습니다.")
        return result
