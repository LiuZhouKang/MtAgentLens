# MtAgentLens Local Tool Comparison Summary

## Round 1 guardrails

- No cloud APIs were used.
- No LLM Judge was used.
- No runtime replay was performed.
- Comparator inputs were sanitized scenario artifacts and fixture files, not MtAgentLens-derived claims or alerts.
- Frozen precision and F1 are not reported without a verified benign set.
- Unsupported cases are excluded from metric denominators and reported separately as unsupported_scenarios.
