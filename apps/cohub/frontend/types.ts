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

export type WorkflowNodeType = "task" | "decision" | "parallel" | "human" | "end";

export type WorkflowNode = {
  type: WorkflowNodeType;
  next?: string;
  routes?: Record<string, string>;
  branches?: string[];
  prompt?: string;
  payload?: Record<string, unknown>;
  max_attempts?: number;
  side_effect?: boolean;
  approval_payload?: Record<string, unknown>;
  output_schema?: Record<string, unknown>;
  local_result?: Record<string, unknown>;
};

export type WorkflowDefinition = {
  name: string;
  description?: string;
  start: string;
  defaults?: { max_attempts?: number };
  nodes: Record<string, WorkflowNode>;
};

export type Workflow = {
  name: string;
  version: number;
  fingerprint: string;
  definition: WorkflowDefinition;
  created_at?: string;
};

export type WorkflowDraft = {
  id: string;
  name: string;
  revision: number;
  status: "active" | "published";
  definition: WorkflowDefinition;
  layout: Record<string, { x: number; y: number }>;
  source_workflow_id?: string;
  published_workflow_id?: string;
  created_at: string;
  updated_at: string;
  published_at?: string;
};

export type DraftDiagnostic = { path: string; message: string };

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
