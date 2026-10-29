use mtagentlens::responsibility::graph::{
    GraphPatternMatcher, ResponsibilityGraph, default_templates,
};
use mtagentlens::responsibility::scoring::baseline::{BaselineTracker, BehaviorSample};
use mtagentlens::responsibility::types::*;
fn node(id: &str, agent: Option<&str>, res: Option<NormalizedResource>) -> ResponsibilityNode {
    ResponsibilityNode {
        node_id: NodeId::from(id),
        node_type: if agent.is_some() {
            ResponsibilityNodeType::Agent
        } else {
            ResponsibilityNodeType::Resource
        },
        agent_id: agent.map(AgentId::from),
        resource: res,
        evidence_refs: vec![],
        timestamp_ns: 1,
        phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
    }
}
fn edge(id: &str, src: &str, dst: &str, kind: ResponsibilityEdgeType) -> ResponsibilityEdge {
    ResponsibilityEdge {
        edge_id: EdgeId::from(id),
        edge_type: kind,
        operation: None,
        source: NodeId::from(src),
        target: NodeId::from(dst),
        evidence_refs: vec![EvidenceId::from("e")],
        confidence: 0.9,
        phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
    }
}

#[test]
fn identical_baseline_batches_preserve_frequency() {
    let mut tracker = BaselineTracker::new();
    for t in 0..2 {
        for _ in 0..50 {
            tracker.observe(
                "a",
                BehaviorSample {
                    syscall_name: Some("openat".into()),
                    file_path: Some("/tmp/a".into()),
                    port: None,
                    is_tool_call: true,
                    timestamp_ns: t,
                },
            );
        }
        tracker.rebuild_baseline("a", t);
    }
    assert!((tracker.get("a").unwrap().syscall_profile["openat"] - 1.0).abs() < 1e-9);
}

#[test]
fn unrelated_component_preserves_exfiltration_pattern() {
    let mut g = ResponsibilityGraph::with_default_config();
    g.add_node(node("a", Some("a"), None));
    g.add_node(node(
        "f",
        None,
        Some(NormalizedResource::File {
            canonical_path: "/tmp/report".into(),
        }),
    ));
    g.add_node(node(
        "net",
        None,
        Some(NormalizedResource::Network {
            host: "outside.example".into(),
            port: 443,
            scheme: "https".into(),
        }),
    ));
    g.add_edge(edge(
        "e1",
        "a",
        "f",
        ResponsibilityEdgeType::IntentCausesAction,
    ));
    g.add_edge(edge(
        "e2",
        "a",
        "net",
        ResponsibilityEdgeType::IntentCausesAction,
    ));
    g.add_edge(edge(
        "e3",
        "a",
        "net",
        ResponsibilityEdgeType::PolicyBoundaryCrossing,
    ));
    let m = GraphPatternMatcher::new(default_templates());
    assert!(
        m.match_graph(&g)
            .iter()
            .any(|p| p.template_id == "data_exfiltration")
    );
    g.add_node(node("b", Some("b"), None));
    g.add_node(node(
        "bf",
        None,
        Some(NormalizedResource::File {
            canonical_path: "/tmp/unrelated".into(),
        }),
    ));
    g.add_edge(edge(
        "e4",
        "b",
        "bf",
        ResponsibilityEdgeType::IntentCausesAction,
    ));
    assert!(
        m.match_graph(&g)
            .iter()
            .any(|p| p.template_id == "data_exfiltration"),
        "unrelated activity must preserve the existing attack subgraph"
    );
}

