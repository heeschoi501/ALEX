// 실시간 통역(ADR-111) — 마이크 소리를 그대로 화면 스크립트로 넘긴다(128 표본씩). 끊기·인코딩은 live.js 가 한다.
class AlexCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor('alex-capture', AlexCapture);
