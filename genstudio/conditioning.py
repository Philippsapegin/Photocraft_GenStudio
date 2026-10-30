"""Optional negative text; distilled CFG=1 models may ignore this branch."""


def negative(clip, text, positive, **tokenize):
    if not text:
        return positive
    encoded = clip.encode_from_tokens_scheduled(clip.tokenize(text, **tokenize))
    refs = positive[0][1].get("reference_latents") if positive else None
    return [[embedding, {**metadata, **({"reference_latents": refs} if refs is not None else {})}]
            for embedding, metadata in encoded]
