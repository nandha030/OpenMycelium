package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
)

const agentEventStream = "OPENMYCELIUM_AGENT_EVENTS"

type AgentPolicy struct {
	TokenBudget      int64    `json:"tokenBudget"`
	CostBudgetUSD    float64  `json:"costBudgetUSD"`
	TimeoutSeconds   int      `json:"timeoutSeconds"`
	MaxRetries       int      `json:"maxRetries"`
	RequireApproval  bool     `json:"requireApproval"`
	AllowedHosts     []string `json:"allowedHosts"`
	AllowCodeExecute bool     `json:"allowCodeExecute"`
}

type AgentMemory struct {
	Mode          string `json:"mode"`
	VectorStore   string `json:"vectorStore"`
	RetentionDays int    `json:"retentionDays"`
	Checkpointing bool   `json:"checkpointing"`
}

type AgentSpec struct {
	CPU           string      `json:"cpu"`
	MemoryRequest string      `json:"memoryRequest"`
	Accelerators  int         `json:"accelerators"`
	Pool          string      `json:"pool"`
	StorageGB     int         `json:"storageGB"`
	ServiceType   string      `json:"serviceType"`
	ToolIDs       []string    `json:"toolIds"`
	Policy        AgentPolicy `json:"policy"`
	Memory        AgentMemory `json:"memory"`
	Environment   []string    `json:"environment"`
}

type AgentDefinition struct {
	ID             string    `json:"id"`
	WorkspaceID    string    `json:"workspaceId,omitempty"`
	WorkspaceName  string    `json:"workspaceName,omitempty"`
	Name           string    `json:"name"`
	Description    string    `json:"description"`
	Framework      string    `json:"framework"`
	Image          string    `json:"image"`
	Command        string    `json:"command,omitempty"`
	Port           int32     `json:"port"`
	ModelVersionID string    `json:"modelVersionId,omitempty"`
	ModelName      string    `json:"modelName,omitempty"`
	Status         string    `json:"status"`
	Version        int       `json:"version"`
	Spec           AgentSpec `json:"spec"`
	CreatedBy      string    `json:"createdBy"`
	CreatedAt      time.Time `json:"createdAt"`
	UpdatedAt      time.Time `json:"updatedAt"`
}

type AgentFlowNode struct {
	ID     string         `json:"id"`
	Type   string         `json:"type"`
	Name   string         `json:"name"`
	RefID  string         `json:"refId,omitempty"`
	Config map[string]any `json:"config,omitempty"`
}

type AgentFlowEdge struct {
	From      string `json:"from"`
	To        string `json:"to"`
	Condition string `json:"condition,omitempty"`
}

type AgentFlowGraph struct {
	Nodes []AgentFlowNode `json:"nodes"`
	Edges []AgentFlowEdge `json:"edges"`
}

type AgentFlow struct {
	ID            string         `json:"id"`
	WorkspaceID   string         `json:"workspaceId"`
	WorkspaceName string         `json:"workspaceName,omitempty"`
	Name          string         `json:"name"`
	Description   string         `json:"description"`
	Version       int            `json:"version"`
	Status        string         `json:"status"`
	Graph         AgentFlowGraph `json:"graph"`
	CreatedBy     string         `json:"createdBy"`
	CreatedAt     time.Time      `json:"createdAt"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}

type AgentRun struct {
	ID            string         `json:"id"`
	WorkspaceID   string         `json:"workspaceId"`
	WorkspaceName string         `json:"workspaceName,omitempty"`
	AgentID       string         `json:"agentId"`
	AgentName     string         `json:"agentName,omitempty"`
	FlowID        string         `json:"flowId,omitempty"`
	FlowName      string         `json:"flowName,omitempty"`
	WorkloadID    string         `json:"workloadId,omitempty"`
	Status        string         `json:"status"`
	CurrentStep   string         `json:"currentStep,omitempty"`
	Input         map[string]any `json:"input"`
	Output        map[string]any `json:"output,omitempty"`
	TokenBudget   int64          `json:"tokenBudget"`
	TokensUsed    int64          `json:"tokensUsed"`
	CostUSD       float64        `json:"costUSD"`
	Error         string         `json:"error,omitempty"`
	CreatedBy     string         `json:"createdBy"`
	CreatedAt     time.Time      `json:"createdAt"`
	StartedAt     *time.Time     `json:"startedAt,omitempty"`
	FinishedAt    *time.Time     `json:"finishedAt,omitempty"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}

type AgentRunEvent struct {
	ID        int64          `json:"id"`
	RunID     string         `json:"runId"`
	EventType string         `json:"eventType"`
	StepID    string         `json:"stepId,omitempty"`
	Payload   map[string]any `json:"payload"`
	CreatedAt time.Time      `json:"createdAt"`
}

type AgentToolBinding struct {
	ID              string    `json:"id"`
	WorkspaceID     string    `json:"workspaceId"`
	AgentID         string    `json:"agentId"`
	AgentName       string    `json:"agentName,omitempty"`
	IntegrationID   string    `json:"integrationId"`
	IntegrationName string    `json:"integrationName,omitempty"`
	Permissions     []string  `json:"permissions"`
	Approved        bool      `json:"approved"`
	CreatedBy       string    `json:"createdBy"`
	CreatedAt       time.Time `json:"createdAt"`
}

type AgentMemoryProfile struct {
	ID            string    `json:"id"`
	WorkspaceID   string    `json:"workspaceId"`
	Name          string    `json:"name"`
	Mode          string    `json:"mode"`
	Provider      string    `json:"provider"`
	RetentionDays int       `json:"retentionDays"`
	Checkpointing bool      `json:"checkpointing"`
	Status        string    `json:"status"`
	CreatedBy     string    `json:"createdBy"`
	CreatedAt     time.Time `json:"createdAt"`
}

type AgentApproval struct {
	ID          string     `json:"id"`
	WorkspaceID string     `json:"workspaceId"`
	RunID       string     `json:"runId"`
	StepID      string     `json:"stepId"`
	Reason      string     `json:"reason"`
	Status      string     `json:"status"`
	RequestedBy string     `json:"requestedBy"`
	ResolvedBy  string     `json:"resolvedBy,omitempty"`
	CreatedAt   time.Time  `json:"createdAt"`
	ResolvedAt  *time.Time `json:"resolvedAt,omitempty"`
}

