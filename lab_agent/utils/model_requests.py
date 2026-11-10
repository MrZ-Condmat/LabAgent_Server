"""Explicit thinking controls shared by scoring and all chat entry points."""


def thinking_request_options(model_config: dict, *, scoring: bool) -> dict:
    effort = model_config.get("reasoningEffort", "high" if scoring else "max")
    if effort not in {"low", "high", "max"}:
        raise ValueError("Unsupported reasoning effort; expected low, high or max")
    options = {
        "reasoning_effort": effort,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    # Omit temperature: DeepSeek's thinking API ignores it.
    # Chat intentionally delegates its output limit to the serving platform.
    if scoring:
        maximum = model_config.get("maxTokens", 16384)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ValueError("Scoring requires a positive integer maxTokens budget")
        options["max_tokens"] = maximum
    return options
