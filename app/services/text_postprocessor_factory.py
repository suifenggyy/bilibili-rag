"""
Text postprocessor factory — backward-compatible shim.

Delegates to ChatCompletionPostProcessor from the unified LLM factory,
but preserves the create_text_postprocessor() API for existing callers.

The role mapping:
  - Default (no role specified) → "asr_correction" role
  - content_summary callers → "content_summary" role

All backends use OpenAI-compatible /v1/chat/completions under the hood.
The LLM factory's get_llm_config() handles the base_url/model resolution
for each role.
"""

from typing import Optional

from app.config import settings
from app.services.llm.postprocessor_adapter import ChatCompletionPostProcessor
from app.services.text_postprocessor import TextPostProcessor


def create_text_postprocessor(
    prompt_template: Optional[str] = None,
    role: str = "asr_correction",
) -> TextPostProcessor:
    """Create a text postprocessor using the unified LLM factory.

    The role determines which LLM provider/model/timeout/prompt to use,
    resolved via settings.get_llm_config(role).

    Args:
        prompt_template: Optional system prompt override. Falls back to
            the role's default prompt (from _ROLE_DEFAULTS).
        role: LLM role name. Defaults to "asr_correction".

    Returns:
        A ChatCompletionPostProcessor implementing the TextPostProcessor Protocol.
    """
    role_config = settings.get_llm_config(role)
    system_prompt = prompt_template or role_config.prompt

    return ChatCompletionPostProcessor(
        role=role,
        system_prompt=system_prompt,
    )