type AgentEvaluation struct {
	ID          string         `json:"id"`
	WorkspaceID string         `json:"workspaceId"`
	AgentID     string         `json:"agentId"`
	AgentName   string         `json:"agentName,omitempty"`
	RunID       string         `json:"runId,omitempty"`
	Suite       string         `json:"suite"`
	Status      string         `json:"status"`
	Score       float64        `json:"score"`
	Metrics     map[string]any `json:"metrics"`
	CreatedBy   string         `json:"createdBy"`
	CreatedAt   time.Time      `json:"createdAt"`
}

type agentWorkspaceSnapshot struct {
	Summary     map[string]any       `json:"summary"`
	Agents      []AgentDefinition    `json:"agents"`
	Flows       []AgentFlow          `json:"flows"`
	Runs        []AgentRun           `json:"runs"`
	Tools       []AgentToolBinding   `json:"tools"`
	Memories    []AgentMemoryProfile `json:"memories"`
	Approvals   []AgentApproval      `json:"approvals"`
	Evaluations []AgentEvaluation    `json:"evaluations"`
	Traces      []AgentRunEvent      `json:"traces"`
	Protocols   map[string]any       `json:"protocols"`
	UpdatedAt   time.Time            `json:"updatedAt"`
}

var agentSchema = []string{
	`CREATE TABLE IF NOT EXISTS agent_definitions (id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', framework TEXT NOT NULL, image TEXT NOT NULL, command TEXT NOT NULL DEFAULT '', port INTEGER NOT NULL DEFAULT 8000, model_version_id TEXT REFERENCES model_versions(id) ON DELETE SET NULL, status TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1, spec JSONB NOT NULL DEFAULT '{}'::jsonb, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,name,version))`,
	`CREATE INDEX IF NOT EXISTS agent_definitions_workspace_idx ON agent_definitions(workspace_id,updated_at DESC)`,
	`CREATE TABLE IF NOT EXISTS agent_flows (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'draft', graph JSONB NOT NULL, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,name,version))`,
	`CREATE INDEX IF NOT EXISTS agent_flows_workspace_idx ON agent_flows(workspace_id,updated_at DESC)`,
	`CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, agent_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE RESTRICT, flow_id TEXT REFERENCES agent_flows(id) ON DELETE SET NULL, workload_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, current_step TEXT NOT NULL DEFAULT '', input JSONB NOT NULL DEFAULT '{}'::jsonb, output JSONB NOT NULL DEFAULT '{}'::jsonb, token_budget BIGINT NOT NULL DEFAULT 0, tokens_used BIGINT NOT NULL DEFAULT 0, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS agent_runs_workspace_idx ON agent_runs(workspace_id,created_at DESC)`,
	`CREATE INDEX IF NOT EXISTS agent_runs_agent_idx ON agent_runs(agent_id,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS agent_run_events (id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE, event_type TEXT NOT NULL, step_id TEXT NOT NULL DEFAULT '', payload JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS agent_run_events_run_idx ON agent_run_events(run_id,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS agent_tool_bindings (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, agent_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE, integration_id TEXT NOT NULL REFERENCES integrations(id) ON DELETE CASCADE, permissions JSONB NOT NULL DEFAULT '[]'::jsonb, approved BOOLEAN NOT NULL DEFAULT false, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(agent_id,integration_id))`,
	`CREATE TABLE IF NOT EXISTS agent_memory_profiles (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, name TEXT NOT NULL, mode TEXT NOT NULL, provider TEXT NOT NULL, retention_days INTEGER NOT NULL DEFAULT 30, checkpointing BOOLEAN NOT NULL DEFAULT true, status TEXT NOT NULL DEFAULT 'ready', created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,name))`,
	`CREATE TABLE IF NOT EXISTS agent_approvals (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE, step_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', requested_by TEXT NOT NULL, resolved_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), resolved_at TIMESTAMPTZ)`,
	`CREATE INDEX IF NOT EXISTS agent_approvals_workspace_idx ON agent_approvals(workspace_id,status,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS agent_evaluations (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, agent_id TEXT NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE, run_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL, suite TEXT NOT NULL, status TEXT NOT NULL, score DOUBLE PRECISION NOT NULL DEFAULT 0, metrics JSONB NOT NULL DEFAULT '{}'::jsonb, created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS agent_evaluations_workspace_idx ON agent_evaluations(workspace_id,created_at DESC)`,
}

func initializeAgentEventStream() {
	if eventBus == nil {
		return
	}
	js, err := eventBus.JetStream()
	if err != nil {
		return
	}
	if _, err = js.StreamInfo(agentEventStream); err == nil {
		return
	}
	_, _ = js.AddStream(&nats.StreamConfig{Name: agentEventStream, Subjects: []string{"openmycelium.agent.>"}, Storage: nats.FileStorage, Retention: nats.LimitsPolicy, MaxAge: 30 * 24 * time.Hour, Replicas: 1})
}

func defaultAgentSpec(spec AgentSpec) AgentSpec {
	if spec.CPU == "" {
		spec.CPU = "1"
	}
	if spec.MemoryRequest == "" {
		spec.MemoryRequest = "2Gi"
	}
	if spec.Pool == "" {
		spec.Pool = "cpu-local"
	}
	if spec.ServiceType == "" {
		spec.ServiceType = "ClusterIP"
	}
	if spec.Policy.TokenBudget == 0 {
		spec.Policy.TokenBudget = 100000
	}
	if spec.Policy.TimeoutSeconds == 0 {
		spec.Policy.TimeoutSeconds = 900
	}
	if spec.Policy.MaxRetries == 0 {
		spec.Policy.MaxRetries = 2
	}
	if spec.Memory.Mode == "" {
		spec.Memory.Mode = "run"
	}
	if spec.Memory.RetentionDays == 0 {
		spec.Memory.RetentionDays = 30
	}
	return spec
}

