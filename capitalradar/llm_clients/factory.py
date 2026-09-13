from typing import Optional

from .base_client import BaseLLMClient

# Providers that use the OpenAI-compatible chat completions API
_OPENAI_COMPATIBLE = (
    "openai", "xai", "deepseek",
    "qwen", "qwen-cn",
    "glm", "glm-cn",
    "minimax", "minimax-cn",
    "ollama", "ollama2", "ollama3", "ollama4", "openrouter",
)


def create_llm_client(
    provider: str,
    model: str,
    base_url: Optional[str] = None,
    **kwargs,
) -> BaseLLMClient:
    """Create an LLM client for the specified provider.

    Provider modules are imported lazily so that simply importing this
    factory (e.g. during test collection) does not pull in heavy LLM SDKs
    or fail when their API keys are absent.

    Args:
        provider: LLM provider name
        model: Model name/identifier
        base_url: Optional base URL for API endpoint
        **kwargs: Additional provider-specific arguments

    Returns:
        Configured BaseLLMClient instance

    Raises:
        ValueError: If provider is not supported
    """
    provider_lower = provider.lower()

    if provider_lower in _OPENAI_COMPATIBLE:
        from .openai_client import OpenAIClient
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)

    if provider_lower == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model, base_url, **kwargs)

    if provider_lower == "google":
        from .google_client import GoogleClient
        return GoogleClient(model, base_url, **kwargs)

    if provider_lower == "azure":
        from .azure_client import AzureOpenAIClient
        return AzureOpenAIClient(model, base_url, **kwargs)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def resolve_role_llm(config, role, model_default=""):
    """Resolve (provider, model, backend_url) for a thinking role.

    role in ("deep", "quick"). A role-specific ``*_think_provider`` config
    key wins; otherwise it falls back to the global ``llm_provider`` so
    existing configs behave identically. ``backend_url`` stays the single
    global value (no per-role backend_url by design); when it is None each
    provider client falls back to its own default endpoint.

    Args:
        config: config dict (e.g. DEFAULT_CONFIG)
        role: "deep" (managers/decision) or "quick" (analysts/utility)
        model_default: fallback model when the role's model key is unset

    Returns:
        tuple (provider, model, backend_url)
    """
    role = role.lower()
    if role not in ("deep", "quick"):
        raise ValueError(f"Unknown thinking role: {role}")
    prefix = f"{role}_think"  # "deep_think" / "quick_think"
    provider = config.get(f"{prefix}_provider") or config.get("llm_provider", "deepseek")
    model = config.get(f"{prefix}_llm") or model_default
    return provider, model, config.get("backend_url")
