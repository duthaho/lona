export type Run = {
  id: string;
  workflow_name: string;
  workflow_version?: number;
  workflow_fingerprint?: string;
  status: string;
  title?: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
  completed_at?: string;
  requested_provider?: string;
  requested_model?: string;
  usage?: Record<string, number>;
};

export type Task = {
  id: string;
  title: string;
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type Approval = {
  id: string;
  run_id: string;
  step_id: string;
  status: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  created_at?: string;
};

export type WorkflowNode = {
  type: string;
  next?: string;
  routes?: Record<string, string>;
  branches?: string[];
  prompt?: string;
  payload?: Record<string, unknown>;
};

export type Workflow = {
  name: string;
  version: number;
  fingerprint: string;
  definition: {
    name: string;
    description?: string;
    start: string;
    nodes: Record<string, WorkflowNode>;
  };
  created_at?: string;
};

export type Artifact = { path: string; sha256: string; size: number };
export type Step = { step_id: string; status: string; attempt: number; reason?: string; error?: string };
export type ExternalExecution = {
  external_run_id: string;
  provider: string;
  status: string;
  step_id: string;
  requested_provider?: string;
  requested_model?: string;
  reported_provider?: string;
  reported_model?: string;
  usage?: Record<string, number>;
  last_error?: string;
};

export type ModelOption = { id: string; featured?: boolean; pricing?: Record<string, number | string> };
export type ModelProvider = { provider: string; label: string; models: ModelOption[] };
export type ModelCatalog = {
  current: { provider: string; model: string };
  providers: ModelProvider[];
};

export type RunDetail = Run & {
  steps: Step[];
  approvals: Approval[];
  artifacts: Artifact[];
  external_executions?: ExternalExecution[];
};

export type Overview = {
  counts: Record<string, number>;
  runs: Run[];
  approvals: Approval[];
  workflows: Workflow[];
  tasks: Task[];
};