func validateAgentFlow(graph AgentFlowGraph) error {
	if len(graph.Nodes) == 0 {
		return errors.New("flow requires at least one node")
	}
	allowed := map[string]bool{"agent": true, "tool": true, "condition": true, "approval": true, "input": true, "output": true}
	nodes := map[string]bool{}
	for _, node := range graph.Nodes {
		if strings.TrimSpace(node.ID) == "" || nodes[node.ID] {
			return errors.New("flow node IDs must be present and unique")
		}
		if !allowed[node.Type] {
			return fmt.Errorf("unsupported flow node type %q", node.Type)
		}
		nodes[node.ID] = true
	}
	for _, edge := range graph.Edges {
		if !nodes[edge.From] || !nodes[edge.To] {
			return errors.New("every flow edge must reference existing nodes")
		}
		if edge.From == edge.To {
			return errors.New("a flow node cannot connect to itself")
		}
	}
	return nil
}

func scanAgent(row interface{ Scan(...any) error }) (AgentDefinition, error) {
	var item AgentDefinition
	var spec []byte
	err := row.Scan(&item.ID, &item.WorkspaceID, &item.WorkspaceName, &item.Name, &item.Description, &item.Framework, &item.Image, &item.Command, &item.Port, &item.ModelVersionID, &item.ModelName, &item.Status, &item.Version, &spec, &item.CreatedBy, &item.CreatedAt, &item.UpdatedAt)
	if err == nil {
		_ = json.Unmarshal(spec, &item.Spec)
		item.Spec = defaultAgentSpec(item.Spec)
	}
	return item, err
}

const agentSelect = `SELECT a.id,COALESCE(a.workspace_id,''),COALESCE(w.name,''),a.name,a.description,a.framework,a.image,a.command,a.port,COALESCE(a.model_version_id,''),COALESCE(m.name,''),a.status,a.version,a.spec,a.created_by,a.created_at,a.updated_at FROM agent_definitions a LEFT JOIN workspaces w ON w.id=a.workspace_id LEFT JOIN model_versions v ON v.id=a.model_version_id LEFT JOIN model_artifacts m ON m.id=v.model_id`

func loadAgent(ctx context.Context, id string) (AgentDefinition, error) {
	if database == nil {
		return AgentDefinition{}, errors.New("PostgreSQL is required")
	}
	return scanAgent(database.QueryRow(ctx, agentSelect+` WHERE a.id=$1`, id))
}

func listAgents(ctx context.Context, workspaceID string) []AgentDefinition {
	items := []AgentDefinition{}
	query, args := agentSelect+` ORDER BY a.updated_at DESC`, []any{}
	if workspaceID != "" {
		query, args = agentSelect+` WHERE a.workspace_id=$1 OR a.workspace_id IS NULL ORDER BY a.workspace_id NULLS LAST,a.updated_at DESC`, []any{workspaceID}
	}
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		if item, scanErr := scanAgent(rows); scanErr == nil {
			items = append(items, item)
		}
	}
	return items
}

func agentsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"agents": listAgents(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input AgentDefinition
	if decode(r, &input) != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid agent definition"})
		return
	}
	input.Name, input.Description, input.Framework = strings.TrimSpace(input.Name), strings.TrimSpace(input.Description), strings.ToLower(strings.TrimSpace(input.Framework))
	input.Image, input.Command, input.WorkspaceID, input.ModelVersionID = strings.TrimSpace(input.Image), strings.TrimSpace(input.Command), strings.TrimSpace(input.WorkspaceID), strings.TrimSpace(input.ModelVersionID)
	if input.Name == "" || input.Framework == "" || input.Image == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "name, framework, and a registry-accessible OCI image are required"})
		return
	}
	if input.Port <= 0 || input.Port > 65535 {
		input.Port = 8000
	}
	if input.WorkspaceID != "" {
		if _, err := loadWorkspace(r.Context(), input.WorkspaceID); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "selected workspace does not exist"})
			return
		}
	}
	if input.ModelVersionID != "" {
		if _, _, err := loadModelVersion(r.Context(), input.ModelVersionID); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "selected model version does not exist"})
			return
		}
	}
	input.ID, input.Version, input.Status = "agt_"+randomToken(9), max(1, input.Version), "ready"
	input.Spec, input.CreatedBy = defaultAgentSpec(input.Spec), requestActor(r)
	input.CreatedAt, input.UpdatedAt = time.Now().UTC(), time.Now().UTC()
	spec, _ := json.Marshal(input.Spec)
	var workspace any
	if input.WorkspaceID != "" {
		workspace = input.WorkspaceID
	}
	_, err := database.Exec(r.Context(), `INSERT INTO agent_definitions(id,workspace_id,name,description,framework,image,command,port,model_version_id,status,version,spec,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,NULLIF($9,''),$10,$11,$12,$13,$14,$15)`, input.ID, workspace, input.Name, input.Description, input.Framework, input.Image, input.Command, input.Port, input.ModelVersionID, input.Status, input.Version, spec, input.CreatedBy, input.CreatedAt, input.UpdatedAt)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "an agent with this name and version already exists in the selected scope"})
		return
	}
	item, _ := loadAgent(r.Context(), input.ID)
	auditRequest(r, "agent.created", map[string]any{"agent_id": input.ID, "workspace_id": input.WorkspaceID, "framework": input.Framework})
	publishEvent("agent.definition.created", item)
	writeJSON(w, http.StatusCreated, item)
}

func agentResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if agentA2ACardHandler(w, r) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/agents/"), "/")
	item, err := loadAgent(r.Context(), id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "agent not found"})
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, item)
		return
	}
	if r.Method == http.MethodDelete {
		result, deleteErr := database.Exec(r.Context(), `DELETE FROM agent_definitions WHERE id=$1`, id)
		if deleteErr != nil {
			writeJSON(w, http.StatusConflict, map[string]string{"error": "agent has run history; retire it instead of deleting it"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "agent not found"})
			return
		}
		auditRequest(r, "agent.deleted", map[string]any{"agent_id": id, "name": item.Name})
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
}

func scanAgentFlow(row interface{ Scan(...any) error }) (AgentFlow, error) {
	var item AgentFlow
	var graph []byte
	err := row.Scan(&item.ID, &item.WorkspaceID, &item.WorkspaceName, &item.Name, &item.Description, &item.Version, &item.Status, &graph, &item.CreatedBy, &item.CreatedAt, &item.UpdatedAt)
	if err == nil {
		_ = json.Unmarshal(graph, &item.Graph)
	}
	return item, err
}