#[test]
fn authoritative_evidence_includes_streaming_matches() {
    let mut correlator = mtagentlens::responsibility::evidence::EvidenceCorrelator::new();
    let snap = mtagentlens::responsibility::signals::SignalGraph::new().snapshot();
    let pid = ProcessId::new(42, 1);
    let intent = Intent {
        intent_id: IntentId::from("i1"),
        process_id: pid,
        message_id: MessageId::from("m1"),
        tool_name: "Read".into(),
        tool_args: vec![NormalizedArg::Path("/etc/shadow".into())],
        intent_class: IntentClass::SideEffect,
        parse_state: IntentParseState::Complete,
        primary_matching_eligible: true,
        authority_source: IntentAuthoritySource::SseResponseToolUse,
        timestamp_ns: 100,
        native_tool_call: None,
        declared_conversation_id: None,
    };
    let action = Action {
        outcome: ActionOutcome::Succeeded,
        syscall_ret: Some(0),
        syscall_name: Some("read".into()),
        action_id: ActionId::from("a1"),
        process_id: pid,
        action_type: ActionCandidateType::FileRead,
        normalized_args: vec![NormalizedArg::Path("/etc/shadow".into())],
        derivation_mode: CandidateDerivationMode::Direct,
        signal_class: SignalClass::HighSignal,
        timestamp_ns: 200,
    };
    correlator.on_intent(intent.clone(), &snap);
    let stream_evidence = correlator.on_action(action.clone(), &snap);
    let batch = correlator.correlate_batch(&[intent.clone()], &[action.clone()], &snap);
    let replay_batch = mtagentlens::responsibility::evidence::EvidenceCorrelator::new()
        .correlate_batch(&[intent], &[action], &snap);
    assert_eq!(stream_evidence.len(), 1);
    assert_eq!(
        batch.evidence.len(),
        replay_batch.evidence.len(),
        "authoritative evidence must include already linked streaming pairs"
    );
    assert!(batch.non_links.is_empty());
}

#[test]
fn authoritative_identity_preserves_delegated_authorization() {
    let mut builder = mtagentlens::responsibility::graph::GraphBuilder::with_default_config();
    builder
        .graph_mut()
        .add_node(node("writer-node", Some("prov-writer"), None));
    builder
        .graph_mut()
        .add_node(node("reader-node", Some("prov-reader"), None));
    builder.graph_mut().add_node(node(
        "boundary",
        None,
        Some(NormalizedResource::File {
            canonical_path: "/tmp/shared".into(),
        }),
    ));
    builder.graph_mut().add_edge(edge(
        "pbc",
        "reader-node",
        "boundary",
        ResponsibilityEdgeType::PolicyBoundaryCrossing,
    ));
    builder.graph_mut().add_edge(edge(
        "taint",
        "writer-node",
        "reader-node",
        ResponsibilityEdgeType::TaintPropagatesTo,
    ));
    builder.record_delegation(
        mtagentlens::responsibility::judgment::DelegationObservation {
            delegation_id: "d1".into(),
            from_agent_id: "prov-writer".into(),
            to_agent_id: "prov-reader".into(),
            parent_pid: 1,
            parent_start_time_ns: 1,
            child_pid: 2,
            child_start_time_ns: 2,
            timestamp_ns: 1,
            child_comm: "child".into(),
            confidence: 0.9,
        },
    );
    let (before, before_diag) =
        mtagentlens::responsibility::claims::ClaimBuilder::build_claims_with_delegations(
            builder.graph(),
            &Default::default(),
            builder.delegation_records(),
        );
    let agents = [
        ("auth-writer", "prov-writer"),
        ("auth-reader", "prov-reader"),
    ]
    .iter()
    .map(|(auth, prov)| AuthoritativeIdentity {
        agent_id: AgentId::from(*auth),
        process_ids: vec![],
        message_ids: vec![],
        tls_sessions: vec![],
        confidence: AgentConfidence::Authoritative,
        provisional_refs: vec![AgentId::from(*prov)],
    })
    .collect();
    builder.remap_agent_identities(
        &mtagentlens::responsibility::identity::union_find::AuthoritativeResolution {
            agents,
            refinements: vec![],
            subagent_boundaries: vec![],
            unresolved: vec![],
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        },
    );
    let (after, after_diag) =
        mtagentlens::responsibility::claims::ClaimBuilder::build_claims_with_delegations(
            builder.graph(),
            &Default::default(),
            builder.delegation_records(),
        );
    assert_eq!(before_diag.taint_authorized, 1);
    assert_eq!(after_diag.taint_authorized, 1);
    assert_eq!(after_diag.taint_unauthorized, 0);
    assert_eq!(before[0].claim_type, after[0].claim_type);
}

