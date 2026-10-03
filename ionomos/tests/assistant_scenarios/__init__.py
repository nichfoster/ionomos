"""The assistant's scenario corpus now ships with Ionomos, so `ionomos ask-eval` can score a real model with it on
the PC (D72): the files are in ionomos/assistant/scenarios/. This name is kept for the tests that import it."""
from ionomos.assistant.scenarios import (  # noqa: F401
    ALWAYS_NOT,
    HARNESS_KEYS,
    HERE,
    KEYS,
    RUBRIC_KEYS,
    STATES,
    for_models,
    is_injection,
    load,
    question_key,
    score,
)
