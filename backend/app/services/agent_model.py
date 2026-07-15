from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from openai import OpenAI

from app.config import Settings


class AgentModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelTurn:
    content: str
    tool_calls: list[ModelToolCall]
    token_usage: dict
    raw_assistant_message: dict


class AgentModelClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> ModelTurn:
        if not self.settings.dashscope_api_key:
            raise AgentModelError("尚未配置 DASHSCOPE_API_KEY")
        client = OpenAI(
            api_key=self.settings.dashscope_api_key,
            base_url=self.settings.dashscope_base_url,
            timeout=self.settings.agent_llm_timeout_seconds,
        )
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0,
            "extra_body": {"enable_thinking": False},
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = tool_choice or "auto"
            request["parallel_tool_calls"] = True
        try:
            response = client.chat.completions.create(**request)
            message = response.choices[0].message
        except Exception as exc:
            raise AgentModelError(f"Agent 模型调用失败：{exc}") from exc

        tool_calls: list[ModelToolCall] = []
        raw_tool_calls: list[dict] = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise AgentModelError(f"工具 {call.function.name} 参数不是有效 JSON") from exc
            tool_calls.append(
                ModelToolCall(id=call.id, name=call.function.name, arguments=arguments)
            )
            raw_tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )

        usage = response.usage
        token_usage = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "estimated": False,
        }
        assistant_message = {"role": "assistant", "content": message.content or ""}
        if raw_tool_calls:
            assistant_message["tool_calls"] = raw_tool_calls
        return ModelTurn(
            content=message.content or "",
            tool_calls=tool_calls,
            token_usage=token_usage,
            raw_assistant_message=assistant_message,
        )
