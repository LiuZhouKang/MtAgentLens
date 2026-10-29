// SPDX-License-Identifier: Apache-2.0 OR MIT
// Candidate identities bind semantic reviews to the facts actually presented.

use crate::responsibility::graph::{GraphPatternMatch, ResponsibilityGraph};
use crate::responsibility::observer::types::CandidateVerdict;
use crate::responsibility::types::{
    Action, ActionCandidateType, ActionOutcome, CorrelationEvidence, NormalizedArg,
    NormalizedResource, ResponsibilityEdgeType,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const MAX_REVIEW_CANDIDATES: usize = 64;

/// Upper bound on evidence ids carried by one candidate.
///
/// A candidate is one fact; a long list of ids adds no review value but every id
/// is serialised into the observer prompt. Unbounded lists blew the model context
/// (observed: a 4.9M-token request against a 1M-token limit), which turned an
/// otherwise-decidable run into `observer_failed`.
pub const MAX_CANDIDATE_EVIDENCE: usize = 8;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct CandidateRef {
    pub id: String,
    pub version: String,
}

/// One action correlated to a presented evidence id.
///
/// `outcome` is the action's own OS outcome. A missing action is explicit
/// `Unknown`; it is never inferred from the operation name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObservedAction {
    /// Action kind when the correlated action exists; `None` when missing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation: Option<ActionCandidateType>,
    #[serde(default)]
    pub outcome: ActionOutcome,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub syscall_ret: Option<i64>,
    /// A capture-limit marker was retained by normalization. False means no
    /// marker was available, not proof that a legacy capture was complete.
    #[serde(default)]
    pub path_capture_incomplete: bool,
    #[serde(default)]
    pub action_id: String,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CandidateSummary {
    pub candidate: CandidateRef,
    /// Stable graph subject: identity refinement does not change this node.
    pub subject_node_id: String,
    pub kind: String,
    pub resource: Option<NormalizedResource>,
    pub evidence_ids: Vec<String>,
    /// Proven-succeeded syscall categories after enrichment. Before enrichment
    /// this may carry graph operation kinds; after enrichment a pending action
    /// is no longer listed here.
    #[serde(default)]
    pub observed_operations: Vec<ActionCandidateType>,
    /// Correlated actions presented with this candidate, including pending,
    /// failed, and missing-action (`Unknown`) outcomes.
    #[serde(default)]
    pub observed_actions: Vec<ObservedAction>,
    /// Graph neighbourhood facts this candidate binds to its version. Internal
    /// identity input, never presented to the reviewer.
    #[serde(skip)]
    pub(crate) graph_context: String,
    #[serde(default)]
    pub task_context_fingerprint: Option<u64>,
    /// Human-readable fact the candidate was derived from.
    ///
    /// Evidence ids are opaque UUIDs. A reviewer cannot tell which file was read
    /// or where data went from an id, so derived candidates state the fact in
    /// words ("credential_read=config/.env; egress=127.0.0.1:9510"). Other
    /// candidates leave this unset and keep their previous payload.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Whether an Unsafe vote may treat this candidate as a violation.
///
/// An Observer that calls *any* suspicious file read Unsafe turns host
/// enumeration into an actionable alert. The protocol says an actionable
/// violation needs a prohibited resource or a data egress, so only those
/// candidates can carry an Unsafe vote:
///
/// * network or credential resources, and the sensitive-egress chain;
/// * secret/prohibited file paths;
/// * writes/deletes/renames under system paths (`/etc`, `/var/log`, `/root`,
///   `.ssh`), which is what log tampering and persistence actually do.
///
/// Heuristic graph patterns are never sufficient on their own, and an
/// unclassified boundary (no resource) keeps the previous conservative
/// behaviour because there is nothing concrete to dismiss.
pub(crate) fn is_security_critical_candidate(candidate: &CandidateSummary) -> bool {
    use crate::responsibility::types::{ActionCandidateType, NormalizedResource};
    if candidate.kind == "pattern" {
        return false;
    }
    let Some(resource) = candidate.resource.as_ref() else {
        return true;
    };
    match resource {
        NormalizedResource::Network { .. } | NormalizedResource::Credential { .. } => true,
        NormalizedResource::File { canonical_path } => {
            let lower = canonical_path.to_lowercase();
            let prohibited = lower == "/etc/shadow"
                || lower == "/etc/sudoers"
                || lower.starts_with("/etc/sudoers.d/")
                || lower.contains("/.ssh/id_")
                || lower.ends_with("/.aws/credentials");
            let writes = candidate.observed_operations.iter().any(|operation| {
                matches!(
                    operation,
                    ActionCandidateType::FileWrite
                        | ActionCandidateType::FileOverwrite
                        | ActionCandidateType::FileDelete
                        | ActionCandidateType::FileRename
                )
            });
            let system_path = lower.starts_with("/etc/")
                || lower.starts_with("/var/log/")
                || lower.starts_with("/root/")
                || lower.contains("/.ssh/");
            prohibited || is_sensitive_path(canonical_path) || (writes && system_path)
        }
        _ => false,
    }
}

/// Safe applies to the bounded input set. Unsafe requires exact citations so a
/// mixed request never assigns one subject's violation to every other subject.
pub fn bind_review(
    candidates: &[CandidateSummary],
    decision: crate::responsibility::types::AlertDecision,
    confidence: f32,
    citations: &[String],
) -> Vec<super::final_decision::Observation> {
    use super::final_decision::Observation;
    use crate::responsibility::types::AlertDecision;
    let reviewed: Vec<_> = candidates
        .iter()
        .take(MAX_REVIEW_CANDIDATES)
        .filter(|candidate| {
            decision != AlertDecision::Unsafe
                || (is_security_critical_candidate(candidate)
                    && citations.iter().any(|citation| {
                        citation == &candidate.candidate.id
                            || candidate.evidence_ids.contains(citation)
                    }))
        })
        .map(|candidate| candidate.candidate.clone())
        .collect();
    if reviewed.is_empty() {
        return vec![match decision {
            AlertDecision::Safe => Observation::ObserverSafe { confidence },

            _ => Observation::ObserverUncertain { confidence },
        }];
    }
    vec![Observation::ScopedReview {
        candidates: reviewed,
        decision,
        confidence,
        cited: citations.len(),
    }]
}

/// Observations that pair every submitted candidate with the single review verdict.
///
/// `ScopedCandidate` asserts "this fact was presented to the reviewer". It must
/// never be recorded for a candidate the reviewer never saw: the aggregator treats
/// a presented-but-unvoted candidate as a review request for the whole run, so a
/// fabricated presentation turns every run that gains a candidate after the prompt
/// into `Uncertain`.
pub fn review_observations(
    candidates: &[CandidateSummary],
    decision: crate::responsibility::types::AlertDecision,
    confidence: f32,
    citations: &[String],
) -> Vec<super::final_decision::Observation> {
    use super::final_decision::Observation;
    let presented = &candidates[..candidates.len().min(MAX_REVIEW_CANDIDATES)];
    let mut observations: Vec<_> = presented
        .iter()
        .map(|candidate| Observation::ScopedCandidate {
            candidate: candidate.candidate.clone(),
        })
        .collect();
    observations.extend(bind_review(presented, decision, confidence, citations));
    observations
}

/// Validate one structured verdict per presented candidate.
///
/// The contract is exact:
///  * every returned id must be one of the presented candidate ids,
///  * no id may be returned twice,
///  * confidence must be in [0.0, 1.0],
///  * every presented id must receive exactly one verdict.
///
/// An empty list is not an error: it is the explicit batch fallback for older
/// or limited providers (see review_observations_with_output).
///
/// A None presented set performs the structural checks only, used while
/// parsing a response before the request's candidate set is bound to it.
pub fn validate_candidate_verdicts(
    presented: Option<&[CandidateSummary]>,
    verdicts: &[CandidateVerdict],
) -> Result<(), crate::responsibility::observer::types::ObserverError> {
    use crate::responsibility::observer::types::ObserverError;
    use std::collections::HashSet;

    if verdicts.is_empty() {
        return Ok(());
    }
    let presented_ids: Option<HashSet<&str>> = presented.map(|candidates| {
        candidates
            .iter()
            .take(MAX_REVIEW_CANDIDATES)
            .map(|candidate| candidate.candidate.id.as_str())
            .collect()
    });
    let mut seen: HashSet<&str> = HashSet::new();
    for verdict in verdicts {
        if verdict.candidate_id.trim().is_empty() {
            return Err(ObserverError::InvalidCandidateId(
                verdict.candidate_id.clone(),
            ));
        }
        if !(0.0..=1.0).contains(&verdict.confidence) {
            return Err(ObserverError::InvalidConfidence(verdict.confidence));
        }
        if let Some(ids) = &presented_ids {
            if !ids.contains(verdict.candidate_id.as_str()) {
                return Err(ObserverError::InvalidCandidateId(
                    verdict.candidate_id.clone(),
                ));
            }
        }
        if !seen.insert(verdict.candidate_id.as_str()) {
            return Err(ObserverError::DuplicateCandidateId(
                verdict.candidate_id.clone(),
            ));
        }
    }
    if let Some(ids) = &presented_ids {
        if let Some(missing) = ids.iter().find(|id| !seen.contains(*id)) {
            return Err(ObserverError::MissingCandidateVerdict(
                (*missing).to_string(),
            ));
        }
    }
    Ok(())
}

/// Turn per-candidate verdicts into scoped review observations.
///
/// Each presented candidate receives exactly one vote with its own decision,
/// confidence and cited count; verdicts that share a decision/confidence/cited
/// triple are grouped into one ScopedReview for compact persistence. A verdict
/// for an id that was not presented is ignored (validation rejects it before
/// this point, so this is defence in depth).
///
/// The existing aggregation is unchanged: the aggregator expands each
/// ScopedReview into one ObserverSafe/ObserverUnsafe/ObserverUncertain vote per
/// listed candidate, so a group is only a storage detail.
pub fn bind_candidate_verdicts(
    candidates: &[CandidateSummary],
    verdicts: &[crate::responsibility::observer::types::CandidateVerdict],
) -> Vec<super::final_decision::Observation> {
    use super::final_decision::Observation;
    use crate::responsibility::observer::types::ObserverDecision;
    use crate::responsibility::types::AlertDecision;
    use std::collections::{BTreeMap, HashMap};

    let presented = &candidates[..candidates.len().min(MAX_REVIEW_CANDIDATES)];
    let by_id: HashMap<&str, &CandidateRef> = presented
        .iter()
        .map(|candidate| (candidate.candidate.id.as_str(), &candidate.candidate))
        .collect();
    let mut groups: BTreeMap<(u8, u32, usize), Vec<CandidateRef>> = BTreeMap::new();
    for verdict in verdicts {
        let Some(reference) = by_id.get(verdict.candidate_id.as_str()) else {
            continue;
        };
        let order = match verdict.decision {
            ObserverDecision::Safe => 0u8,
            ObserverDecision::Unsafe => 1u8,
            ObserverDecision::Uncertain => 2u8,
        };
        groups
            .entry((order, verdict.confidence.to_bits(), verdict.cited))
            .or_default()
            .push((*reference).clone());
    }
    groups
        .into_iter()
        .map(
            |((order, bits, cited), candidates)| Observation::ScopedReview {
                candidates,
                decision: match order {
                    0 => AlertDecision::Safe,
                    1 => AlertDecision::Unsafe,
                    _ => AlertDecision::Uncertain,
                },
                confidence: f32::from_bits(bits),
                cited,
            },
        )
        .collect()
}

/// The review contract carried by one Observer output.
///
/// per_candidate means the votes came from the structured per-candidate list;
/// batch_fallback means the provider returned no list and the batch decision
/// applies to every presented candidate. The same marker is recorded in
/// ObserverOutput.review_provenance by the classification engine.
pub fn review_contract(
    output: &crate::responsibility::observer::types::ObserverOutput,
) -> &'static str {
    use crate::responsibility::observer::types::{
        REVIEW_CONTRACT_BATCH_FALLBACK, REVIEW_CONTRACT_PER_CANDIDATE,
    };
    if output.candidate_verdicts.is_empty() {
        REVIEW_CONTRACT_BATCH_FALLBACK
    } else {
        REVIEW_CONTRACT_PER_CANDIDATE
    }
}

/// Build review observations from one Observer output.
///
/// This is the runtime integration point: it produces the same
/// ScopedCandidate presentation markers as review_observations, then uses the
/// per-candidate verdicts when present. With no per-candidate list it
/// reproduces the batch verdict exactly (old/limited providers and old
/// checkpoints), so the batch path is not silently weakened.
pub fn review_observations_with_output(
    candidates: &[CandidateSummary],
    output: &crate::responsibility::observer::types::ObserverOutput,
) -> Vec<super::final_decision::Observation> {
    use super::final_decision::Observation;
    use crate::responsibility::observer::types::ObserverDecision;
    use crate::responsibility::types::AlertDecision;

    let presented = &candidates[..candidates.len().min(MAX_REVIEW_CANDIDATES)];
    let mut observations: Vec<_> = presented
        .iter()
        .map(|candidate| Observation::ScopedCandidate {
            candidate: candidate.candidate.clone(),
        })
        .collect();
    if output.candidate_verdicts.is_empty() {
        let decision = match output.decision {
            ObserverDecision::Safe => AlertDecision::Safe,
            ObserverDecision::Unsafe => AlertDecision::Unsafe,
            ObserverDecision::Uncertain => AlertDecision::Uncertain,
        };
        observations.extend(bind_review(
            presented,
            decision,
            output.confidence,
            &output.evidence_cited,
        ));
    } else {
        observations.extend(bind_candidate_verdicts(
            presented,
            &output.candidate_verdicts,
        ));
    }
    observations
}

/// Final-graph candidates that no review verdict covers yet.
///
/// Phase 2 can link evidence after the last review request; those candidates have
/// to be reviewed before they may influence the authoritative verdict. This helper
/// does not enrich outcomes; callers that present or record a review must enrich
/// the candidates first and use `unreviewed_candidates_of` on those exact facts.
pub fn unreviewed_candidates(
    observations: &[super::final_decision::Observation],
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
    sensitive_egress: bool,
) -> Vec<CandidateSummary> {
    unreviewed_candidates_of(
        observations,
        graph_candidates_with(graph, patterns, sensitive_egress),
    )
}

/// Filter already-built (and, for a completed review contract, enriched)
/// candidates against stored review verdicts.
///
/// Rebuilding candidates after a review would produce a different version and
/// lose an accepted verdict, so callers that enrich must pass those exact
/// candidates here.
pub fn unreviewed_candidates_of(
    observations: &[super::final_decision::Observation],
    candidates: Vec<CandidateSummary>,
) -> Vec<CandidateSummary> {
    use super::final_decision::Observation;
    let reviewed: std::collections::HashSet<&CandidateRef> = observations
        .iter()
        .filter_map(|observation| match observation {
            Observation::ScopedReview { candidates, .. } => Some(candidates.iter()),
            _ => None,
        })
        .flatten()
        .collect();
    candidates
        .into_iter()
        .filter(|candidate| !reviewed.contains(&candidate.candidate))
        .collect()
}

/// Sorted, deduplicated, bounded evidence ids.
fn bounded_evidence(mut ids: Vec<String>) -> Vec<String> {
    ids.sort();
    ids.dedup();
    ids.truncate(MAX_CANDIDATE_EVIDENCE);
    ids
}

fn digest(value: &impl Serialize) -> String {
    hex::encode(Sha256::digest(
        serde_json::to_vec(value).expect("candidate fields are serializable"),
    ))
}

/// Version over every fact presented to a reviewer. Any change to a correlated
/// outcome or evidence id invalidates the previous review.
fn refresh_candidate_version(candidate: &mut CandidateSummary) {
    candidate.candidate.version = digest(&(
        &candidate.candidate.id,
        &candidate.subject_node_id,
        &candidate.kind,
        &candidate.resource,
        &candidate.evidence_ids,
        &candidate.observed_operations,
        &candidate.observed_actions,
        &candidate.graph_context,
        &candidate.task_context_fingerprint,
        &candidate.detail,
    ));
}

/// Bind a review to the captured task without promoting it to authorization.
pub fn bind_task_context(candidates: &mut [CandidateSummary], fingerprint: u64) {
    for candidate in candidates {
        candidate.task_context_fingerprint = Some(fingerprint);
        refresh_candidate_version(candidate);
    }
}

/// Join presented evidence ids to correlated actions.
///
/// `CorrelationEvidence.evidence_id -> action_id -> Action` is the only
/// completion proof. `observed_operations` keeps proven successes; pending and
/// failed attempts stay visible in `observed_actions`. A missing action is an
/// explicit `Unknown`, never a completion inferred from the operation name.
pub fn enrich_candidate_outcomes(
    candidates: &mut [CandidateSummary],
    evidence: &[CorrelationEvidence],
    actions: &[Action],
) {
    use std::collections::{BTreeMap, HashMap, HashSet};

    // Only evidence ids referenced by a candidate are needed. Building a map of
    // every persisted/streamed correlation made each trigger O(all evidence)
    // and dominated multi-agent finalization (448k rows / 7 GB RSS).
    let needed: HashSet<&str> = candidates
        .iter()
        .flat_map(|candidate| candidate.evidence_ids.iter().map(String::as_str))
        .collect();
    let evidence_by_id: HashMap<&str, &CorrelationEvidence> = evidence
        .iter()
        .filter(|record| needed.contains(record.evidence_id.as_str()))
        .map(|record| (record.evidence_id.as_str(), record))
        .collect();
    let action_by_id: HashMap<&str, &Action> = actions
        .iter()
        .map(|action| (action.action_id.as_str(), action))
        .collect();

    for candidate in candidates {
        let mut observed: BTreeMap<String, ObservedAction> = BTreeMap::new();
        for evidence_id in &candidate.evidence_ids {
            let Some(link) = evidence_by_id.get(evidence_id.as_str()) else {
                continue;
            };
            let action = action_by_id.get(link.action_id.as_str()).copied();
            let entry = observed
                .entry(link.action_id.to_string())
                .or_insert_with(|| ObservedAction {
                    operation: action.map(|action| action.action_type.clone()),
                    outcome: action.map_or(ActionOutcome::Unknown, |action| action.outcome),
                    syscall_ret: action.and_then(|action| action.syscall_ret),
                    path_capture_incomplete: action.is_some_and(|action| action.normalized_args.iter().any(|arg| matches!(arg, crate::responsibility::types::NormalizedArg::PathCaptureIncomplete { .. }))),
                    action_id: link.action_id.to_string(),
                    evidence_ids: Vec::new(),
                });
            entry.evidence_ids.push(evidence_id.clone());
        }

        let mut observed: Vec<_> = observed.into_values().collect();
        for action in &mut observed {
            action.evidence_ids.sort();
            action.evidence_ids.dedup();
        }
        let mut operations: Vec<_> = observed
            .iter()
            .filter(|action| action.outcome.is_success())
            .filter_map(|action| action.operation.clone())
            .collect();
        operations.sort_by_key(|operation| format!("{operation:?}"));
        operations.dedup();

        candidate.observed_operations = operations;
        candidate.observed_actions = observed;
        refresh_candidate_version(candidate);
    }
}

/// Exact facts submitted to a semantic review. If there are no structural
/// candidates, graph-observed IC operations can still be reviewed without
/// pretending a requested tool call was executed or completed.
pub fn review_candidates(
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
) -> Vec<CandidateSummary> {
    review_candidates_with(graph, patterns, false)
}

/// Review candidates under an explicit configuration.
pub fn review_candidates_with(
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
    sensitive_egress: bool,
) -> Vec<CandidateSummary> {
    let candidates = graph_candidates_with(graph, patterns, sensitive_egress);
    if !candidates.is_empty() {
        return candidates;
    }
    let mut completed: Vec<_> = graph
        .all_edges()
        .filter(|edge| {
            edge.edge_type == ResponsibilityEdgeType::IntentCausesAction
                && edge.operation.is_some()
                && !edge.evidence_refs.is_empty()
        })
        .map(|edge| {
            let mut evidence_ids: Vec<_> =
                edge.evidence_refs.iter().map(ToString::to_string).collect();
            evidence_ids.sort();
            let graph_context = digest(&(&evidence_ids, &edge.operation, edge.confidence));
            let mut candidate = CandidateSummary {
                candidate: CandidateRef {
                    id: format!("operation:{}", edge.edge_id),
                    version: String::new(),
                },
                subject_node_id: edge.source.to_string(),
                kind: "observed_operation".into(),
                resource: graph
                    .get_node(&edge.target)
                    .and_then(|node| node.resource.clone()),
                evidence_ids,
                observed_operations: edge.operation.clone().into_iter().collect(),
                observed_actions: Vec::new(),
                graph_context,
                task_context_fingerprint: None,
                detail: None,
            };
            refresh_candidate_version(&mut candidate);
            candidate
        })
        .collect();
    completed.sort_by(|left, right| left.candidate.cmp(&right.candidate));
    completed
}

/// Longest gap between a credential-class read and an egress still attributed to it.
pub const SENSITIVE_EGRESS_WINDOW_NS: u64 = 600 * 1_000_000_000;

/// Whether a path names a credential-class secret.
///
/// Kept narrow on purpose: the candidate is a review request, so a false positive
/// costs a review, not a verdict. Example/template files are not secrets.
pub fn is_sensitive_path(path: &str) -> bool {
    let lower = path.to_lowercase();
    if lower.ends_with(".example") || lower.ends_with(".sample") || lower.ends_with(".template") {
        return false;
    }
    const SUFFIXES: &[&str] = &[
        ".aws/credentials",
        ".aws/config",
        ".ssh/id_rsa",
        ".ssh/id_ed25519",
        ".ssh/id_ecdsa",
        ".netrc",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".docker/config.json",
        ".kube/config",
        "credentials.json",
        "service-account.json",
    ];
    if SUFFIXES
        .iter()
        .any(|suffix| lower.ends_with(suffix) || lower.contains(&format!("/{suffix}")))
    {
        return true;
    }
    // Any file whose basename ends in `.env` is a configuration secret
    // (`config/service.env`, `prod.env`, `.env`, `.env.local`), not only the
    // literal `.env`. Example/sample/template suffixes were rejected above, so
    // `service.env.example` stays non-sensitive.
    let name = lower.rsplit('/').next().unwrap_or("");
    if name.ends_with(".env") || name.starts_with(".env.") {
        return true;
    }
    // Token/account exports are credential-class even though the extension is
    // CSV. Scoped to security-ish names so ordinary data tables stay benign.
    name.ends_with(".csv")
        && (name.contains("account") || name.contains("credential") || name.contains("secret"))
}

fn node_timestamp(graph: &ResponsibilityGraph, id: &crate::responsibility::types::NodeId) -> u64 {
    graph
        .get_node(id)
        .map(|node| node.timestamp_ns)
        .unwrap_or(0)
}

/// Credential-class paths whose read *completed* in this run.
///
/// `sensitive_egress_candidates` is also built from attempt-only edges: an
/// entry capture or a pending open still names the file. That is enough for a
/// review request, but not for a hard violation. The hard rule additionally
/// requires a succeeded read from the recorded OS actions, so a rejected or
/// unfinished open can never be promoted to Authoritative/Critical.
pub(crate) fn succeeded_sensitive_read_paths(actions: &[Action]) -> Vec<String> {
    let mut paths: Vec<String> = actions
        .iter()
        .filter(|action| action.outcome.is_success())
        .filter(|action| {
            matches!(
                action.action_type,
                ActionCandidateType::FileOpen | ActionCandidateType::FileRead
            )
        })
        .flat_map(|action| action.normalized_args.iter())
        .filter_map(|arg| match arg {
            NormalizedArg::Path(path) => Some(path.clone()),
            NormalizedArg::PathCaptureIncomplete {
                normalized_path, ..
            } => Some(normalized_path.clone()),
            _ => None,
        })
        .filter(|path| is_sensitive_path(path))
        .collect();
    paths.sort();
    paths.dedup();
    paths
}

/// Candidates for "a credential-class file was read, then the same agent sent
/// data over the network".
///
/// The boundary is data leaving the agent, so the destination does not matter:
/// a loopback collection sink is exactly as much an egress as a public host.
/// Every candidate carries the evidence of both halves, so a review can cite
/// them instead of guessing.
pub(crate) fn sensitive_egress_candidates(graph: &ResponsibilityGraph) -> Vec<CandidateSummary> {
    use crate::responsibility::types::NormalizedResource;

    let mut reads: std::collections::HashMap<
        crate::responsibility::types::NodeId,
        Vec<(u64, String, Vec<String>)>,
    > = std::collections::HashMap::new();
    let mut egresses: Vec<(
        crate::responsibility::types::NodeId,
        u64,
        Vec<String>,
        Option<NormalizedResource>,
    )> = Vec::new();

    for edge in graph
        .all_edges()
        .filter(|edge| edge.edge_type == ResponsibilityEdgeType::IntentCausesAction)
    {
        if edge.evidence_refs.is_empty() {
            continue;
        }
        let Some(target) = graph.get_node(&edge.target) else {
            continue;
        };
        let Some(resource) = target.resource.as_ref() else {
            continue;
        };
        let mut evidence: Vec<_> = edge.evidence_refs.iter().map(ToString::to_string).collect();
        evidence.sort();
        match resource {
            NormalizedResource::File { canonical_path } => {
                if is_sensitive_path(canonical_path) {
                    reads.entry(edge.source.clone()).or_default().push((
                        node_timestamp(graph, &edge.target),
                        canonical_path.clone(),
                        evidence,
                    ));
                }
            }
            NormalizedResource::Network { .. } => {
                if matches!(
                    edge.operation,
                    Some(crate::responsibility::types::ActionCandidateType::NetworkConnect)
                        | Some(crate::responsibility::types::ActionCandidateType::NetworkSend)
                ) {
                    egresses.push((
                        edge.source.clone(),
                        node_timestamp(graph, &edge.target),
                        evidence,
                        Some(resource.clone()),
                    ));
                }
            }
            _ => {}
        }
    }

    // One candidate per (agent, credential path, destination). Repeated opens and
    // repeated connects are the same fact; presenting 100 copies buries the
    // signal in the review prompt and lets the reviewer answer "not decisive".
    let mut grouped: std::collections::HashMap<
        (String, String, String, u16),
        (Option<NormalizedResource>, Vec<String>),
    > = std::collections::HashMap::new();
    for (agent, egress_at, egress_evidence, resource) in egresses {
        let Some(agent_reads) = reads.get(&agent) else {
            continue;
        };
        let destination = match resource.as_ref() {
            Some(NormalizedResource::Network { host, port, .. }) => (host.clone(), *port),
            _ => continue,
        };
        for (read_at, path, read_evidence) in agent_reads {
            if *read_at == 0 || egress_at == 0 {
                continue;
            }
            // A connection can be established before the credential read and
            // reused for the later send; connect time is only a proxy for the
            // send, so allow either order inside the window.
            let delta = read_at.abs_diff(egress_at);
            if delta > SENSITIVE_EGRESS_WINDOW_NS {
                continue;
            }
            let entry = grouped
                .entry((
                    agent.to_string(),
                    path.clone(),
                    destination.0.clone(),
                    destination.1,
                ))
                .or_insert_with(|| (resource.clone(), Vec::new()));
            entry.1.extend(read_evidence.iter().cloned());
            entry.1.extend(egress_evidence.iter().cloned());
        }
    }

    let mut candidates = Vec::new();
    for ((agent, path, host, port), (resource, mut evidence_ids)) in grouped {
        evidence_ids.sort();
        evidence_ids.dedup();
        let subject_node_id = agent;
        let mut candidate = CandidateSummary {
            candidate: CandidateRef {
                id: format!(
                    "sensitive_egress:{}",
                    digest(&(&subject_node_id, &path, &host, port))
                ),
                version: String::new(),
            },
            subject_node_id,
            kind: "sensitive_egress".into(),
            resource,
            evidence_ids: bounded_evidence(evidence_ids),
            observed_operations: vec![ActionCandidateType::NetworkConnect],
            observed_actions: Vec::new(),
            graph_context: String::new(),
            task_context_fingerprint: None,
            detail: Some(format!(
                "credential_read={path}; egress={host}:{port}; the task never named this destination"
            )),
        };
        refresh_candidate_version(&mut candidate);
        candidates.push(candidate);
    }
    candidates.sort_by(|left, right| left.candidate.cmp(&right.candidate));
    candidates.dedup_by(|left, right| left.candidate == right.candidate);
    if !candidates.is_empty() {
        eprintln!(
            "[candidates] sensitive_egress: {} credential-to-network pair(s)",
            candidates.len()
        );
    }
    candidates
}

/// Candidates under the default (measured) configuration.
pub fn graph_candidates(
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
) -> Vec<CandidateSummary> {
    graph_candidates_with(graph, patterns, false)
}

/// Collapse repeated policy-boundary questions before the Observer sees them.
///
/// A long run can produce hundreds of PolicyBoundaryCrossing edges for the same
/// subject and the same resource (e.g. the same credential read repeatedly).
/// Each edge is a real fact, but they are one review question: "did this
/// subject cross this boundary for this resource?" Reviewing each edge
/// separately multiplied Observer calls and latency without adding information.
///
/// Merge only exact questions:
/// - same subject node;
/// - same resource (never merge a missing resource, because distinct boundary
///   targets cannot be told apart from the summary alone);
/// - kind is policy_boundary (pattern and sensitive_egress candidates keep
///   their existing semantics).
///
/// Evidence, operations, observed actions and graph contexts are unioned,
/// deduplicated and sorted; the version is recomputed from the merged facts.
/// Singleton candidates keep their original id, so existing review bindings
/// remain valid.
fn coalesce_boundary_candidates(candidates: Vec<CandidateSummary>) -> Vec<CandidateSummary> {
    use std::collections::{BTreeMap, BTreeSet, HashMap};

    fn mergeable(resource: &NormalizedResource) -> bool {
        match resource {
            NormalizedResource::Credential { .. } | NormalizedResource::Network { .. } => true,
            NormalizedResource::File { canonical_path } => is_sensitive_path(canonical_path),
            _ => false,
        }
    }

    let mut groups: HashMap<(String, NormalizedResource), Vec<CandidateSummary>> = HashMap::new();
    let mut out = Vec::new();
    for candidate in candidates {
        if candidate.kind == "policy_boundary"
            && let Some(resource) = candidate.resource.clone()
            && mergeable(&resource)
        {
            groups
                .entry((candidate.subject_node_id.clone(), resource))
                .or_default()
                .push(candidate);
        } else {
            out.push(candidate);
        }
    }

    for ((subject, resource), mut group) in groups {
        group.sort_by(|left, right| left.candidate.cmp(&right.candidate));
        if group.len() == 1 {
            if let Some(candidate) = group.pop() {
                out.push(candidate);
            }
            continue;
        }

        let mut evidence = BTreeSet::new();
        let mut operations = Vec::new();
        let mut actions: BTreeMap<String, ObservedAction> = BTreeMap::new();
        let mut contexts = BTreeSet::new();
        let mut fingerprints = BTreeSet::new();
        let mut details = BTreeSet::new();
        for candidate in &group {
            evidence.extend(candidate.evidence_ids.iter().cloned());
            operations.extend(candidate.observed_operations.iter().cloned());
            for action in &candidate.observed_actions {
                actions
                    .entry(serde_json::to_string(action).unwrap_or_default())
                    .or_insert_with(|| action.clone());
            }
            contexts.insert(candidate.graph_context.clone());
            fingerprints.insert(candidate.task_context_fingerprint);
            if let Some(detail) = &candidate.detail {
                details.insert(detail.clone());
            }
        }

        let mut merged = group.remove(0);
        merged.candidate.id = format!("boundary:merged:{}", digest(&(&subject, &resource)));
        merged.evidence_ids = bounded_evidence(evidence.into_iter().collect());
        operations.sort_by_key(|operation| format!("{operation:?}"));
        operations.dedup();
        merged.observed_operations = operations;
        merged.observed_actions = actions.into_values().collect();
        merged.graph_context = digest(&contexts.iter().collect::<Vec<_>>());
        merged.task_context_fingerprint = if fingerprints.len() == 1 {
            fingerprints.into_iter().next().flatten()
        } else {
            None
        };
        merged.detail = if details.is_empty() {
            None
        } else {
            Some(details.into_iter().collect::<Vec<_>>().join(" | "))
        };
        refresh_candidate_version(&mut merged);
        out.push(merged);
    }

    out.sort_by(|left, right| left.candidate.cmp(&right.candidate));
    out
}

/// Candidates including the experimental content-egress source when enabled.
pub fn graph_candidates_with(
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
    sensitive_egress: bool,
) -> Vec<CandidateSummary> {
    let mut candidates = coalesce_boundary_candidates(graph_candidates_structural(graph, patterns));
    if sensitive_egress {
        candidates.extend(sensitive_egress_candidates(graph));
        candidates.sort_by(|left, right| left.candidate.cmp(&right.candidate));
    }
    candidates
}

fn subject_context(
    graph: &ResponsibilityGraph,
    subject: &crate::responsibility::types::NodeId,
) -> String {
    let mut ids: Vec<_> = graph
        .outgoing_from(subject)
        .iter()
        .chain(graph.incoming_to(subject))
        .collect();
    ids.sort_by(|left, right| left.as_str().cmp(right.as_str()));
    ids.dedup();
    let mut facts: Vec<_> = ids
        .into_iter()
        .filter_map(|id| graph.get_edge(id))
        .map(|edge| {
            let mut evidence: Vec<_> = edge.evidence_refs.iter().map(ToString::to_string).collect();
            evidence.sort();
            (
                edge.edge_id.to_string(),
                &edge.edge_type,
                &edge.operation,
                edge.source.to_string(),
                edge.target.to_string(),
                graph
                    .get_node(&edge.target)
                    .and_then(|node| node.resource.as_ref()),
                edge.confidence,
                evidence,
            )
        })
        .collect();
    facts.sort_by(|left, right| left.0.cmp(&right.0));
    digest(&facts)
}

fn graph_candidates_structural(
    graph: &ResponsibilityGraph,
    patterns: &[GraphPatternMatch],
) -> Vec<CandidateSummary> {
    let mut candidates = Vec::new();
    let mut contexts = std::collections::HashMap::new();
    for edge in graph
        .all_edges()
        .filter(|edge| edge.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing)
    {
        let mut evidence_ids: Vec<_> = edge.evidence_refs.iter().map(ToString::to_string).collect();
        evidence_ids.sort();
        evidence_ids.dedup();
        let resource = graph
            .get_node(&edge.target)
            .and_then(|node| node.resource.clone());
        let mut observed_operations: Vec<_> = graph
            .outgoing_from(&edge.source)
            .iter()
            .filter_map(|id| graph.get_edge(id))
            .filter(|related| {
                related.edge_type == ResponsibilityEdgeType::IntentCausesAction
                    && related
                        .evidence_refs
                        .iter()
                        .any(|id| edge.evidence_refs.contains(id))
            })
            .filter_map(|related| related.operation.clone())
            .collect();
        observed_operations.sort_by_key(|operation| format!("{operation:?}"));
        observed_operations.dedup();
        let graph_context = contexts
            .entry(edge.source.clone())
            .or_insert_with(|| subject_context(graph, &edge.source))
            .clone();
        let mut candidate = CandidateSummary {
            candidate: CandidateRef {
                id: format!("boundary:{}", edge.edge_id),
                version: String::new(),
            },
            subject_node_id: edge.source.to_string(),
            kind: "policy_boundary".into(),
            resource,
            evidence_ids: bounded_evidence(evidence_ids),
            observed_operations,
            observed_actions: Vec::new(),
            graph_context,
            task_context_fingerprint: None,
            detail: None,
        };
        refresh_candidate_version(&mut candidate);
        candidates.push(candidate);
    }
    for pattern in patterns {
        let mut edges = pattern.matched_edge_ids.clone();
        edges.sort();
        let mut facts: Vec<_> = edges
            .iter()
            .filter_map(|id| {
                graph.get_edge(&crate::responsibility::types::EdgeId::from(id.as_str()))
            })
            .map(|edge| {
                let mut refs: Vec<_> = edge.evidence_refs.iter().map(ToString::to_string).collect();
                refs.sort();
                (edge.edge_id.to_string(), refs, edge.confidence)
            })
            .collect();
        facts.sort_by(|a, b| a.0.cmp(&b.0));
        let graph_context = digest(&facts);
        let mut candidate = CandidateSummary {
            candidate: CandidateRef {
                id: format!("pattern:{}:{}", pattern.template_id, digest(&edges)),
                version: String::new(),
            },
            subject_node_id: pattern
                .matched_node_ids
                .iter()
                .min()
                .cloned()
                .unwrap_or_default(),
            kind: format!("graph_pattern:{}", pattern.template_id),
            resource: None,
            evidence_ids: bounded_evidence(
                facts.into_iter().flat_map(|(_, refs, _)| refs).collect(),
            ),
            observed_operations: edges
                .iter()
                .filter_map(|id| {
                    graph.get_edge(&crate::responsibility::types::EdgeId::from(id.as_str()))
                })
                .filter_map(|edge| edge.operation.clone())
                .collect(),
            observed_actions: Vec::new(),
            graph_context,
            task_context_fingerprint: None,
            detail: None,
        };
        refresh_candidate_version(&mut candidate);
        candidates.push(candidate);
    }
    candidates.sort_by(|a, b| a.candidate.cmp(&b.candidate));
    candidates
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::classification::final_decision::{
        Observation, aggregate_final_decision,
    };
    use crate::responsibility::types::{
        Action, ActionCandidateType, ActionId, ActionOutcome, AgentId, AlertDecision,
        CandidateDerivationMode, CorrelationEvidence, EdgeId, EvidenceId, IntentId, MatchLevel,
        NodeId, NormalizedArgs, PhaseIndicator, ProcessId, ResponsibilityEdge,
        ResponsibilityEdgeType, ResponsibilityNode, ResponsibilityNodeType, SignalClass,
    };

    fn node(id: &str, node_type: ResponsibilityNodeType) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type,
            agent_id: Some(AgentId::from("agent-1")),
            resource: None,
            evidence_refs: Vec::new(),
            timestamp_ns: 1,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        }
    }

    fn boundary_graph() -> ResponsibilityGraph {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node("a1", ResponsibilityNodeType::Agent));
        graph.add_node(node("r1", ResponsibilityNodeType::Resource));
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::from("e1"),
            edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
            operation: None,
            source: NodeId::from("a1"),
            target: NodeId::from("r1"),
            evidence_refs: vec![EvidenceId::from("ev-1")],
            confidence: 0.9,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        });
        graph
    }

    fn summary(id: &str, evidence: &[&str]) -> CandidateSummary {
        CandidateSummary {
            candidate: CandidateRef {
                id: id.into(),
                version: "v1".into(),
            },
            subject_node_id: "a1".into(),
            kind: "policy_boundary".into(),
            resource: None,
            evidence_ids: evidence.iter().map(|value| (*value).to_string()).collect(),
            observed_operations: Vec::new(),
            observed_actions: Vec::new(),
            graph_context: String::new(),
            task_context_fingerprint: None,
            detail: None,
        }
    }

    fn boundary_summary(id: &str, evidence: &[&str], path: &str) -> CandidateSummary {
        let mut candidate = summary(id, evidence);
        candidate.resource = Some(NormalizedResource::File {
            canonical_path: path.into(),
        });
        candidate
    }

    #[test]
    fn repeated_boundary_questions_for_one_subject_and_resource_are_coalesced() {
        let mut first = boundary_summary("boundary:a", &["ev-1"], "/tmp/config/.env");
        first.observed_operations = vec![ActionCandidateType::FileOpen];
        let mut second = boundary_summary("boundary:b", &["ev-2", "ev-3"], "/tmp/config/.env");
        second.observed_operations = vec![ActionCandidateType::FileOpen];
        let merged = coalesce_boundary_candidates(vec![first, second]);
        assert_eq!(merged.len(), 1);
        assert!(merged[0].candidate.id.starts_with("boundary:merged:"));
        assert_eq!(
            merged[0].evidence_ids,
            vec!["ev-1".to_string(), "ev-2".to_string(), "ev-3".to_string()]
        );
        assert_eq!(
            merged[0].observed_operations,
            vec![ActionCandidateType::FileOpen]
        );
    }

    #[test]
    fn different_subjects_or_resources_are_never_coalesced() {
        let first = boundary_summary("boundary:a", &["ev-1"], "/tmp/config/.env");
        let mut other_subject = boundary_summary("boundary:b", &["ev-2"], "/tmp/config/.env");
        other_subject.subject_node_id = "a2".into();
        let other_resource = boundary_summary("boundary:c", &["ev-3"], "/tmp/config/.npmrc");
        let merged = coalesce_boundary_candidates(vec![first, other_subject, other_resource]);
        assert_eq!(merged.len(), 3);
    }

    #[test]
    fn a_boundary_without_a_resource_stays_singleton() {
        let first = summary("boundary:a", &["ev-1"]);
        let second = summary("boundary:b", &["ev-2"]);
        let merged = coalesce_boundary_candidates(vec![first, second]);
        assert_eq!(merged.len(), 2);
    }

    #[test]
    fn singleton_boundary_keeps_its_existing_id() {
        let first = boundary_summary("boundary:a", &["ev-1"], "/tmp/config/.env");
        let merged = coalesce_boundary_candidates(vec![first.clone()]);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].candidate.id, first.candidate.id);
    }

    #[test]
    fn reviewed_candidates_never_report_as_unreviewed() {
        let graph = boundary_graph();
        let candidates = review_candidates(&graph, &[]);
        assert_eq!(candidates.len(), 1);
        let observations = review_observations(&candidates, AlertDecision::Safe, 0.9, &[]);
        let verdict = aggregate_final_decision(&observations);
        assert_eq!(verdict.decision, AlertDecision::Safe);
        assert!(
            !verdict
                .provenance
                .iter()
                .any(|entry| entry.starts_with("unreviewed:")),
            "a reviewed candidate must not be reported as unreviewed: {:?}",
            verdict.provenance
        );
    }

    #[test]
    fn an_unpresented_candidate_is_never_marked_presented() {
        let graph = boundary_graph();
        assert_eq!(unreviewed_candidates(&[], &graph, &[], false).len(), 1);
        let candidates = review_candidates(&graph, &[]);
        let observations = review_observations(&candidates, AlertDecision::Safe, 0.9, &[]);
        assert!(unreviewed_candidates(&observations, &graph, &[], false).is_empty());
    }

    #[test]
    fn changed_candidate_facts_require_a_new_review() {
        let mut graph = boundary_graph();
        let candidates = review_candidates(&graph, &[]);
        let observations = review_observations(&candidates, AlertDecision::Safe, 0.9, &[]);
        let mut edge = graph.all_edges().next().unwrap().clone();
        edge.evidence_refs.push(EvidenceId::from("ev-2"));
        graph.add_edge(edge);

        let current = review_candidates(&graph, &[]);
        assert_eq!(current[0].candidate.id, candidates[0].candidate.id);
        assert_ne!(
            current[0].candidate.version,
            candidates[0].candidate.version
        );
        let pending = unreviewed_candidates(&observations, &graph, &[], false);
        assert_eq!(
            pending.len(),
            1,
            "old facts cannot clear the current version"
        );
        assert_eq!(pending[0].candidate, current[0].candidate);
    }

    fn candidate_verdict(
        candidate_id: &str,
        decision: crate::responsibility::observer::types::ObserverDecision,
        confidence: f32,
        cited: usize,
    ) -> crate::responsibility::observer::types::CandidateVerdict {
        crate::responsibility::observer::types::CandidateVerdict {
            candidate_id: candidate_id.into(),
            decision,
            confidence,
            cited,
        }
    }

    fn batch_output(
        decision: crate::responsibility::observer::types::ObserverDecision,
        confidence: f32,
    ) -> crate::responsibility::observer::types::ObserverOutput {
        crate::responsibility::observer::types::ObserverOutput {
            decision,
            severity: crate::responsibility::claims::ClaimSeverity::Info,
            confidence,
            cache_ttl: crate::responsibility::observer::types::CacheTTL::Once,
            authority: crate::responsibility::observer::types::AlertAuthority::Unavailable,
            reasoning: "batch summary".into(),
            attacked_flag: false,
            evidence_cited: vec![],
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            candidate_verdicts: vec![],
            review_provenance: vec![],
        }
    }

    fn scoped_reviews(observations: &[Observation]) -> Vec<&Observation> {
        observations
            .iter()
            .filter(|observation| matches!(observation, Observation::ScopedReview { .. }))
            .collect()
    }

    fn scoped_candidates(observations: &[Observation]) -> Vec<&CandidateRef> {
        observations
            .iter()
            .filter_map(|observation| match observation {
                Observation::ScopedCandidate { candidate } => Some(candidate),
                _ => None,
            })
            .collect()
    }

    /// One verdict per candidate: divergent verdicts must stay attached to
    /// their own candidate instead of being averaged into one batch label.
    #[test]
    fn per_candidate_verdicts_become_one_scoped_vote_per_candidate() {
        use crate::responsibility::observer::types::ObserverDecision;

        let candidates = vec![
            summary("boundary:e1", &["ev-1"]),
            summary("pattern:p:1", &["ev-2"]),
            summary("pattern:p:2", &["ev-3"]),
        ];
        let verdicts = vec![
            candidate_verdict("boundary:e1", ObserverDecision::Unsafe, 0.9, 1),
            candidate_verdict("pattern:p:1", ObserverDecision::Safe, 0.8, 0),
            candidate_verdict("pattern:p:2", ObserverDecision::Uncertain, 0.5, 0),
        ];

        let observations = bind_candidate_verdicts(&candidates, &verdicts);

        for candidate in &candidates {
            let votes = observations
                .iter()
                .filter(|observation| match observation {
                    Observation::ScopedReview { candidates, .. } => {
                        candidates.contains(&candidate.candidate)
                    }
                    _ => false,
                })
                .count();
            assert_eq!(
                votes, 1,
                "{} must receive exactly one scoped vote",
                candidate.candidate.id
            );
        }

        let unsafe_review = scoped_reviews(&observations)
            .into_iter()
            .find_map(|observation| match observation {
                Observation::ScopedReview {
                    candidates,
                    decision,
                    confidence,
                    cited,
                } if *decision == AlertDecision::Unsafe => {
                    Some((candidates.clone(), *confidence, *cited))
                }
                _ => None,
            })
            .expect("unsafe candidate keeps its own verdict");
        assert_eq!(
            unsafe_review.0,
            vec![candidates[0].candidate.clone()],
            "the unsafe verdict must not be spread to safe candidates"
        );
        assert_eq!(unsafe_review.1, 0.9);
        assert_eq!(unsafe_review.2, 1, "the per-candidate cited count is kept");

        let verdict = aggregate_final_decision(&observations);
        assert_eq!(
            verdict.decision,
            AlertDecision::Unsafe,
            "one diverging unsafe candidate must not be diluted by safe ones"
        );
    }

    /// An Unsafe per-candidate verdict that cites nothing is not evidence; the
    /// existing aggregator already treats a zero citation count as uncertain.
    #[test]
    fn per_candidate_unsafe_without_citation_is_uncertain() {
        use crate::responsibility::observer::types::ObserverDecision;

        let candidates = vec![summary("boundary:e1", &["ev-1"])];
        let verdicts = vec![candidate_verdict(
            "boundary:e1",
            ObserverDecision::Unsafe,
            0.9,
            0,
        )];

        let observations = bind_candidate_verdicts(&candidates, &verdicts);
        assert_eq!(
            aggregate_final_decision(&observations).decision,
            AlertDecision::Uncertain
        );
    }

    /// Old/limited providers return no list: the batch decision applies to
    /// every presented candidate, exactly as before this contract.
    #[test]
    fn empty_candidate_verdicts_fall_back_to_the_batch_decision() {
        use crate::responsibility::observer::types::ObserverDecision;

        let candidates = vec![
            summary("boundary:e1", &["ev-1"]),
            summary("pattern:p:1", &["ev-2"]),
        ];
        let output = batch_output(ObserverDecision::Safe, 0.77);

        let observations = review_observations_with_output(&candidates, &output);

        assert_eq!(
            scoped_candidates(&observations),
            candidates.iter().map(|c| &c.candidate).collect::<Vec<_>>(),
            "every presented candidate is still marked presented"
        );
        let reviews = scoped_reviews(&observations);
        assert_eq!(reviews.len(), 1, "the batch verdict is one scoped review");
        match reviews[0] {
            Observation::ScopedReview {
                candidates: reviewed,
                decision,
                confidence,
                ..
            } => {
                assert_eq!(*decision, AlertDecision::Safe);
                assert_eq!(*confidence, 0.77);
                assert_eq!(reviewed.len(), 2);
            }
            other => panic!("unexpected observation: {other:?}"),
        }
        assert_eq!(
            aggregate_final_decision(&observations).decision,
            AlertDecision::Safe
        );
    }

    /// The helper reproduces the old batch path exactly when the provider did
    /// not return a per-candidate list.
    #[test]
    fn batch_fallback_is_identical_to_review_observations() {
        use crate::responsibility::observer::types::ObserverDecision;

        let candidates = vec![
            summary("boundary:e1", &["ev-1"]),
            summary("pattern:p:1", &["ev-2"]),
        ];
        let citations = vec!["ev-1".to_string()];
        let mut output = batch_output(ObserverDecision::Unsafe, 0.8);
        output.evidence_cited = citations.clone();

        let new = review_observations_with_output(&candidates, &output);
        let old = review_observations(&candidates, AlertDecision::Unsafe, 0.8, &citations);
        assert_eq!(new, old);
    }

    #[test]
    fn unsafe_review_keeps_the_unsafe_verdict_without_inventing_votes() {
        let candidates = vec![
            summary("boundary:e1", &["ev-1"]),
            summary("pattern:p:1", &["ev-2"]),
        ];
        let observations = review_observations(
            &candidates,
            AlertDecision::Unsafe,
            0.8,
            &["ev-1".to_string()],
        );
        let verdict = aggregate_final_decision(&observations);
        assert_eq!(verdict.decision, AlertDecision::Unsafe);
        let presented: Vec<_> = observations
            .iter()
            .filter_map(|observation| match observation {
                Observation::ScopedCandidate { candidate } => Some(candidate.id.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(presented, vec!["boundary:e1", "pattern:p:1"]);
    }

    fn egress_graph(
        read_path: &str,
        read_at: u64,
        egress_at: u64,
        egress_agent: &str,
    ) -> ResponsibilityGraph {
        use crate::responsibility::types::{ActionCandidateType, NormalizedResource};
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node("agent-1", ResponsibilityNodeType::Agent));
        graph.add_node(node(egress_agent, ResponsibilityNodeType::Agent));
        let mut file = node("r-file", ResponsibilityNodeType::Resource);
        file.resource = Some(NormalizedResource::File {
            canonical_path: read_path.into(),
        });
        file.timestamp_ns = read_at;
        graph.add_node(file);
        let mut network = node("r-net", ResponsibilityNodeType::Resource);
        network.resource = Some(NormalizedResource::Network {
            host: "127.0.0.1".into(),
            port: 9514,
            scheme: "http".into(),
        });
        network.timestamp_ns = egress_at;
        graph.add_node(network);
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::from("e-read"),
            edge_type: ResponsibilityEdgeType::IntentCausesAction,
            operation: Some(ActionCandidateType::FileOpen),
            source: NodeId::from("agent-1"),
            target: NodeId::from("r-file"),
            evidence_refs: vec![EvidenceId::from("ev-read")],
            confidence: 0.9,
            phase: PhaseIndicator::Provisional {
                timestamp_ns: read_at,
            },
        });
        graph.add_edge(ResponsibilityEdge {
            edge_id: EdgeId::from("e-net"),
            edge_type: ResponsibilityEdgeType::IntentCausesAction,
            operation: Some(ActionCandidateType::NetworkConnect),
            source: NodeId::from(egress_agent),
            target: NodeId::from("r-net"),
            evidence_refs: vec![EvidenceId::from("ev-net")],
            confidence: 0.9,
            phase: PhaseIndicator::Provisional {
                timestamp_ns: egress_at,
            },
        });
        graph
    }

    #[test]
    fn a_credential_read_followed_by_egress_is_a_review_candidate() {
        // Loopback is still an egress: the boundary is the data leaving the agent.
        let graph = egress_graph("config/.aws/credentials", 1_000, 2_000, "agent-1");
        let candidates = graph_candidates_with(&graph, &[], true);
        let egress: Vec<_> = candidates
            .iter()
            .filter(|candidate| candidate.kind == "sensitive_egress")
            .collect();
        assert_eq!(egress.len(), 1, "one read/egress pair, one candidate");
        assert!(egress[0].evidence_ids.contains(&"ev-read".to_string()));
        assert!(egress[0].evidence_ids.contains(&"ev-net".to_string()));
        // Evidence ids are opaque: the readable fact must travel with them, or
        // the reviewer cannot see that a credential file was read at all.
        let detail = egress[0].detail.clone().unwrap_or_default();
        assert!(
            detail.contains("credential_read=config/.aws/credentials"),
            "{detail}"
        );
        assert!(detail.contains("egress=127.0.0.1:9514"), "{detail}");
        assert_eq!(egress[0].subject_node_id, "agent-1");
    }

    #[test]
    fn the_measured_default_does_not_add_egress_candidates() {
        let graph = egress_graph("config/.aws/credentials", 1_000, 2_000, "agent-1");
        assert!(
            graph_candidates(&graph, &[])
                .iter()
                .all(|candidate| candidate.kind != "sensitive_egress")
        );
    }

    #[test]
    fn a_read_without_egress_is_not_a_candidate_but_either_order_pairs() {
        let no_egress = egress_graph("config/.aws/credentials", 1_000, 0, "agent-1");
        assert!(
            graph_candidates_with(&no_egress, &[], true)
                .iter()
                .all(|candidate| candidate.kind != "sensitive_egress")
        );
        // A reused connection can be established before the read; the send
        // still follows, so the pair is a candidate in either order.
        let reversed = egress_graph("config/.aws/credentials", 5_000, 1_000, "agent-1");
        assert!(
            graph_candidates_with(&reversed, &[], true)
                .iter()
                .any(|candidate| candidate.kind == "sensitive_egress")
        );
    }

    #[test]
    fn another_agents_egress_is_not_attributed_to_the_reader() {
        let graph = egress_graph("config/.aws/credentials", 1_000, 2_000, "agent-2");
        assert!(
            graph_candidates_with(&graph, &[], true)
                .iter()
                .all(|candidate| candidate.kind != "sensitive_egress")
        );
    }

    #[test]
    fn a_plain_read_and_an_old_egress_pair_are_bounded_by_the_window() {
        let plain = egress_graph("docs/readme.md", 1_000, 2_000, "agent-1");
        assert!(
            graph_candidates_with(&plain, &[], true)
                .iter()
                .all(|candidate| candidate.kind != "sensitive_egress")
        );
        let stale = egress_graph(
            "config/.aws/credentials",
            1_000,
            1_000 + SENSITIVE_EGRESS_WINDOW_NS + 1,
            "agent-1",
        );
        assert!(
            graph_candidates_with(&stale, &[], true)
                .iter()
                .all(|candidate| candidate.kind != "sensitive_egress")
        );
    }

    #[test]
    fn secrets_are_named_narrowly() {
        for path in [
            "config/.aws/credentials",
            "/home/u/.ssh/id_ed25519",
            "config/.env",
            "config/service.env",
            "deploy/prod.env",
            "a/.netrc",
            "svc/service-account.json",
            "config/accounts.csv",
            "exports/credentials.csv",
        ] {
            assert!(is_sensitive_path(path), "{path} should be sensitive");
        }
        for path in [
            "config/.env.example",
            "docs/readme.md",
            "src/config/.env.template",
            "http://127.0.0.1:9514/collect",
        ] {
            assert!(!is_sensitive_path(path), "{path} should not be sensitive");
        }
    }

    #[test]
    fn candidate_evidence_lists_are_bounded() {
        // Unbounded evidence lists are serialised into the observer prompt and
        // overflowed the model context (4.9M tokens for a 1M-token model).
        let many: Vec<String> = (0..500).map(|i| format!("ev-{i:03}")).collect();
        assert_eq!(bounded_evidence(many.clone()).len(), MAX_CANDIDATE_EVIDENCE);
        let seen: Vec<String> = bounded_evidence(many);
        assert_eq!(seen.first().map(String::as_str), Some("ev-000"));
        assert!(
            seen.windows(2).all(|pair| pair[0] < pair[1]),
            "sorted and unique"
        );
    }

    fn resource_node(
        id: &str,
        resource: NormalizedResource,
        timestamp_ns: u64,
    ) -> ResponsibilityNode {
        ResponsibilityNode {
            node_id: NodeId::from(id),
            node_type: ResponsibilityNodeType::Resource,
            agent_id: None,
            resource: Some(resource),
            evidence_refs: Vec::new(),
            timestamp_ns,
            phase: PhaseIndicator::Provisional { timestamp_ns },
        }
    }

    fn ic_edge(
        id: &str,
        source: &str,
        target: &str,
        operation: Option<crate::responsibility::types::ActionCandidateType>,
        evidence: &[&str],
    ) -> ResponsibilityEdge {
        ResponsibilityEdge {
            edge_id: EdgeId::from(id),
            edge_type: ResponsibilityEdgeType::IntentCausesAction,
            operation,
            source: NodeId::from(source),
            target: NodeId::from(target),
            evidence_refs: evidence
                .iter()
                .map(|value| EvidenceId::from(*value))
                .collect(),
            confidence: 0.9,
            phase: PhaseIndicator::Authoritative {
                timestamp_ns: 1,
                overrides: None,
            },
        }
    }

    #[test]
    fn bounded_evidence_is_sorted_unique_and_bounded() {
        let ids: Vec<String> = (0..12).map(|index| format!("ev-{index:02}")).collect();
        let bounded = bounded_evidence(ids);
        assert_eq!(bounded.len(), MAX_CANDIDATE_EVIDENCE);
        let mut sorted = bounded.clone();
        sorted.sort();
        assert_eq!(bounded, sorted);
        assert_eq!(
            bounded_evidence(vec!["b".into(), "a".into(), "b".into()]),
            vec!["a".to_string(), "b".to_string()]
        );
    }

    #[test]
    fn candidate_detail_is_omitted_unless_present() {
        let without = serde_json::to_value(summary("c1", &[])).expect("serializable");
        assert!(without.get("detail").is_none());
        let mut with_detail = summary("c2", &[]);
        with_detail.detail = Some("credential_read=config/.env; egress=127.0.0.1:9510".into());
        let encoded = serde_json::to_value(with_detail).expect("serializable");
        assert!(
            encoded["detail"]
                .as_str()
                .unwrap()
                .contains("credential_read=")
        );
    }

    #[test]
    fn sensitivity_predicate_accepts_secrets_and_rejects_templates() {
        assert!(is_sensitive_path("/home/agent/config/.env"));
        assert!(is_sensitive_path("/home/agent/config/service.env"));
        assert!(is_sensitive_path("prod.env"));
        assert!(is_sensitive_path("/home/agent/.ssh/id_rsa"));
        assert!(is_sensitive_path("/home/agent/.aws/credentials"));
        assert!(!is_sensitive_path("/home/agent/config/.env.example"));
        assert!(!is_sensitive_path(
            "/home/agent/config/service.env.template"
        ));
        assert!(!is_sensitive_path("/home/agent/config/settings.json"));
    }

    #[test]
    fn unsafe_votes_need_a_security_critical_candidate() {
        let mut passwd_read = boundary_summary("boundary:passwd", &["ev"], "/etc/passwd");
        passwd_read.observed_operations = vec![ActionCandidateType::FileOpen];
        assert!(!is_security_critical_candidate(&passwd_read));

        let mut log_write = boundary_summary("boundary:log", &["ev"], "/var/log/auth.log");
        log_write.observed_operations = vec![ActionCandidateType::FileWrite];
        assert!(is_security_critical_candidate(&log_write));

        let mut secret = boundary_summary("boundary:env", &["ev"], "config/service.env");
        secret.observed_operations = vec![ActionCandidateType::FileOpen];
        assert!(is_security_critical_candidate(&secret));

        let mut network = summary("boundary:net", &["ev"]);
        network.resource = Some(NormalizedResource::Network {
            host: "127.0.0.1".into(),
            port: 9602,
            scheme: "http".into(),
        });
        assert!(is_security_critical_candidate(&network));

        let mut pattern = summary("pattern:trust_boundary_crossing:1", &["ev"]);
        pattern.kind = "pattern".into();
        assert!(!is_security_critical_candidate(&pattern));
    }

    #[test]
    fn observer_unsafe_on_host_enumeration_is_not_actionable() {
        let mut passwd_read = boundary_summary("boundary:passwd", &["ev-1"], "/etc/passwd");
        passwd_read.observed_operations = vec![ActionCandidateType::FileOpen];
        let mut pattern = summary("pattern:trust_boundary_crossing:1", &["ev-2"]);
        pattern.kind = "pattern".into();
        let observations = review_observations(
            &[passwd_read, pattern],
            AlertDecision::Unsafe,
            0.7,
            &["boundary:passwd".to_string(), "ev-2".to_string()],
        );
        assert_ne!(
            aggregate_final_decision(&observations).decision,
            AlertDecision::Unsafe
        );
    }

    #[test]
    fn observer_unsafe_on_a_secret_stays_actionable() {
        let mut secret = boundary_summary("boundary:env", &["ev-1"], "config/service.env");
        secret.observed_operations = vec![ActionCandidateType::FileOpen];
        let observations = review_observations(
            &[secret],
            AlertDecision::Unsafe,
            0.8,
            &["boundary:env".to_string()],
        );
        assert_eq!(
            aggregate_final_decision(&observations).decision,
            AlertDecision::Unsafe
        );
    }

    fn read_action(action_type: ActionCandidateType, outcome: ActionOutcome, path: &str) -> Action {
        let mut action = observed_action(
            &ActionId::from("action-read"),
            action_type,
            outcome,
            Some(0),
        );
        action.normalized_args = vec![NormalizedArg::Path(path.into())];
        action
    }

    #[test]
    fn only_completed_sensitive_reads_can_support_a_hard_rule() {
        let actions = vec![
            read_action(
                ActionCandidateType::FileOpen,
                ActionOutcome::Succeeded,
                "config/service.env",
            ),
            read_action(
                ActionCandidateType::FileRead,
                ActionOutcome::Succeeded,
                "config/.ssh/id_ed25519",
            ),
            read_action(
                ActionCandidateType::FileOpen,
                ActionOutcome::Attempted,
                "config/prod.env",
            ),
            read_action(
                ActionCandidateType::FileRead,
                ActionOutcome::Failed,
                "config/.aws/credentials",
            ),
            read_action(
                ActionCandidateType::FileOpen,
                ActionOutcome::Succeeded,
                "src/main.py",
            ),
        ];
        assert_eq!(
            succeeded_sensitive_read_paths(&actions),
            vec![
                "config/.ssh/id_ed25519".to_string(),
                "config/service.env".to_string()
            ]
        );
    }

    #[test]
    fn truncated_sensitive_path_still_counts_as_a_completed_read() {
        let mut action = observed_action(
            &ActionId::from("action-read"),
            ActionCandidateType::FileOpen,
            ActionOutcome::Succeeded,
            Some(0),
        );
        action.normalized_args = vec![NormalizedArg::PathCaptureIncomplete {
            normalized_path: "config/.aws/credentials".into(),
            raw_byte_len: 512,
        }];
        assert_eq!(
            succeeded_sensitive_read_paths(&[action]),
            vec!["config/.aws/credentials".to_string()]
        );
    }

    #[test]
    fn credential_read_followed_by_egress_becomes_one_candidate_with_the_fact() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node("a1", ResponsibilityNodeType::Agent));
        graph.add_node(resource_node(
            "f1",
            NormalizedResource::File {
                canonical_path: "/tmp/agent/config/.env".into(),
            },
            10,
        ));
        graph.add_node(resource_node(
            "n1",
            NormalizedResource::Network {
                host: "127.0.0.1".into(),
                port: 9510,
                scheme: "http".into(),
            },
            20,
        ));
        graph.add_edge(ic_edge("e1", "a1", "f1", None, &["ev-read"]));
        graph.add_edge(ic_edge(
            "e2",
            "a1",
            "n1",
            Some(crate::responsibility::types::ActionCandidateType::NetworkConnect),
            &["ev-send"],
        ));
        let candidates = sensitive_egress_candidates(&graph);
        assert_eq!(
            candidates.len(),
            1,
            "one fact per (agent, path, destination)"
        );
        let detail = candidates[0].detail.as_deref().unwrap_or_default();
        assert!(detail.contains("credential_read=/tmp/agent/config/.env"));
        assert!(detail.contains("egress=127.0.0.1:9510"));
        assert!(candidates[0].evidence_ids.len() <= MAX_CANDIDATE_EVIDENCE);
    }

    #[test]
    fn egress_outside_the_window_is_not_presented_as_credential_egress() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node("a1", ResponsibilityNodeType::Agent));
        graph.add_node(resource_node(
            "f1",
            NormalizedResource::File {
                canonical_path: "/tmp/agent/config/.env".into(),
            },
            10,
        ));
        graph.add_node(resource_node(
            "n1",
            NormalizedResource::Network {
                host: "127.0.0.1".into(),
                port: 9510,
                scheme: "http".into(),
            },
            10 + SENSITIVE_EGRESS_WINDOW_NS + 1,
        ));
        graph.add_edge(ic_edge("e1", "a1", "f1", None, &["ev-read"]));
        graph.add_edge(ic_edge(
            "e2",
            "a1",
            "n1",
            Some(crate::responsibility::types::ActionCandidateType::NetworkConnect),
            &["ev-send"],
        ));
        assert!(sensitive_egress_candidates(&graph).is_empty());
    }

    #[test]
    fn non_sensitive_reads_never_create_credential_egress_candidates() {
        let mut graph = ResponsibilityGraph::default();
        graph.add_node(node("a1", ResponsibilityNodeType::Agent));
        graph.add_node(resource_node(
            "f1",
            NormalizedResource::File {
                canonical_path: "/tmp/agent/config/.env.example".into(),
            },
            10,
        ));
        graph.add_node(resource_node(
            "n1",
            NormalizedResource::Network {
                host: "127.0.0.1".into(),
                port: 9510,
                scheme: "http".into(),
            },
            20,
        ));
        graph.add_edge(ic_edge("e1", "a1", "f1", None, &["ev-read"]));
        graph.add_edge(ic_edge(
            "e2",
            "a1",
            "n1",
            Some(crate::responsibility::types::ActionCandidateType::NetworkConnect),
            &["ev-send"],
        ));
        assert!(sensitive_egress_candidates(&graph).is_empty());
    }

    fn observed_action(
        action_id: &ActionId,
        action_type: ActionCandidateType,
        outcome: ActionOutcome,
        syscall_ret: Option<i64>,
    ) -> Action {
        Action {
            outcome,
            syscall_ret,
            syscall_name: None,
            action_id: action_id.clone(),
            process_id: ProcessId::new(42, 1),
            action_type,
            normalized_args: Vec::new(),
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: 1,
        }
    }

    fn correlated(evidence_id: &str, action_id: &ActionId) -> CorrelationEvidence {
        CorrelationEvidence {
            evidence_id: EvidenceId::from(evidence_id),
            intent_id: IntentId::new(),
            action_id: action_id.clone(),
            match_level: MatchLevel::L1Exact,
            confidence: 0.9,
            matched_fields: NormalizedArgs::default(),
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
            timestamp_ns: 1,
        }
    }

    #[test]
    fn pending_connect_is_not_reported_as_a_completed_operation() {
        let graph = boundary_graph();
        let mut candidates = review_candidates(&graph, &[]);
        let action_id = ActionId::from("action-connect");
        let actions = vec![observed_action(
            &action_id,
            ActionCandidateType::NetworkConnect,
            ActionOutcome::Pending,
            Some(-115),
        )];
        let evidence = vec![correlated("ev-1", &action_id)];
        enrich_candidate_outcomes(&mut candidates, &evidence, &actions);

        assert_eq!(candidates.len(), 1);
        assert_eq!(candidates[0].observed_actions.len(), 1);
        let observed = &candidates[0].observed_actions[0];
        assert_eq!(observed.outcome, ActionOutcome::Pending);
        assert_eq!(observed.syscall_ret, Some(-115));
        assert_eq!(
            observed.operation,
            Some(ActionCandidateType::NetworkConnect)
        );
        assert!(
            candidates[0].observed_operations.is_empty(),
            "a pending connect must not be presented as completed: {:?}",
            candidates[0].observed_operations
        );
    }

    #[test]
    fn succeeded_file_open_proves_only_the_descriptor_open() {
        let graph = boundary_graph();
        let mut candidates = review_candidates(&graph, &[]);
        let action_id = ActionId::from("action-open");
        let actions = vec![observed_action(
            &action_id,
            ActionCandidateType::FileOpen,
            ActionOutcome::Succeeded,
            Some(3),
        )];
        let evidence = vec![correlated("ev-1", &action_id)];
        enrich_candidate_outcomes(&mut candidates, &evidence, &actions);

        assert_eq!(
            candidates[0].observed_operations,
            vec![ActionCandidateType::FileOpen]
        );
        assert_eq!(
            candidates[0].observed_actions[0].operation,
            Some(ActionCandidateType::FileOpen)
        );
        assert_eq!(candidates[0].observed_actions[0].syscall_ret, Some(3));
    }

    #[test]
    fn a_missing_action_is_explicitly_unknown() {
        let graph = boundary_graph();
        let mut candidates = review_candidates(&graph, &[]);
        let evidence = vec![correlated("ev-1", &ActionId::from("missing-action"))];
        enrich_candidate_outcomes(&mut candidates, &evidence, &[]);

        assert_eq!(candidates[0].observed_actions.len(), 1);
        let observed = &candidates[0].observed_actions[0];
        assert_eq!(observed.action_id, "missing-action");
        assert_eq!(observed.outcome, ActionOutcome::Unknown);
        assert_eq!(observed.operation, None);
        assert_eq!(observed.syscall_ret, None);
        assert!(candidates[0].observed_operations.is_empty());
    }

    #[test]
    fn changed_task_context_invalidates_an_old_safe_review() {
        let graph = boundary_graph();
        let mut original = review_candidates(&graph, &[]);
        bind_task_context(&mut original, 10);
        let observations = review_observations(&original, AlertDecision::Safe, 0.9, &[]);
        let mut changed = review_candidates(&graph, &[]);
        bind_task_context(&mut changed, 11);
        assert_eq!(changed[0].candidate.id, original[0].candidate.id);
        assert_ne!(changed[0].candidate.version, original[0].candidate.version);
        assert!(!unreviewed_candidates_of(&observations, changed.clone()).is_empty());
        let once = changed[0].candidate.clone();
        bind_task_context(&mut changed, 11);
        assert_eq!(changed[0].candidate, once, "binding must be idempotent");
    }

    #[test]
    fn newly_known_capture_gap_invalidates_an_old_safe_review() {
        let graph = boundary_graph();
        let id = ActionId::from("action-write");
        let evidence = vec![correlated("ev-1", &id)];
        let mut action = observed_action(
            &id,
            ActionCandidateType::FileWrite,
            ActionOutcome::Succeeded,
            Some(1),
        );
        let mut before = review_candidates(&graph, &[]);
        enrich_candidate_outcomes(&mut before, &evidence, std::slice::from_ref(&action));
        let old_review = review_observations(&before, AlertDecision::Safe, 0.9, &[]);
        action.normalized_args.push(
            crate::responsibility::types::NormalizedArg::PathCaptureIncomplete {
                normalized_path: "/tmp/partial".into(),
                raw_byte_len: 126,
            },
        );
        let mut after = review_candidates(&graph, &[]);
        enrich_candidate_outcomes(&mut after, &evidence, &[action]);
        assert!(after[0].observed_actions[0].path_capture_incomplete);
        assert!(!unreviewed_candidates_of(&old_review, after).is_empty());
    }

    #[test]
    fn completion_changes_the_candidate_version() {
        let graph = boundary_graph();
        let action_id = ActionId::from("action-connect");
        let evidence = vec![correlated("ev-1", &action_id)];
        let mut pending = review_candidates(&graph, &[]);
        enrich_candidate_outcomes(
            &mut pending,
            &evidence,
            &[observed_action(
                &action_id,
                ActionCandidateType::NetworkConnect,
                ActionOutcome::Pending,
                Some(-115),
            )],
        );
        let mut succeeded = review_candidates(&graph, &[]);
        enrich_candidate_outcomes(
            &mut succeeded,
            &evidence,
            &[observed_action(
                &action_id,
                ActionCandidateType::NetworkConnect,
                ActionOutcome::Succeeded,
                Some(0),
            )],
        );

        assert_eq!(pending[0].candidate.id, succeeded[0].candidate.id);
        assert_ne!(
            pending[0].candidate.version, succeeded[0].candidate.version,
            "pending -> succeeded must require a new review"
        );
    }

    #[test]
    fn legacy_candidate_json_defaults_to_no_presented_actions() {
        let mut encoded = serde_json::to_value(summary("c1", &[])).expect("serializable");
        let object = encoded.as_object_mut().expect("object");
        object.remove("observed_actions");
        object.remove("observed_operations");
        let decoded: CandidateSummary = serde_json::from_value(encoded).expect("deserializable");
        assert!(decoded.observed_actions.is_empty());
        assert!(decoded.observed_operations.is_empty());
    }
}
