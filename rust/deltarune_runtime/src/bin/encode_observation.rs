use deltarune_runtime::{encode, TelemetryInput};
use serde::Deserialize;
use std::io::{self, BufRead};

#[derive(Deserialize)]
struct Request {
    #[serde(default)]
    telemetry: TelemetryInput,
    #[serde(default)]
    previous_action: Option<String>,
    #[serde(default)]
    previous_action_duration: Option<f64>,
}

fn main() {
    let stdin = io::stdin();
    for (line_number, line) in stdin.lock().lines().enumerate() {
        let line = match line {
            Ok(value) => value,
            Err(error) => { eprintln!("stdin line {}: {error}", line_number + 1); std::process::exit(2); }
        };
        if line.trim().is_empty() { continue; }
        let request: Request = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(error) => { eprintln!("invalid JSON on line {}: {error}", line_number + 1); std::process::exit(2); }
        };
        let observation = encode(
            &request.telemetry,
            request.previous_action.as_deref(),
            request.previous_action_duration,
        );
        match serde_json::to_string(&observation) {
            Ok(json) => println!("{json}"),
            Err(error) => { eprintln!("serialization failed: {error}"); std::process::exit(3); }
        }
    }
}
