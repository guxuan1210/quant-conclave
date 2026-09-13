import logging
import os
import warnings

# Load .env files at package import so DEFAULT_CONFIG's env-var overlay
# (and every llm_clients consumer) sees the user's keys regardless of
# which entry point started the process. find_dotenv(usecwd=True) walks
# from the CWD, so the installed `quantconclave` console script picks up
# the project's .env instead of stepping up from site-packages.
# load_dotenv defaults to override=False, so it never clobbers values
# the caller has already exported.
try:
    from dotenv import find_dotenv, load_dotenv, dotenv_values

    load_dotenv(find_dotenv(usecwd=True))
    load_dotenv(find_dotenv(".env.enterprise", usecwd=True), override=False)

    # Ensure domestic APIs bypass the proxy even when NO_PROXY is pre-set
    # to empty string in the shell (load_dotenv won't override with override=False).
    if not os.environ.get("NO_PROXY") and not os.environ.get("no_proxy"):
        _dotenv_values = dotenv_values(find_dotenv(usecwd=True))
        _no_proxy = _dotenv_values.get("NO_PROXY") or _dotenv_values.get("no_proxy")
        if _no_proxy:
            os.environ["NO_PROXY"] = _no_proxy
except ImportError:
    pass

# One-time migration of the legacy ``~/.capitalradar`` data directory to the
# new ``~/.quantconclave`` home. Non-destructive and idempotent; a failure is
# logged and the process continues (data stays in the legacy directory and the
# migration is retried on the next startup).
try:
    from quantconclave.migration import migrate_home

    migrate_home()
except Exception:  # noqa: BLE001 — never block startup on migration
    logging.getLogger(__name__).warning(
        "legacy home migration did not complete; continuing without it",
        exc_info=True,
    )

# langchain-core 1.3.3 calls surface_langchain_deprecation_warnings() in
# its own __init__, which prepends default-action filters for its
# subclassed warning categories. To suppress a specific warning we must
# install our filter AFTER langchain-core has installed its own, so import
# it first. The package is a guaranteed transitive dep via langgraph.
try:
    import langchain_core  # noqa: F401
except ImportError:
    pass

# langgraph-checkpoint 4.0.3 calls Reviver() at module load without an
# explicit allowed_objects, which triggers a noisy pending-deprecation
# warning from langchain-core 1.3.3 on every interpreter start. The fix
# is already merged upstream (langchain-ai/langgraph#7743, 2026-05-08)
# and will arrive in the next langgraph-checkpoint release. Remove this
# block (and the langchain_core preload above) when we bump past it.
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects`.*",
    category=PendingDeprecationWarning,
)
