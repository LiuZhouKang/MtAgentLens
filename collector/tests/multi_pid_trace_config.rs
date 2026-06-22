fn build_process_args_for_pid(
    pid: u32,
    comm: Option<&str>,
    duration: Option<u32>,
    mode: Option<u32>,
) -> Vec<String> {
    let mut args = Vec::new();
    if let Some(comm_filter) = comm {
        args.extend(["-c".to_string(), comm_filter.to_string()]);
    }
    args.extend(["-p".to_string(), pid.to_string()]);
    if let Some(duration_filter) = duration {
        args.extend(["-d".to_string(), duration_filter.to_string()]);
    }
    if let Some(mode_filter) = mode {
        args.extend(["-m".to_string(), mode_filter.to_string()]);
    }
    args.extend([
        "--trace-fs".to_string(),
        "--trace-net".to_string(),
        "--trace-signals".to_string(),
    ]);
    args
}

fn build_system_targets(single_pid: Option<u32>, pid_set: &[u32]) -> Vec<u32> {
    if !pid_set.is_empty() {
        return pid_set.to_vec();
    }
    single_pid.into_iter().collect()
}

#[test]
fn multi_pid_process_runner_args_expand_per_target_pid() {
    let pid_set = vec![1111, 2222, 3333];

    let per_pid_args: Vec<Vec<String>> = pid_set
        .iter()
        .map(|pid| build_process_args_for_pid(*pid, Some("claude"), None, None))
        .collect();

    assert_eq!(per_pid_args.len(), 3);
    assert_eq!(
        per_pid_args[0],
        vec![
            "-c",
            "claude",
            "-p",
            "1111",
            "--trace-fs",
            "--trace-net",
            "--trace-signals"
        ]
    );
    assert_eq!(
        per_pid_args[1],
        vec![
            "-c",
            "claude",
            "-p",
            "2222",
            "--trace-fs",
            "--trace-net",
            "--trace-signals"
        ]
    );
    assert_eq!(
        per_pid_args[2],
        vec![
            "-c",
            "claude",
            "-p",
            "3333",
            "--trace-fs",
            "--trace-net",
            "--trace-signals"
        ]
    );
}

#[test]
fn multi_pid_system_targets_prefer_pid_set_over_single_pid() {
    let targets = build_system_targets(Some(9999), &[1111, 2222]);
    assert_eq!(targets, vec![1111, 2222]);
}

#[test]
fn multi_pid_system_targets_fall_back_to_single_pid() {
    let targets = build_system_targets(Some(9999), &[]);
    assert_eq!(targets, vec![9999]);
}
