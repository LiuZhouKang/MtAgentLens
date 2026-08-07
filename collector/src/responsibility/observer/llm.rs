// SPDX-License-Identifier: MIT
//
// observer/llm.rs — ObserverLLM: prompt building and response parsing.
//
// Canonical location for the LLM interface logic.  Builds structured prompts
// from ObserverSignatures and validates/parses raw LLM responses.
// Used by ObserverProvider implementations (provider.rs).

use serde::Deserialize;
use crate::responsibility::observer::types::{
    AlertAuthority, CacheTTL, ObserverDecision, ObserverError,
    ObserverInput, ObserverOutput, ObserverSignature, CachePrior,
    RuleVerdict, PatternVerdict, ContentSignals,
};
use crate::responsibility::claims::ClaimSeverity;

/// Observer LLM interface: validates input/output, detects injection.
pub struct ObserverLLM;

/// Raw deserialized response from the LLM (before validation).
#[derive(Debug, Deserialize)]
struct RawVerdict {
    rule_id: Option<String>,
    template_id: Option<String>,
    agreed: bool,
    #[serde(default)]
    comment: Option<String>,
}

#[derive(Debug, Deserialize)]
struct RawObserverResponse {
    #[serde(default = "default_decision")]
    decision: String,
    #[serde(default = "default_severity")]
    severity: String,
    #[serde(default = "default_confidence")]
    confidence: f32,
    #[serde(default = "default_cache_ttl")]
    cache_ttl: String,
    #[serde(default)]
    reasoning: String,
    #[serde(default)]
    attacked_flag: Option<bool>,
    #[serde(default)]
    evidence_cited: Vec<String>,
    #[serde(default)]
    rule_verdicts: Vec<RawVerdict>,
    #[serde(default)]
    pattern_verdicts: Vec<RawVerdict>,
}

fn default_decision() -> String { "uncertain".into() }
fn default_severity() -> String { "warning".into() }
fn default_confidence() -> f32 { 0.5 }
fn default_cache_ttl() -> String { "once".into() }

impl ObserverLLM {
    /// Build the structured prompt from observer signature (full JSON format).
    /// Returns JSON string ready for LLM consumption.
    pub fn build_prompt(signature: &ObserverSignature, prior: &CachePrior) -> String {
        let input = ObserverInput {
            signature: signature.clone(),
            content_signals: None,
            prior: prior.clone(),
        };
        serde_json::to_string(&input).unwrap_or_default()
    }

    /// Build a compact, token-efficient prompt for classification arbitration.
    ///
    /// Instead of serializing the full ObserverSignature (1500+ tokens), this
    /// extracts essential signals including resources touched and correlation
    /// flags for informed arbitration. Typical output: 400-600 tokens.
    ///
    /// Cost reduction: ~60% fewer input tokens vs full JSON serialization,
    /// while preserving all decision-critical information.
    pub fn build_compact_prompt(signature: &ObserverSignature, prior: &CachePrior, content: Option<&ContentSignals>) -> String {
        let sig = &signature.classification; let score = sig.score; let sb = &sig.score_breakdown;
        let pbc = signature.graph.boundary_crossings; let agents = signature.graph.agent_count;
        let mut cf = Vec::new();
        if signature.correlation_evidence.has_delegation_chain { cf.push("delegation"); }
        if signature.correlation_evidence.has_tainted_context { cf.push("tainted"); }
        if signature.correlation_evidence.has_credential_pattern { cf.push("credential"); }
        let evq = if signature.evidence.total > 0 { format!("{}/{} avg={:.2}", signature.evidence.direct, signature.evidence.total, signature.evidence.avg_confidence) } else { "none".into() };
        let ops = if let Some(cs) = content { cs.resource_paths_touched.iter().take(10).map(|s| s.as_str()).collect::<Vec<_>>().join("; ") } else { "none".into() };
        let pf = format!("{:.3}", sig.progressive_fused);
        let cu = if sig.cusum_triggered { format!("YES [{}]", sig.cusum_dimensions.join(",")) } else { "no".into() };
        format!("Operations: {ops}\nDims: D1={d1:.2} D2={d2:.2} D3={d3:.2} D4={d4:.2} D5={d5:.2} D6={d6:.2} | Score={s:.3} pfuse={pf} | {pbc}PBC {ag}agents | Ev:{evq} | Corr:{corr} | CUSUM:{cu} | L2:hit={l2} rate={r:.2}\n\nReply JSON: {{\"decision\":\"safe|unsafe|uncertain\",\"severity\":\"info|warning|critical\",\"confidence\":0.0-1.0,\"reasoning\":\"<200 chars\",\"evidence_cited\":[],\"cache_ttl\":\"once|task|all\"}}",
            ops=ops, d1=if sb.boundary_score>sb.rule_score{sb.boundary_score}else{sb.rule_score}, d2=sb.pattern_score, d3=sb.role_score, d4=sb.repetition_score,
            d5=sb.evidence_score, d6=sb.behavioral_score, s=score, pf=pf, pbc=pbc, ag=agents, evq=evq,
            corr=if cf.is_empty(){"none".into()}else{cf.join(",")}, cu=cu, l2=prior.l2_hit, r=prior.l2_prior_anomaly_rate.unwrap_or(0.0))
    }

