"""LoRA 학습 일꾼 — ALEX 학습 전용 환경에서만 돈다(2026-10-05, ADR-084).

ALEX 앱 환경에는 torch 가 없다. `scripts/lora.py` 가 이 파일을 학습 환경의 파이썬(C:\\ALEX\\tools\\lora\\venv)으로 실행하고,
결과는 파일(JSON·어댑터 폴더)로만 주고받는다. 이 파일은 jarvis 패키지를 가져오지 않는다.

표준 조합을 그대로 쓴다: Hugging Face transformers + PEFT LoRA(https://huggingface.co/docs/peft).
손실은 마지막 assistant 답 토큰에만 건다(질문 부분은 -100). 평가는 학습에 쓰지 않은 eval 답의 평균 손실(낮을수록 잘 맞힘)을
학습 전후로 같은 방식으로 잰다.

명령:
  fetch  --base MODEL                                   바탕 모델을 내려받고 로컬 경로를 출력(JSON)
  train  --base MODEL --data DIR --out DIR [--device cuda|cpu] ...   → DIR/adapter/, DIR/result.json
  merge  --base MODEL --adapter DIR --out DIR           어댑터를 바탕 모델에 합친 16비트 모델(CPU)
  sample --base MODEL [--adapter DIR] --prompts FILE --out FILE      CPU 생성(그리디) — GPU 없이 결과 확인
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_example(tokenizer, messages: list[dict], max_len: int) -> dict | None:
    """대화 → input_ids/labels. 손실은 마지막 assistant 답에만 — 질문까지 외우게 하지 않는다.

    채팅 틀로 만든 전체 문장이 '답 직전까지의 틀(add_generation_prompt)' 로 시작해야 한다. 아니면 이 모델 형식은 지원하지 않는다.
    답이 max_len 에서 모두 잘리면 None(학습할 토큰이 없다).
    """
    if not messages or messages[-1].get("role") != "assistant":
        raise ValueError("마지막 메시지는 assistant 답이어야 합니다")
    prompt = tokenizer.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full = tokenizer.apply_chat_template(messages, tokenize=False)
    if not full.startswith(prompt):
        raise ValueError("채팅 틀의 앞부분이 맞지 않습니다 — 이 바탕 모델의 형식은 아직 지원하지 않습니다")
    prompt_ids = list(tokenizer(prompt, add_special_tokens=False)["input_ids"])
    target_ids = list(tokenizer(full[len(prompt):], add_special_tokens=False)["input_ids"])
    ids = (prompt_ids + target_ids)[:max_len]
    labels = ([-100] * len(prompt_ids) + target_ids)[:max_len]
    if sum(1 for label in labels[1:] if label != -100) == 0:
        return None
    return {"input_ids": ids, "labels": labels}


def lr_at(step: int, total: int, peak: float, warmup: int) -> float:
    """선형 예열 뒤 선형 감소(Hugging Face get_linear_schedule_with_warmup 과 같은 모양)."""
    if total <= 0:
        return 0.0
    if step < warmup:
        return peak * (step + 1) / max(1, warmup)
    return peak * max(0.0, (total - step) / max(1, total - warmup))


def mean_loss(model, examples: list[dict], device: str) -> float | None:
    """답 토큰 수로 가중한 평균 손실. 예가 없으면 None."""
    import torch

    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for example in examples:
            ids = torch.tensor([example["input_ids"]], device=device)
            labels = torch.tensor([example["labels"]], device=device)
            loss = model(input_ids=ids, labels=labels).loss
            tokens = sum(1 for label in example["labels"][1:] if label != -100)
            total += float(loss) * tokens
            count += tokens
    return round(total / count, 4) if count else None


def _load(base: str, device: str):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    dtype = torch.bfloat16  # CPU 도 bf16(Zen 4·5 는 AVX-512 BF16). fp32 면 4B 가 16GB 다.
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=dtype)
    model.to(device)
    return tokenizer, model


def cmd_fetch(args: argparse.Namespace) -> int:
    from huggingface_hub import snapshot_download

    path = snapshot_download(args.base, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken", "LICENSE*"])
    print(json.dumps({"path": path}, ensure_ascii=False))
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    import torch
    from peft import LoraConfig, get_peft_model

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = args.device
    if device == "cuda":
        if not torch.cuda.is_available():
            log("CUDA 를 쓸 수 없습니다")
            return 2
        total_bytes = torch.cuda.get_device_properties(0).total_memory
        # 한도를 넘으면 이 프로세스만 메모리 부족으로 멈춘다 — 그래픽카드를 꽉 채우지 않는다(PC 꺼짐 기록, gpu-load-safety).
        torch.cuda.set_per_process_memory_fraction(min(0.9, args.vram_limit_gb * 1024 ** 3 / total_bytes), 0)
    else:
        torch.set_num_threads(max(1, args.threads))

    tokenizer, model = _load(args.base, device)
    data = Path(args.data)
    train_rows, eval_rows = read_jsonl(data / "train.jsonl"), read_jsonl(data / "eval.jsonl")
    train = [e for e in (build_example(tokenizer, r["messages"], args.max_len) for r in train_rows) if e]
    evals = [e for e in (build_example(tokenizer, r["messages"], args.max_len) for r in eval_rows) if e]
    if not train:
        log("학습할 예가 없습니다")
        return 3
    log(f"학습 {len(train)}개 · 평가 {len(evals)}개 · 장치 {device} · 바탕 {args.base}")

    before = mean_loss(model, evals, device)
    log(f"학습 전 평가 손실 {before}")

    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=0.0,
                                             target_modules=TARGET_MODULES, task_type="CAUSAL_LM"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)

    per_epoch = math.ceil(len(train) / args.grad_accum)
    total_steps = per_epoch * args.epochs
    if args.max_steps:
        total_steps = min(total_steps, args.max_steps)
    warmup = max(1, int(total_steps * 0.1))
    log(f"LoRA r={args.rank} alpha={args.alpha} 학습 매개변수 {trainable:,} · 단계 {total_steps}")

    step, losses, order = 0, [], list(range(len(train)))
    model.train()
    while step < total_steps:
        random.shuffle(order)
        for start in range(0, len(order), args.grad_accum):
            if step >= total_steps:
                break
            chunk = order[start:start + args.grad_accum]
            running = 0.0
            for index in chunk:
                example = train[index]
                ids = torch.tensor([example["input_ids"]], device=device)
                labels = torch.tensor([example["labels"]], device=device)
                loss = model(input_ids=ids, labels=labels).loss / len(chunk)
                loss.backward()
                running += float(loss.detach())
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            for group in optimizer.param_groups:
                group["lr"] = lr_at(step, total_steps, args.lr, warmup)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            losses.append(round(running, 4))
            log(f"단계 {step}/{total_steps} 손실 {running:.4f}")

    after = mean_loss(model, evals, device)
    log(f"학습 후 평가 손실 {after}")
    model.save_pretrained(out / "adapter")
    result = {
        "base_model": args.base, "device": device, "train_examples": len(train), "eval_examples": len(evals),
        "steps": step, "epochs": args.epochs, "lr": args.lr, "rank": args.rank, "alpha": args.alpha,
        "max_len": args.max_len, "trainable_params": trainable,
        "train_loss_first": losses[0] if losses else None, "train_loss_last": losses[-1] if losses else None,
        "eval_loss_before": before, "eval_loss_after": after,
        "seconds": round(time.monotonic() - started, 1),
        "peak_vram_gb": round(torch.cuda.max_memory_reserved() / 1024 ** 3, 2) if device == "cuda" else None,
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log("완료")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """GPU 를 쓰지 않는다. Ollama 0.34.1 부터 ADAPTER 가 없어져 합친 모델을 GGUF 로 바꿔야 한다."""
    from peft import PeftModel

    tokenizer, base = _load(args.base, "cpu")
    merged = PeftModel.from_pretrained(base, args.adapter).merge_and_unload()
    out = Path(args.out)
    merged.save_pretrained(out, safe_serialization=True, max_shard_size="5GB")
    tokenizer.save_pretrained(out)
    log(f"합친 모델 → {out}")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """그리디 생성(같은 입력이면 같은 출력) — 바탕 모델과 학습 모델을 나란히 볼 때 쓴다."""
    import torch

    torch.set_num_threads(max(1, args.threads))
    tokenizer, model = _load(args.base, "cpu")
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()
    answers = []
    for prompt in json.loads(Path(args.prompts).read_text(encoding="utf-8")):
        text = tokenizer.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
        ids = tokenizer(text, return_tensors="pt", add_special_tokens=False)
        started = time.monotonic()
        with torch.no_grad():
            output = model.generate(**ids, max_new_tokens=args.max_new, do_sample=False)
        answer = tokenizer.decode(output[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        answers.append({"prompt": prompt, "answer": answer, "seconds": round(time.monotonic() - started, 1)})
        log(f"생성 {len(answers)}개")
    Path(args.out).write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--base", required=True)
    train = sub.add_parser("train")
    train.add_argument("--base", required=True)
    train.add_argument("--data", required=True)
    train.add_argument("--out", required=True)
    train.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    train.add_argument("--epochs", type=int, default=3)
    train.add_argument("--max-steps", type=int, default=0)
    train.add_argument("--lr", type=float, default=2e-4)
    train.add_argument("--rank", type=int, default=16)
    train.add_argument("--alpha", type=int, default=16)
    train.add_argument("--grad-accum", type=int, default=4)
    train.add_argument("--max-len", type=int, default=1024)
    train.add_argument("--vram-limit-gb", type=float, default=11.0)
    train.add_argument("--threads", type=int, default=8)
    train.add_argument("--seed", type=int, default=3407)
    merge = sub.add_parser("merge")
    merge.add_argument("--base", required=True)
    merge.add_argument("--adapter", required=True)
    merge.add_argument("--out", required=True)
    sample = sub.add_parser("sample")
    sample.add_argument("--base", required=True)
    sample.add_argument("--adapter", default="")
    sample.add_argument("--prompts", required=True)
    sample.add_argument("--out", required=True)
    sample.add_argument("--max-new", type=int, default=160)
    sample.add_argument("--threads", type=int, default=8)
    args = parser.parse_args(argv)
    return {"fetch": cmd_fetch, "train": cmd_train, "merge": cmd_merge, "sample": cmd_sample}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
