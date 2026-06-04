# Self-hosted inference for korgex — fast *and* cheap

Run korgex against **your own model, on your own GPU**, at near-zero marginal cost — and fast
enough to actually drive the agent loop. This is the same speed recipe behind fast commercial
coding agents, reassembled from open parts. korgex is provider-agnostic, so this is purely a
*serving* recipe: nothing in korgex changes.

## Why it works

korgex's COGS is already near-zero by design (bring-your-own-key, no token resale). The only
remaining lever is **the model + how it's served**. Three things make a self-hosted coding
agent feel instant:

1. **A low-active-parameter MoE base** — few parameters fire per token, so decode is fast even
   on modest hardware.
2. **Speculative decoding** — predict several tokens per step and verify them in parallel.
   It's especially fast for *edits*, where the existing file is a near-perfect draft.
3. **Short, relevant prompts** — korgex's lean-context + recall already do this: retrieve the
   few verified events that matter instead of carrying the whole history.

## The recipe

### 1. Pick a base model — a low-active-param MoE
The sweet spot for one GPU is a Mixture-of-Experts with a small *active* parameter count:
- e.g. a **`Qwen3-30B-A3B`-class** model (≈30B total, **~3B active**) or a similar `*-A3B` /
  `*-flash` variant. ~3B active = fast decode.
- An **L4 (24 GB)** serves a quantized ~30B-MoE interactively; larger MoEs want more VRAM.

### 2. (Optional) Fine-tune it for your loop
[Unsloth](https://github.com/unslothai/unsloth) makes a LoRA fine-tune cheap. Train on your own
korgex ledgers — tool-call/edit formatting, your repo conventions — so the model wastes fewer
tokens. Optional: a good base works out of the box.

### 3. Quantize
**AWQ / GPTQ / FP8** cut VRAM and speed up decode with minimal quality loss (FP8 on Hopper;
AWQ/GPTQ broadly). Quantization is **orthogonal** to everything below — stack it.

### 4. Serve with vLLM + speculative decoding
[vLLM](https://github.com/vllm-project/vllm) exposes an OpenAI-compatible endpoint. Turn on
speculative decoding for the latency win:

```bash
vllm serve <model> \
  --quantization awq \
  --port 8000 \
  --speculative-config '{"method": "ngram", "num_speculative_tokens": 5}'
  # or an EAGLE / draft-model config — check your vLLM version's exact flags
```

Speculative decoding is **transparent to the API client**, so korgex needs no changes — it just
sees a faster endpoint.

### 5. Point korgex at it (one command)
```bash
korgex providers add vllm --url http://your-box:8000/v1 --model <model-id>
korgex providers use vllm
korgex "fix the failing auth test"     # …now runs against your box
```
(Or set `KORGEX_API_URL=http://your-box:8000/v1` directly — same routing.)

### 6. Keep prompts short — the free speed lever
```bash
export KORGEX_LEAN_CONTEXT=1
```
korgex then injects only the **relevant, verified** past ledger events (FTS5 BM25 + causal-DAG
expansion + MMR diversification) instead of the whole history → shorter prompts → faster
time-to-first-token *and* cheaper. This is the "smart context" half of the speed recipe, built
in — and because the events are hash-chained, the context is trustworthy, not a summary the
model has to believe.

## The result

**Low-active MoE + quantization + speculative decoding + lean context** = a fast, cheap, private
coding agent on your own hardware — with korgex's **verifiable ledger** on top, so every run is
still provable end-to-end.

## Honest caveats

- End-to-end speed depends on your GPU and model; **validate on your hardware** — the numbers
  above are the shape of the recipe, not a promise.
- Speculative-decoding gains vary by model and method (ngram vs. EAGLE vs. draft-model).
- Quantization trades a little quality for speed/VRAM.
- This recipe serves *one* endpoint; korgex stays provider-agnostic, so you can swap any
  OpenAI-compatible server (vLLM, SGLang, LM Studio, llama.cpp) behind the same preset.
