pub const AUTHORITY_AUTHORITATIVE: &str = "authoritative";
pub const AUTHORITY_SUPPORTING: &str = "supporting";
pub const AUTHORITY_LEGACY: &str = "legacy";
pub const AUTHORITY_UNAVAILABLE: &str = "unavailable";
pub const AUTHORITY_ERROR: &str = "error";

pub const ACCOUNTABILITY_TRACEBACK_AVAILABLE: &str = "available";
pub const ACCOUNTABILITY_TRACEBACK_UNAVAILABLE: &str = "unavailable";

pub const CORRELATION_EVIDENCE_KIND: &str = "correlation_evidence";
pub const CORRELATION_EVIDENCE_AUTHORITY: &str = AUTHORITY_SUPPORTING;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authority_contract_values_are_stable() {
        assert_eq!(AUTHORITY_AUTHORITATIVE, "authoritative");
        assert_eq!(AUTHORITY_SUPPORTING, "supporting");
        assert_eq!(AUTHORITY_LEGACY, "legacy");
        assert_eq!(AUTHORITY_UNAVAILABLE, "unavailable");
        assert_eq!(AUTHORITY_ERROR, "error");
        assert_eq!(CORRELATION_EVIDENCE_KIND, "correlation_evidence");
        assert_eq!(CORRELATION_EVIDENCE_AUTHORITY, "supporting");
    }
}
