from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    data: Any = None
    error: str | None = None
    iterations_used: int = 0
    trace: list[dict] = field(default_factory=list)

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
