"""Fine-tune a small reply model on character_data.py output (LoRA, one GPU).

Usage (needs the separate training venv, see handoff/dev-box.md):
    .runtime/train-venv/bin/python scripts/dev/train_lora.py \
        --base .runtime/hf/gemma-4-E2B-it-qat --out .runtime/dev/lora/gemma4-e2b

Then convert the adapter for llama.cpp (convert_lora_to_gguf.py ships in the
llama.cpp source tree of the same build as llama-server):
    python <llama.cpp>/convert_lora_to_gguf.py --base <hf snapshot dir> \
        --outtype f16 --outfile persona-lora.gguf .runtime/dev/lora/gemma4-e2b

One generic adapter is trained on many synthetic character sheets, so it
works for whatever persona the user writes; it is not per-persona.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA = PROJECT_DIR / ".runtime" / "dev" / "distill" / "replies.jsonl"
TARGET_MODULES = (
    r"^(?!.*(vision|audio)).*layers\.\d+\."
    r"(self_attn\.(q|k|v|o)_proj|mlp\.(gate|up|down)_proj)$"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="HF model id or local path")
    parser.add_argument("--out", required=True)
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--holdout", type=float, default=0.03)
    parser.add_argument("--max-steps", type=int, default=-1, help="dry runs only")
    parser.add_argument(
        "--max-length", type=int, default=2048, help="tokens; longer rows are truncated"
    )
    parser.add_argument(
        "--4bit",
        dest="four_bit",
        action="store_true",
        help="QLoRA: load the base in 4-bit (nf4); E4B in bf16 does not fit next to other tenants",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="build everything, show which tokens are trained on, one short forward; no training",
    )
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(args.base)

    def render(row: dict) -> dict[str, str]:
        # Render exactly like llama-server --jinja with enable_thinking off, so
        # the adapter sees the same tokens at inference time. Loss is on the
        # completion (the four lines) only.
        prompt = tokenizer.apply_chat_template(
            row["messages"][:-1],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        # The completion ends with the template's own end-of-turn marker
        # (Gemma: <turn|>, Qwen: <|im_end|>), which llama.cpp stops on.
        full = tokenizer.apply_chat_template(
            row["messages"], tokenize=False, enable_thinking=False
        )
        if not full.startswith(prompt):
            raise ValueError("chat template does not extend the prompt")
        return {"prompt": prompt, "completion": full[len(prompt) :]}

    rows = [
        json.loads(line)
        for line in Path(args.data).read_text(encoding="utf-8").splitlines()
    ]
    random.Random(0).shuffle(rows)
    rendered = [render(r) for r in rows]
    # The trainer cuts long rows from the right, which would train on half an
    # answer with no end marker: drop them instead, and say how many.
    lengths = [
        len(tokenizer(r["prompt"] + r["completion"])["input_ids"]) for r in rendered
    ]
    rendered = [
        r for r, n in zip(rendered, lengths, strict=True) if n <= args.max_length
    ]
    print(
        f"longest row {max(lengths)} tokens; "
        f"{len(rows) - len(rendered)} rows over --max-length {args.max_length} dropped"
    )
    split = max(1, int(len(rendered) * args.holdout))
    train = Dataset.from_list(rendered[split:])
    evaluation = Dataset.from_list(rendered[:split])
    print(f"train {len(train)}  eval {len(evaluation)}")
    print(train[0]["prompt"][-300:] + train[0]["completion"])

    quantized: dict = {}
    if args.four_bit:
        from transformers import BitsAndBytesConfig

        quantized = {
            "quantization_config": BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            ),
            "device_map": {"": 0},
        }
    model = AutoModelForCausalLM.from_pretrained(
        args.base, dtype=torch.bfloat16, attn_implementation="sdpa", **quantized
    )
    # Text only: Gemma 4 also ships vision/audio towers (~0.9 GiB) that a
    # text forward never calls. Drop them before the trainer moves the model
    # to a GPU that other services share.
    inner = getattr(model, "model", None)
    for name in ("vision_tower", "audio_tower", "embed_vision", "embed_audio"):
        if inner is not None and getattr(inner, name, None) is not None:
            setattr(inner, name, None)
    if args.four_bit:
        # Not peft.prepare_model_for_kbit_training: it casts every
        # non-quantized parameter to float32, and Gemma 4's per-layer
        # embedding tables alone then need 8.75 GiB more. They stay bf16 and
        # frozen; only the inputs need gradients for checkpointing to work.
        model.enable_input_require_grads()
    config = SFTConfig(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=10,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        bf16=True,
        use_cpu=not torch.cuda.is_available(),  # CPU only for pipeline dry runs
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=50,
        save_strategy="no",
        report_to=[],
        completion_only_loss=True,
        max_length=args.max_length,
    )
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=train,
        eval_dataset=evaluation,
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=args.rank,
            lora_alpha=args.rank * 2,
            lora_dropout=0.05,
            # Text layers only: Gemma 4 checkpoints also carry vision and
            # audio towers, which llama.cpp does not load for this use.
            target_modules=TARGET_MODULES,
            task_type="CAUSAL_LM",
        ),
    )
    if args.check:
        return check(trainer, tokenizer)
    trainer.train()
    trainer.save_model(args.out)
    print(f"saved adapter to {args.out}")
    return 0


def check(trainer, tokenizer) -> int:
    """Print the tokens the loss is computed on (must be exactly the answer
    lines plus the end-of-turn marker) and run one short forward pass."""
    import torch

    row = trainer.train_dataset[0]
    ids = row["input_ids"]
    # TRL marks the prompt with label -100; everything else is trained on.
    positions = [i for i, label in enumerate(row["labels"]) if label != -100]
    trained = [ids[i] for i in positions]
    prompt_tokens = len(tokenizer(row["prompt"], add_special_tokens=False)["input_ids"])
    print(f"tokens: {len(ids)}, prompt: {prompt_tokens}, trained on: {len(trained)}")
    print("TRAINED TEXT >>>" + tokenizer.decode(trained) + "<<<")
    if not positions or positions[0] != prompt_tokens:
        print("MASK WRONG: trained tokens must start right after the prompt")
        return 1
    model = trainer.model
    sample = torch.tensor([ids[-64:]], device=model.device)
    with torch.no_grad():
        logits = model(input_ids=sample).logits
    print(f"forward ok: logits {tuple(logits.shape)}")
    return 0 if trained else 1


if __name__ == "__main__":
    raise SystemExit(main())