func listAgentFlows(ctx context.Context, workspaceID string) []AgentFlow {
	items := []AgentFlow{}
	query := `SELECT f.id,f.workspace_id,w.name,f.name,f.description,f.version,f.status,f.graph,f.created_by,f.created_at,f.updated_at FROM agent_flows f JOIN workspaces w ON w.id=f.workspace_id`
	args := []any{}
	if workspaceID != "" {
		query += ` WHERE f.workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY f.updated_at DESC`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		if item, scanErr := scanAgentFlow(rows); scanErr == nil {
			items = append(items, item)
		}
	}
	return items
}

func agentFlowsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"flows": listAgentFlows(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input AgentFlow
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.WorkspaceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace, flow name, and graph are required"})
		return
	}
	if _, err := loadWorkspace(r.Context(), input.WorkspaceID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "selected workspace does not exist"})
		return
	}
	if err := validateAgentFlow(input.Graph); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	input.ID, input.Version, input.Status = "flow_"+randomToken(9), max(1, input.Version), "ready"
	input.Name, input.Description, input.CreatedBy = strings.TrimSpace(input.Name), strings.TrimSpace(input.Description), requestActor(r)
	input.CreatedAt, input.UpdatedAt = time.Now().UTC(), time.Now().UTC()
	graph, _ := json.Marshal(input.Graph)
	_, err := database.Exec(r.Context(), `INSERT INTO agent_flows(id,workspace_id,name,description,version,status,graph,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, input.ID, input.WorkspaceID, input.Name, input.Description, input.Version, input.Status, graph, input.CreatedBy, input.CreatedAt, input.UpdatedAt)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "a flow with this name and version already exists"})
		return
	}
	auditRequest(r, "agent.flow.created", map[string]any{"flow_id": input.ID, "workspace_id": input.WorkspaceID})
	publishEvent("agent.flow.created", input)
	writeJSON(w, http.StatusCreated, input)
}

func agentFlowResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/agent-flows/"), "/")
	if r.Method != http.MethodDelete {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	result, err := database.Exec(r.Context(), `DELETE FROM agent_flows WHERE id=$1`, id)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "flow has run history and cannot be deleted"})
		return
	}
	if result.RowsAffected() == 0 {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "flow not found"})
		return
	}
	auditRequest(r, "agent.flow.deleted", map[string]any{"flow_id": id})
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

func scanAgentRun(row interface{ Scan(...any) error }) (AgentRun, error) {
	var item AgentRun
	var input, output []byte
	err := row.Scan(&item.ID, &item.WorkspaceID, &item.WorkspaceName, &item.AgentID, &item.AgentName, &item.FlowID, &item.FlowName, &item.WorkloadID, &item.Status, &item.CurrentStep, &input, &output, &item.TokenBudget, &item.TokensUsed, &item.CostUSD, &item.Error, &item.CreatedBy, &item.CreatedAt, &item.StartedAt, &item.FinishedAt, &item.UpdatedAt)
	if err == nil {
		_ = json.Unmarshal(input, &item.Input)
		_ = json.Unmarshal(output, &item.Output)
	}
	return item, err
}

const agentRunSelect = `SELECT r.id,r.workspace_id,w.name,r.agent_id,a.name,COALESCE(r.flow_id,''),COALESCE(f.name,''),r.workload_id,r.status,r.current_step,r.input,r.output,r.token_budget,r.tokens_used,r.cost_usd,r.error,r.created_by,r.created_at,r.started_at,r.finished_at,r.updated_at FROM agent_runs r JOIN workspaces w ON w.id=r.workspace_id JOIN agent_definitions a ON a.id=r.agent_id LEFT JOIN agent_flows f ON f.id=r.flow_id`

func loadAgentRun(ctx context.Context, id string) (AgentRun, error) {
	return scanAgentRun(database.QueryRow(ctx, agentRunSelect+` WHERE r.id=$1`, id))
}

func listAgentRuns(ctx context.Context, workspaceID string) []AgentRun {
	items := []AgentRun{}
	query, args := agentRunSelect, []any{}
	if workspaceID != "" {
		query += ` WHERE r.workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY r.created_at DESC LIMIT 250`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		if item, scanErr := scanAgentRun(rows); scanErr == nil {
			items = append(items, item)
		}
	}
	return items
}

func publishAgentRunEvent(ctx context.Context, runID, eventType, stepID string, payload map[string]any) {
	if database == nil {
		return
	}
	if payload == nil {
		payload = map[string]any{}
	}
	encoded, _ := json.Marshal(payload)
	_, _ = database.Exec(ctx, `INSERT INTO agent_run_events(run_id,event_type,step_id,payload) VALUES($1,$2,$3,$4)`, runID, eventType, stepID, encoded)
	publishEvent("agent.run."+runID+"."+strings.ReplaceAll(eventType, "_", "."), map[string]any{"runId": runID, "eventType": eventType, "stepId": stepID, "payload": payload, "timestamp": time.Now().UTC()})
}

func deployAgentRun(ctx context.Context, run AgentRun, agent AgentDefinition) (Workload, error) {
	workspace, err := loadWorkspace(ctx, run.WorkspaceID)
	if err != nil {
		return Workload{}, err
	}
	now := time.Now().UTC()
	name := agent.Name + "-" + strings.ToLower(strings.TrimPrefix(run.ID, "run_"))
	if len(name) > 80 {
		name = name[:80]
	}
	item := Workload{ID: "job-" + fmt.Sprint(time.Now().UnixNano()), AgentID: agent.ID, AgentRunID: run.ID, WorkspaceID: workspace.ID, ReleaseID: "rel_" + randomToken(9), Name: name, Kind: "agent", Runtime: "kubernetes", Pool: agent.Spec.Pool, Image: agent.Image, Accelerators: agent.Spec.Accelerators, Status: "queued", ClusterID: workspace.ClusterID, Namespace: workspace.Namespace, ModelVersionID: agent.ModelVersionID, Command: agent.Command, CPU: agent.Spec.CPU, Memory: agent.Spec.MemoryRequest, StorageGB: agent.Spec.StorageGB, StorageClass: workspace.StorageClass, ServiceType: agent.Spec.ServiceType, Port: agent.Port, DesiredCount: 1, CreatedAt: now, UpdatedAt: now}
	specJSON, _ := json.Marshal(agent.Spec)
	item.AgentSpecJSON = string(specJSON)
	tools := []AgentToolBinding{}
	for _, binding := range listAgentTools(ctx, workspace.ID) {
		if binding.AgentID == agent.ID && binding.Approved {
			tools = append(tools, binding)
		}
	}
	toolsJSON, _ := json.Marshal(tools)
	item.AgentToolsJSON = string(toolsJSON)
	if run.FlowID != "" {
		for _, flow := range listAgentFlows(ctx, workspace.ID) {
			if flow.ID == run.FlowID {
				flowJSON, _ := json.Marshal(flow)
				item.AgentFlowJSON = string(flowJSON)
				break
			}
		}
	}
	if item.ModelVersionID != "" {
		model, version, modelErr := loadModelVersion(ctx, item.ModelVersionID)
		if modelErr != nil {
			return Workload{}, modelErr
		}
		item.Model, item.ModelSourceURI, item.ModelRuntime = model.Name, version.SourceURI, version.Runtime
	}
	item.ResourceName, item.ServiceName = workloadResourceName(item.ID, item.Name), ""
	item.ServiceName = item.ResourceName
	if item.StorageGB > 0 {
		item.PVCName = item.ResourceName + "-models"
	}
	if err = deployKubernetesWorkload(ctx, &item); err != nil {
		return Workload{}, err
	}
	if err = workspaceRelease(ctx, item.WorkspaceID, item.ReleaseID, item.Name, "agent-run", agent.ID, item.Status, map[string]any{"agentId": agent.ID, "runId": run.ID, "workload": item}); err != nil {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		_ = deleteKubernetesWorkload(cleanup, item)
		cancel()
		return Workload{}, err
	}
	state.Lock()
	state.Workloads = append(state.Workloads, item)
	persistStateLocked()
	state.Unlock()
	return item, nil
}

func launchAgentRun(ctx context.Context, run AgentRun, actor string) (AgentRun, error) {
	agent, err := loadAgent(ctx, run.AgentID)
	if err != nil {
		return run, err
	}
	publishAgentRunEvent(ctx, run.ID, "scheduling", "scheduler", map[string]any{"clusterScope": run.WorkspaceID, "image": agent.Image})
	workload, err := deployAgentRun(ctx, run, agent)
	if err != nil {
		_, _ = database.Exec(ctx, `UPDATE agent_runs SET status='failed',error=$2,finished_at=now(),updated_at=now() WHERE id=$1`, run.ID, err.Error())
		publishAgentRunEvent(ctx, run.ID, "failed", "scheduler", map[string]any{"error": err.Error()})
		return run, err
	}
	_, err = database.Exec(ctx, `UPDATE agent_runs SET workload_id=$2,status='deploying',current_step='runtime',started_at=now(),updated_at=now() WHERE id=$1`, run.ID, workload.ID)
	if err != nil {
		return run, err
	}
	publishAgentRunEvent(ctx, run.ID, "deployed", "runtime", map[string]any{"workloadId": workload.ID, "releaseId": workload.ReleaseID, "resourceName": workload.ResourceName, "actor": actor})
	return loadAgentRun(ctx, run.ID)
}

func reconcileAgentRuns(ctx context.Context) {
	if database == nil {
		return
	}
	reconcileAllKubernetesWorkloads(ctx)
	state.RLock()
	workloads := make(map[string]Workload, len(state.Workloads))
	for _, item := range state.Workloads {
		workloads[item.ID] = item
	}
	state.RUnlock()
	for _, run := range listAgentRuns(ctx, "") {
		if run.WorkloadID == "" || run.Status == "completed" || run.Status == "failed" || run.Status == "cancelled" {
			continue
		}
		workload, ok := workloads[run.WorkloadID]
		if !ok {
			continue
		}
		status := run.Status
		switch strings.ToLower(workload.Status) {
		case "running", "ready":
			status = "running"
		case "failed":
			status = "failed"
		case "stopped":
			status = "cancelled"
		default:
			status = "deploying"
		}
		if status == run.Status {
			continue
		}
		_, _ = database.Exec(ctx, `UPDATE agent_runs SET status=$2,error=$3,finished_at=CASE WHEN $2 IN ('failed','cancelled') THEN now() ELSE finished_at END,updated_at=now() WHERE id=$1`, run.ID, status, workload.LastError)
		publishAgentRunEvent(ctx, run.ID, "runtime_"+status, "runtime", map[string]any{"workloadStatus": workload.Status, "pod": workload.PodName, "node": workload.NodeName, "restarts": workload.Restarts})
	}
}

func agentRunsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		reconcileAgentRuns(r.Context())
		writeJSON(w, http.StatusOK, map[string]any{"runs": listAgentRuns(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct {
		WorkspaceID string         `json:"workspaceId"`
		AgentID     string         `json:"agentId"`
		FlowID      string         `json:"flowId"`
		Input       map[string]any `json:"input"`
		TokenBudget int64          `json:"tokenBudget"`
	}
	if decode(r, &input) != nil || strings.TrimSpace(input.WorkspaceID) == "" || strings.TrimSpace(input.AgentID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace and agent are required"})
		return
	}
	agent, err := loadAgent(r.Context(), input.AgentID)
	if err != nil || agent.WorkspaceID != "" && agent.WorkspaceID != input.WorkspaceID {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent is not available in this workspace"})
		return
	}
	if _, err = loadWorkspace(r.Context(), input.WorkspaceID); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace does not exist"})
		return
	}
	if input.FlowID != "" {
		var flowWorkspace string
		if database.QueryRow(r.Context(), `SELECT workspace_id FROM agent_flows WHERE id=$1`, input.FlowID).Scan(&flowWorkspace) != nil || flowWorkspace != input.WorkspaceID {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "flow is not available in this workspace"})
			return
		}
	}
	if input.Input == nil {
		input.Input = map[string]any{}
	}
	if input.TokenBudget <= 0 {
		input.TokenBudget = agent.Spec.Policy.TokenBudget
	}
	now := time.Now().UTC()
	run := AgentRun{ID: "run_" + randomToken(10), WorkspaceID: input.WorkspaceID, AgentID: input.AgentID, FlowID: input.FlowID, Status: "accepted", CurrentStep: "admission", Input: input.Input, Output: map[string]any{}, TokenBudget: input.TokenBudget, CreatedBy: requestActor(r), CreatedAt: now, UpdatedAt: now}
	encodedInput, _ := json.Marshal(run.Input)
	_, err = database.Exec(r.Context(), `INSERT INTO agent_runs(id,workspace_id,agent_id,flow_id,status,current_step,input,token_budget,created_by,created_at,updated_at) VALUES($1,$2,$3,NULLIF($4,''),$5,$6,$7,$8,$9,$10,$11)`, run.ID, run.WorkspaceID, run.AgentID, run.FlowID, run.Status, run.CurrentStep, encodedInput, run.TokenBudget, run.CreatedBy, run.CreatedAt, run.UpdatedAt)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "agent run could not be recorded"})
		return
	}
	publishAgentRunEvent(r.Context(), run.ID, "accepted", "admission", map[string]any{"agentId": agent.ID, "flowId": input.FlowID, "tokenBudget": input.TokenBudget})
	if agent.Spec.Policy.RequireApproval {
		approval := AgentApproval{ID: "apr_" + randomToken(9), WorkspaceID: run.WorkspaceID, RunID: run.ID, StepID: "admission", Reason: "Agent policy requires operator approval before Kubernetes deployment.", Status: "pending", RequestedBy: run.CreatedBy, CreatedAt: now}
		_, _ = database.Exec(r.Context(), `INSERT INTO agent_approvals(id,workspace_id,run_id,step_id,reason,status,requested_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, approval.ID, approval.WorkspaceID, approval.RunID, approval.StepID, approval.Reason, approval.Status, approval.RequestedBy, approval.CreatedAt)
		_, _ = database.Exec(r.Context(), `UPDATE agent_runs SET status='waiting_approval',current_step='approval',updated_at=now() WHERE id=$1`, run.ID)
		publishAgentRunEvent(r.Context(), run.ID, "approval_requested", "approval", map[string]any{"approvalId": approval.ID, "reason": approval.Reason})
		run, _ = loadAgentRun(r.Context(), run.ID)
	} else {
		run, err = launchAgentRun(r.Context(), run, requestActor(r))
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error(), "runId": run.ID})
			return
		}
	}
	auditRequest(r, "agent.run.created", map[string]any{"run_id": run.ID, "agent_id": run.AgentID, "workspace_id": run.WorkspaceID, "status": run.Status})
	writeJSON(w, http.StatusCreated, run)
}

