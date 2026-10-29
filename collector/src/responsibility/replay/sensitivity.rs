// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Threshold sensitivity over recorded analysis checkpoints (review comment 6).
//!
//! The reviewer asks whether the 0.15 / 0.70 thresholds and the surrounding
//! parameters were tuned on the same data used for evaluation, and whether the
//! conclusions depend on a single point. The live pipeline cannot answer that
//! without re-running agents, but the frozen checkpoints can: the decision kernel
//! is pure, so replaying a checkpoint under a different `ClassificationConfig`
//! shows which runs change *route* (cost) and whether the evidence-based final
//! verdict moves at all.
//!
//! Scope discipline: this is an analysis-kernel replay on recorded inputs, not a
//! campaign re-run. Thresholds only choose between DirectSafe/ToObserver/
//! DirectUnsafe; the final verdict aggregates typed evidence, so a verdict that
//! does move is a real finding and a verdict that does not is expected.

use serde::Serialize;

pub const SENSITIVITY_SCHEMA: &str = "mtagentlens.threshold-sensitivity/1";

/// Parse `0.10,0.15,0.20` into a sorted, de-duplicated threshold grid.
pub fn parse_threshold_grid(text: &str) -> Result<Vec<f32>, String> {
    let mut values = Vec::new();
    for part in text.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let value: f32 = part
            .parse()
            .map_err(|_| format!("not a number: {part:?}"))?;
        if !(0.0..=1.0).contains(&value) {
            return Err(format!("threshold out of [0,1]: {value}"));
        }
        values.push(value);
    }
    if values.is_empty() {
        return Err("threshold grid is empty".into());
    }
    values.sort_by(f32::total_cmp);
    values.dedup();
    Ok(values)
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct ThresholdPoint {
    pub threshold_safe: f32,
    pub threshold_unsafe: f32,
    pub runs: usize,
    pub direct_safe: usize,
    pub to_observer: usize,
    pub direct_unsafe: usize,
    pub verdict_safe: usize,
    pub verdict_review: usize,
    pub verdict_unsafe: usize,
    /// Runs whose route differs from their own recorded configuration.
    pub route_flips_vs_baseline: usize,
    /// Runs whose final verdict differs from their own recorded configuration.
    pub verdict_flips_vs_baseline: usize,
}

/// One point of the weight / evidence-gate sweep (review comment 6's second half).
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct ScoringPoint {
    pub weight_factor: f64,
    pub gate_center: f64,
    pub runs: usize,
    pub verdict_safe: usize,
    pub verdict_review: usize,
    pub verdict_unsafe: usize,
    pub route_flips_vs_baseline: usize,
    pub verdict_flips_vs_baseline: usize,
}

/// One-dimension ablation point: the named fusion dimension is set to zero.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct AblationPoint {
    pub dimension: u8,
    pub dimension_name: &'static str,
    pub runs: usize,
    pub verdict_safe: usize,
    pub verdict_review: usize,
    pub verdict_unsafe: usize,
    pub route_flips_vs_baseline: usize,
    pub verdict_flips_vs_baseline: usize,
}

pub const DIMENSION_NAMES: [&str; 6] = [
    "boundary",
    "pattern",
    "role",
    "repetition",
    "evidence",
    "behavioral",
];

#[derive(Debug, Clone, Serialize)]
pub struct SensitivityReport {
    pub schema: &'static str,
    pub runs: usize,
    pub baseline_route: Vec<(String, usize)>,
    pub points: Vec<ThresholdPoint>,
    pub note: &'static str,
}

const NOTE: &str = "Analysis-kernel replay over recorded checkpoints with overridden \
thresholds. Thresholds choose the route only; the verdict aggregates typed evidence. \
Route flips measure cost/routing sensitivity, verdict flips measure whether the label \
depends on the thresholds.";

