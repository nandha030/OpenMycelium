// OpenMycelium Developer Edition control plane.
package main

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
)

//go:embed index.html login.html login-fabric.png app.js styles.css premium.css
var ui embed.FS

type Accelerator struct {
	ID        string `json:"id"`
	Vendor    string `json:"vendor"`
	Model     string `json:"model"`
	Runtime   string `json:"runtime"`
	Memory    string `json:"memory"`
	Health    string `json:"health"`
	Simulated bool   `json:"simulated"`
}
type HostProfile struct {
	Name         string        `json:"name"`
	OS           string        `json:"os"`
	CPU          string        `json:"cpu"`
	LogicalCores int           `json:"logicalCores"`
	MemoryGB     float64       `json:"memoryGB"`
	Accelerators []Accelerator `json:"accelerators"`
	ReportedAt   time.Time     `json:"reportedAt"`
}
type Pool struct {
	ID, Name, Policy string
	Members          []Accelerator `json:"members"`
}
type Workload struct {
	ID                 string            `json:"id"`
	AgentID            string            `json:"agentId,omitempty"`
	AgentRunID         string            `json:"agentRunId,omitempty"`
	AgentSpecJSON      string            `json:"agentSpecJson,omitempty"`
	AgentFlowJSON      string            `json:"agentFlowJson,omitempty"`
	AgentToolsJSON     string            `json:"agentToolsJson,omitempty"`
	WorkspaceID        string            `json:"workspaceId,omitempty"`
	ReleaseID          string            `json:"releaseId,omitempty"`
	Name               string            `json:"name"`
	Kind               string            `json:"kind"`
	Runtime            string            `json:"runtime"`
	Pool               string            `json:"pool"`
	Image              string            `json:"image"`
	ImagePullSecret    string            `json:"imagePullSecret,omitempty"`
	Accelerators       int               `json:"accelerators"`
	SchedulerBackend   string            `json:"schedulerBackend,omitempty"`
	QueueName          string            `json:"queueName,omitempty"`
	GangMinAvailable   int32             `json:"gangMinAvailable,omitempty"`
	TopologyMode       string            `json:"topologyMode,omitempty"`
	TopologyKey        string            `json:"topologyKey,omitempty"`
	NetworkMode        string            `json:"networkMode,omitempty"`
	PriorityClass      string            `json:"priorityClass,omitempty"`
	Status             string            `json:"status"`
	Checkpoint         string            `json:"checkpoint"`
	ClusterID          string            `json:"clusterId,omitempty"`
	ClusterName        string            `json:"clusterName,omitempty"`
	Namespace          string            `json:"namespace,omitempty"`
	ResourceName       string            `json:"resourceName,omitempty"`
	PodName            string            `json:"podName,omitempty"`
	NodeName           string            `json:"nodeName,omitempty"`
	ServiceName        string            `json:"serviceName,omitempty"`
	Endpoint           string            `json:"endpoint,omitempty"`
	ServiceType        string            `json:"serviceType,omitempty"`
	Port               int32             `json:"port,omitempty"`
	Model              string            `json:"model,omitempty"`
	ModelVersionID     string            `json:"modelVersionId,omitempty"`
	ModelSourceURI     string            `json:"modelSourceUri,omitempty"`
	ModelRuntime       string            `json:"modelRuntime,omitempty"`
	Command            string            `json:"command,omitempty"`
	CPU                string            `json:"cpu,omitempty"`
	Memory             string            `json:"memory,omitempty"`
	StorageGB          int               `json:"storageGB,omitempty"`
	StorageClass       string            `json:"storageClass,omitempty"`
	PVCName            string            `json:"pvcName,omitempty"`
	DesiredCount       int32             `json:"desiredCount,omitempty"`
	Placement          PlacementDecision `json:"placement,omitempty"`
	PodPhase           string            `json:"podPhase,omitempty"`
	StatusReason       string            `json:"statusReason,omitempty"`
	StatusMessage      string            `json:"statusMessage,omitempty"`
	Restarts           int32             `json:"restarts,omitempty"`
	FabricPlanID       string            `json:"fabricPlanId,omitempty"`
	FabricAddress      string            `json:"fabricAddress,omitempty"`
	FabricMode         string            `json:"fabricMode,omitempty"`
	FabricPlan         string            `json:"-"`
	ExecutionPlanID    string            `json:"executionPlanId,omitempty"`
	ExecutionTransport string            `json:"executionTransport,omitempty"`
	ExecutionPlan      string            `json:"-"`
	ExecutionGroupID   string            `json:"-"`
	ExecutionRankBase  int               `json:"-"`
	ExecutionGroupSize int               `json:"-"`
	ExecutionWorldSize int               `json:"-"`
	LastError          string            `json:"lastError,omitempty"`
	UpdatedAt          time.Time         `json:"updatedAt,omitempty"`
	SSHHost            string            `json:"sshHost,omitempty"`
	SSHPort            int               `json:"sshPort,omitempty"`
	SSHUser            string            `json:"sshUser,omitempty"`
	CreatedAt          time.Time         `json:"createdAt"`
}

var sshNamePattern = regexp.MustCompile(`^[a-zA-Z0-9._-]+$`)
var sshHostPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9.:-]*$`)
var kubernetesObjectNamePattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$`)

type Workflow struct {
	ID, Name, Status string
	Stages           []WorkflowStage `json:"stages"`
}
type WorkflowStage struct {
	Name      string `json:"name"`
	Kind      string `json:"kind"`
	Pool      string `json:"pool"`
	DependsOn string `json:"dependsOn"`
}
type OllamaModel struct {
	Name       string    `json:"name"`
	Model      string    `json:"model"`
	Size       int64     `json:"size"`
	ModifiedAt time.Time `json:"modified_at"`
}
type PlatformSettings struct {
	Organization      string `json:"organization"`
	DefaultQueue      string `json:"defaultQueue"`
	OIDCIssuer        string `json:"oidcIssuer"`
	TelemetryEndpoint string `json:"telemetryEndpoint"`
	RegistrationOpen  bool   `json:"registrationOpen"`
}
type State struct {
	sync.RWMutex
	Accelerators []Accelerator
	Host         HostProfile
	Settings     PlatformSettings
	Pools        []Pool
	Workloads    []Workload
	Workflows    []Workflow
}

