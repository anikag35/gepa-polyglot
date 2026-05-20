use std::future::Future;

use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::transport::Channel;
use tonic::Request;

use crate::error::GEPAError;
use crate::generated::{
    client_message::Payload as ClientPayload, g_e_p_a_service_client::GEPAServiceClient,
    server_message::Payload as ServerPayload, ClientMessage, EvaluateBatchResponse,
    Example as ProtoExample, ReflectiveComponentData, ReflectiveDatasetResponse,
    ReflectiveEntry as ProtoEntry, StartRequest, Trajectory as ProtoTrajectory,
};
use crate::types::{
    Candidate, EvalRequest, EvalResult, Example, OptimizeOpts, OptimizeResult, ProgressUpdate,
    ReflectiveEntry, ReflectiveRequest, ReflectiveResult, Trajectory,
};

pub struct Client {
    target: String,
}

impl Client {
    pub fn new(target: impl Into<String>) -> Self {
        Self {
            target: target.into(),
        }
    }

    pub async fn optimize<E, M, EFut, MFut>(
        &self,
        mut opts: OptimizeOpts<E, M>,
    ) -> Result<OptimizeResult, GEPAError>
    where
        E: FnMut(EvalRequest) -> EFut,
        EFut: Future<Output = Result<EvalResult, GEPAError>> + Send,
        M: FnMut(ReflectiveRequest) -> MFut,
        MFut: Future<Output = Result<ReflectiveResult, GEPAError>> + Send,
    {
        let channel = Channel::from_shared(format!("http://{}", self.target))
            .map_err(|e| GEPAError::InvalidAddress(e.to_string()))?
            .connect()
            .await?;

        let mut grpc_client = GEPAServiceClient::new(channel);

        let (tx, rx) = mpsc::channel::<ClientMessage>(32);
        let stream = ReceiverStream::new(rx);

        tx.send(ClientMessage {
            payload: Some(ClientPayload::StartRequest(StartRequest {
                run_id: opts.run_id.clone(),
                seed_candidate: opts.seed_candidate.clone(),
                trainset: opts.trainset.iter().map(example_to_proto).collect(),
                valset: opts
                    .valset
                    .as_deref()
                    .unwrap_or(&[])
                    .iter()
                    .map(example_to_proto)
                    .collect(),
                reflection_lm: String::new(),
                max_metric_calls: opts.max_metric_calls as i32,
            })),
        })
        .await
        .map_err(|_| GEPAError::ChannelSend)?;

        let mut response = grpc_client
            .run_optimization(Request::new(stream))
            .await?
            .into_inner();

        loop {
            let msg = response.message().await?;
            match msg.and_then(|m| m.payload) {
                None => return Err(GEPAError::StreamClosed),
                Some(ServerPayload::EvaluateBatchRequest(req)) => {
                    let args = EvalRequest {
                        request_id: req.request_id.clone(),
                        candidate: Candidate::from_map(req.candidate.clone()),
                        batch: req.batch.iter().map(proto_to_example).collect(),
                        capture_traces: req.capture_traces,
                    };
                    let result = (opts.evaluate)(args).await?;
                    tx.send(ClientMessage {
                        payload: Some(ClientPayload::EvaluateBatchResponse(
                            EvaluateBatchResponse {
                                request_id: req.request_id,
                                outputs: result.outputs,
                                scores: result.scores,
                                trajectories: result
                                    .trajectories
                                    .unwrap_or_default()
                                    .into_iter()
                                    .map(traj_to_proto)
                                    .collect(),
                            },
                        )),
                    })
                    .await
                    .map_err(|_| GEPAError::ChannelSend)?;
                }
                Some(ServerPayload::ReflectiveDatasetRequest(req)) => {
                    let args = ReflectiveRequest {
                        request_id: req.request_id.clone(),
                        candidate: Candidate::from_map(req.candidate.clone()),
                        components_to_update: req.components_to_update.clone(),
                        trajectories: req.trajectories.iter().map(proto_to_traj).collect(),
                    };
                    let result = (opts.make_reflective_dataset)(args).await?;
                    let reflective_data = result
                        .into_iter()
                        .map(|(comp, entries)| {
                            let proto_entries = entries
                                .into_iter()
                                .map(|e| ProtoEntry {
                                    inputs: e.inputs,
                                    generated_output: e.generated_output,
                                    feedback: e.feedback,
                                })
                                .collect();
                            (comp, ReflectiveComponentData { entries: proto_entries })
                        })
                        .collect();
                    tx.send(ClientMessage {
                        payload: Some(ClientPayload::ReflectiveDatasetResponse(
                            ReflectiveDatasetResponse {
                                request_id: req.request_id,
                                reflective_data,
                            },
                        )),
                    })
                    .await
                    .map_err(|_| GEPAError::ChannelSend)?;
                }
                Some(ServerPayload::ProgressUpdate(u)) => {
                    if let Some(ref cb) = opts.on_progress {
                        cb(ProgressUpdate {
                            metric_calls_used: u.metric_calls_used,
                            max_metric_calls: u.max_metric_calls,
                            best_score: u.best_score,
                            best_candidate: Candidate::from_map(u.best_candidate),
                        });
                    }
                }
                Some(ServerPayload::OptimizationComplete(c)) => {
                    return Ok(OptimizeResult {
                        run_id: c.run_id,
                        best_candidate: Candidate::from_map(c.best_candidate),
                        best_score: c.best_score,
                    });
                }
                Some(ServerPayload::OptimizationError(e)) => {
                    return Err(GEPAError::OptimizationFailed(e.message));
                }
            }
        }
    }
}

fn example_to_proto(e: &Example) -> ProtoExample {
    ProtoExample {
        id: e.id.clone(),
        fields: e.fields.clone(),
    }
}

fn proto_to_example(e: &ProtoExample) -> Example {
    Example {
        id: e.id.clone(),
        fields: e.fields.clone(),
    }
}

fn traj_to_proto(t: Trajectory) -> ProtoTrajectory {
    ProtoTrajectory {
        input_fields: t.input_fields,
        output: t.output,
        feedback: t.feedback,
    }
}

fn proto_to_traj(t: &ProtoTrajectory) -> Trajectory {
    Trajectory {
        input_fields: t.input_fields.clone(),
        output: t.output.clone(),
        feedback: t.feedback.clone(),
    }
}