/// Sweep a grid over `runs`, where `evaluator(run_index, safe, unsafe)` returns
/// `(route_label, verdict_label)` for that configuration.
pub fn sweep_with<F>(
    baseline: &[(String, String)],
    safe_grid: &[f32],
    unsafe_grid: &[f32],
    mut evaluator: F,
) -> SensitivityReport
where
    F: FnMut(usize, f32, f32) -> (String, String),
{
    let mut baseline_route: std::collections::BTreeMap<String, usize> =
        std::collections::BTreeMap::new();
    for (route, _) in baseline {
        *baseline_route.entry(route.clone()).or_default() += 1;
    }

    let mut points = Vec::new();
    for &safe in safe_grid {
        for &unsafe_ in unsafe_grid {
            let mut point = ThresholdPoint {
                threshold_safe: safe,
                threshold_unsafe: unsafe_,
                runs: baseline.len(),
                direct_safe: 0,
                to_observer: 0,
                direct_unsafe: 0,
                verdict_safe: 0,
                verdict_review: 0,
                verdict_unsafe: 0,
                route_flips_vs_baseline: 0,
                verdict_flips_vs_baseline: 0,
            };
            for (index, (baseline_route, baseline_verdict)) in baseline.iter().enumerate() {
                let (route, verdict) = evaluator(index, safe, unsafe_);
                match route.as_str() {
                    "DirectSafe" => point.direct_safe += 1,
                    "ToObserver" => point.to_observer += 1,
                    "DirectUnsafe" => point.direct_unsafe += 1,
                    _ => {}
                }
                match verdict.as_str() {
                    "Safe" => point.verdict_safe += 1,
                    "Uncertain" => point.verdict_review += 1,
                    "Unsafe" => point.verdict_unsafe += 1,
                    _ => {}
                }
                if &route != baseline_route {
                    point.route_flips_vs_baseline += 1;
                }
                if &verdict != baseline_verdict {
                    point.verdict_flips_vs_baseline += 1;
                }
            }
            points.push(point);
        }
    }

    SensitivityReport {
        schema: SENSITIVITY_SCHEMA,
        runs: baseline.len(),
        baseline_route: baseline_route.into_iter().collect(),
        points,
        note: NOTE,
    }
}

/// Parse a positive-number grid (weights factors, gate centres).
pub fn parse_number_grid(text: &str) -> Result<Vec<f64>, String> {
    let mut values = Vec::new();
    for part in text.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let value: f64 = part
            .parse()
            .map_err(|_| format!("not a number: {part:?}"))?;
        if !(value.is_finite() && value > 0.0) {
            return Err(format!("value must be positive and finite: {value}"));
        }
        values.push(value);
    }
    if values.is_empty() {
        return Err("number grid is empty".into());
    }
    values.sort_by(f64::total_cmp);
    values.dedup();
    Ok(values)
}

/// Parse a dimension list (`1..=6`) for the leave-one-out ablation.
pub fn parse_dimension_grid(text: &str) -> Result<Vec<u8>, String> {
    let mut values = Vec::new();
    for part in text.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let value: u8 = part
            .parse()
            .map_err(|_| format!("not a dimension: {part:?}"))?;
        if !(1..=6).contains(&value) {
            return Err(format!("dimension out of 1..6: {value}"));
        }
        values.push(value);
    }
    if values.is_empty() {
        return Err("dimension grid is empty".into());
    }
    values.sort_unstable();
    values.dedup();
    Ok(values)
}

/// Sweep weight scaling × evidence-gate centre, where
/// `evaluator(run_index, weight_factor, gate_center)` returns
/// `(route_label, verdict_label)`.
pub fn sweep_scoring_with<F>(
    baseline: &[(String, String)],
    weight_factors: &[f64],
    gate_centers: &[f64],
    mut evaluator: F,
) -> Vec<ScoringPoint>
where
    F: FnMut(usize, f64, f64) -> (String, String),
{
    let mut points = Vec::new();
    for &weight_factor in weight_factors {
        for &gate_center in gate_centers {
            let mut point = ScoringPoint {
                weight_factor,
                gate_center,
                runs: baseline.len(),
                verdict_safe: 0,
                verdict_review: 0,
                verdict_unsafe: 0,
                route_flips_vs_baseline: 0,
                verdict_flips_vs_baseline: 0,
            };
            for (index, (baseline_route, baseline_verdict)) in baseline.iter().enumerate() {
                let (route, verdict) = evaluator(index, weight_factor, gate_center);
                match verdict.as_str() {
                    "Safe" => point.verdict_safe += 1,
                    "Uncertain" => point.verdict_review += 1,
                    "Unsafe" => point.verdict_unsafe += 1,
                    _ => {}
                }
                if &route != baseline_route {
                    point.route_flips_vs_baseline += 1;
                }
                if &verdict != baseline_verdict {
                    point.verdict_flips_vs_baseline += 1;
                }
            }
            points.push(point);
        }
    }
    points
}

