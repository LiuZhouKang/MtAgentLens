# InjecAgent Dataset

Source: https://github.com/uiuc-kang-lab/InjecAgent
Paper: "InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated LLM Agents" (USENIX Security 2025)

Files:
- test_cases_dh_base.json (510 cases): Direct Harm, base test cases
- test_cases_dh_enhanced.json (510 cases): Direct Harm, enhanced test cases
- test_cases_ds_base.json (544 cases): Direct/Indirect Safety, base
- test_cases_ds_enhanced.json (544 cases): Direct/Indirect Safety, enhanced
- tools.json (38 tools): Tool definitions used in test cases

Total: 2,108 test cases

Attack Types: direct_prompt_injection, indirect_prompt_injection,
  tool_response_injection, parameter_injection, combined_injection
