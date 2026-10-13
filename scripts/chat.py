"""Talk to the agent in your terminal, as if you were the caller.

Run from the project folder:
    python -m scripts.chat                      # customer C001, local backend
    python -m scripts.chat --customer C003      # a different demo customer
    python -m scripts.chat --http               # use the running API (failure switch works)

Commands while chatting:  /reset  start a new call    /quit  exit
"""
import argparse

from agent.loop import Agent

GREY, GREEN, YELLOW, RED, END = "\033[90m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def show(turn):
    for i, m in enumerate(turn.model_calls, 1):
        print(f"{GREY}  model call {i}: {m.tools_offered} tools offered, {m.prompt_tokens:,} prompt tokens, "
              f"{m.latency_ms} ms{END}")
        for c in m.tool_calls:
            status = f"{GREEN}ok{END}" if c["ok"] else f"{RED}{c['error']}{END}"
            print(f"{YELLOW}    -> {c['name']}({c['arguments']}) {status}{END}")
    print(f"{GREEN}Agent:{END} {turn.reply}")
    print(f"{GREY}  turn total: {turn.prompt_tokens:,} prompt tokens, {turn.total_ms} ms{END}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", default="C001")
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args()

    def new_agent():
        return Agent(customer_id=args.customer, executor_mode="http" if args.http else "local")

    agent = new_agent()
    print(f"Calling as {args.customer}. Type /reset for a new call, /quit to exit.\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            agent = new_agent()
            print("--- new call ---\n")
            continue
        show(agent.respond(text))
    if agent.log_path and agent.log_path.exists():
        print(f"Log saved to {agent.log_path}")


if __name__ == "__main__":
    main()