func listAgentRunEvents(ctx context.Context, runID, workspaceID string, limit int) []AgentRunEvent {
	items := []AgentRunEvent{}
	if limit <= 0 || limit > 1000 {
		limit = 250
	}
	query := `SELECT e.id,e.run_id,e.event_type,e.step_id,e.payload,e.created_at FROM agent_run_events e JOIN agent_runs r ON r.id=e.run_id`
	args := []any{}
	if runID != "" {
		query += ` WHERE e.run_id=$1`
		args = append(args, runID)
	} else if workspaceID != "" {
		query += ` WHERE r.workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY e.created_at DESC LIMIT ` + fmt.Sprint(limit)
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item AgentRunEvent
		var payload []byte
		if rows.Scan(&item.ID, &item.RunID, &item.EventType, &item.StepID, &payload, &item.CreatedAt) == nil {
			_ = json.Unmarshal(payload, &item.Payload)
			items = append(items, item)
		}
	}
	return items
}

func agentRunResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/agent-runs/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "agent run not found"})
		return
	}
	run, err := loadAgentRun(r.Context(), parts[0])
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "agent run not found"})
		return
	}
	if len(parts) == 1 && r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"run": run, "events": listAgentRunEvents(r.Context(), run.ID, "", 500)})
		return
	}
	if len(parts) == 2 && parts[1] == "events" && r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"events": listAgentRunEvents(r.Context(), run.ID, "", 500)})
		return
	}
	if len(parts) == 2 && parts[1] == "action" && r.Method == http.MethodPost {
		var input struct {
			Action string `json:"action"`
		}
		if decode(r, &input) != nil || input.Action != "cancel" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "supported action: cancel"})
			return
		}
		if run.WorkloadID != "" {
			state.RLock()
			var target Workload
			for _, item := range state.Workloads {
				if item.ID == run.WorkloadID {
					target = item
					break
				}
			}
			state.RUnlock()
			if target.ID != "" {
				ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
				err = deleteKubernetesWorkload(ctx, target)
				cancel()
				if err != nil {
					writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
					return
				}
				state.Lock()
				for index, item := range state.Workloads {
					if item.ID == run.WorkloadID {
						state.Workloads = append(state.Workloads[:index], state.Workloads[index+1:]...)
						break
					}
				}
				persistStateLocked()
				state.Unlock()
			}
		}
		_, _ = database.Exec(r.Context(), `UPDATE agent_runs SET status='cancelled',current_step='finished',finished_at=now(),updated_at=now() WHERE id=$1`, run.ID)
		publishAgentRunEvent(r.Context(), run.ID, "cancelled", "runtime", map[string]any{"actor": requestActor(r)})
		auditRequest(r, "agent.run.cancelled", map[string]any{"run_id": run.ID})
		run, _ = loadAgentRun(r.Context(), run.ID)
		writeJSON(w, http.StatusOK, run)
		return
	}
	writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
}

