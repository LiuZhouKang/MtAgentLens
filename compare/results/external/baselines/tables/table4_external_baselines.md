| Method (input) | TPR | FPR | Precision | F1 | TPR_A | TPR_B |
|---|---|---|---|---|---|---|
| MtAgentLens (OS + app) | 0.793 | 0.057 | 0.920 | 0.852 | 0.750 | 0.889 |
| Agent Sentinel-style (task) | 0.138 | 0.114 | 0.500 | 0.216 | 0.150 | 0.111 |
| LLM Guard (task + fixture) | 0.138 | 0.286 | 0.286 | 0.186 | 0.150 | 0.111 |
| Meta Prompt Guard (task + fixture) | 0.517 | 0.571 | 0.429 | 0.469 | 0.350 | 0.889 |
| R-Judge (app trace) | 0.552 | 0.029 | 0.941 | 0.696 | 0.550 | 0.556 |
