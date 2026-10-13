"""Check the setup works: loads the key from .env and makes one tool call.

Run from the project folder:  python -m scripts.hello_sarvam
"""
from agent.sarvam_client import chat

tools = [{
    "type": "function",
    "function": {
        "name": "get_next_emi",
        "description": "Get the next EMI due date and amount for a customer's loan",
        "parameters": {
            "type": "object",
            "properties": {"loan_id": {"type": "string", "description": "Loan ID, e.g. L123"}},
            "required": ["loan_id"],
        },
    },
}]

result = chat(
    messages=[{"role": "user", "content": "Mera loan L123 hai, agli EMI kab hai?"}],
    tools=tools,
)

for tc in result["tool_calls"]:
    print("Tool called:", tc["function"]["name"], tc["function"]["arguments"])
print("Prompt tokens:", result["usage"].get("prompt_tokens"))
print("Latency:", result["latency_ms"], "ms")
print("Setup OK")
