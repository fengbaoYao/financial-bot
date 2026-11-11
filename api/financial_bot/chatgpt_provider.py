"""ChatGPT plan model using OpenAI's public streaming Responses contract."""
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.profiles import merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.openai_codex import openai_codex_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

PREFERRED_CHATGPT_MODEL = "gpt-6.1-terra"
DEFAULT_CHATGPT_REASONING_EFFORT = "medium"


class ChatGPTPlanModel(OpenAIResponsesModel):
    # The subscription route rejects Responses tool_search; expose regular
    # client-side function tools directly instead.
    supported_tool_deferral_modes = frozenset()

    async def _build_responses_request_params(self, *args, **kwargs):
        from openai import omit
        params = await super()._build_responses_request_params(*args, **kwargs)
        if isinstance(params.tools, list):
            functions = [tool for tool in params.tools if tool.get('type') in {'function', 'custom'}]
            other = [tool for tool in params.tools if tool.get('type') not in {'function', 'custom'}]
            if functions:
                params.input.insert(0, {'type': 'additional_tools', 'role': 'developer', 'tools': functions})
                params.tools = other or omit
                params.tool_choice = omit
        return params


def _chatgpt_profile(model_name: str):
    """Codex terra profile currently disables reasoning; OpenAI still accepts effort."""
    base = openai_codex_model_profile(model_name)
    overrides = OpenAIModelProfile(openai_system_prompt_role="developer")
    if model_name == PREFERRED_CHATGPT_MODEL or not base.get("openai_supports_reasoning", False):
        overrides = merge_profile(
            overrides,
            OpenAIModelProfile(
                openai_supports_reasoning=True,
                openai_supports_encrypted_reasoning_content=True,
                openai_supports_minimal_reasoning_effort=True,
                supports_thinking=True,
            ),
        )
    return merge_profile(base, overrides)


def select_chatgpt_model(choices: list[str], preferred: str = PREFERRED_CHATGPT_MODEL) -> str:
    if not choices:
        raise ValueError("chatgpt_no_models")
    return preferred if preferred in choices else choices[0]


def make_chatgpt_model(credential, model_name, *, http_client=None, reasoning_effort: str = DEFAULT_CHATGPT_REASONING_EFFORT):
    import asyncio

    async def access_token():
        return await asyncio.to_thread(credential)

    client = AsyncOpenAI(api_key=access_token, base_url="https://api.openai.com/v1", max_retries=0, timeout=45, http_client=http_client)
    settings = OpenAIResponsesModelSettings(openai_reasoning_effort=reasoning_effort) if reasoning_effort else None
    return ChatGPTPlanModel(
        model_name,
        provider=OpenAIProvider(openai_client=client),
        profile=_chatgpt_profile(model_name),
        settings=settings,
    )


def can_fallback_after_failure(exc, deps):
    """Only explicitly rejected requests can restart, before tools/output exist."""
    from pydantic_ai.exceptions import ModelHTTPError
    return (isinstance(exc, ModelHTTPError) and exc.status_code in {401, 403, 429}
            and not deps.tool_results and not deps.stream_text)
