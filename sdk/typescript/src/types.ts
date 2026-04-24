import type { ChannelCredentials } from "@grpc/grpc-js";

export interface Example {
  id: string;
  fields: Record<string, string>;
}

export interface Trajectory {
  inputFields: Record<string, string>;
  output: string;
  feedback: string;
}

export interface ReflectiveEntry {
  inputs: Record<string, string>;
  generatedOutput: string;
  feedback: string;
}

export interface EvaluateBatchArgs {
  requestId: string;
  candidate: Record<string, string>;
  batch: Example[];
  captureTraces: boolean;
}

export interface EvaluateBatchResult {
  outputs: string[];
  scores: number[];
  trajectories?: Trajectory[];
}

export interface ReflectiveDatasetArgs {
  requestId: string;
  candidate: Record<string, string>;
  componentsToUpdate: string[];
  trajectories: Trajectory[];
}

export type ReflectiveDatasetResult = Record<string, ReflectiveEntry[]>;

export interface ProgressUpdate {
  metricCallsUsed: number;
  maxMetricCalls: number;
  bestScore: number;
  bestCandidate: Record<string, string>;
}

export interface OptimizeOptions {
  runId: string;
  seedCandidate: Record<string, string>;
  trainset: Example[];
  valset?: Example[];
  reflectionLm?: string;
  maxMetricCalls: number;

  evaluate: (args: EvaluateBatchArgs) => Promise<EvaluateBatchResult>;
  makeReflectiveDataset: (
    args: ReflectiveDatasetArgs,
  ) => Promise<ReflectiveDatasetResult>;
  onProgress?: (update: ProgressUpdate) => void;
}

export interface OptimizeResult {
  runId: string;
  bestCandidate: Record<string, string>;
  bestScore: number;
}

export interface ClientOptions {
  target: string;
  credentials?: ChannelCredentials;
}