func listAgentTools(ctx context.Context, workspaceID string) []AgentToolBinding {
	items := []AgentToolBinding{}
	query := `SELECT b.id,b.workspace_id,b.agent_id,a.name,b.integration_id,i.name,b.permissions,b.approved,b.created_by,b.created_at FROM agent_tool_bindings b JOIN agent_definitions a ON a.id=b.agent_id JOIN integrations i ON i.id=b.integration_id`
	args := []any{}
	if workspaceID != "" {
		query += ` WHERE b.workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY b.created_at DESC`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item AgentToolBinding
		var permissions []byte
		if rows.Scan(&item.ID, &item.WorkspaceID, &item.AgentID, &item.AgentName, &item.IntegrationID, &item.IntegrationName, &permissions, &item.Approved, &item.CreatedBy, &item.CreatedAt) == nil {
			_ = json.Unmarshal(permissions, &item.Permissions)
			items = append(items, item)
		}
	}
	return items
}

func agentToolsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"tools": listAgentTools(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input AgentToolBinding
	if decode(r, &input) != nil || input.WorkspaceID == "" || input.AgentID == "" || input.IntegrationID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace, agent, and integration are required"})
		return
	}
	agent, err := loadAgent(r.Context(), input.AgentID)
	if err != nil || agent.WorkspaceID != "" && agent.WorkspaceID != input.WorkspaceID {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent is not available in this workspace"})
		return
	}
	input.ID, input.CreatedBy, input.CreatedAt = "tool_"+randomToken(9), requestActor(r), time.Now().UTC()
	permissions, _ := json.Marshal(input.Permissions)
	_, err = database.Exec(r.Context(), `INSERT INTO agent_tool_bindings(id,workspace_id,agent_id,integration_id,permissions,approved,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, input.ID, input.WorkspaceID, input.AgentID, input.IntegrationID, permissions, input.Approved, input.CreatedBy, input.CreatedAt)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "this integration is already bound to the agent"})
		return
	}
	auditRequest(r, "agent.tool.bound", map[string]any{"binding_id": input.ID, "agent_id": input.AgentID, "integration_id": input.IntegrationID, "approved": input.Approved})
	writeJSON(w, http.StatusCreated, input)
}

func listAgentMemories(ctx context.Context, workspaceID string) []AgentMemoryProfile {
	items := []AgentMemoryProfile{}
	query, args := `SELECT id,workspace_id,name,mode,provider,retention_days,checkpointing,status,created_by,created_at FROM agent_memory_profiles`, []any{}
	if workspaceID != "" {
		query += ` WHERE workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY created_at DESC`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item AgentMemoryProfile
		if rows.Scan(&item.ID, &item.WorkspaceID, &item.Name, &item.Mode, &item.Provider, &item.RetentionDays, &item.Checkpointing, &item.Status, &item.CreatedBy, &item.CreatedAt) == nil {
			items = append(items, item)
		}
	}
	return items
}

func agentMemoryHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"memories": listAgentMemories(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input AgentMemoryProfile
	if decode(r, &input) != nil || strings.TrimSpace(input.WorkspaceID) == "" || strings.TrimSpace(input.Name) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace and memory profile name are required"})
		return
	}
	if input.Mode == "" {
		input.Mode = "run"
	}
	if input.Provider == "" {
		input.Provider = "postgres"
	}
	if input.RetentionDays <= 0 {
		input.RetentionDays = 30
	}
	input.ID, input.Status, input.CreatedBy, input.CreatedAt = "mem_"+randomToken(9), "ready", requestActor(r), time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO agent_memory_profiles(id,workspace_id,name,mode,provider,retention_days,checkpointing,status,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`, input.ID, input.WorkspaceID, strings.TrimSpace(input.Name), input.Mode, input.Provider, input.RetentionDays, input.Checkpointing, input.Status, input.CreatedBy, input.CreatedAt)
	if err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "a memory profile with this name already exists"})
		return
	}
	auditRequest(r, "agent.memory.created", map[string]any{"memory_id": input.ID, "workspace_id": input.WorkspaceID, "provider": input.Provider})
	writeJSON(w, http.StatusCreated, input)
}

