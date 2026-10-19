// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Tool-name vocabulary shared by every intent path.
//!
//! # Why this module exists
//!
//! Two `classify_intent_class` functions used to exist, in
//! `types/support_types.rs` and `evidence/intent_extractor.rs`, backed by two
//! hard-coded tables. The tables were **not** copies of each other — each knew
//! tool names the other did not:
//!
//! | Only in the legacy table | Only in the live-extractor table |
//! |---|---|
//! | `TaskCreate`, `TaskUpdate`, `Skill`, `AskUserQuestion`, `Agent`, `SendMessage` (Orchestration) | `Task`, `TodoWrite`, `TaskOutput`, `ExitPlanMode`, `EnterPlanMode` (Orchestration) |
//! | `text_response` (SideEffect) | `Grep`, `NotebookEdit`, `LS` (SideEffect) |
//!
//! and they **contradicted** each other on `Bash`: `SideEffect` in one,
//! `Orchestration` in the other. That is not cosmetic. `Bash` is the tool whose
//! intents the correlator links to shell actions, and
//! `IntentEvent::refresh_matching_eligibility` gates correlation on
//! `intent_class == SideEffect` — so with the live table's value a Bash intent
//! could never be matched, and classification differed depending on which path
//! built the intent.
//!
//! # The resolution
//!
//! One table, the **union** of both, with `Bash` as `SideEffect`:
//!
//! * A bash invocation performs an observable action; it is not agent
//!   coordination. The live table's own comment ("agent coordination, task
//!   planning") argues against the entry it carried.
//! * The legacy correlator's tests require Bash intents to be eligible; they
//!   encode the intent→action link as intended behaviour.
//! * `compare/scripts/evaluate_mtagentlens.py` scores the
//!   `side_effect`/`orchestration` ratio, so this reclassifies Bash intents in
//!   that metric. The change makes the ratio match the table's stated
//!   semantics.
//!
//! # Casing contract
//!
//! `IntentClass` is persisted through `Display`, i.e. lower-case
//! (`side_effect`), and the evaluation scripts query
//! `WHERE intent_class='side_effect'`. Keep the `Display` spelling.
//!
//! # Generalisation note
//!
//! These lists are Claude Code's tool names. A different agent CLI exposes
//! different ones (`exec`, `wait`, `sessions_yield`, ...), which land in
//! [`IntentClass::Unknown`]. The original name is **never** lost —
//! `IntentEvent::tool_name` keeps it verbatim — so an unknown tool is a missing
//! label, not missing data. Making the table profile-driven is the follow-up.

use crate::responsibility::types::evidence_types::IntentClass;

/// Tools that orchestrate work: agent coordination and task planning.
///
/// Deliberately excludes `Bash` — see the module docs.
const ORCHESTRATION_TOOLS: &[&str] = &[
    "Task",
    "TaskCreate",
    "TaskUpdate",
    "TaskOutput",
    "TodoWrite",
    "ExitPlanMode",
    "EnterPlanMode",
    "Skill",
    "AskUserQuestion",
    "Agent",
    "SendMessage",
];

/// Tools that produce observable side effects.
const SIDE_EFFECT_TOOLS: &[&str] = &[
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "LS",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "text_response",
];

/// Classify a tool name into an [`IntentClass`].
///
/// Unknown names — including every tool of an agent CLI this table has not
/// been taught — yield [`IntentClass::Unknown`]. Callers must keep the original
/// tool name around; the class is a coarse label, not an identity.
pub fn classify_intent_class(tool_name: &str) -> IntentClass {
    if ORCHESTRATION_TOOLS.contains(&tool_name) {
        IntentClass::Orchestration
    } else if SIDE_EFFECT_TOOLS.contains(&tool_name) {
        IntentClass::SideEffect
    } else {
        IntentClass::Unknown
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The class of a tool is now a single fact rather than two contradictory
    /// ones. `Bash` is the case the two old tables disagreed on: it is a side
    /// effect, because a bash invocation performs an action, and because the
    /// correlator has to be able to link it to the shell action it produces.
    #[test]
    fn class_table_is_single_valued() {
        assert_eq!(classify_intent_class("Bash"), IntentClass::SideEffect);
        assert_eq!(classify_intent_class("Read"), IntentClass::SideEffect);
        assert_eq!(classify_intent_class("Grep"), IntentClass::SideEffect);
        assert_eq!(classify_intent_class("LS"), IntentClass::SideEffect);
        assert_eq!(
            classify_intent_class("text_response"),
            IntentClass::SideEffect
        );
        assert_eq!(classify_intent_class("Task"), IntentClass::Orchestration);
        assert_eq!(
            classify_intent_class("TaskCreate"),
            IntentClass::Orchestration
        );
        assert_eq!(
            classify_intent_class("SendMessage"),
            IntentClass::Orchestration
        );
    }

    /// Both old tables contributed names; the union must keep all of them. A
    /// name present in only one table used to be classified differently
    /// depending on which path built the intent.
    #[test]
    fn union_keeps_names_from_both_former_tables() {
        // Formerly only in the legacy (support_types) table:
        for name in [
            "TaskCreate",
            "TaskUpdate",
            "Skill",
            "AskUserQuestion",
            "Agent",
            "SendMessage",
        ] {
            assert_eq!(
                classify_intent_class(name),
                IntentClass::Orchestration,
                "{name}"
            );
        }
        assert_eq!(
            classify_intent_class("text_response"),
            IntentClass::SideEffect
        );

        // Formerly only in the live-extractor table:
        for name in [
            "Task",
            "TodoWrite",
            "TaskOutput",
            "ExitPlanMode",
            "EnterPlanMode",
        ] {
            assert_eq!(
                classify_intent_class(name),
                IntentClass::Orchestration,
                "{name}"
            );
        }
        for name in ["Grep", "NotebookEdit", "LS"] {
            assert_eq!(
                classify_intent_class(name),
                IntentClass::SideEffect,
                "{name}"
            );
        }
    }

    /// A tool of another agent CLI must not silently become a known class.
    #[test]
    fn unknown_tools_stay_unknown() {
        for name in ["exec", "wait", "sessions_yield", "openclaw", "ls"] {
            assert_eq!(classify_intent_class(name), IntentClass::Unknown, "{name}");
        }
    }

    #[test]
    fn classification_is_case_sensitive_by_design() {
        // Tool names are matched exactly: the live extractor takes them from
        // the wire verbatim, and OpenClaw documents them as case-sensitive.
        assert_eq!(classify_intent_class("bash"), IntentClass::Unknown);
    }
}
