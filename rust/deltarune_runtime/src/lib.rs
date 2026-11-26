//! Numerical observation encoder matching deltarune_agent.neural_env.
//!
//! Keep feature order, normalization constants, missing-value behavior, and
//! room-bucket hashing synchronized with the Python reference implementation.

use blake2::digest::consts::U2;
use blake2::{Blake2b, Digest};
use serde::{Deserialize, Serialize};

type Blake2b2 = Blake2b<U2>;

pub const FACING_ORDER: [&str; 5] = ["down", "left", "right", "up", "unknown"];
pub const MODE_ORDER: [&str; 6] = ["overworld", "dialogue", "cutscene", "menu", "battle", "unknown"];
pub const ACTION_ORDER: [&str; 8] = ["wait", "confirm", "cancel", "menu", "up", "down", "left", "right"];

pub const FEATURE_NAMES: [&str; 55] = [
    "position.x", "position.y", "position.foot_x", "position.foot_y",
    "velocity.x", "velocity.y", "motion.speed", "motion.direction_sin",
    "motion.direction_cos", "room.width", "room.height", "camera.x",
    "camera.y", "camera.width", "camera.height", "bbox.left", "bbox.top",
    "bbox.right", "bbox.bottom", "sprite.width", "sprite.height",
    "sprite.xscale", "sprite.yscale", "timing.room_speed", "timing.fps",
    "timing.game_time", "timing.sample_interval", "delta.x", "delta.y",
    "identity.room_id", "identity.room_bucket", "state.interaction",
    "state.player_controlled", "state.visible", "state.facing.down",
    "state.facing.left", "state.facing.right", "state.facing.up",
    "state.facing.unknown", "state.mode.overworld", "state.mode.dialogue",
    "state.mode.cutscene", "state.mode.menu", "state.mode.battle",
    "state.mode.unknown", "action.previous.wait", "action.previous.confirm",
    "action.previous.cancel", "action.previous.menu", "action.previous.up",
    "action.previous.down", "action.previous.left", "action.previous.right",
    "action.previous_duration",
];

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(default)]
pub struct TelemetryInput {
    pub mode: String,
    pub room_id: i64,
    pub room_name: String,
    pub x: f64,
    pub y: f64,
    pub player_x: Option<f64>,
    pub player_y: Option<f64>,
    pub player_foot_x: Option<f64>,
    pub player_foot_y: Option<f64>,
    pub hspeed: Option<f64>,
    pub vspeed: Option<f64>,
    pub speed: Option<f64>,
    pub direction: Option<f64>,
    pub room_width: Option<f64>,
    pub room_height: Option<f64>,
    pub camera_x: Option<f64>,
    pub camera_y: Option<f64>,
    pub camera_width: Option<f64>,
    pub camera_height: Option<f64>,
    pub player_bbox_left: Option<f64>,
    pub player_bbox_top: Option<f64>,
    pub player_bbox_right: Option<f64>,
    pub player_bbox_bottom: Option<f64>,
    pub sprite_width: Option<f64>,
    pub sprite_height: Option<f64>,
    pub image_xscale: Option<f64>,
    pub image_yscale: Option<f64>,
    pub room_speed: Option<f64>,
    pub fps: Option<f64>,
    pub game_time_ms: Option<f64>,
    pub sample_interval_ms: Option<f64>,
    pub sample_delta_x: Option<f64>,
    pub sample_delta_y: Option<f64>,
    pub interaction_state: Option<i64>,
    pub player_controlled: Option<bool>,
    pub visible: Option<bool>,
    pub player_facing_direction: Option<String>,
    pub packet_sequence: Option<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NeuralObservation {
    pub values: Vec<f64>,
    pub available: Vec<f64>,
    pub feature_names: Vec<&'static str>,
    pub source_sequence: Option<i64>,
    pub room_id: i64,
    pub room_name: String,
}

impl NeuralObservation {
    pub fn combined(&self) -> Vec<f64> {
        self.values.iter().chain(self.available.iter()).copied().collect()
    }
}

fn clip(value: f64, limit: f64) -> f64 {
    if !value.is_finite() { return 0.0; }
    value.max(-limit).min(limit)
}

fn norm_signed(value: Option<f64>, scale: f64) -> (f64, f64) {
    match value {
        Some(v) if v.is_finite() => (clip(v / scale, 1.0), 1.0),
        _ => (0.0, 0.0),
    }
}

fn norm_positive(value: Option<f64>, scale: f64) -> (f64, f64) {
    match value {
        Some(v) if v.is_finite() => (v / scale).max(0.0).min(1.0),
        _ => (0.0, 0.0),
    }
}

fn room_bucket(room_name: &str, room_id: i64) -> f64 {
    let key = format!("{room_id}:{room_name}");
    let mut hasher = Blake2b2::new();
    hasher.update(key.as_bytes());
    let digest = hasher.finalize();
    f64::from(digest[0]) / 255.0
}

fn angle_components(direction: Option<f64>) -> (f64, f64, f64) {
    match direction {
        Some(v) if v.is_finite() => {
            let radians = v.to_radians();
            (radians.sin(), radians.cos(), 1.0)
        }
        _ => (0.0, 0.0, 0.0),
    }
}

fn one_hot(value: Option<&str>, order: &[&str]) -> Vec<f64> {
    let normalized = value.unwrap_or("unknown").to_lowercase();
    order.iter().map(|item| if normalized == *item { 1.0 } else { 0.0 }).collect()
}

pub fn encode(
    sample: &TelemetryInput,
    previous_action: Option<&str>,
    previous_action_duration: Option<f64>,
) -> NeuralObservation {
    let mut values = Vec::with_capacity(FEATURE_NAMES.len());
    let mut available = Vec::with_capacity(FEATURE_NAMES.len());

    fn add(values: &mut Vec<f64>, masks: &mut Vec<f64>, value: Option<f64>, mask: f64) {
        match value {
            Some(v) if v.is_finite() => { values.push(v); masks.push(mask); }
            _ => { values.push(0.0); masks.push(0.0); }
        }
    }
    fn signed(values: &mut Vec<f64>, masks: &mut Vec<f64>, value: Option<f64>, scale: f64) {
        let (v, m) = norm_signed(value, scale); add(values, masks, Some(v), m);
    }
    fn positive(values: &mut Vec<f64>, masks: &mut Vec<f64>, value: Option<f64>, scale: f64) {
        let (v, m) = norm_positive(value, scale); add(values, masks, Some(v), m);
    }

    signed(&mut values, &mut available, sample.player_x.or(Some(sample.x)), 4096.0);
    signed(&mut values, &mut available, sample.player_y.or(Some(sample.y)), 4096.0);
    signed(&mut values, &mut available, sample.player_foot_x, 4096.0);
    signed(&mut values, &mut available, sample.player_foot_y, 4096.0);
    signed(&mut values, &mut available, sample.hspeed, 32.0);
    signed(&mut values, &mut available, sample.vspeed, 32.0);
    positive(&mut values, &mut available, sample.speed, 32.0);

    let (sin, cos, mask) = angle_components(sample.direction);
    add(&mut values, &mut available, Some(sin), mask);
    add(&mut values, &mut available, Some(cos), mask);

    positive(&mut values, &mut available, sample.room_width, 4096.0);
    positive(&mut values, &mut available, sample.room_height, 4096.0);
    signed(&mut values, &mut available, sample.camera_x, 4096.0);
    signed(&mut values, &mut available, sample.camera_y, 4096.0);
    positive(&mut values, &mut available, sample.camera_width, 4096.0);
    positive(&mut values, &mut available, sample.camera_height, 4096.0);

    signed(&mut values, &mut available, sample.player_bbox_left, 4096.0);
    signed(&mut values, &mut available, sample.player_bbox_top, 4096.0);
    signed(&mut values, &mut available, sample.player_bbox_right, 4096.0);
    signed(&mut values, &mut available, sample.player_bbox_bottom, 4096.0);
    positive(&mut values, &mut available, sample.sprite_width, 4096.0);
    positive(&mut values, &mut available, sample.sprite_height, 4096.0);
    signed(&mut values, &mut available, sample.image_xscale, 4.0);
    signed(&mut values, &mut available, sample.image_yscale, 4.0);
    positive(&mut values, &mut available, sample.room_speed, 120.0);
    positive(&mut values, &mut available, sample.fps, 240.0);
    positive(&mut values, &mut available, sample.game_time_ms, 100_000.0);
    positive(&mut values, &mut available, sample.sample_interval_ms, 1000.0);
    signed(&mut values, &mut available, sample.sample_delta_x, 4096.0);
    signed(&mut values, &mut available, sample.sample_delta_y, 4096.0);

    add(&mut values, &mut available, Some(clip(sample.room_id as f64 / 1000.0, 1.0)), 1.0);
    add(&mut values, &mut available, Some(room_bucket(&sample.room_name, sample.room_id)), 1.0);
    match sample.interaction_state {
        Some(v) => add(&mut values, &mut available, Some(clip(v as f64 / 4.0, 1.0)), 1.0),
        None => add(&mut values, &mut available, None, 0.0),
    }
    add(&mut values, &mut available, sample.player_controlled.map(|v| if v { 1.0 } else { 0.0 }), 1.0);
    add(&mut values, &mut available, sample.visible.map(|v| if v { 1.0 } else { 0.0 }), 1.0);

    for v in one_hot(sample.player_facing_direction.as_deref(), &FACING_ORDER) {
        add(&mut values, &mut available, Some(v), 1.0);
    }
    for v in one_hot(Some(&sample.mode), &MODE_ORDER) {
        add(&mut values, &mut available, Some(v), 1.0);
    }

    let action = previous_action.filter(|name| ACTION_ORDER.contains(name)).unwrap_or("wait");
    for candidate in ACTION_ORDER {
        add(&mut values, &mut available, Some(if action == candidate { 1.0 } else { 0.0 }), 1.0);
    }
    positive(&mut values, &mut available, previous_action_duration, 1.0);

    assert_eq!(values.len(), FEATURE_NAMES.len(), "feature width drift");
    NeuralObservation {
        values,
        available,
        feature_names: FEATURE_NAMES.to_vec(),
        source_sequence: sample.packet_sequence,
        room_id: sample.room_id,
        room_name: sample.room_name.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn feature_contract_is_exactly_55_values_and_masks() {
        let obs = encode(&TelemetryInput::default(), None, None);
        assert_eq!(obs.values.len(), 55);
        assert_eq!(obs.available.len(), 55);
        assert_eq!(obs.combined().len(), 110);
        assert_eq!(obs.feature_names, FEATURE_NAMES);
        assert!(obs.values.iter().all(|v| v.is_finite() && v.abs() <= 1.0));
    }

    #[test]
    fn missing_values_are_zero_and_unavailable() {
        let obs = encode(&TelemetryInput::default(), None, None);
        for index in [2usize, 3, 4, 5, 6, 11, 12, 13, 14] {
            assert_eq!(obs.values[index], 0.0);
            assert_eq!(obs.available[index], 0.0);
        }
    }

    #[test]
    fn previous_action_is_one_hot_and_invalid_action_falls_back_to_wait() {
        let sample = TelemetryInput::default();
        let right = encode(&sample, Some("right"), Some(0.25));
        assert_eq!(right.values[52], 1.0);
        assert_eq!(right.values[45], 0.0);
        let invalid = encode(&sample, Some("teleport"), None);
        assert_eq!(invalid.values[45], 1.0);
    }

    #[test]
    fn room_bucket_matches_python_blake2b_digest_size_two() {
        let sample = TelemetryInput { room_id: 7, room_name: "room_home".into(), ..TelemetryInput::default() };
        let obs = encode(&sample, None, None);
        assert!((obs.values[30] - (253.0 / 255.0)).abs() < 1e-15);
    }

    #[test]
    fn non_finite_optional_values_are_missing() {
        let mut sample = TelemetryInput::default();
        sample.hspeed = Some(f64::NAN);
        let obs = encode(&sample, None, None);
        assert_eq!(obs.values[4], 0.0);
        assert_eq!(obs.available[4], 0.0);
    }
}