/// Relative-weight sensitivity changes exactly one weight, preserving the other
/// five. Uniform scaling is retained separately as a normalization control.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct RelativeWeightPoint {
    pub dimension: u8,
    pub dimension_name: &'static str,
    #[serde(flatten)]
    pub result: ScoringPoint,
}

pub fn sweep_relative_weights_with<F>(
    baseline: &[(String, String)],
    weight_factors: &[f64],
    gate_centers: &[f64],
    mut evaluator: F,
) -> Vec<RelativeWeightPoint>
where
    F: FnMut(usize, u8, f64, f64) -> (String, String),
{
    let mut points = Vec::new();
    for (index, &dimension_name) in DIMENSION_NAMES.iter().enumerate() {
        let dimension = (index + 1) as u8;
        points.extend(
            sweep_scoring_with(
                baseline,
                weight_factors,
                gate_centers,
                |run, factor, center| evaluator(run, dimension, factor, center),
            )
            .into_iter()
            .map(|result| RelativeWeightPoint {
                dimension,
                dimension_name,
                result,
            }),
        );
    }
    points
}

/// Zero one fusion dimension at a time and report the change against the
/// recorded configuration. `evaluator(run_index, dimension)` returns
/// `(route_label, verdict_label)` with that dimension's weight zeroed.
pub fn sweep_ablation_with<F>(
    baseline: &[(String, String)],
    dimensions: &[u8],
    mut evaluator: F,
) -> Vec<AblationPoint>
where
    F: FnMut(usize, u8) -> (String, String),
{
    let mut points = Vec::new();
    for &dimension in dimensions {
        let index = usize::from(dimension.saturating_sub(1));
        let name = DIMENSION_NAMES.get(index).copied().unwrap_or("unknown");
        let mut point = AblationPoint {
            dimension,
            dimension_name: name,
            runs: baseline.len(),
            verdict_safe: 0,
            verdict_review: 0,
            verdict_unsafe: 0,
            route_flips_vs_baseline: 0,
            verdict_flips_vs_baseline: 0,
        };
        for (run_index, (baseline_route, baseline_verdict)) in baseline.iter().enumerate() {
            let (route, verdict) = evaluator(run_index, dimension);
            match verdict.as_str() {
                "Safe" => point.verdict_safe += 1,
                "Uncertain" => point.verdict_review += 1,
                "Unsafe" => point.verdict_unsafe += 1,
                _ => {}
            }
            if &route != baseline_route {
                point.route_flips_vs_baseline += 1;
            }
            if &verdict != baseline_verdict {
                point.verdict_flips_vs_baseline += 1;
            }
        }
        points.push(point);
    }
    points
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grid_parsing_sorts_and_deduplicates() {
        let grid = parse_threshold_grid("0.20, 0.10,0.20,0.3").expect("grid");
        assert_eq!(grid, vec![0.10, 0.20, 0.3]);
    }

    #[test]
    fn grid_parsing_rejects_junk_and_out_of_range() {
        assert!(parse_threshold_grid("abc").is_err());
        assert!(parse_threshold_grid("1.5").is_err());
        assert!(parse_threshold_grid(" ").is_err());
    }

    #[test]
    fn dimension_grid_validates_range() {
        assert_eq!(parse_dimension_grid("3,1").expect("grid"), vec![1, 3]);
        assert!(parse_dimension_grid("0").is_err());
        assert!(parse_dimension_grid("7").is_err());
        assert!(parse_dimension_grid("x").is_err());
    }

    #[test]
    fn ablation_sweep_names_dimensions_and_counts_flips() {
        let baseline = vec![
            ("DirectSafe".to_string(), "Safe".to_string()),
            ("ToObserver".to_string(), "Uncertain".to_string()),
        ];
        let points = sweep_ablation_with(&baseline, &[1, 5], |run, dimension| {
            if dimension == 1 && run == 0 {
                ("DirectUnsafe".to_string(), "Unsafe".to_string())
            } else {
                (baseline[run].0.clone(), baseline[run].1.clone())
            }
        });
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].dimension_name, "boundary");
        assert_eq!(points[0].verdict_flips_vs_baseline, 1);
        assert_eq!(points[1].dimension_name, "evidence");
        assert_eq!(points[1].verdict_flips_vs_baseline, 0);
    }

    #[test]
    fn number_grid_rejects_zero_and_junk() {
        assert!(parse_number_grid("0").is_err());
        assert!(parse_number_grid("-1").is_err());
        assert!(parse_number_grid("x").is_err());
        assert_eq!(parse_number_grid("1.2, 0.8").expect("grid"), vec![0.8, 1.2]);
    }

    #[test]
    fn relative_weight_sweep_varies_one_dimension_and_preserves_gate_cross_product() {
        let baseline = vec![("DirectSafe".to_string(), "Safe".to_string())];
        let mut visited = Vec::new();
        let points = sweep_relative_weights_with(
            &baseline,
            &[0.8, 1.2],
            &[5.0, 10.0],
            |run, dimension, factor, center| {
                visited.push((run, dimension, factor, center));
                if dimension == 2 && factor < 1.0 {
                    ("ToObserver".to_string(), "Uncertain".to_string())
                } else {
                    baseline[run].clone()
                }
            },
        );
        assert_eq!(points.len(), 24);
        assert_eq!(visited.len(), 24);
        for dimension in 1..=6 {
            assert_eq!(
                points
                    .iter()
                    .filter(|point| point.dimension == dimension)
                    .count(),
                4
            );
        }
        assert_eq!(
            points
                .iter()
                .filter(|point| point.result.verdict_flips_vs_baseline == 1)
                .count(),
            2
        );
        assert_eq!(points[4].dimension_name, "pattern");
    }

    #[test]
    fn scoring_sweep_reports_a_verdict_flip() {
        let baseline = vec![("DirectSafe".to_string(), "Safe".to_string())];
        let points =
            sweep_scoring_with(&baseline, &[0.8, 1.2], &[10.0], |_run, factor, _center| {
                if factor < 1.0 {
                    ("DirectSafe".to_string(), "Unsafe".to_string())
                } else {
                    ("DirectSafe".to_string(), "Safe".to_string())
                }
            });
        assert_eq!(points.len(), 2);
        assert_eq!(points[0].weight_factor, 0.8);
        assert_eq!(points[0].verdict_flips_vs_baseline, 1);
        assert_eq!(points[1].verdict_flips_vs_baseline, 0);
    }

    #[test]
    fn sweep_counts_routes_and_flips_against_baseline() {
        // Two recorded runs: one routed safe, one to the observer.
        let baseline = vec![
            ("DirectSafe".to_string(), "Safe".to_string()),
            ("ToObserver".to_string(), "Uncertain".to_string()),
        ];
        // The fake kernel routes by thresholds: below safe -> DirectSafe,
        // above unsafe -> DirectUnsafe, otherwise ToObserver.
        let report = sweep_with(
            &baseline,
            &[0.10, 0.90],
            &[0.95, 0.50],
            |_run, safe, unsafe_| {
                if safe > unsafe_ {
                    ("DirectUnsafe".to_string(), "Unsafe".to_string())
                } else {
                    ("ToObserver".to_string(), "Uncertain".to_string())
                }
            },
        );
        assert_eq!(report.schema, SENSITIVITY_SCHEMA);
        assert_eq!(report.runs, 2);
        assert_eq!(report.points.len(), 4);
        let invalid = report
            .points
            .iter()
            .find(|point| point.threshold_safe > point.threshold_unsafe)
            .expect("inverted point");
        assert_eq!(invalid.direct_unsafe, 2);
        assert_eq!(invalid.verdict_unsafe, 2);
        assert_eq!(invalid.route_flips_vs_baseline, 2);
        let valid = report
            .points
            .iter()
            .find(|point| point.threshold_safe < point.threshold_unsafe)
            .expect("valid point");
        assert_eq!(valid.to_observer, 2);
        assert_eq!(
            valid.verdict_safe + valid.verdict_review + valid.verdict_unsafe,
            2
        );
        assert_eq!(report.baseline_route.len(), 2);
    }
}