func listAgentApprovals(ctx context.Context, workspaceID string) []AgentApproval {
	items := []AgentApproval{}
	query, args := `SELECT id,workspace_id,run_id,step_id,reason,status,requested_by,resolved_by,created_at,resolved_at FROM agent_approvals`, []any{}
	if workspaceID != "" {
		query += ` WHERE workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END,created_at DESC`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item AgentApproval
		if rows.Scan(&item.ID, &item.WorkspaceID, &item.RunID, &item.StepID, &item.Reason, &item.Status, &item.RequestedBy, &item.ResolvedBy, &item.CreatedAt, &item.ResolvedAt) == nil {
			items = append(items, item)
		}
	}
	return items
}

func agentApprovalsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"approvals": listAgentApprovals(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPatch {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct {
		ID       string `json:"id"`
		Decision string `json:"decision"`
	}
	if decode(r, &input) != nil || input.ID == "" || input.Decision != "approve" && input.Decision != "reject" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "approval id and approve or reject decision are required"})
		return
	}
	var approval AgentApproval
	err := database.QueryRow(r.Context(), `SELECT id,workspace_id,run_id,step_id,reason,status,requested_by,resolved_by,created_at,resolved_at FROM agent_approvals WHERE id=$1`, input.ID).Scan(&approval.ID, &approval.WorkspaceID, &approval.RunID, &approval.StepID, &approval.Reason, &approval.Status, &approval.RequestedBy, &approval.ResolvedBy, &approval.CreatedAt, &approval.ResolvedAt)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "approval not found"})
		return
	}
	if approval.Status != "pending" {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "approval has already been resolved"})
		return
	}
	status := "approved"
	if input.Decision == "reject" {
		status = "rejected"
	}
	_, _ = database.Exec(r.Context(), `UPDATE agent_approvals SET status=$2,resolved_by=$3,resolved_at=now() WHERE id=$1`, approval.ID, status, requestActor(r))
	run, err := loadAgentRun(r.Context(), approval.RunID)
	if err == nil && status == "approved" {
		run, err = launchAgentRun(r.Context(), run, requestActor(r))
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
	} else if err == nil {
		_, _ = database.Exec(r.Context(), `UPDATE agent_runs SET status='cancelled',current_step='approval',finished_at=now(),updated_at=now() WHERE id=$1`, run.ID)
		publishAgentRunEvent(r.Context(), run.ID, "approval_rejected", "approval", map[string]any{"actor": requestActor(r)})
	}
	auditRequest(r, "agent.approval.resolved", map[string]any{"approval_id": approval.ID, "run_id": approval.RunID, "decision": input.Decision})
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "status": status, "run": run})
}

