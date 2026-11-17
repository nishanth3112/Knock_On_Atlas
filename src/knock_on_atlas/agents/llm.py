"""Chat models per role: Ollama locally, Groq on AWS, a scripted fake in tests."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

from knock_on_atlas.config import LLMProvider, Settings
from knock_on_atlas.workflow.budgets import WALL_CLOCK_SECONDS

# One model for every role: two resident models swap a 16 GB laptop to a halt.
OLLAMA_MODEL = "qwen3:8b"
# Ollama otherwise cuts prompts to its small default window, dropping the system
# prompt and tool definitions once search results arrive.
OLLAMA_NUM_CTX = 8192
# Groq's free tier; both support tool calls (console.groq.com/docs/tool-use). The
# small model writes the planner's queries, the large one the analysis and brief.
GROQ_SMALL = "llama-3.1-8b-instant"
GROQ_LARGE = "llama-3.3-70b-versatile"
# A 429 on the free tier clears within seconds; retry instead of failing the run.
GROQ_RETRIES = 4


@dataclass(frozen=True)
class Models:
    small: BaseChatModel  # planner queries
    reason: BaseChatModel  # exposure analyst, evidence researcher
    hard: BaseChatModel  # brief synthesizer


def models_from_settings(settings: Settings) -> Models:
    if settings.llm_provider is LLMProvider.GROQ:
        return groq_models(settings)
    if settings.llm_provider is not LLMProvider.OLLAMA:
        raise ValueError(f"no chat models for LLM_PROVIDER={settings.llm_provider}")

    # Thinking tokens would eat the run's 25k-token budget; one call may not outlast
    # the whole run's wall clock.
    model = ChatOllama(
        model=OLLAMA_MODEL,
        temperature=0,
        reasoning=False,
        num_ctx=OLLAMA_NUM_CTX,
        client_kwargs={
            "timeout": settings.run_wall_clock_seconds or WALL_CLOCK_SECONDS
        },
    )
    return Models(small=model, reason=model, hard=model)


def groq_models(settings: Settings) -> Models:
    if settings.groq_api_key is None:
        raise ValueError("LLM_PROVIDER=groq needs GROQ_API_KEY")
    timeout = settings.run_wall_clock_seconds or WALL_CLOCK_SECONDS

    def chat(model: str) -> ChatGroq:
        return ChatGroq(
            model_name=model,
            api_key=settings.groq_api_key,
            temperature=0,
            max_retries=GROQ_RETRIES,
            timeout=timeout,
        )

    large = chat(GROQ_LARGE)
    return Models(small=chat(GROQ_SMALL), reason=large, hard=large)


Responder = Callable[[list[BaseMessage], list[str]], AIMessage]


class FakeChatModel(BaseChatModel):
    """Answers with `responder(messages, bound tool names)`; offline and repeatable."""

    responder: Responder

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        names = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.bind(tool_names=names, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        tool_names: Sequence[str] = (),
        **kwargs: Any,
    ) -> ChatResult:
        message = self.responder(messages, list(tool_names))
        if message.usage_metadata is None:
            # About 4 characters a token, so budgets still count something.
            chars = sum(len(str(m.content)) for m in messages)
            message.usage_metadata = {
                "input_tokens": chars // 4,
                "output_tokens": len(str(message.content)) // 4 + 1,
                "total_tokens": chars // 4 + len(str(message.content)) // 4 + 1,
            }
        return ChatResult(generations=[ChatGeneration(message=message)])