    /// Parse and validate raw LLM response into ObserverOutput.
    ///
    /// Handles both pure JSON and markdown-wrapped JSON (```json ... ```)
    /// which some LLMs (including DeepSeek) may produce.
    pub fn parse_and_validate(raw_json: &str) -> Result<ObserverOutput, ObserverError> {
        // Strip markdown code fences if present
        let json_str = raw_json
            .trim()
            .strip_prefix("```json")
            .or_else(|| raw_json.trim().strip_prefix("```"))
            .map(|s| s.strip_suffix("```").unwrap_or(s))
            .unwrap_or(raw_json)
            .trim();

        // Try direct parse first
        let raw: RawObserverResponse = match serde_json::from_str(json_str) {
            Ok(r) => r,
            Err(e) => {
                // Auto-recover truncated JSON: close unclosed braces/strings
                let recovered = Self::recover_truncated_json(json_str);
                serde_json::from_str(&recovered)
                    .map_err(|_| ObserverError::InvalidDecision(format!("JSON parse: {}", e)))?
            }
        };

        // 1. Value domain validation
        if !matches!(raw.decision.as_str(), "safe" | "unsafe" | "uncertain") {
            return Err(ObserverError::InvalidDecision(raw.decision));
        }
        if !matches!(raw.severity.as_str(), "info" | "warning" | "critical") {
            return Err(ObserverError::InvalidSeverity(raw.severity));
        }
        if !(0.0..=1.0).contains(&raw.confidence) {
            return Err(ObserverError::InvalidConfidence(raw.confidence));
        }
        if !matches!(raw.cache_ttl.as_str(), "once" | "task" | "all") {
            return Err(ObserverError::InvalidCacheTTL(raw.cache_ttl));
        }
        if raw.reasoning.len() > 500 {
            return Err(ObserverError::ReasoningTooLong(raw.reasoning.len()));
        }

        // 2. Injection detection in reasoning
        let forbidden = ["!OVERRIDE", "!SYSTEM", "SYSTEM:", "!IGNORE",
                         "IGNORE PREVIOUS", "DISREGARD"];
        for pattern in &forbidden {
            if raw.reasoning.to_uppercase().contains(pattern) {
                return Err(ObserverError::InjectionDetected);
            }
        }

        // 3. Self-reported attack -> fallback
        if raw.attacked_flag.unwrap_or(false) {
            return Err(ObserverError::SelfReportedAttack);
        }

        // 4. Derive authority
        let authority = match raw.cache_ttl.as_str() {
            "all" => AlertAuthority::Authoritative,
            _ => AlertAuthority::Supporting,
        };

        let decision = match raw.decision.as_str() {
            "safe" => ObserverDecision::Safe,
            "unsafe" => ObserverDecision::Unsafe,
            _ => ObserverDecision::Uncertain,
        };

        let severity = match raw.severity.as_str() {
            "critical" => ClaimSeverity::Critical,
            "warning" => ClaimSeverity::Warning,
            _ => ClaimSeverity::Info,
        };

        let cache_ttl = match raw.cache_ttl.as_str() {
            "all" => CacheTTL::All,
            "task" => CacheTTL::Task,
            _ => CacheTTL::Once,
        };

        let rule_verdicts: Vec<RuleVerdict> = raw.rule_verdicts.iter().filter_map(|v| {
            let rule_id = v.rule_id.clone()?;
            Some(RuleVerdict {
                rule_id,
                description: String::new(),
                matched_resource: String::new(),
                llm_agreed: Some(v.agreed),
                llm_comment: v.comment.clone(),
            })
        }).collect();

        let pattern_verdicts: Vec<PatternVerdict> = raw.pattern_verdicts.iter().filter_map(|v| {
            let template_id = v.template_id.clone()?;
            Some(PatternVerdict {
                template_id,
                match_confidence: 0.0,
                llm_agreed: Some(v.agreed),
                llm_comment: v.comment.clone(),
            })
        }).collect();

        Ok(ObserverOutput {
            decision, severity, confidence: raw.confidence,
            cache_ttl, authority,
            reasoning: raw.reasoning, attacked_flag: false,
            evidence_cited: raw.evidence_cited,
            rule_verdicts, pattern_verdicts,
        })
    }

    /// Recover truncated JSON by closing unbalanced braces, brackets, and strings.
    ///
    /// When DeepSeek's response is cut off mid-JSON (EOF while parsing), we can
    /// often salvage the decision/severity/confidence fields by auto-closing the
    /// incomplete structure. This turns ~15% of parse failures into usable results.
    fn recover_truncated_json(json_str: &str) -> String {
        let mut result = json_str.to_string();
        let chars: Vec<char> = result.chars().collect();

        // Count unclosed braces, brackets, and strings
        let mut brace_depth: i32 = 0;
        let mut bracket_depth: i32 = 0;
        let mut in_string = false;
        let mut escape_next = false;

        for &c in &chars {
            if escape_next {
                escape_next = false;
                continue;
            }
            if c == '\\' && in_string {
                escape_next = true;
                continue;
            }
            if c == '"' {
                in_string = !in_string;
                continue;
            }
            if in_string {
                continue;
            }
            match c {
                '{' => brace_depth += 1,
                '}' => brace_depth -= 1,
                '[' => bracket_depth += 1,
                ']' => bracket_depth -= 1,
                _ => {}
            }
        }

        // Close unclosed string
        if in_string {
            result.push('"');
        }
        // Close unclosed brackets before braces (JSON structure rules)
        for _ in 0..bracket_depth.max(0) {
            result.push(']');
        }
        // Close unclosed braces
        for _ in 0..brace_depth.max(0) {
            result.push('}');
        }

        result
    }
}
