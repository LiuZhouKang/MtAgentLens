// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// labels.rs — Provenance lattice for OS-observed agent content.
//
// The monitor sees two kinds of content: what the user/system authored, and
// what the agent retrieved from files, command output or the network. An
// action that carries retrieved content is only a violation when the content
// was neither authorized by the user nor safe to use. These labels make that
// distinction explicit; they are the OS-observable analogue of the integrity
// and confidentiality labels used by CaMeL/Fides at the application layer.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Origin {
    /// User prompt, system prompt, or an instruction the user authored.
    Authored,
    /// File content, command output, tool result, or network response.
    Retrieved,
    /// Model output (a tool call or a reply) built from its context.
    Derived,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Integrity {
    Trusted,
    Untrusted,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Confidentiality {
    Public,
    Private,
    Secret,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Authorization {
    /// The user named this content/file and asked for it to be used.
    UserNamed,
    /// The user did not ask for this content.
    Unrequested,
    /// No authenticated permission root establishes whether this content is
    /// authorized. A source match is evidence, not proof of denial.
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProvenanceTag {
    pub origin: Origin,
    pub integrity: Integrity,
    pub confidentiality: Confidentiality,
    pub authorization: Authorization,
}

impl ProvenanceTag {
    pub fn authored_user() -> Self {
        Self {
            origin: Origin::Authored,
            integrity: Integrity::Trusted,
            confidentiality: Confidentiality::Public,
            authorization: Authorization::UserNamed,
        }
    }

    pub fn authored_secret() -> Self {
        Self {
            origin: Origin::Authored,
            integrity: Integrity::Trusted,
            confidentiality: Confidentiality::Secret,
            authorization: Authorization::UserNamed,
        }
    }

    pub fn retrieved_untrusted(source_is_secret: bool) -> Self {
        Self {
            origin: Origin::Retrieved,
            integrity: Integrity::Untrusted,
            confidentiality: if source_is_secret {
                Confidentiality::Secret
            } else {
                Confidentiality::Public
            },
            authorization: Authorization::Unrequested,
        }
    }

    /// A retrieved span from a source the user explicitly named.
    ///
    /// `UserNamed` here is source-selection metadata: the user pointed at the
    /// source, not authorized every instruction inside it. Action
    /// authorization remains unknown unless an authenticated permission root
    /// supplies it.
    pub fn retrieved_user_named(source_is_secret: bool) -> Self {
        Self {
            origin: Origin::Retrieved,
            integrity: Integrity::Untrusted,
            confidentiality: if source_is_secret {
                Confidentiality::Secret
            } else {
                Confidentiality::Public
            },
            authorization: Authorization::UserNamed,
        }
    }

    /// A tool call derived from these inputs.
    ///
    /// Integrity is `min`: one untrusted input makes the derived call
    /// untrusted (the CaMeL/Fides rule). Confidentiality is `max`: the call
    /// must not launder a secret down to public. Authorization is
    /// propagated as uncertainty: `Unknown` if any input is unknown, otherwise
    /// `Unrequested` if any input was unrequested, otherwise `UserNamed`.
    /// Empty inputs are `Unknown`: the absence of a permission root is not
    /// proof of denial.
    pub fn derived_from(inputs: &[ProvenanceTag]) -> Self {
        if inputs.is_empty() {
            return Self {
                origin: Origin::Derived,
                integrity: Integrity::Trusted,
                confidentiality: Confidentiality::Public,
                authorization: Authorization::Unknown,
            };
        }
        let integrity = if inputs.iter().any(|t| t.integrity == Integrity::Untrusted) {
            Integrity::Untrusted
        } else {
            Integrity::Trusted
        };
        let confidentiality = inputs
            .iter()
            .map(|t| t.confidentiality)
            .max()
            .unwrap_or(Confidentiality::Public);
        let authorization = if inputs
            .iter()
            .any(|t| t.authorization == Authorization::Unknown)
        {
            Authorization::Unknown
        } else if inputs
            .iter()
            .any(|t| t.authorization == Authorization::Unrequested)
        {
            Authorization::Unrequested
        } else {
            Authorization::UserNamed
        };
        Self {
            origin: Origin::Derived,
            integrity,
            confidentiality,
            authorization,
        }
    }

    pub fn is_untrusted_unrequested(&self) -> bool {
        self.integrity == Integrity::Untrusted && self.authorization == Authorization::Unrequested
    }

    /// Whether this tag is an untrusted execution candidate.
    ///
    /// `Unknown` qualifies because a source match does not establish whether
    /// the action was authorized; it is a candidate for review, never a
    /// violation by itself. `Unrequested` remains a candidate for the legacy
    /// selection policy.
    pub fn is_untrusted_execution_candidate(&self) -> bool {
        self.integrity == Integrity::Untrusted
            && matches!(
                self.authorization,
                Authorization::Unknown | Authorization::Unrequested
            )
    }

    pub fn is_secret(&self) -> bool {
        self.confidentiality == Confidentiality::Secret
    }

    /// Human-readable summary for the Observer's structured evidence.
    pub fn summary(&self) -> String {
        format!(
            "origin={:?} integrity={:?} confidentiality={:?} authorization={:?}",
            self.origin, self.integrity, self.confidentiality, self.authorization
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_untrusted_input_makes_derived_untrusted() {
        let tag = ProvenanceTag::derived_from(&[
            ProvenanceTag::authored_user(),
            ProvenanceTag::retrieved_untrusted(false),
        ]);
        assert_eq!(tag.integrity, Integrity::Untrusted);
        assert_eq!(tag.authorization, Authorization::Unrequested);
        assert!(tag.is_untrusted_unrequested());
    }

    #[test]
    fn user_named_retrieval_keeps_authorization() {
        let tag = ProvenanceTag::derived_from(&[ProvenanceTag::retrieved_user_named(false)]);
        assert_eq!(tag.integrity, Integrity::Untrusted);
        assert_eq!(tag.authorization, Authorization::UserNamed);
        assert!(!tag.is_untrusted_unrequested());
    }

    #[test]
    fn confidentiality_is_max_not_min() {
        let tag = ProvenanceTag::derived_from(&[
            ProvenanceTag::authored_user(),
            ProvenanceTag::retrieved_untrusted(true),
        ]);
        assert_eq!(tag.confidentiality, Confidentiality::Secret);
    }

    #[test]
    fn unknown_is_a_candidate_but_not_an_exact_unrequested_violation() {
        let tag = ProvenanceTag {
            authorization: Authorization::Unknown,
            ..ProvenanceTag::retrieved_untrusted(false)
        };
        assert!(tag.is_untrusted_execution_candidate());
        assert!(!tag.is_untrusted_unrequested());
    }

    #[test]
    fn authored_is_trusted_and_named() {
        let tag = ProvenanceTag::authored_user();
        assert_eq!(tag.integrity, Integrity::Trusted);
        assert_eq!(tag.authorization, Authorization::UserNamed);
        assert!(!tag.is_untrusted_unrequested());
    }

    #[test]
    fn empty_derivation_authorization_is_unknown_not_denied() {
        let tag = ProvenanceTag::derived_from(&[]);
        assert_eq!(tag.authorization, Authorization::Unknown);
    }

    #[test]
    fn unknown_authorization_propagates_through_derivation() {
        let unknown = ProvenanceTag {
            authorization: Authorization::Unknown,
            ..ProvenanceTag::retrieved_untrusted(false)
        };
        let tag = ProvenanceTag::derived_from(&[ProvenanceTag::authored_user(), unknown]);
        assert_eq!(tag.integrity, Integrity::Untrusted);
        assert_eq!(tag.authorization, Authorization::Unknown);
        assert!(!tag.is_untrusted_unrequested());
    }
}
