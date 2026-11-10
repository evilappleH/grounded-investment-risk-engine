# Upstream attribution: Buffett and Munger personas

This directory contains adapted portions of
[`virattt/ai-hedge-fund`](https://github.com/virattt/ai-hedge-fund), committed
upstream as `fc1bf250ead209ae5f02c39c3d0062c4bb554505`.

| Upstream path | Local path | Adaptation |
| --- | --- | --- |
| `hedge_fund/signals/buffett.py` | `investment_agents/agents/buffett.py` | Retained persona name and system prompt; replaced upstream `LLMAgent` base with a non-executing local adapter. |
| `hedge_fund/signals/munger.py` | `investment_agents/agents/munger.py` | Retained persona name and system prompt; replaced upstream `LLMAgent` base with a non-executing local adapter. |
| `hedge_fund/signals/llm_agent.py`, `hedge_fund/signals/base.py`, `hedge_fund/models.py` | `investment_agents/agents/upstream_adapter.py`, `investment_agents/models.py` | Adapted only the persona/prompt and response-shape boundary. The upstream LLM, data-client, snapshot, conviction and portfolio runtime are intentionally not migrated. |
| `hedge_fund/data/protocol.py`, `hedge_fund/data/models.py`, `hedge_fund/features/snapshot.py` | `investment_agents/data/context.py` | Replaced data-client dependency with an injected, point-in-time offline context. |
| `hedge_fund/signals/test_llm_agents.py` | `investment_agents/tests/test_upstream_personas.py` | Reworked to test adapter preparation/response validation without LLM or data access. |

The upstream project represents these as stylized personas, not actual people or
endorsements. The upstream personas have been migrated, but only as persona plus
offline review adapter: the upstream `LLMAgent` runtime is not present. There is
no real LLM execution, PromptCache, snapshot runtime, or TradingAgents
integration; the local adapter cannot trade.

Copyright (c) 2024 Virat Singh

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
