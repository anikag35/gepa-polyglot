use std::collections::HashMap;

pub struct Example {
    pub id: String,
    pub fields: HashMap<String, String>,
}

pub struct Candidate {
    inner: HashMap<String, String>,
}

impl Candidate {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.inner.get(key).map(|s| s.as_str())
    }

    pub fn as_map(&self) -> &HashMap<String, String> {
        &self.inner
    }

    pub(crate) fn from_map(map: HashMap<String, String>) -> Self {
        Self { inner: map }
    }
}

pub struct Trajectory {
    pub input_fields: HashMap<String, String>,
    pub output: String,
    pub feedback: String,
}

pub struct EvalRequest {
    pub request_id: String,
    pub candidate: Candidate,
    pub batch: Vec<Example>,
    pub capture_traces: bool,
}

pub struct EvalResult {
    pub outputs: Vec<String>,
    pub scores: Vec<f32>,
    pub trajectories: Option<Vec<Trajectory>>,
}

pub struct ReflectiveEntry {
    pub inputs: HashMap<String, String>,
    pub generated_output: String,
    pub feedback: String,
}

pub struct ReflectiveRequest {
    pub request_id: String,
    pub candidate: Candidate,
    pub components_to_update: Vec<String>,
    pub trajectories: Vec<Trajectory>,
}

pub type ReflectiveResult = HashMap<String, Vec<ReflectiveEntry>>;

pub struct ProgressUpdate {
    pub metric_calls_used: i32,
    pub max_metric_calls: i32,
    pub best_score: f32,
    pub best_candidate: Candidate,
}

pub struct OptimizeResult {
    pub run_id: String,
    pub best_candidate: Candidate,
    pub best_score: f32,
}

pub struct OptimizeOpts<E, M> {
    pub run_id: String,
    pub seed_candidate: HashMap<String, String>,
    pub trainset: Vec<Example>,
    pub valset: Option<Vec<Example>>,
    pub max_metric_calls: u32,
    pub evaluate: E,
    pub make_reflective_dataset: M,
    pub on_progress: Option<Box<dyn Fn(ProgressUpdate) + Send>>,
}
