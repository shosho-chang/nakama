"""Entry point: python -m gateway."""

from gateway.bot import run
from shared.llm_context import set_runtime_group

if __name__ == "__main__":
    set_runtime_group("gateway")  # ADR-070：S1a 依這個值把 gateway 的 Claude 呼叫切到 L1
    run()
