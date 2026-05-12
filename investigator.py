import asyncio
import json
import re

from groq import Groq

MODEL = "llama-3.1-8b-instant"
_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq()
    return _client


# ReAct-style system prompt — no Groq native tool-use, just text parsing
SYSTEM = """You are a codebase investigator. Explore a GitHub repo using the tools below, then write a grounded answer.

TOOL CALL FORMAT — output exactly this, one tool at a time:
<tool_call>{{"name": "TOOL", "input": {{ARGS}}}}</tool_call>

TOOLS:
  get_structure   {{"max_depth": 2}}
  list_files      {{"directory": "src", "pattern": "*.py"}}
  search_code     {{"pattern": "def login", "file_glob": "*.py", "context_lines": 3}}
  read_file       {{"path": "src/auth.py", "start_line": 1, "end_line": 60}}

RULES:
1. Call ONE tool per turn. Wait for TOOL_RESULT before the next call.
2. Start with get_structure to orient yourself.
3. Cite every factual claim: `path/file.ext:start-end`
4. When done (or after 6 tool calls), write your final answer with NO <tool_call> tags.
5. Distinguish facts (code you read) from inferences.

PRIOR CLAIMS THIS SESSION:
{claims_summary}"""

MAX_TOOL_CALLS = 6


def run_investigation(
    tools_instance,
    messages: list[dict],
    claims_summary: str,
    event_queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """ReAct loop: model outputs <tool_call> tags, we execute and feed results back."""

    def put(event_type: str, data: dict) -> None:
        asyncio.run_coroutine_threadsafe(event_queue.put((event_type, data)), loop).result()

    client = _get_client()
    system_content = SYSTEM.format(claims_summary=claims_summary)
    local_messages = [{"role": "system", "content": system_content}] + list(messages)

    try:
        for step in range(MAX_TOOL_CALLS + 1):
            response = client.chat.completions.create(
                model=MODEL,
                messages=local_messages,
                max_tokens=1024,
                temperature=0.1,
            )
            text = (response.choices[0].message.content or "").strip()

            # Look for a tool call anywhere in the response
            match = re.search(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)

            if not match or step == MAX_TOOL_CALLS:
                # Final answer — clean up any stray tool_call syntax
                answer = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL).strip()
                put("answer_complete", {"text": answer or text})
                break

            # Parse the tool call JSON
            try:
                tc = json.loads(match.group(1))
                name = str(tc["name"])
                args = tc.get("input", {})
                if not isinstance(args, dict):
                    args = {}
            except (json.JSONDecodeError, KeyError, TypeError):
                # Bad JSON — treat as final answer
                answer = text.replace(match.group(0), "").strip() or text
                put("answer_complete", {"text": answer})
                break

            # Emit tool events for the UI
            put("tool_start", {"name": name, "id": f"{name}_{step}"})
            put("tool_execute", {"name": name, "input": args})

            result = tools_instance.call(name, args)

            # Cap result size — 8b model has 8192 token total window
            result_str = json.dumps(result)
            if len(result_str) > 2000:
                for key in ("content", "matches", "structure", "files"):
                    if isinstance(result.get(key), str):
                        result[key] = result[key][:1800] + "\n...[truncated — use start_line/end_line for details]"
                        result_str = json.dumps(result)
                        break
                    elif key == "files" and isinstance(result.get(key), list):
                        result[key] = result[key][:50]
                        result_str = json.dumps(result)
                        break

            put("tool_done", {"name": name, "preview": result_str[:300]})

            # Append this turn to the conversation
            local_messages.append({"role": "assistant", "content": text})
            local_messages.append({
                "role": "user",
                "content": f"TOOL_RESULT for {name}:\n{result_str}\n\nContinue your investigation.",
            })

    except Exception as e:
        put("error", {"message": str(e)})
    finally:
        put("done", {})
