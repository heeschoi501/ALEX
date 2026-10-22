"""Optional speech-to-text worker for live interpreting(2026-10-22, ADR-111).

C:\\ALEX\\tools\\stt\\venv(sherpa-onnx 1.13.8 + numpy)에서 CPU 로만 돈다. 모델(SenseVoice-Small int8 2024-07-17 — 중국어·영어·
일본어·한국어·광둥어, 언어 자동 인식)을 한 번 올려 두고 표준 입력으로 JSON 한 줄씩 받는다:
    {"id": 1, "wav": "<base64 16kHz 16bit 모노 WAV>"}  →  {"id": 1, "text": "…", "lang": "en", "took": 0.05}
시작하면 {"ready": true} 한 줄. 이 PC 실측: 4~7초 말에 0.05~0.1초. (2025-09-09 판은 광둥어 전용이라 쓰지 않는다.)
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import time
import wave

import numpy as np
import sherpa_onnx


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    model = Path(sys.argv[1])
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model / "model.int8.onnx"), tokens=str(model / "tokens.txt"), num_threads=4, use_itn=True,
        language="auto")
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            with wave.open(io.BytesIO(base64.b64decode(request["wav"]))) as audio:
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                    raise ValueError("16bit 모노 WAV 만 받습니다.")
                rate = audio.getframerate()
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            started = time.monotonic()
            stream = recognizer.create_stream()
            stream.accept_waveform(rate, samples)
            recognizer.decode_stream(stream)
            result = stream.result
            reply = {"id": request.get("id"), "text": result.text.strip(), "lang": result.lang.strip("<|>"),
                     "took": round(time.monotonic() - started, 3)}
        except Exception as exc:  # noqa: BLE001 — 한 줄 실패를 알리고 다음 줄을 받는다
            reply = {"id": request.get("id"), "error": str(exc)[:300]}
        print(json.dumps(reply, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