async fn observer_against_response(
    response: &'static [u8],
) -> (
    tokio::task::JoinHandle<()>,
    Result<
        (
            mtagentlens::responsibility::classification::ClassificationResult,
            mtagentlens::responsibility::classification::Route,
        ),
        tokio::time::error::Elapsed,
    >,
) {
    use mtagentlens::responsibility::observer::provider::{
        ObserverLLMProvider, ObserverProviderConfig,
    };
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 65536];
        let _ = socket.read(&mut request).await.unwrap();
        socket.write_all(response).await.unwrap();
        std::future::pending::<()>().await;
    });
    let provider = ObserverLLMProvider::new(ObserverProviderConfig {
        base_url: format!("http://{address}"),
        auth_token: "test-placeholder".into(),
        model: "test-model".into(),
        timeout_ms: 40,
        max_retries: 0,
    })
    .unwrap();
    let engine = mtagentlens::responsibility::classification::ClassificationEngine::new();
    let graph = ResponsibilityGraph::default();
    let result = tokio::time::timeout(
        std::time::Duration::from_millis(500),
        engine.classify_provisional(
            &graph,
            &[],
            &[],
            Some(&provider),
            0,
            true,
            Default::default(),
            &[],
            0,
            Default::default(),
            &mtagentlens::responsibility::classification::candidates::review_candidates(
                &graph,
                &[],
            ),
        ),
    )
    .await;
    (server, result)
}

#[tokio::test]
async fn observer_deadline_includes_a_body_that_never_finishes() {
    let (server, result) =
        observer_against_response(b"HTTP/1.1 200 OK\r\nContent-Length: 512\r\n\r\n").await;
    server.abort();
    let (classification, _) = result.expect("provider deadline must expire before the outer guard");
    assert_eq!(classification.route, "observer_failed");
}