var state = State{Settings: PlatformSettings{Organization: "OpenMycelium", DefaultQueue: "default", RegistrationOpen: false}}
var database *pgxpool.Pool
var eventBus *nats.Conn

type persistedState struct {
	Host      HostProfile      `json:"host"`
	Settings  PlatformSettings `json:"settings"`
	Workloads []Workload       `json:"workloads"`
}

func persistencePath() string {
	if value := os.Getenv("STATE_PATH"); value != "" {
		return value
	}
	return ".openmycelium-state.json"
}
func persistStateLocked() {
	saved := persistedState{Host: state.Host, Settings: state.Settings, Workloads: state.Workloads}
	snapshot, _ := json.Marshal(saved)
	path := persistencePath()
	_ = os.MkdirAll(filepath.Dir(path), 0755)
	_ = os.WriteFile(path, snapshot, 0600)
	syncDatabase(saved)
}
func restoreState() {
	data, err := os.ReadFile(persistencePath())
	if err != nil {
		return
	}
	var saved persistedState
	if json.Unmarshal(data, &saved) == nil {
		state.Host = saved.Host
		state.Settings = saved.Settings
		state.Workloads = saved.Workloads
	}
}

var schema = []string{`CREATE TABLE IF NOT EXISTS hosts (name TEXT PRIMARY KEY, profile JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS workloads (id TEXT PRIMARY KEY, workload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS platform_settings (id BOOLEAN PRIMARY KEY DEFAULT true, settings JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS audit_events (id BIGSERIAL PRIMARY KEY, subject TEXT NOT NULL, payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`}

func publishEvent(subject string, payload any) {
	if eventBus == nil {
		return
	}
	data, err := json.Marshal(payload)
	if err == nil {
		_ = eventBus.Publish("openmycelium."+subject, data)
	}
}
func syncDatabase(snapshot persistedState) {
	if database == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tx, err := database.Begin(ctx)
	if err != nil {
		return
	}
	defer tx.Rollback(ctx)
	if snapshot.Host.Name != "" {
		profile, _ := json.Marshal(snapshot.Host)
		if _, err = tx.Exec(ctx, "INSERT INTO hosts(name,profile,updated_at) VALUES($1,$2,now()) ON CONFLICT(name) DO UPDATE SET profile=excluded.profile,updated_at=now()", snapshot.Host.Name, profile); err != nil {
			return
		}
	}
	settings, _ := json.Marshal(snapshot.Settings)
	if _, err = tx.Exec(ctx, "INSERT INTO platform_settings(id,settings,updated_at) VALUES(true,$1,now()) ON CONFLICT(id) DO UPDATE SET settings=excluded.settings,updated_at=now()", settings); err != nil {
		return
	}
	if _, err = tx.Exec(ctx, "DELETE FROM workloads"); err != nil {
		return
	}
	for _, workload := range snapshot.Workloads {
		value, _ := json.Marshal(workload)
		if _, err = tx.Exec(ctx, "INSERT INTO workloads(id,workload,updated_at) VALUES($1,$2,now())", workload.ID, value); err != nil {
			return
		}
	}
	_ = tx.Commit(ctx)
}
func restoreDatabase() {
	if database == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var profile []byte
	if err := database.QueryRow(ctx, "SELECT profile FROM hosts ORDER BY updated_at DESC LIMIT 1").Scan(&profile); err == nil {
		_ = json.Unmarshal(profile, &state.Host)
	}
	var settings []byte
	if err := database.QueryRow(ctx, "SELECT settings FROM platform_settings WHERE id=true").Scan(&settings); err == nil {
		_ = json.Unmarshal(settings, &state.Settings)
	}
	rows, err := database.Query(ctx, "SELECT workload FROM workloads ORDER BY updated_at")
	if err != nil {
		return
	}
	defer rows.Close()
	restored := []Workload{}
	for rows.Next() {
		var value []byte
		if rows.Scan(&value) == nil {
			var workload Workload
			if json.Unmarshal(value, &workload) == nil {
				restored = append(restored, workload)
			}
		}
	}
	state.Workloads = restored
}
func initializeInfrastructure() {
	restoreState()
	if url := os.Getenv("DATABASE_URL"); url != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		pool, err := pgxpool.New(ctx, url)
		if err != nil {
			fmt.Printf("PostgreSQL unavailable: %v\n", err)
		} else {
			statements := append(append(append([]string{}, schema...), resourceSchema...), agentSchema...)
			for _, statement := range statements {
				_, err = pool.Exec(ctx, statement)
				if err != nil {
					break
				}
			}
			if err != nil {
				fmt.Printf("PostgreSQL migration failed: %v\n", err)
				pool.Close()
			} else {
				database = pool
				restoreDatabase()
				fmt.Println("PostgreSQL persistence ready")
			}
		}
	}
	if url := os.Getenv("NATS_URL"); url != "" {
		connection, err := nats.Connect(url, nats.Timeout(5*time.Second))
		if err != nil {
			fmt.Printf("NATS unavailable: %v\n", err)
		} else {
			eventBus = connection
			initializeAgentEventStream()
			fmt.Println("NATS event bus ready")
		}
	}
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}
func decode(r *http.Request, value any) error {
	return json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(value)
}

