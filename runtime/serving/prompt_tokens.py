"""Real tokenizer output for the prefill and decode runs.

Prefill has to be exercised with token ids a user could actually produce, not a
range of integers: the chat template inserts control tokens, and a wrong
template silently changes what the model is being asked. So the ids come from
the checkpoint's own tokenizer, through its own template.

Longer sequences are produced by repeating the user's text *before* templating
and then truncating the tokenised result to exactly the requested length. The
ids stay genuine tokenizer output and the prefix is the real templated prefix;
only the wording repeats, which shape and timing measurements do not care about.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

DEFAULT_PROMPT = (
    "Explain how pipeline parallelism splits a transformer across two "
    "accelerators, and what has to cross the boundary between them."
)


def load_tokenizer(model_path: str) -> Any:
    """The checkpoint's tokenizer, with the Mistral regex correction applied.

    Transformers warns that the pattern shipped with these checkpoints
    mis-splits text and asks for `fix_mistral_regex=True`. Ignoring that would
    mean prefill runs on ids no correct client would produce. The flag is
    passed defensively because older releases do not accept it.
    """
    from transformers import AutoTokenizer  # noqa: PLC0415
    try:
        return AutoTokenizer.from_pretrained(model_path, fix_mistral_regex=True)
    except TypeError:
        return AutoTokenizer.from_pretrained(model_path)


def templated_ids(tokenizer: Any, prompt: str, use_chat_template: bool = True
                  ) -> Tuple[List[int], str]:
    """Token ids for one user turn, and the text the template produced."""
    if use_chat_template and getattr(tokenizer, "chat_template", None):
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False, add_generation_prompt=True)
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
    else:
        text = prompt
        ids = tokenizer(text)["input_ids"]
    return [int(i) for i in ids], text


def ids_of_length(tokenizer: Any, prompt: str, length: int,
                  use_chat_template: bool = True) -> Tuple[List[int], Dict[str, Any]]:
    """Exactly `length` real token ids, with a record of how they were made."""
    if length < 1:
        raise ValueError(f"sequence length must be positive, got {length}")
    repeats = 1
    ids, text = templated_ids(tokenizer, prompt, use_chat_template)
    base = len(ids)
    while len(ids) < length:
        repeats += 1
        ids, text = templated_ids(tokenizer, " ".join([prompt] * repeats),
                                  use_chat_template)
        if repeats > 4096:
            raise RuntimeError("prompt repetition is not reaching the requested length")
    truncated = len(ids) > length
    ids = ids[:length]
    return ids, {
        "requestedLength": length,
        "baseTemplatedLength": base,
        "promptRepeats": repeats,
        "truncated": truncated,
        "usedChatTemplate": bool(use_chat_template
                                 and getattr(tokenizer, "chat_template", None)),
        "templatePreview": text[:160],
        "firstIds": ids[:8],
        "lastIds": ids[-4:],
    }
