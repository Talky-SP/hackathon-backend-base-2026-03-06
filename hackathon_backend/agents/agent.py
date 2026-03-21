from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import litellm

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    data: Any = None
    error: str | None = None
    iterations_used: int = 0
    trace: list[dict] = field(default_factory=list)
    chart_html: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "data": self.data,
                "error": self.error,
                "iterations_used": self.iterations_used,
            },
            indent=2,
            default=str,
        )



class Agent(ABC):
    """Base agent that uses liteLLM for reasoning and iterative task execution."""

    def __init__(
        self,
        model: str = "anthropic/claude-sonnet-4-5-20250514",
        max_iterations: int = 8,
        temperature: float = 0.2,
    ):
        self.model = model
        self.max_iterations = max_iterations
        self.temperature = temperature
        self._messages: list[dict] = []
        self._trace: list[dict] = []

    # ------------------------------------------------------------------
    # LLM interface
    # ------------------------------------------------------------------

    def _call_llm(self, messages: list[dict], response_format: dict | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = litellm.completion(**kwargs)
        content = response.choices[0].message.content
        self._trace.append({"role": "assistant", "content": content})
        return content

    def _build_system_prompt(self) -> str:
        return "You are a helpful assistant."

    # ------------------------------------------------------------------
    # Lifecycle hooks — override in subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _plan(self, user_request: str) -> str:
        """Produce an initial plan / reasoning for the request."""

    @abstractmethod
    def _execute(self, plan: str) -> Any:
        """Execute the plan and return raw results."""

    @abstractmethod
    def _validate(self, result: Any) -> bool:
        """Return True if the result satisfies the original request."""

    @abstractmethod
    def _refine(self, result: Any, iteration: int) -> str:
        """Given a bad result, produce a new plan for the next iteration."""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, user_request: str) -> AgentResult:
        logger.info("Agent.run | request=%s", user_request[:120])
        self._messages = []
        self._trace = []

        plan = self._plan(user_request)
        self._trace.append({"phase": "plan", "content": plan})

        for iteration in range(1, self.max_iterations + 1):
            logger.info("Agent.run | iteration %d/%d", iteration, self.max_iterations)

            try:
                result = self._execute(plan)
            except Exception as exc:
                logger.warning("Agent.run | execution error: %s", exc)
                self._trace.append({"phase": "execute_error", "iteration": iteration, "error": str(exc)})
                plan = self._refine(None, iteration)
                continue

            if self._validate(result):
                logger.info("Agent.run | success on iteration %d", iteration)
                return AgentResult(
                    success=True,
                    data=result,
                    iterations_used=iteration,
                    trace=self._trace,
                )

            self._trace.append({"phase": "validate_fail", "iteration": iteration})
            plan = self._refine(result, iteration)

        return AgentResult(
            success=False,
            error=f"Failed to produce valid results after {self.max_iterations} iterations",
            iterations_used=self.max_iterations,
            trace=self._trace,
        )


class ToolUseAgent:
    """Base class for tool-use conversation loop agents.

    Subclasses register tools via _register_tool() and implement _build_system_prompt().
    The loop sends messages to the LLM, dispatches tool calls locally, and repeats
    until the LLM stops calling tools or max_tool_calls is reached.
    """

    def __init__(
        self,
        completion_fn: Callable,
        model_id: str,
        max_tool_calls: int = 15,
        temperature: float = 0.0,
    ):
        self._completion_fn = completion_fn
        self._model_id = model_id
        self._max_tool_calls = max_tool_calls
        self._temperature = temperature
        self._tools: list[dict] = []
        self._tool_handlers: dict[str, Callable] = {}
        self._sources: list[dict] = []
        self._tool_call_count: int = 0
        self._trace: list[dict] = []

    def _register_tool(self, schema: dict, handler: Callable) -> None:
        """Register a tool with its JSON schema and handler function."""
        self._tools.append({"type": "function", "function": schema})
        self._tool_handlers[schema["name"]] = handler

    def _build_system_prompt(self) -> str:
        raise NotImplementedError

    def _run_tool_loop(
        self,
        user_message: str,
        extra_messages: list[dict] | None = None,
    ) -> str | None:
        """Run the tool-use conversation loop.

        Returns the final text content from the LLM when it stops calling tools.
        """
        messages: list[dict] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]
        if extra_messages:
            messages.extend(extra_messages)
        messages.append({"role": "user", "content": user_message})

        self._tool_call_count = 0
        self._sources = []

        while self._tool_call_count < self._max_tool_calls:
            response = self._completion_fn(
                model_id=self._model_id,
                messages=messages,
                tools=self._tools if self._tools else None,
                temperature=self._temperature,
            )

            choice = response.choices[0]
            assistant_msg = choice.message

            # Build assistant message dict for conversation
            msg_dict: dict[str, Any] = {"role": "assistant"}
            if assistant_msg.content:
                msg_dict["content"] = assistant_msg.content
            if assistant_msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_msg.tool_calls
                ]
            messages.append(msg_dict)

            # If no tool calls, we're done
            if not assistant_msg.tool_calls:
                return assistant_msg.content

            # Dispatch each tool call
            for tc in assistant_msg.tool_calls:
                self._tool_call_count += 1
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                self._trace.append({
                    "phase": "tool_call",
                    "tool": fn_name,
                    "args": fn_args,
                    "call_number": self._tool_call_count,
                })

                handler = self._tool_handlers.get(fn_name)
                if handler is None:
                    result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})
                else:
                    try:
                        result = handler(**fn_args)
                        result_str = json.dumps(result, default=str)
                    except Exception as exc:
                        logger.warning("Tool %s failed: %s", fn_name, exc)
                        result_str = json.dumps({"error": str(exc)})

                self._trace.append({
                    "phase": "tool_result",
                    "tool": fn_name,
                    "call_number": self._tool_call_count,
                    "result_length": len(result_str),
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                if self._tool_call_count >= self._max_tool_calls:
                    logger.warning("Max tool calls (%d) reached", self._max_tool_calls)
                    break

        # If we exhausted tool calls, do one final call without tools
        response = self._completion_fn(
            model_id=self._model_id,
            messages=messages,
            temperature=self._temperature,
        )
        return response.choices[0].message.content
