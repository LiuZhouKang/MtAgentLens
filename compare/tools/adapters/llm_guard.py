"""Run the official llm-guard engine over a frozen prompt artifact."""
from compare.tools.adapters.official_prompt import OfficialPromptAdapter


class Adapter(OfficialPromptAdapter):
    engine = "llm-guard"