#[tokio::test]
async fn observer_rejects_excessive_declared_response_length() {
    let (server, result) =
        observer_against_response(b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n").await;
    server.abort();
    let (classification, _) =
        result.expect("oversized body must be rejected without waiting for its bytes");
    assert_eq!(classification.route, "observer_failed");
}

#[test]
fn unsuccessful_operations_remain_candidates_without_completed_evidence() {
    use mtagentlens::responsibility::classification::RuleEngine;
    use mtagentlens::responsibility::identity::union_find::{
        AuthoritativeIdentityProvider, AuthoritativeResolution,
    };
    let pid = ProcessId::new(42, 1);
    let resolution = AuthoritativeResolution {
        agents: vec![AuthoritativeIdentity {
            agent_id: AgentId::from("agent"),
            process_ids: vec![pid],
            message_ids: vec![],
            tls_sessions: vec![],
            confidence: AgentConfidence::Authoritative,
            provisional_refs: vec![],
        }],
        refinements: vec![],
        subagent_boundaries: vec![],
        unresolved: vec![],
        conversation_agents: Vec::new(),
        ambiguous_messages: Vec::new(),
    };
    let provider = AuthoritativeIdentityProvider::new(&resolution);
    let intent = Intent {
        intent_id: IntentId::from("read-attempt"),
        process_id: pid,
        message_id: MessageId::from("message"),
        tool_name: "Read".into(),
        tool_args: vec![NormalizedArg::Path("/etc/shadow".into())],
        intent_class: IntentClass::SideEffect,
        parse_state: IntentParseState::Complete,
        primary_matching_eligible: true,
        authority_source: IntentAuthoritySource::SseResponseToolUse,
        timestamp_ns: 100,
        native_tool_call: None,
        declared_conversation_id: None,
    };
    for outcome in [
        ActionOutcome::Failed,
        ActionOutcome::Pending,
        ActionOutcome::Unknown,
        ActionOutcome::Attempted,
    ] {
        let mut graph = mtagentlens::responsibility::graph::GraphBuilder::with_default_config();
        let action = Action {
            action_id: ActionId::new(),
            process_id: pid,
            action_type: ActionCandidateType::FileRead,
            normalized_args: intent.tool_args.clone(),
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: 200,
            outcome,
            syscall_ret: Some(-13),
            syscall_name: Some("openat".into()),
        };
        let evidence = CorrelationEvidence {
            evidence_id: EvidenceId::new(),
            intent_id: intent.intent_id.clone(),
            action_id: action.action_id.clone(),
            match_level: MatchLevel::L1Exact,
            confidence: 0.93,
            matched_fields: Default::default(),
            derivation_mode: CandidateDerivationMode::Direct,
            phase: PhaseIndicator::Provisional { timestamp_ns: 200 },
            timestamp_ns: 200,
        };
        graph.handle(&evidence, &intent, &action, &provider);
        assert!(
            !graph
                .graph()
                .edges_of_type(&ResponsibilityEdgeType::PolicyBoundaryCrossing)
                .is_empty(),
            "suspicious attempts remain candidates"
        );
        let (_, hits) = RuleEngine::new().evaluate(graph.graph());
        assert!(
            hits.iter().all(|hit| hit.decision
                != mtagentlens::responsibility::classification::RuleDecision::Unsafe),
            "{outcome:?} is not a completed prohibited operation"
        );
        assert!(graph.taint_tracker().contaminated_artifacts().is_empty());
    }
}

#[test]
fn safe_review_covers_only_the_exact_candidate_version() {
    use mtagentlens::responsibility::classification::candidates::CandidateRef;
    use mtagentlens::responsibility::classification::final_decision::{
        Observation, aggregate_final_decision,
    };
    let old = CandidateRef {
        id: "boundary:1".into(),
        version: "before-syscall".into(),
    };
    let new = CandidateRef {
        id: "boundary:1".into(),
        version: "after-syscall".into(),
    };
    let reviewed = Observation::ScopedReview {
        candidates: vec![old.clone()],
        decision: AlertDecision::Safe,
        confidence: 0.99,
        cited: 0,
    };
    assert_eq!(
        aggregate_final_decision(&[
            reviewed.clone(),
            Observation::ScopedCandidate { candidate: old }
        ])
        .decision,
        AlertDecision::Safe
    );
    assert_eq!(
        aggregate_final_decision(&[reviewed, Observation::ScopedCandidate { candidate: new }])
            .decision,
        AlertDecision::Uncertain
    );
}

#[test]
fn safe_reviews_of_benign_candidates_do_not_erase_distinct_unsafe_evidence() {
    use mtagentlens::responsibility::classification::candidates::CandidateRef;
    use mtagentlens::responsibility::classification::final_decision::{
        Observation, aggregate_final_decision,
    };
    let a = CandidateRef {
        id: "boundary:a".into(),
        version: "1".into(),
    };
    let b = CandidateRef {
        id: "boundary:b".into(),
        version: "1".into(),
    };
    let observations = [
        Observation::ScopedCandidate {
            candidate: a.clone(),
        },
        Observation::ScopedCandidate {
            candidate: b.clone(),
        },
        Observation::ScopedReview {
            candidates: vec![a],
            decision: AlertDecision::Safe,
            confidence: 0.99,
            cited: 0,
        },
        Observation::ScopedReview {
            candidates: vec![b],
            decision: AlertDecision::Unsafe,
            confidence: 0.99,
            cited: 1,
        },
    ];
    assert_eq!(
        aggregate_final_decision(&observations).decision,
        AlertDecision::Unsafe
    );
}

#[test]
fn behavioral_windows_use_new_actions_and_keep_agents_separate() {
    let mut tracker = BaselineTracker::new();
    let mut actions = Vec::new();
    for index in 0..60 {
        actions.push(Action {
            action_id: ActionId::from(format!("normal-{index}")),
            process_id: ProcessId::new(1, 1),
            action_type: ActionCandidateType::FileRead,
            normalized_args: vec![NormalizedArg::Path("/work/project/source.rs".into())],
            derivation_mode: CandidateDerivationMode::Direct,
            signal_class: SignalClass::HighSignal,
            timestamp_ns: index + 1,
            outcome: ActionOutcome::Succeeded,
            syscall_ret: Some(12),
            syscall_name: Some("read".into()),
        });
    }
    for action in &actions {
        tracker.observe_action("agent-a", action);
    }
    assert!(tracker.finish_window("agent-a", 100).is_none());
    for action in &actions {
        tracker.observe_action("agent-a", action);
    }
    assert!(
        tracker.finish_window("agent-a", 200).is_none(),
        "old actions cannot be sampled twice"
    );
    assert_eq!(tracker.get("agent-a").unwrap().sample_count, 60);
    assert!(tracker.get("agent-b").is_none());
    let mut unusual = actions[0].clone();
    unusual.action_id = ActionId::from("unusual");
    unusual.normalized_args = vec![NormalizedArg::Url {
        host: "outside.example".into(),
        port: Some(4444),
    }];
    unusual.syscall_name = Some("connect".into());
    tracker.observe_action("agent-a", &unusual);
    assert!(
        tracker.finish_window("agent-a", 300).unwrap() > 0.3,
        "new syscall/port must change deviation"
    );
    assert_eq!(
        tracker.get("agent-a").unwrap().sample_count,
        60,
        "reference stays frozen after warmup"
    );
}

#[tokio::test]
async fn replay_requires_complete_versioned_analysis_inputs() {
    use mtagentlens::responsibility::persistence::{
        PersistRecord, PersistenceReader, PersistenceWriter, WriterConfig,
    };
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy.db");
    let writer = PersistenceWriter::new(WriterConfig {
        db_path: path.to_string_lossy().into(),
        ..Default::default()
    })
    .unwrap();
    writer
        .write_blocking(PersistRecord::RunStart {
            run_id: "legacy".into(),
            timestamp_ns: 1,
        })
        .await;
    writer
        .write_blocking(PersistRecord::RunEnd {
            run_id: "legacy".into(),
            timestamp_ns: 2,
        })
        .await;
    writer.flush().await.unwrap();
    let reader = PersistenceReader::open(&path.to_string_lossy()).unwrap();
    let result =
        mtagentlens::responsibility::replay::ReplayExecutor::reauthoritative(&reader, "legacy");
    assert!(
        result.is_err(),
        "legacy incomplete inputs must not produce a fabricated authoritative replay"
    );
}

#[test]
fn capture_quality_requires_explicit_zero_loss_and_complete_diagnostics() {
    use mtagentlens::responsibility::replay::checkpoint::quality_status;
    use serde_json::json;
    assert_eq!(
        quality_status(&json!({"sources":{"process":{"final_stats_seen":true,"exit_status":0}}})),
        "unknown"
    );
    let healthy = json!({"sources":{"process":{"final_stats_seen":true,"exit_status":0,"lost":0,"rate_limited":0,"drain_complete":true}}});
    assert_eq!(quality_status(&healthy), "healthy");
    let mut degraded = healthy.clone();
    degraded["sources"]["process"]["parse_errors"] = 1.into();
    assert_eq!(quality_status(&degraded), "degraded");
    degraded = healthy.clone();
    degraded["sources"]["process"]["attach_error"] = json!({"error":"failed to attach"});
    assert_eq!(quality_status(&degraded), "degraded");
}

#[test]
fn capture_quality_never_upgrades_an_explicit_producer_status() {
    use mtagentlens::responsibility::replay::checkpoint::quality_status;
    use serde_json::json;
    let mut quality = json!({"sources":{"process":{
        "final_stats_seen":true,"exit_status":0,"lost":0,
        "rate_limited":0,"drain_complete":true
    }}});
    assert_eq!(quality_status(&quality), "healthy");
    quality["status"] = "unknown".into();
    assert_eq!(quality_status(&quality), "unknown");
    quality["status"] = "degraded".into();
    assert_eq!(quality_status(&quality), "degraded");
    quality["status"] = "healthy".into();
    quality["sources"]["process"]["lost"] = 3.into();
    assert_eq!(quality_status(&quality), "degraded");
    quality["status"] = "unknown".into();
    assert_eq!(quality_status(&quality), "degraded");
    assert_eq!(quality_status(&json!({})), "unknown");
}

#[test]
fn candidate_versions_track_new_facts_of_the_same_subject() {
    use mtagentlens::responsibility::classification::candidates::graph_candidates;
    let mut graph = ResponsibilityGraph::with_default_config();
    graph.add_node(node("agent", Some("a"), None));
    graph.add_node(node(
        "file",
        None,
        Some(NormalizedResource::File {
            canonical_path: "/tmp/report".into(),
        }),
    ));
    graph.add_edge(edge(
        "boundary",
        "agent",
        "file",
        ResponsibilityEdgeType::PolicyBoundaryCrossing,
    ));
    let before = graph_candidates(&graph, &[])[0].candidate.clone();
    graph.add_node(node(
        "network",
        None,
        Some(NormalizedResource::Network {
            host: "outside.example".into(),
            port: 443,
            scheme: "https".into(),
        }),
    ));
    graph.add_edge(edge(
        "new-fact",
        "agent",
        "network",
        ResponsibilityEdgeType::IntentCausesAction,
    ));
    let after = graph_candidates(&graph, &[])[0].candidate.clone();
    assert_eq!(before.id, after.id);
    assert_ne!(
        before.version, after.version,
        "a prior semantic review must not cover newly observed subject facts"
    );
}

#[test]
fn unsafe_review_is_not_assigned_to_every_candidate_in_a_mixed_request() {
    use mtagentlens::responsibility::classification::candidates::{bind_review, graph_candidates};
    use mtagentlens::responsibility::classification::final_decision::Observation;
    let mut graph = ResponsibilityGraph::with_default_config();
    graph.add_node(node("a", Some("a"), None));
    for name in ["benign", "suspect"] {
        // An Unsafe vote is actionable only for a prohibited/secret fact, so the
        // suspect candidate must be a credential file, not an arbitrary /tmp read.
        let path = if name == "suspect" {
            "config/service.env".to_string()
        } else {
            "/tmp/benign".to_string()
        };
        graph.add_node(node(
            name,
            None,
            Some(NormalizedResource::File {
                canonical_path: path,
            }),
        ));
        graph.add_edge(edge(
            name,
            "a",
            name,
            ResponsibilityEdgeType::PolicyBoundaryCrossing,
        ));
    }
    let candidates = graph_candidates(&graph, &[]);
    let observations = bind_review(
        &candidates,
        AlertDecision::Unsafe,
        0.9,
        &["boundary:suspect".into()],
    );
    assert!(
        matches!(&observations[0], Observation::ScopedReview { candidates: reviewed, .. }
        if reviewed.len() == 1 && reviewed[0].id == "boundary:suspect")
    );
    let invalid = bind_review(
        &candidates,
        AlertDecision::Unsafe,
        0.9,
        &["invented-evidence".into()],
    );
    assert!(
        matches!(&invalid[0], Observation::ObserverUncertain { .. }),
        "unbound citations cannot establish a candidate verdict"
    );
}

#[test]
fn independent_unsafe_observation_survives_a_later_unreviewed_candidate() {
    use mtagentlens::responsibility::classification::candidates::CandidateRef;
    use mtagentlens::responsibility::classification::final_decision::{
        Observation, aggregate_final_decision,
    };
    let verdict = aggregate_final_decision(&[
        Observation::ObserverUnsafe {
            confidence: 0.9,
            cited: 1,
        },
        Observation::ScopedCandidate {
            candidate: CandidateRef {
                id: "new".into(),
                version: "1".into(),
            },
        },
    ]);
    assert_eq!(verdict.decision, AlertDecision::Unsafe);
}

#[test]
fn semantic_review_requires_recorded_facts_even_without_structural_candidates() {
    use mtagentlens::responsibility::classification::candidates::{bind_review, review_candidates};
    use mtagentlens::responsibility::classification::final_decision::Observation;
    let no_facts = bind_review(
        &[],
        AlertDecision::Unsafe,
        0.99,
        &["invented-operation".into()],
    );
    assert!(matches!(
        &no_facts[0],
        Observation::ObserverUncertain { .. }
    ));
    let mut graph = ResponsibilityGraph::with_default_config();
    graph.add_node(node("a", Some("a"), None));
    graph.add_node(node(
        "f",
        None,
        Some(NormalizedResource::File {
            canonical_path: "config/service.env".into(),
        }),
    ));
    let mut observed = edge("op", "a", "f", ResponsibilityEdgeType::IntentCausesAction);
    observed.operation = Some(ActionCandidateType::FileOpen);
    graph.add_edge(observed);
    let candidates = review_candidates(&graph, &[]);
    assert_eq!(candidates.len(), 1);
    let backed = bind_review(
        &candidates,
        AlertDecision::Unsafe,
        0.9,
        &[candidates[0].candidate.id.clone()],
    );
    assert!(matches!(
        &backed[0],
        Observation::ScopedReview {
            decision: AlertDecision::Unsafe,
            ..
        }
    ));
}

#[tokio::test]
async fn classifier_rejects_fabricated_observer_citations_before_emitting_alerts() {
    use mtagentlens::responsibility::classification::{
        ClassificationEngine, ObserverDecision, ObserverOutput, Route,
    };
    use mtagentlens::responsibility::observer::{llm::ObserverLLM, mock::MockObserverProvider};
    let output: ObserverOutput = ObserverLLM::parse_and_validate(r#"{"decision":"unsafe","severity":"critical","confidence":0.99,"reasoning":"invented operation","evidence_cited":["made-up"],"cache_ttl":"once"}"#).unwrap();
    let observer = MockObserverProvider::with_responses(vec![output]);
    let graph = ResponsibilityGraph::with_default_config();
    let (result, route) = ClassificationEngine::new()
        .classify_provisional(
            &graph,
            &[],
            &[],
            Some(&observer),
            0,
            true,
            Default::default(),
            &[],
            0,
            Default::default(),
            &mtagentlens::responsibility::classification::candidates::review_candidates(
                &graph,
                &[],
            ),
        )
        .await;
    assert_eq!(result.route, "observer_uncertain");
    assert!(
        matches!(route, Route::DirectSafe(output) if output.decision == ObserverDecision::Uncertain)
    );
}

#[test]
fn successful_open_preserves_flags_without_inventing_content_io() {
    use mtagentlens::responsibility::evidence::ActionClassifier;
    for flags in [0, 1, 2, 64, 512, 577] {
        let opened = ActionClassifier::classify(
            "openat",
            &["/etc/shadow".into(), flags.to_string()],
            ProcessId::new(1, 1),
            1,
        )
        .unwrap();
        assert_eq!(opened.action_type, ActionCandidateType::FileOpen);
        assert!(
            opened
                .normalized_args
                .contains(&NormalizedArg::OpenFlags(flags))
        );
        assert_eq!(opened.syscall_name.as_deref(), Some("openat"));
    }
    let mut graph = ResponsibilityGraph::with_default_config();
    graph.add_node(node("a", Some("a"), None));
    graph.add_node(node(
        "sensitive",
        None,
        Some(NormalizedResource::File {
            canonical_path: "/etc/shadow".into(),
        }),
    ));
    let mut opened = edge(
        "opened",
        "a",
        "sensitive",
        ResponsibilityEdgeType::IntentCausesAction,
    );
    opened.operation = Some(ActionCandidateType::FileOpen);
    graph.add_edge(opened);
    let (_, rules) =
        mtagentlens::responsibility::classification::RuleEngine::new().evaluate(&graph);
    assert!(
        rules.iter().any(|rule| rule.rule_id == "unsafe.shadow"),
        "sensitive access remains detectable without claiming content was read"
    );
}
