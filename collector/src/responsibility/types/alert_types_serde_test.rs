
#[cfg(test)]
mod serde_tests {
    use super::*;
    
    #[test]
    fn test_deberta_score_serialization() {
        let es = EngineSection {
            rule_verdicts: vec![],
            pattern_verdicts: vec![],
            classification_score: 0.5,
            classification_route: "direct_safe".into(),
            score_breakdown: Default::default(),
            cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            deberta_score: 0.85,
        };
        let json = serde_json::to_string(&es).unwrap();
        println!("JSON: {}", json);
        assert!(json.contains("deberta_score"), "deberta_score field should be serialized");
        assert!(json.contains("0.85"), "deberta_score value should be present");
    }
}