func listAgentEvaluations(ctx context.Context, workspaceID string) []AgentEvaluation {
	items := []AgentEvaluation{}
	query := `SELECT e.id,e.workspace_id,e.agent_id,a.name,COALESCE(e.run_id,''),e.suite,e.status,e.score,e.metrics,e.created_by,e.created_at FROM agent_evaluations e JOIN agent_definitions a ON a.id=e.agent_id`
	args := []any{}
	if workspaceID != "" {
		query += ` WHERE e.workspace_id=$1`
		args = append(args, workspaceID)
	}
	query += ` ORDER BY e.created_at DESC LIMIT 250`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item AgentEvaluation
		var metrics []byte
		if rows.Scan(&item.ID, &item.WorkspaceID, &item.AgentID, &item.AgentName, &item.RunID, &item.Suite, &item.Status, &item.Score, &metrics, &item.CreatedBy, &item.CreatedAt) == nil {
			_ = json.Unmarshal(metrics, &item.Metrics)
			items = append(items, item)
		}
	}
	return items
}

func agentEvaluationsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"evaluations": listAgentEvaluations(r.Context(), strings.TrimSpace(r.URL.Query().Get("workspaceId")))})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input AgentEvaluation
	if decode(r, &input) != nil || input.WorkspaceID == "" || input.AgentID == "" || strings.TrimSpace(input.Suite) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspace, agent, and evaluation suite are required"})
		return
	}
	if input.Score < 0 || input.Score > 1 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "evaluation score must be between 0 and 1"})
		return
	}
	if input.Metrics == nil {
		input.Metrics = map[string]any{}
	}
	input.ID, input.Status, input.CreatedBy, input.CreatedAt = "eval_"+randomToken(9), "completed", requestActor(r), time.Now().UTC()
	metrics, _ := json.Marshal(input.Metrics)
	_, err := database.Exec(r.Context(), `INSERT INTO agent_evaluations(id,workspace_id,agent_id,run_id,suite,status,score,metrics,created_by,created_at) VALUES($1,$2,$3,NULLIF($4,''),$5,$6,$7,$8,$9,$10)`, input.ID, input.WorkspaceID, input.AgentID, input.RunID, strings.TrimSpace(input.Suite), input.Status, input.Score, metrics, input.CreatedBy, input.CreatedAt)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "evaluation references an unavailable agent or run"})
		return
	}
	auditRequest(r, "agent.evaluation.recorded", map[string]any{"evaluation_id": input.ID, "agent_id": input.AgentID, "score": input.Score})
	if input.RunID != "" {
		publishAgentRunEvent(r.Context(), input.RunID, "evaluation_completed", "evaluation", map[string]any{"suite": input.Suite, "score": input.Score})
	}
	writeJSON(w, http.StatusCreated, input)
}

func agentOrchestrationHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	workspaceID := strings.TrimSpace(r.URL.Query().Get("workspaceId"))
	if workspaceID != "" {
		if _, err := loadWorkspace(r.Context(), workspaceID); err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "workspace not found"})
			return
		}
	}
	reconcileAgentRuns(r.Context())
	agents, flows, runs := listAgents(r.Context(), workspaceID), listAgentFlows(r.Context(), workspaceID), listAgentRuns(r.Context(), workspaceID)
	tools, memories := listAgentTools(r.Context(), workspaceID), listAgentMemories(r.Context(), workspaceID)
	approvals, evaluations := listAgentApprovals(r.Context(), workspaceID), listAgentEvaluations(r.Context(), workspaceID)
	traces := listAgentRunEvents(r.Context(), "", workspaceID, 300)
	running, waiting, failed := 0, 0, 0
	for _, run := range runs {
		switch run.Status {
		case "running", "deploying":
			running++
		case "waiting_approval":
			waiting++
		case "failed":
			failed++
		}
	}
	writeJSON(w, http.StatusOK, agentWorkspaceSnapshot{
		Summary: map[string]any{"agents": len(agents), "flows": len(flows), "runs": len(runs), "running": running, "waitingApproval": waiting, "failed": failed, "tools": len(tools), "memories": len(memories)},
		Agents:  agents, Flows: flows, Runs: runs, Tools: tools, Memories: memories, Approvals: approvals, Evaluations: evaluations, Traces: traces,
		Protocols: map[string]any{"mcp": map[string]any{"status": "enabled", "role": "tool and context gateway"}, "a2a": map[string]any{"status": "metadata-ready", "version": "1.0", "agentCardPath": "/api/v1/agents/{id}/a2a-card"}, "events": map[string]any{"status": "enabled", "transport": "NATS JetStream", "stream": agentEventStream}, "telemetry": map[string]any{"status": "enabled", "format": "OpenTelemetry-compatible run events"}},
		UpdatedAt: time.Now().UTC(),
	})
}

func agentA2ACardHandler(w http.ResponseWriter, r *http.Request) bool {
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/agents/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) != 2 || parts[1] != "a2a-card" || r.Method != http.MethodGet {
		return false
	}
	agent, err := loadAgent(r.Context(), parts[0])
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "agent not found"})
		return true
	}
	writeJSON(w, http.StatusOK, map[string]any{"protocolVersion": "1.0", "name": agent.Name, "description": agent.Description, "version": fmt.Sprint(agent.Version), "preferredTransport": "HTTP+JSON", "capabilities": map[string]bool{"streaming": true, "pushNotifications": false, "stateTransitionHistory": true}, "defaultInputModes": []string{"application/json", "text/plain"}, "defaultOutputModes": []string{"application/json", "text/plain"}, "skills": []map[string]any{{"id": agent.ID, "name": agent.Name, "description": agent.Description, "tags": []string{agent.Framework, "openmycelium"}}}, "security": []map[string][]string{{"openmyceliumSession": []string{}}}})
	return true
}

func sortAgentRunsNewest(items []AgentRun) {
	sort.Slice(items, func(i, j int) bool { return items[i].CreatedAt.After(items[j].CreatedAt) })
}
