use blake2::{Blake2b, Digest};
use serde::{Deserialize, Serialize};
use std::f64::consts::PI;

pub const OBSERVATION_WIDTH: usize = 54;
const POSITION_SCALE: f64 = 4096.0;
const VELOCITY_SCALE: f64 = 32.0;
const SPEED_SCALE: f64 = 32.0;
const ROOM_SIZE_SCALE: f64 = 4096.0;
const CAMERA_SCALE: f64 = 4096.0;
const TIME_SCALE_MS: f64 = 100_000.0;
const INTERVAL_SCALE_MS: f64 = 1000.0;
const ACTION_DURATION_SCALE: f64 = 1.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Action {
    Wait, Confirm, Cancel, Menu, Up, Down, Left, Right,
}

impl Action {
    pub const ALL: [Self; 8] = [
        Self::Wait, Self::Confirm, Self::Cancel, Self::Menu,
        Self::Up, Self::Down, Self::Left, Self::Right,
    ];
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObservationInput {
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
    pub sample_delta_x: Option<f64>,
    pub sample_delta_y: Option<f64>,
    pub sample_interval_ms: Option<f64>,
    pub interaction_state: Option<i64>,
    pub player_controlled: Option<bool>,
    pub visible: Option<bool>,
    pub player_facing_direction: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncodedObservation {
    pub values: Vec<f64>,
    pub available: Vec<f64>,
}

impl EncodedObservation {
    pub fn combined(&self) -> Vec<f64> {
        let mut result = Vec::with_capacity(self.values.len() * 2);
        result.extend_from_slice(&self.values);
        result.extend_from_slice(&self.available);
        result
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Feature {
    pub name: &'static str,
}

const FEATURES: [&str; OBSERVATION_WIDTH] = [
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

pub fn features() -> impl Iterator<Item = Feature> {
    FEATURES.iter().copied().map(|name| Feature { name })
}

fn finite(v: f64) -> bool { v.is_finite() }

fn clip(v: f64, limit: f64) -> f64 {
    if !finite(v) { 0.0 } else { v.clamp(-limit, limit) }
}

fn norm_signed(v: Option<f64>, scale: f64) -> (f64, f64) {
    match v {
        Some(v) if finite(v) => (clip(v / scale, 1.0), 1.0),
        _ => (0.0, 0.0),
    }
}

fn norm_positive(v: Option<f64>, scale: f64) -> (f64, f64) {
    match v {
        Some(v) if finite(v) => ((v / scale).clamp(0.0, 1.0), 1.0),
        _ => (0.0, 0.0),
    }
}

fn add(values: &mut Vec<f64>, masks: &mut Vec<f64>, v: Option<f64>, mask: f64) {
    match v {
        Some(v) if finite(v) => { values.push(v); masks.push(mask); }
        _ => { values.push(0.0); masks.push(0.0); }
    }
}

fn add_signed(values: &mut Vec<f64>, masks: &mut Vec<f64>, v: Option<f64>, scale: f64) {
    let (v, mask) = norm_signed(v, scale);
    add(values, masks, Some(v), mask);
}

fn add_positive(values: &mut Vec<f64>, masks: &mut Vec<f64>, v: Option<f64>, scale: f64) {
    let (v, mask) = norm_positive(v, scale);
    add(values, masks, Some(v), mask);
}

fn one_hot(value: Option<&str>, order: &[&str]) -> Vec<f64> {
    let normalized = value.unwrap_or("unknown").to_ascii_lowercase();
    order.iter().map(|candidate| if normalized == *candidate { 1.0 } else { 0.0 }).collect()
}

fn room_bucket(room_name: &str, room_id: i64) -> f64 {
    let key = format!("{}:{}", room_id, room_name);
    // Python: hashlib.blake2b(key, digest_size=2).digest()[0] / 255.
    let mut hasher = Blake2b::<blake2::digest::consts::U2>::new();
    hasher.update(key.as_bytes());
    let digest = hasher.finalize();
    digest[0] as f64 / 255.0
}

fn angle_components(direction: Option<f64>) -> (f64, f64, f64) {
    match direction {
        Some(degrees) if finite(degrees) => {
            let r = degrees * PI / 180.0;
            (r.sin(), r.cos(), 1.0)
        }
        _ => (0.0, 0.0, 0.0),
    }
}

pub fn encode_observation(
    sample: &ObservationInput,
    previous_action: Option<Action>,
    previous_action_duration: Option<f64>,
) -> EncodedObservation {
    let mut values = Vec::with_capacity(OBSERVATION_WIDTH);
    let mut masks = Vec::with_capacity(OBSERVATION_WIDTH);

    add_signed(&mut values, &mut masks, sample.player_x.or(Some(sample.x)), POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_y.or(Some(sample.y)), POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_foot_x, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_foot_y, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.hspeed, VELOCITY_SCALE);
    add_signed(&mut values, &mut masks, sample.vspeed, VELOCITY_SCALE);
    add_positive(&mut values, &mut masks, sample.speed, SPEED_SCALE);

    let (sin, cos, mask) = angle_components(sample.direction);
    add(&mut values, &mut masks, Some(sin), mask);
    add(&mut values, &mut masks, Some(cos), mask);

    add_positive(&mut values, &mut masks, sample.room_width, ROOM_SIZE_SCALE);
    add_positive(&mut values, &mut masks, sample.room_height, ROOM_SIZE_SCALE);
    add_signed(&mut values, &mut masks, sample.camera_x, CAMERA_SCALE);
    add_signed(&mut values, &mut masks, sample.camera_y, CAMERA_SCALE);
    add_positive(&mut values, &mut masks, sample.camera_width, CAMERA_SCALE);
    add_positive(&mut values, &mut masks, sample.camera_height, CAMERA_SCALE);

    add_signed(&mut values, &mut masks, sample.player_bbox_left, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_bbox_top, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_bbox_right, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.player_bbox_bottom, POSITION_SCALE);

    add_positive(&mut values, &mut masks, sample.sprite_width, POSITION_SCALE);
    add_positive(&mut values, &mut masks, sample.sprite_height, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.image_xscale, 4.0);
    add_signed(&mut values, &mut masks, sample.image_yscale, 4.0);
    add_positive(&mut values, &mut masks, sample.room_speed, 120.0);
    add_positive(&mut values, &mut masks, sample.fps, 240.0);
    add_positive(&mut values, &mut masks, sample.game_time_ms, TIME_SCALE_MS);
    add_positive(&mut values, &mut masks, sample.sample_interval_ms, INTERVAL_SCALE_MS);
    add_signed(&mut values, &mut masks, sample.sample_delta_x, POSITION_SCALE);
    add_signed(&mut values, &mut masks, sample.sample_delta_y, POSITION_SCALE);

    add(&mut values, &mut masks, Some(clip(sample.room_id as f64 / 1000.0, 1.0)), 1.0);
    add(&mut values, &mut masks, Some(room_bucket(&sample.room_name, sample.room_id)), 1.0);

    add(&mut values, &mut masks,
        sample.interaction_state.map(|v| clip(v as f64 / 4.0, 1.0)), 1.0);
    add(&mut values, &mut masks,
        sample.player_controlled.map(|v| if v { 1.0 } else { 0.0 }),
        if sample.player_controlled.is_some() { 1.0 } else { 0.0 });
    add(&mut values, &mut masks,
        sample.visible.map(|v| if v { 1.0 } else { 0.0 }),
        if sample.visible.is_some() { 1.0 } else { 0.0 });

    for v in one_hot(sample.player_facing_direction.as_deref(),
                     &["down", "left", "right", "up", "unknown"]) {
        add(&mut values, &mut masks, Some(v), 1.0);
    }
    for v in one_hot(Some(&sample.mode),
                     &["overworld", "dialogue", "cutscene", "menu", "battle", "unknown"]) {
        add(&mut values, &mut masks, Some(v), 1.0);
    }

    let action = previous_action.unwrap_or(Action::Wait);
    for candidate in Action::ALL {
        add(&mut values, &mut masks,
            Some(if candidate == action { 1.0 } else { 0.0 }), 1.0);
    }
    add_positive(&mut values, &mut masks, previous_action_duration, ACTION_DURATION_SCALE);

    assert_eq!(values.len(), OBSERVATION_WIDTH);
    assert_eq!(masks.len(), OBSERVATION_WIDTH);
    EncodedObservation { values, available: masks }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> ObservationInput {
        ObservationInput {
            mode: "overworld".into(), room_id: 7, room_name: "room_home".into(),
            x: 120.0, y: 42.0,
            player_x: Some(120.0), player_y: Some(42.0),
            player_foot_x: Some(120.0), player_foot_y: Some(50.0),
            hspeed: Some(4.0), vspeed: Some(0.0), speed: Some(4.0),
            direction: Some(0.0), room_width: Some(640.0), room_height: Some(480.0),
            camera_x: Some(32.0), camera_y: Some(16.0),
            camera_width: Some(320.0), camera_height: Some(240.0),
            player_bbox_left: Some(112.0), player_bbox_top: Some(30.0),
            player_bbox_right: Some(128.0), player_bbox_bottom: Some(50.0),
            sprite_width: Some(40.0), sprite_height: Some(56.0),
            image_xscale: Some(1.0), image_yscale: Some(1.0),
            room_speed: Some(30.0), fps: Some(60.0), game_time_ms: Some(5000.0),
            sample_delta_x: Some(4.0), sample_delta_y: Some(0.0),
            sample_interval_ms: Some(33.3), interaction_state: Some(0),
            player_controlled: Some(true), visible: Some(true),
            player_facing_direction: Some("right".into()),
        }
    }

    #[test]
    fn width_and_masks_match_contract() {
        let encoded = encode_observation(&sample(), Some(Action::Right), Some(0.1));
        assert_eq!(encoded.values.len(), OBSERVATION_WIDTH);
        assert_eq!(encoded.available.len(), OBSERVATION_WIDTH);
        assert!(encoded.values.iter().all(|v| v.is_finite() && *v >= -1.0 && *v <= 1.0));
        assert!(encoded.available.iter().all(|v| *v == 0.0 || *v == 1.0));
    }

    #[test]
    fn missing_optional_fields_are_zero_and_unavailable() {
        let mut s = sample();
        s.hspeed = None; s.camera_x = None; s.player_controlled = None;
        let encoded = encode_observation(&s, None, None);
        for name in ["velocity.x", "camera.x", "state.player_controlled", "action.previous_duration"] {
            let index = FEATURES.iter().position(|candidate| *candidate == name).unwrap();
            assert_eq!(encoded.values[index], 0.0);
            assert_eq!(encoded.available[index], 0.0);
        }
    }

    #[test]
    fn action_encoding_matches_python_order() {
        let encoded = encode_observation(&sample(), Some(Action::Right), Some(0.25));
        let right = FEATURES.iter().position(|name| *name == "action.previous.right").unwrap();
        let left = FEATURES.iter().position(|name| *name == "action.previous.left").unwrap();
        assert_eq!(encoded.values[right], 1.0);
        assert_eq!(encoded.values[left], 0.0);
    }
}