func hostAccelerators() []Accelerator {
	found := []Accelerator{}
	if path, err := exec.LookPath("nvidia-smi"); err == nil {
		out, _ := exec.Command(path, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits").Output()
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.Split(line, ",")
			if len(parts) == 2 {
				found = append(found, Accelerator{ID: "nvidia-" + strings.ReplaceAll(strings.TrimSpace(parts[0]), " ", "-"), Vendor: "NVIDIA", Model: strings.TrimSpace(parts[0]), Runtime: "cuda", Memory: strings.TrimSpace(parts[1]) + " MiB", Health: "healthy"})
			}
		}
	}
	if path, err := exec.LookPath("rocm-smi"); err == nil {
		out, _ := exec.Command(path, "--showproductname").Output()
		for _, line := range strings.Split(string(out), "\n") {
			if strings.Contains(line, "Card series") {
				found = append(found, Accelerator{ID: "amd-" + fmt.Sprint(len(found)), Vendor: "AMD", Model: strings.TrimSpace(strings.Split(line, ":")[len(strings.Split(line, ":"))-1]), Runtime: "rocm", Memory: "reported by ROCm", Health: "healthy"})
			}
		}
	}
	if runtime.GOOS == "darwin" {
		found = append(found, Accelerator{ID: "apple-uma", Vendor: "Apple", Model: "Apple Silicon GPU / Neural Engine", Runtime: "metal,coreml", Memory: "shared unified memory", Health: "detected"})
	}
	return found
}
func scanHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	state.Lock()
	state.Accelerators = hostAccelerators()
	values := state.Accelerators
	profile := state.Host
	state.Unlock()
	if len(profile.Accelerators) > 0 {
		values = append(profile.Accelerators, values...)
	}
	source := "container"
	if profile.Name != "" {
		source = "host agent: " + profile.Name
	}
	writeJSON(w, http.StatusOK, map[string]any{"source": source, "host": runtime.GOOS, "hostProfile": profile, "accelerators": values, "count": len(values)})
}
func importHostHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var profile HostProfile
	if err := decode(r, &profile); err != nil || profile.Name == "" || profile.CPU == "" {
		writeJSON(w, 400, map[string]string{"error": "host name and CPU are required"})
		return
	}
	profile.ReportedAt = time.Now().UTC()
	state.Lock()
	state.Host = profile
	persistStateLocked()
	state.Unlock()
	publishEvent("host.registered", profile)
	writeJSON(w, http.StatusCreated, profile)
}
func poolsHandler(w http.ResponseWriter, r *http.Request) {
	state.RLock()
	defer state.RUnlock()
	writeJSON(w, http.StatusOK, map[string]any{"pools": state.Pools})
}
func workloadsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		reconcileAllKubernetesWorkloads(r.Context())
		state.RLock()
		defer state.RUnlock()
		writeJSON(w, http.StatusOK, map[string]any{"workloads": state.Workloads})
		return
	}
	var input struct {
		Name             string `json:"name"`
		Kind             string `json:"kind"`
		Pool             string `json:"pool"`
		Image            string `json:"image"`
		ImagePullSecret  string `json:"imagePullSecret"`
		Accelerators     int    `json:"accelerators"`
		SSHHost          string `json:"sshHost"`
		SSHPort          int    `json:"sshPort"`
		SSHUser          string `json:"sshUser"`
		Runtime          string `json:"runtime"`
		ClusterID        string `json:"clusterId"`
		Namespace        string `json:"namespace"`
		Model            string `json:"model"`
		ModelVersionID   string `json:"modelVersionId"`
		Command          string `json:"command"`
		CPU              string `json:"cpu"`
		Memory           string `json:"memory"`
		StorageGB        int    `json:"storageGB"`
		StorageClass     string `json:"storageClass"`
		ServiceType      string `json:"serviceType"`
		Port             int32  `json:"port"`
		Replicas         int32  `json:"replicas"`
		FabricPlanID     string `json:"fabricPlanId"`
		ExecutionPlanID  string `json:"executionPlanId"`
		WorkspaceID      string `json:"workspaceId"`
		SchedulerBackend string `json:"schedulerBackend"`
		QueueName        string `json:"queueName"`
		GangMinAvailable int32  `json:"gangMinAvailable"`
		TopologyMode     string `json:"topologyMode"`
		TopologyKey      string `json:"topologyKey"`
		NetworkMode      string `json:"networkMode"`
		PriorityClass    string `json:"priorityClass"`
	}
	if err := decode(r, &input); err != nil || strings.TrimSpace(input.Name) == "" {
		writeJSON(w, 400, map[string]string{"error": "name is required"})
		return
	}
	if input.Accelerators < 0 {
		writeJSON(w, 400, map[string]string{"error": "accelerator count cannot be negative"})
		return
	}
	if input.StorageGB < 0 || input.Replicas < 0 || input.Port < 0 || input.Port > 65535 {
		writeJSON(w, 400, map[string]string{"error": "storage, replicas, and service port must be valid non-negative values"})
		return
	}
	input.ImagePullSecret = strings.TrimSpace(input.ImagePullSecret)
	if input.ImagePullSecret != "" && (len(input.ImagePullSecret) > 253 || !kubernetesObjectNamePattern.MatchString(input.ImagePullSecret)) {
		writeJSON(w, 400, map[string]string{"error": "imagePullSecret must be a valid Kubernetes DNS name"})
		return
	}
	input.Name = strings.TrimSpace(input.Name)
	input.SSHHost = strings.TrimSpace(input.SSHHost)
	input.SSHUser = strings.TrimSpace(input.SSHUser)
	if input.SSHHost != "" || input.SSHUser != "" || input.SSHPort != 0 {
		if input.SSHPort == 0 {
			input.SSHPort = 22
		}
		if input.SSHHost == "" || input.SSHUser == "" || input.SSHPort < 1 || input.SSHPort > 65535 || !sshHostPattern.MatchString(input.SSHHost) || !sshNamePattern.MatchString(input.SSHUser) {
			writeJSON(w, 400, map[string]string{"error": "SSH host, user, and a port from 1 to 65535 are required; credentials remain in your SSH agent"})
			return
		}
	}
	if input.Kind != "inference" && input.Kind != "training" && input.Kind != "finetuning" && input.Kind != "batch" && input.Kind != "interactive" && input.Kind != "agent" {
		writeJSON(w, 400, map[string]string{"error": "kind must be inference, training, finetuning, batch, interactive, or agent"})
		return
	}
	input.WorkspaceID = strings.TrimSpace(input.WorkspaceID)
	input.SchedulerBackend = strings.ToLower(strings.TrimSpace(input.SchedulerBackend))
	if input.SchedulerBackend == "" {
		input.SchedulerBackend = "kubernetes"
	}
	if input.SchedulerBackend != "kubernetes" && input.SchedulerBackend != "kueue" && input.SchedulerBackend != "volcano" {
		writeJSON(w, 400, map[string]string{"error": "schedulerBackend must be kubernetes, kueue, or volcano"})
		return
	}
	input.QueueName = strings.TrimSpace(input.QueueName)
	input.TopologyMode = strings.ToLower(strings.TrimSpace(input.TopologyMode))
	if input.TopologyMode == "" {
		input.TopologyMode = "none"
	}
	if input.TopologyMode != "none" && input.TopologyMode != "compact" && input.TopologyMode != "spread" {
		writeJSON(w, 400, map[string]string{"error": "topologyMode must be none, compact, or spread"})
		return
	}
	input.TopologyKey = strings.TrimSpace(input.TopologyKey)
	if input.TopologyKey == "" {
		if input.TopologyMode == "spread" {
			input.TopologyKey = "topology.kubernetes.io/zone"
		} else {
			input.TopologyKey = "kubernetes.io/hostname"
		}
	}
	if !validKubernetesQualifiedName(input.TopologyKey) {
		writeJSON(w, 400, map[string]string{"error": "topologyKey must be a valid Kubernetes label key"})
		return
	}
	input.NetworkMode = strings.ToLower(strings.TrimSpace(input.NetworkMode))
	if input.NetworkMode == "" {
		input.NetworkMode = "standard"
	}
	if input.NetworkMode != "standard" && input.NetworkMode != "rdma" && input.NetworkMode != "infiniband" {
		writeJSON(w, 400, map[string]string{"error": "networkMode must be standard, rdma, or infiniband"})
		return
	}
	input.PriorityClass = strings.TrimSpace(input.PriorityClass)
	if input.QueueName != "" && (len(input.QueueName) > 253 || !kubernetesObjectNamePattern.MatchString(input.QueueName)) {
		writeJSON(w, 400, map[string]string{"error": "queueName must be a valid Kubernetes object name"})
		return
	}
	if input.PriorityClass != "" && (len(input.PriorityClass) > 253 || !kubernetesObjectNamePattern.MatchString(input.PriorityClass)) {
		writeJSON(w, 400, map[string]string{"error": "priorityClass must be a valid Kubernetes object name"})
		return
	}
	if input.WorkspaceID != "" {
		workspace, workspaceErr := loadWorkspace(r.Context(), input.WorkspaceID)
		if workspaceErr != nil {
			writeJSON(w, 400, map[string]string{"error": "selected workspace does not exist"})
			return
		}
		input.ClusterID, input.Namespace = workspace.ClusterID, workspace.Namespace
		if strings.TrimSpace(input.StorageClass) == "" {
			input.StorageClass = workspace.StorageClass
		}
		if input.QueueName == "" {
			input.QueueName = workspace.Queue
		}
	}
	if strings.TrimSpace(input.ModelVersionID) != "" {
		if database == nil {
			writeJSON(w, 503, map[string]string{"error": "PostgreSQL is required to attach a catalog model"})
			return
		}
		model, version, err := loadModelVersion(r.Context(), strings.TrimSpace(input.ModelVersionID))
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "selected model version does not exist"})
			return
		}
		input.Model = model.Name
		if strings.TrimSpace(input.Image) == "" {
			input.Image = modelRuntimeImage(version.Runtime)
		}
	}
	if input.Runtime == "" && input.ClusterID != "" {
		input.Runtime = "kubernetes"
	}
	if input.Runtime == "kubernetes" {
		if strings.TrimSpace(input.Image) == "" && strings.TrimSpace(input.Model) == "" {
			writeJSON(w, 400, map[string]string{"error": "container image is required for a Kubernetes workload"})
			return
		}
		if strings.TrimSpace(input.Model) != "" && strings.TrimSpace(input.Image) == "" {
			input.Image = "ollama/ollama:latest"
			input.Runtime = "ollama"
		}
	} else if input.Runtime == "" {
		writeJSON(w, 400, map[string]string{"error": "select a connected Kubernetes cluster as the deployment target"})
		return
	}
	if input.Replicas == 0 {
		input.Replicas = 1
	}
	if input.GangMinAvailable < 0 || input.GangMinAvailable > input.Replicas {
		writeJSON(w, 400, map[string]string{"error": "gangMinAvailable must be between 0 and the replica count"})
		return
	}
	if input.SchedulerBackend == "kueue" && !workloadUsesJob(input.Kind) {
		writeJSON(w, 400, map[string]string{"error": "Kueue admission currently supports training, fine-tuning, and batch Jobs"})
		return
	}
	if input.SchedulerBackend == "kueue" && input.QueueName == "" {
		writeJSON(w, 400, map[string]string{"error": "Kueue workloads require a LocalQueue name"})
		return
	}
	if input.GangMinAvailable > 1 && !workloadUsesJob(input.Kind) {
		writeJSON(w, 400, map[string]string{"error": "gang scheduling is available for training, fine-tuning, and batch Jobs"})
		return
	}
	if input.GangMinAvailable > 1 && input.SchedulerBackend == "kubernetes" {
		writeJSON(w, 400, map[string]string{"error": "strict gang scheduling requires the Kueue or Volcano backend"})
		return
	}
	if input.Port == 0 && (input.Kind == "inference" || input.Kind == "interactive" || input.Kind == "agent") {
		input.Port = 8000
	}
	if input.ServiceType == "" {
		input.ServiceType = "ClusterIP"
	}
	now := time.Now().UTC()
	item := Workload{ID: fmt.Sprintf("job-%d", time.Now().UnixNano()), WorkspaceID: input.WorkspaceID, Name: input.Name, Kind: input.Kind, Runtime: input.Runtime, Pool: input.Pool, Image: strings.TrimSpace(input.Image), ImagePullSecret: input.ImagePullSecret, Accelerators: input.Accelerators, SchedulerBackend: input.SchedulerBackend, QueueName: input.QueueName, GangMinAvailable: input.GangMinAvailable, TopologyMode: input.TopologyMode, TopologyKey: input.TopologyKey, NetworkMode: input.NetworkMode, PriorityClass: input.PriorityClass, Status: "queued", ClusterID: strings.TrimSpace(input.ClusterID), Namespace: strings.TrimSpace(input.Namespace), Model: strings.TrimSpace(input.Model), Command: strings.TrimSpace(input.Command), CPU: strings.TrimSpace(input.CPU), Memory: strings.TrimSpace(input.Memory), StorageGB: input.StorageGB, StorageClass: strings.TrimSpace(input.StorageClass), ServiceType: input.ServiceType, Port: input.Port, DesiredCount: input.Replicas, FabricPlanID: strings.TrimSpace(input.FabricPlanID), ExecutionPlanID: strings.TrimSpace(input.ExecutionPlanID), SSHHost: input.SSHHost, SSHPort: input.SSHPort, SSHUser: input.SSHUser, CreatedAt: now, UpdatedAt: now}
	if item.WorkspaceID != "" {
		item.ReleaseID = "rel_" + randomToken(9)
	}
	item.ModelVersionID = strings.TrimSpace(input.ModelVersionID)
	if item.ModelVersionID != "" {
		if database == nil {
			writeJSON(w, 503, map[string]string{"error": "PostgreSQL is required to attach a catalog model"})
			return
		}
		model, version, err := loadModelVersion(r.Context(), item.ModelVersionID)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "selected model version does not exist"})
			return
		}
		item.Model, item.ModelSourceURI, item.ModelRuntime = model.Name, version.SourceURI, version.Runtime
	}
	if item.FabricPlanID != "" {
		if database == nil {
			writeJSON(w, 503, map[string]string{"error": "PostgreSQL is required to attach a Hypha fabric plan"})
			return
		}
		plan, err := loadFabricPlan(r.Context(), item.FabricPlanID)
		if err != nil {
			writeJSON(w, 400, map[string]string{"error": "selected Hypha fabric plan does not exist"})
			return
		}
		if plan.ClusterID != item.ClusterID {
			writeJSON(w, 400, map[string]string{"error": "Hypha fabric plan belongs to a different cluster"})
			return
		}
		item.FabricAddress, item.FabricMode = plan.LogicalAddress, plan.Consistency
		planJSON, err := json.Marshal(plan)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "Hypha fabric plan could not be encoded for the workload"})
			return
		}
		item.FabricPlan = string(planJSON)
	}
	if item.ExecutionPlanID != "" {
		if err := hydrateExecutionContract(r.Context(), &item); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
	}
	item.ResourceName = workloadResourceName(item.ID, item.Name)
	if item.Kind == "inference" || item.Kind == "interactive" || item.Kind == "agent" {
		item.ServiceName = item.ResourceName
	} else if workloadUsesJob(item.Kind) && item.DesiredCount > 1 {
		item.ServiceName = item.ResourceName + "-workers"
	}
	if item.StorageGB > 0 {
		item.PVCName = item.ResourceName + "-models"
	}
	if item.Runtime == "ollama" {
		item.Port = 11434
	}
	if item.Runtime == "kubernetes" && item.ModelRuntime == "ollama" {
		item.Port = 11434
	}
	if item.Runtime == "kubernetes" || item.Runtime == "ollama" {
		ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
		err := deployKubernetesWorkload(ctx, &item)
		cancel()
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		item.Runtime = "kubernetes"
	}
	if item.WorkspaceID != "" {
		sourceRef := item.Image
		if item.ModelSourceURI != "" {
			sourceRef = item.ModelSourceURI
		}
		if err := workspaceRelease(r.Context(), item.WorkspaceID, item.ReleaseID, item.Name, "celium-ai", sourceRef, item.Status, item); err != nil {
			if item.Runtime == "kubernetes" {
				ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
				_ = deleteKubernetesWorkload(ctx, item)
				cancel()
			}
			writeJSON(w, 500, map[string]string{"error": "workload deployed but workspace release could not be recorded"})
			return
		}
	}
	state.Lock()
	state.Workloads = append(state.Workloads, item)
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "workload.created", map[string]any{"workload_id": item.ID, "workspace_id": item.WorkspaceID, "release_id": item.ReleaseID, "name": item.Name, "kind": item.Kind})
	publishEvent("workload.created", item)
	writeJSON(w, 201, item)
}
func workloadActionHandler(w http.ResponseWriter, r *http.Request) {
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/workloads/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" || len(parts) > 2 {
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	id := parts[0]
	if len(parts) == 1 && r.Method == http.MethodDelete {
		state.RLock()
		var target Workload
		for _, item := range state.Workloads {
			if item.ID == id {
				target = item
				break
			}
		}
		state.RUnlock()
		if target.ID != "" && target.Runtime == "kubernetes" {
			ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
			err := deleteKubernetesWorkload(ctx, target)
			cancel()
			if err != nil {
				writeJSON(w, http.StatusBadGateway, map[string]string{"error": "Kubernetes resources were not deleted: " + err.Error()})
				return
			}
		}
		state.Lock()
		for i := range state.Workloads {
			if state.Workloads[i].ID == id {
				item := state.Workloads[i]
				state.Workloads = append(state.Workloads[:i], state.Workloads[i+1:]...)
				persistStateLocked()
				state.Unlock()
				auditRequest(r, "workload.deleted", map[string]any{"workload_id": item.ID, "name": item.Name})
				publishEvent("workload.deleted", item)
				writeJSON(w, 200, map[string]bool{"ok": true})
				return
			}
		}
		state.Unlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	readOperation := len(parts) == 2 && r.Method == http.MethodGet && (parts[1] == "diagnostics" || parts[1] == "logs" || parts[1] == "events" || parts[1] == "service" || parts[1] == "manifest" || parts[1] == "storage")
	writeOperation := len(parts) == 2 && r.Method == http.MethodPost && (parts[1] == "exec" || parts[1] == "probe")
	if readOperation || writeOperation {
		state.RLock()
		var target Workload
		for _, item := range state.Workloads {
			if item.ID == id {
				target = item
				break
			}
		}
		state.RUnlock()
		if target.ID == "" {
			writeJSON(w, 404, map[string]string{"error": "workload not found"})
			return
		}
		if kubernetesWorkloadSubresourceHandler(w, r, target, parts[1]) {
			return
		}
		writeJSON(w, 409, map[string]string{"error": "this operation is available only for Kubernetes workloads"})
		return
	}
	if len(parts) == 2 && parts[1] == "ssh" && r.Method == http.MethodGet {
		state.RLock()
		for _, item := range state.Workloads {
			if item.ID == id {
				state.RUnlock()
				if item.SSHHost == "" || item.SSHUser == "" {
					writeJSON(w, 409, map[string]string{"error": "SSH is not configured for this workload; add host, port, and user when submitting it"})
					return
				}
				port := item.SSHPort
				if port == 0 {
					port = 22
				}
				if port < 1 || port > 65535 || !sshHostPattern.MatchString(item.SSHHost) || !sshNamePattern.MatchString(item.SSHUser) {
					writeJSON(w, 409, map[string]string{"error": "stored SSH coordinates are invalid; redeploy the workload with a valid host, port, and user"})
					return
				}
				command := fmt.Sprintf("ssh -p %d %s@%s", port, item.SSHUser, item.SSHHost)
				auditRequest(r, "workload.ssh_command_requested", map[string]any{"workload_id": item.ID, "host": item.SSHHost})
				writeJSON(w, 200, map[string]any{"workloadId": item.ID, "name": item.Name, "status": item.Status, "host": item.SSHHost, "port": port, "user": item.SSHUser, "command": command})
				return
			}
		}
		state.RUnlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	if len(parts) == 2 && parts[1] == "ssh" && r.Method == http.MethodPatch {
		var input struct {
			Host string `json:"host"`
			Port int    `json:"port"`
			User string `json:"user"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid SSH configuration"})
			return
		}
		input.Host = strings.TrimSpace(input.Host)
		input.User = strings.TrimSpace(input.User)
		if input.Port == 0 {
			input.Port = 22
		}
		if input.Host == "" || input.User == "" || input.Port < 1 || input.Port > 65535 || !sshHostPattern.MatchString(input.Host) || !sshNamePattern.MatchString(input.User) {
			writeJSON(w, 400, map[string]string{"error": "a valid SSH host, port, and user are required"})
			return
		}
		state.Lock()
		for i := range state.Workloads {
			if state.Workloads[i].ID == id {
				state.Workloads[i].SSHHost = input.Host
				state.Workloads[i].SSHPort = input.Port
				state.Workloads[i].SSHUser = input.User
				persistStateLocked()
				item := state.Workloads[i]
				state.Unlock()
				auditRequest(r, "workload.ssh_configured", map[string]any{"workload_id": item.ID, "host": item.SSHHost, "port": item.SSHPort})
				publishEvent("workload.ssh_configured", item)
				writeJSON(w, 200, item)
				return
			}
		}
		state.Unlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	if len(parts) != 2 || parts[1] != "action" || r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct{ Action string }
	if err := decode(r, &input); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid action"})
		return
	}
	state.RLock()
	var kubernetesTarget Workload
	for _, item := range state.Workloads {
		if item.ID == id {
			kubernetesTarget = item
			break
		}
	}
	state.RUnlock()
	if kubernetesTarget.ID != "" && kubernetesTarget.Runtime == "kubernetes" {
		ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
		err := actOnKubernetesWorkload(ctx, &kubernetesTarget, input.Action)
		cancel()
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		state.Lock()
		for i := range state.Workloads {
			if state.Workloads[i].ID == id {
				state.Workloads[i] = kubernetesTarget
				break
			}
		}
		persistStateLocked()
		state.Unlock()
		auditRequest(r, "workload."+input.Action, map[string]any{"workload_id": kubernetesTarget.ID, "name": kubernetesTarget.Name, "cluster_id": kubernetesTarget.ClusterID})
		publishEvent("workload."+input.Action, kubernetesTarget)
		writeJSON(w, 200, kubernetesTarget)
		return
	}
	state.Lock()
	for i := range state.Workloads {
		if state.Workloads[i].ID == id {
			switch input.Action {
			case "start":
				state.Workloads[i].Status = "running"
			case "stop":
				state.Workloads[i].Status = "stopped"
			case "redeploy":
				state.Workloads[i].Status = "queued"
			case "checkpoint":
				state.Workloads[i].Checkpoint = "checkpoint://local/" + id
			default:
				state.Unlock()
				writeJSON(w, 400, map[string]string{"error": "supported actions: start, stop, redeploy, checkpoint"})
				return
			}
			persistStateLocked()
			item := state.Workloads[i]
			state.Unlock()
			auditRequest(r, "workload."+input.Action, map[string]any{"workload_id": item.ID, "name": item.Name})
			publishEvent("workload."+input.Action, item)
			writeJSON(w, 200, item)
			return
		}
	}
	state.Unlock()
	writeJSON(w, 404, map[string]string{"error": "workload not found"})
}
func workflowsHandler(w http.ResponseWriter, r *http.Request) {
	state.RLock()
	defer state.RUnlock()
	writeJSON(w, 200, map[string]any{"workflows": state.Workflows})
}
func settingsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		state.RLock()
		defer state.RUnlock()
		writeJSON(w, 200, state.Settings)
		return
	}
	var settings PlatformSettings
	if err := decode(r, &settings); err != nil || settings.Organization == "" {
		writeJSON(w, 400, map[string]string{"error": "organization is required"})
		return
	}
	state.Lock()
	state.Settings = settings
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "settings.updated", map[string]any{"organization": settings.Organization, "default_queue": settings.DefaultQueue})
	publishEvent("settings.updated", settings)
	writeJSON(w, 200, settings)
}
func ollamaBaseURL() string {
	if value := os.Getenv("OLLAMA_BASE_URL"); value != "" {
		return strings.TrimRight(value, "/")
	}
	return "http://host.docker.internal:11434"
}
func ollamaModels() ([]OllamaModel, error) {
	response, err := (&http.Client{Timeout: 8 * time.Second}).Get(ollamaBaseURL() + "/api/tags")
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("Ollama returned %s", response.Status)
	}
	var payload struct {
		Models []OllamaModel `json:"models"`
	}
	err = json.NewDecoder(io.LimitReader(response.Body, 8<<20)).Decode(&payload)
	return payload.Models, err
}
func modelsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	models, err := ollamaModels()
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "cannot reach Ollama at " + ollamaBaseURL() + ": " + err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"runtime": "ollama", "baseURL": ollamaBaseURL(), "models": models})
}
func deployInferenceHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct {
		Model string `json:"model"`
	}
	if err := decode(r, &input); err != nil || input.Model == "" {
		writeJSON(w, 400, map[string]string{"error": "model is required"})
		return
	}
	models, err := ollamaModels()
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "cannot reach local Ollama runtime"})
		return
	}
	exists := false
	for _, model := range models {
		if model.Name == input.Model || model.Model == input.Model {
			exists = true
			break
		}
	}
	if !exists {
		writeJSON(w, 404, map[string]string{"error": "model is not installed in Ollama"})
		return
	}
	state.Lock()
	workload := Workload{ID: fmt.Sprintf("ollama-%d", time.Now().UnixNano()), Name: input.Model, Kind: "ollama-inference", Pool: "local-ollama", Status: "running", Checkpoint: ollamaBaseURL(), CreatedAt: time.Now()}
	state.Workloads = append(state.Workloads, workload)
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "inference.deployed", map[string]any{"workload_id": workload.ID, "model": input.Model, "runtime": "ollama"})
	publishEvent("inference.deployed", workload)
	writeJSON(w, 201, map[string]any{"workload": workload, "endpoint": "/api/v1/inference/generate"})
}
func generateHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 16<<20))
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "cannot read request"})
		return
	}
	var input map[string]any
	if json.Unmarshal(body, &input) != nil || input["model"] == nil || input["prompt"] == nil {
		writeJSON(w, 400, map[string]string{"error": "model and prompt are required"})
		return
	}
	model, _ := input["model"].(string)
	state.Lock()
	exists := false
	for _, workload := range state.Workloads {
		if workload.Kind == "ollama-inference" && workload.Name == model && workload.Status == "running" {
			exists = true
			break
		}
	}
	if !exists {
		state.Workloads = append(state.Workloads, Workload{ID: fmt.Sprintf("ollama-%d", time.Now().UnixNano()), Name: model, Kind: "ollama-inference", Pool: "local-ollama", Status: "running", Checkpoint: ollamaBaseURL(), CreatedAt: time.Now()})
		persistStateLocked()
	}
	state.Unlock()
	input["stream"] = false
	payload, _ := json.Marshal(input)
	response, err := (&http.Client{Timeout: 5 * time.Minute}).Post(ollamaBaseURL()+"/api/generate", "application/json", bytes.NewReader(payload))
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "Ollama generation failed: " + err.Error()})
		return
	}
	defer response.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(response.Body, 32<<20))
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(data)
}
func staticHandler(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/")
	if path == "" {
		path = "index.html"
	}
	if path != "index.html" && path != "login.html" && path != "login-fabric.png" && path != "app.js" && path != "styles.css" && path != "premium.css" {
		http.NotFound(w, r)
		return
	}
	if path == "index.html" && authEnabled() {
		if _, ok := currentUser(r); !ok {
			http.Redirect(w, r, "/login.html", http.StatusSeeOther)
			return
		}
	}
	if path == "login.html" && authEnabled() {
		if _, ok := currentUser(r); ok {
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
	}
	content, err := ui.ReadFile(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if strings.HasSuffix(path, ".js") {
		w.Header().Set("Content-Type", "application/javascript")
	} else if strings.HasSuffix(path, ".css") {
		w.Header().Set("Content-Type", "text/css")
	} else if strings.HasSuffix(path, ".png") {
		w.Header().Set("Content-Type", "image/png")
	} else {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
	}
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(content)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}

func main() {
	initializeInfrastructure()
	controllerContext, stopController := context.WithCancel(context.Background())
	defer stopController()
	startKubernetesReconciler(controllerContext)
	if err := initializeAuth(); err != nil {
		fmt.Printf("Authentication initialization failed: %v\n", err)
	} else if authEnabled() {
		fmt.Println("Local authentication ready")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	siteID := strings.TrimSpace(os.Getenv("OPENMYCELIUM_SITE_ID"))
	if siteID == "" {
		siteID = "OM-LOCAL"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "healthy", "edition": "developer", "siteId": siteID})
	})
	mux.HandleFunc("/api/v1/ready", func(w http.ResponseWriter, r *http.Request) {
		if database == nil || database.Ping(r.Context()) != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not ready", "dependency": "postgres", "siteId": siteID})
			return
		}
		if eventBus == nil || !eventBus.IsConnected() {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not ready", "dependency": "nats", "siteId": siteID})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready", "siteId": siteID})
	})
	mux.HandleFunc("/api/v1/auth/status", authStatusHandler)
	mux.HandleFunc("/api/v1/auth/login", loginHandler)
	mux.HandleFunc("/api/v1/auth/signup", signupHandler)
	mux.HandleFunc("/api/v1/auth/me", meHandler)
	mux.HandleFunc("/api/v1/auth/logout", logoutHandler)
	mux.HandleFunc("/api/v1/discovery/scan", requireOperator(scanHandler))
	mux.HandleFunc("/api/v1/discovery/import", requireAgentOrOperator(importHostHandler))
	mux.HandleFunc("/api/v1/clusters", requireOperator(clustersHandler))
	mux.HandleFunc("/api/v1/clusters/", requireOperator(clusterResourceHandler))
	mux.HandleFunc("/api/v1/workspaces", requireOperator(workspacesHandler))
	mux.HandleFunc("/api/v1/workspaces/", requireOperator(workspaceResourceHandler))
	mux.HandleFunc("/api/v1/pools", requireOperator(managedPoolsHandler))
	mux.HandleFunc("/api/v1/pools/", requireOperator(poolResourceHandler))
	mux.HandleFunc("/api/v1/queues", requireOperator(queuesHandler))
	mux.HandleFunc("/api/v1/queues/", requireOperator(queueResourceHandler))
	mux.HandleFunc("/api/v1/fabric/capabilities", requireOperator(fabricCapabilitiesHandler))
	mux.HandleFunc("/api/v1/fabric/qualification", requireOperator(fabricQualificationHandler))
	mux.HandleFunc("/api/v1/fabric/profiles", requireOperator(fabricProfilesHandler))
	mux.HandleFunc("/api/v1/fabric/profiles/", requireOperator(fabricProfileResourceHandler))
	mux.HandleFunc("/api/v1/fabric/plans", requireOperator(fabricPlansHandler))
	mux.HandleFunc("/api/v1/fabric/plans/", requireOperator(fabricPlanResourceHandler))
	mux.HandleFunc("/api/v1/fabric/execution-plans", requireOperator(executionPlansHandler))
	mux.HandleFunc("/api/v1/fabric/execution-plans/", requireOperator(executionPlanResourceHandler))
	mux.HandleFunc("/api/v1/lab/catalog", requireAuth(labCatalogHandler))
	mux.HandleFunc("/api/v1/lab/assets", requireOperator(labAssetsHandler))
	mux.HandleFunc("/api/v1/lab/assets/", requireOperator(labAssetResourceHandler))
	mux.HandleFunc("/api/v1/lab/simulate", requireOperator(labSimulationHandler))
	mux.HandleFunc("/api/v1/integrations", requireOperator(integrationsHandler))
	mux.HandleFunc("/api/v1/integrations/", requireOperator(integrationResourceHandler))
	mux.HandleFunc("/api/v1/agents", requireOperator(agentsHandler))
	mux.HandleFunc("/api/v1/agents/", requireOperator(agentResourceHandler))
	mux.HandleFunc("/api/v1/agent-flows", requireOperator(agentFlowsHandler))
	mux.HandleFunc("/api/v1/agent-flows/", requireOperator(agentFlowResourceHandler))
	mux.HandleFunc("/api/v1/agent-runs", requireOperator(agentRunsHandler))
	mux.HandleFunc("/api/v1/agent-runs/", requireOperator(agentRunResourceHandler))
	mux.HandleFunc("/api/v1/agent-tools", requireOperator(agentToolsHandler))
	mux.HandleFunc("/api/v1/agent-memory", requireOperator(agentMemoryHandler))
	mux.HandleFunc("/api/v1/agent-approvals", requireOperator(agentApprovalsHandler))
	mux.HandleFunc("/api/v1/agent-evaluations", requireOperator(agentEvaluationsHandler))
	mux.HandleFunc("/api/v1/agent-orchestration", requireOperator(agentOrchestrationHandler))
	mux.HandleFunc("/api/v1/users", requireAdmin(usersHandler))
	mux.HandleFunc("/api/v1/users/", requireAdmin(userResourceHandler))
	mux.HandleFunc("/api/v1/audit", requireAdmin(auditEventsHandler))
	mux.HandleFunc("/api/v1/observability/summary", requireAuth(observabilityHandler))
	mux.HandleFunc("/api/v1/operations/mlops", requireAuth(mlopsHandler))
	mux.HandleFunc("/api/v1/operations/aiops", requireAuth(aiopsHandler))
	mux.HandleFunc("/metrics", metricsHandler)
	mux.HandleFunc("/api/v1/workloads", requireOperator(workloadsHandler))
	mux.HandleFunc("/api/v1/workloads/", requireOperator(workloadActionHandler))
	mux.HandleFunc("/api/v1/workflows", requireOperator(workflowsHandler))
	mux.HandleFunc("/api/v1/settings", requireAdmin(settingsHandler))
	mux.HandleFunc("/api/v1/models", requireAuth(modelsHandler))
	mux.HandleFunc("/api/v1/model-catalog", requireOperator(modelCatalogHandler))
	mux.HandleFunc("/api/v1/model-catalog/", requireOperator(modelCatalogResourceHandler))
	mux.HandleFunc("/api/v1/model-catalog-sync/ollama", requireOperator(syncOllamaCatalogHandler))
	mux.HandleFunc("/api/v1/manifests/", requireOperator(manifestDeploymentHandler))
	mux.HandleFunc("/api/v1/inference/deploy", requireOperator(deployInferenceHandler))
	mux.HandleFunc("/api/v1/inference/generate", requireAuth(generateHandler))
	mux.HandleFunc("/", staticHandler)
	fmt.Printf("OpenMycelium Developer Edition listening on http://127.0.0.1:%s\n", port)
	server := &http.Server{Addr: ":" + port, Handler: securityHeaders(mux), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 15 * time.Minute, IdleTimeout: 60 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		panic(err)
	}
}
