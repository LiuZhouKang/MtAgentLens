"""Application-layer ("text + tool-call log") baseline, R-Judge protocol.

Pipeline
--------
``trace_export``  run artifacts -> neutral ``mtagentlens.app_trace.v1`` JSON
``record``        ``app_trace.v1`` -> R-Judge judge-input record (no labels/GT)
``protocol``      pinned upstream protocol: prompts, message construction, parse
``judge``         two-step judge adapter with injectable client, cache, resume
``cache``         content-addressed response cache

The package never reads ground truth and never computes detection metrics, so
it cannot fabricate TPR/FPR numbers: labelling and metrics belong to the
evaluation layer, which pairs judge outputs with the frozen dataset.
"""

from __future__ import annotations

from .cache import CACHE_SCHEMA, ResponseCache
from .judge import (
    AnthropicMessagesClient,
    ExecutionMode,
    JudgeClient,
    JudgeClientUnavailable,
    JudgeConfig,
    JudgeRunner,
    JudgeVerdict,
    RJudgeJudge,
    RESULT_SCHEMA,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    STATUS_TRACE_INCOMPLETE,
    SUMMARY_SCHEMA,
    UnavailableClient,
)
from .record import (
    RECORD_SCHEMA,
    MissingTraceError,
    RJudgeRecord,
    record_sha256,
    trace_to_record,
)
from .trace_export import (
    APP_TRACE_SCHEMA,
    EXPORTER_VERSION,
    TruncationPolicy,
    export_run_trace,
    export_traces,
    load_trace,
)
from .protocol import (
    ACCESS_DATE,
    ARXIV_ID,
    DEFAULT_LABEL_PARSE_MODE,
    LABEL_PROMPT,
    PINNED_COMMIT,
    REASON_PROMPT,
    JudgeParseError,
    LabelParseMode,
    ParseErrorCode,
    RecordShapeError,
    build_judge_messages,
    build_step_one_messages,
    build_step_two_messages,
    official_normalize_prediction,
    parse_label,
    prompt_bundle_sha256,
    protocol_pin,
    validate_record_shape,
)

__all__ = [
    "ACCESS_DATE",
    "ARXIV_ID",
    "DEFAULT_LABEL_PARSE_MODE",
    "LABEL_PROMPT",
    "PINNED_COMMIT",
    "REASON_PROMPT",
    "JudgeParseError",
    "LabelParseMode",
    "ParseErrorCode",
    "RecordShapeError",
    "build_judge_messages",
    "build_step_one_messages",
    "build_step_two_messages",
    "official_normalize_prediction",
    "parse_label",
    "prompt_bundle_sha256",
    "protocol_pin",
    "validate_record_shape",
    "APP_TRACE_SCHEMA",
    "EXPORTER_VERSION",
    "TruncationPolicy",
    "export_run_trace",
    "export_traces",
    "load_trace",
    "RECORD_SCHEMA",
    "MissingTraceError",
    "RJudgeRecord",
    "record_sha256",
    "trace_to_record",
    "CACHE_SCHEMA",
    "ResponseCache",
    "AnthropicMessagesClient",
    "ExecutionMode",
    "JudgeClient",
    "JudgeClientUnavailable",
    "JudgeConfig",
    "JudgeRunner",
    "JudgeVerdict",
    "RJudgeJudge",
    "RESULT_SCHEMA",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_PARSE_ERROR",
    "STATUS_TRACE_INCOMPLETE",
    "SUMMARY_SCHEMA",
    "UnavailableClient",
]
