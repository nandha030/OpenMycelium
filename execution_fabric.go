package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"
)

type FabricBenchmarkProfile struct {
	ID                string            `json:"id"`
	Name              string            `json:"name"`
	ClusterID         string            `json:"clusterId"`
	Node              string            `json:"node,omitempty"`
	Vendor            string            `json:"vendor"`
	Runtime           string            `json:"runtime"`
	Model             string            `json:"model"`
	Precision         string            `json:"precision"`
	SequenceLength    int               `json:"sequenceLength"`
	MicroBatch        int               `json:"microBatch"`
	TokensPerSecond   float64           `json:"tokensPerSecond"`
	LayerMilliseconds float64           `json:"layerMilliseconds"`
	P2PBandwidthGBps  float64           `json:"p2pBandwidthGBps"`
	AllReduceGBps     float64           `json:"allReduceGBps"`
	MemoryGiB         float64           `json:"memoryGiB"`
	PowerWatts        float64           `json:"powerWatts,omitempty"`
	Source            string            `json:"source"`
	Status            string            `json:"status"`
	Metadata          map[string]string `json:"metadata,omitempty"`
	CreatedAt         time.Time         `json:"createdAt"`
	UpdatedAt         time.Time         `json:"updatedAt"`
}

type ExecutionPlanRequest struct {
	Name               string            `json:"name"`
	ClusterID          string            `json:"clusterId"`
	Model              string            `json:"model"`
	ParametersB        float64           `json:"parametersB"`
	Layers             int               `json:"layers"`
	SequenceLength     int               `json:"sequenceLength"`
	GlobalBatch        int               `json:"globalBatch"`
	Precision          string            `json:"precision"`
	WorkloadKind       string            `json:"workloadKind"`
	Strategy           string            `json:"strategy"`
	ZeroStage          int               `json:"zeroStage"`
	MaxDevices         int               `json:"maxDevices"`
	MaxPipelineStages  int               `json:"maxPipelineStages"`
	RequireRDMA        bool              `json:"requireRdma"`
	AllowCPUFallback   bool              `json:"allowCpuFallback"`
	DynamicMicroBatch  bool              `json:"dynamicMicroBatch"`
	Vendors            []string          `json:"vendors,omitempty"`
	PreferredTransport string            `json:"preferredTransport,omitempty"`
	DefaultMemoryGiB   float64           `json:"defaultMemoryGiB,omitempty"`
	Objective          string            `json:"objective,omitempty"`
	VendorImages       map[string]string `json:"vendorImages,omitempty"`
}

type ExecutionGroup struct {
	ID               string   `json:"id"`
	Vendor           string   `json:"vendor"`
	Runtime          string   `json:"runtime"`
	Model            string   `json:"model"`
	Resource         string   `json:"resource"`
	Nodes            []string `json:"nodes"`
	Devices          int      `json:"devices"`
	NativeBackend    string   `json:"nativeBackend"`
	ProfileSource    string   `json:"profileSource"`
	ThroughputWeight float64  `json:"throughputWeight"`
	MemoryGiB        float64  `json:"memoryGiB"`
	PowerWatts       float64  `json:"powerWatts,omitempty"`
	Image            string   `json:"image,omitempty"`
	Layers           int      `json:"layers"`
	MicroBatch       int      `json:"microBatch"`
}

type ParallelContract struct {
	Pipeline int    `json:"pipeline"`
	Tensor   int    `json:"tensor"`
	Data     int    `json:"data"`
	Expert   int    `json:"expert"`
	Zero     int    `json:"zeroStage"`
	Mode     string `json:"mode"`
}

type ExecutionEstimate struct {
	ModelStateGiB          float64 `json:"modelStateGiB"`
	MinimumDeviceMemoryGiB float64 `json:"minimumDeviceMemoryGiB"`
	PredictedTokensSecond  float64 `json:"predictedTokensPerSecond"`
	PredictedIterationMS   float64 `json:"predictedIterationMs"`
	CommunicationPenalty   float64 `json:"communicationPenalty"`
	Confidence             string  `json:"confidence"`
}

type ExecutionPlan struct {
	ID               string               `json:"id"`
	Name             string               `json:"name"`
	ClusterID        string               `json:"clusterId"`
	Model            string               `json:"model"`
	Status           string               `json:"status"`
	Executable       bool                 `json:"executable"`
	Transport        string               `json:"transport"`
	TransportStatus  string               `json:"transportStatus"`
	RequiredAdapters []string             `json:"requiredAdapters,omitempty"`
	Groups           []ExecutionGroup     `json:"groups"`
	Parallel         ParallelContract     `json:"parallel"`
	Estimate         ExecutionEstimate    `json:"estimate"`
	Optimization     MyceliumOptimization `json:"optimization"`
	Environment      map[string]string    `json:"environment"`
	Warnings         []string             `json:"warnings"`
	CreatedAt        time.Time            `json:"createdAt"`
}

type fabricGroupKey struct {
	Vendor, Runtime, Model, Resource string
}

func normalizeProfile(profile *FabricBenchmarkProfile) error {
	profile.Name = strings.TrimSpace(profile.Name)
	profile.ClusterID = strings.TrimSpace(profile.ClusterID)
	profile.Node = strings.TrimSpace(profile.Node)
	profile.Vendor = strings.ToLower(strings.TrimSpace(profile.Vendor))
	profile.Runtime = strings.ToLower(strings.TrimSpace(profile.Runtime))
	profile.Model = strings.TrimSpace(profile.Model)
	profile.Precision = strings.ToLower(strings.TrimSpace(profile.Precision))
	profile.Source = strings.ToLower(strings.TrimSpace(profile.Source))
	if profile.Name == "" || profile.ClusterID == "" || profile.Vendor == "" || profile.Runtime == "" || profile.Model == "" {
		return fmt.Errorf("name, clusterId, vendor, runtime, and model are required")
	}
	if profile.TokensPerSecond <= 0 && profile.LayerMilliseconds <= 0 && profile.P2PBandwidthGBps <= 0 && profile.AllReduceGBps <= 0 {
		return fmt.Errorf("at least one positive benchmark measurement is required")
	}
	if profile.SequenceLength < 0 || profile.MicroBatch < 0 || profile.MemoryGiB < 0 || profile.PowerWatts < 0 {
		return fmt.Errorf("profile measurements cannot be negative")
	}
	if profile.Precision == "" {
		profile.Precision = "bf16"
	}
	if profile.Source == "" {
		profile.Source = "operator-supplied"
	}
	if profile.Source != "measured" && profile.Source != "operator-supplied" && profile.Source != "imported" {
		return fmt.Errorf("source must be measured, operator-supplied, or imported")
	}
	profile.Status = "ready"
	if profile.Metadata == nil {
		profile.Metadata = map[string]string{}
	}
	return nil
}

func loadFabricProfiles(ctx context.Context, clusterID string) ([]FabricBenchmarkProfile, error) {
	query := `SELECT profile FROM fabric_profiles`
	args := []any{}
	if clusterID != "" {
		query += ` WHERE cluster_id=$1`
		args = append(args, clusterID)
	}
	query += ` ORDER BY updated_at DESC`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	profiles := []FabricBenchmarkProfile{}
	for rows.Next() {
		var payload []byte
		var profile FabricBenchmarkProfile
		if rows.Scan(&payload) == nil && json.Unmarshal(payload, &profile) == nil {
			profiles = append(profiles, profile)
		}
	}
	return profiles, rows.Err()
}

func profileForGroup(group ExecutionGroup, profiles []FabricBenchmarkProfile, precision string) (FabricBenchmarkProfile, bool) {
	var best FabricBenchmarkProfile
	bestScore := -1
	for _, profile := range profiles {
		if !strings.EqualFold(profile.Vendor, group.Vendor) || !strings.EqualFold(profile.Runtime, group.Runtime) {
			continue
		}
		score := 1
		if strings.EqualFold(profile.Model, group.Model) {
			score += 4
		}
		if strings.EqualFold(profile.Precision, precision) {
			score += 2
		}
		if profile.Source == "measured" {
			score++
		}
		if score > bestScore {
			best, bestScore = profile, score
		}
	}
	return best, bestScore >= 0
}

func bytesPerParameter(precision string, training bool) float64 {
	weightBytes := 2.0
	switch strings.ToLower(precision) {
	case "fp32":
		weightBytes = 4
	case "fp8", "int8":
		weightBytes = 1
	case "int4", "nf4":
		weightBytes = 0.5
	}
	if training {
		// Weights, gradients, and mixed-precision Adam states before ZeRO sharding.
		return weightBytes + 2 + 8
	}
	return weightBytes
}

func validateExecutionRequest(request *ExecutionPlanRequest) error {
	request.Name = strings.TrimSpace(request.Name)
	request.ClusterID = strings.TrimSpace(request.ClusterID)
	request.Model = strings.TrimSpace(request.Model)
	request.Precision = strings.ToLower(strings.TrimSpace(request.Precision))
	request.WorkloadKind = strings.ToLower(strings.TrimSpace(request.WorkloadKind))
	request.Strategy = strings.ToLower(strings.TrimSpace(request.Strategy))
	request.PreferredTransport = strings.ToLower(strings.TrimSpace(request.PreferredTransport))
	request.Objective = strings.ToLower(strings.TrimSpace(request.Objective))
	if request.Name == "" || request.ClusterID == "" || request.Model == "" || request.ParametersB <= 0 || request.Layers < 1 {
		return fmt.Errorf("name, clusterId, model, positive parametersB, and layers are required")
	}
	if request.SequenceLength <= 0 {
		request.SequenceLength = 2048
	}
	if request.GlobalBatch <= 0 {
		request.GlobalBatch = 1
	}
	if request.Precision == "" {
		request.Precision = "bf16"
	}
	if request.WorkloadKind == "" {
		request.WorkloadKind = "training"
	}
	if request.WorkloadKind != "training" && request.WorkloadKind != "finetuning" && request.WorkloadKind != "inference" {
		return fmt.Errorf("workloadKind must be training, finetuning, or inference")
	}
	if request.Strategy == "" {
		request.Strategy = "auto"
	}
	if request.Strategy != "auto" && request.Strategy != "pipeline" && request.Strategy != "zero" && request.Strategy != "data" {
		return fmt.Errorf("strategy must be auto, pipeline, zero, or data")
	}
	if request.ZeroStage < 0 || request.ZeroStage > 3 {
		return fmt.Errorf("zeroStage must be from 0 through 3")
	}
	if request.MaxDevices == 0 {
		request.MaxDevices = 64
	}
	if request.MaxDevices < 1 || request.MaxDevices > 4096 {
		return fmt.Errorf("maxDevices must be from 1 through 4096")
	}
	if request.MaxPipelineStages == 0 {
		request.MaxPipelineStages = 16
	}
	if request.MaxPipelineStages < 1 || request.MaxPipelineStages > 128 {
		return fmt.Errorf("maxPipelineStages must be from 1 through 128")
	}
	if request.DefaultMemoryGiB < 0 {
		return fmt.Errorf("defaultMemoryGiB cannot be negative")
	}
	if request.Objective == "" {
		request.Objective = "throughput"
	}
	if request.Objective != "throughput" && request.Objective != "balanced" && request.Objective != "efficiency" {
		return fmt.Errorf("objective must be throughput, balanced, or efficiency")
	}
	if request.PreferredTransport != "" && request.PreferredTransport != "auto" && request.PreferredTransport != "hetccl" && request.PreferredTransport != "hetccl-tcp" && request.PreferredTransport != "device-direct" && request.PreferredTransport != "gloo" && request.PreferredTransport != "cpu-forwarding" {
		return fmt.Errorf("preferredTransport must be auto, hetccl, hetccl-tcp, device-direct, or gloo")
	}
	images := map[string]string{}
	for vendor, image := range request.VendorImages {
		vendor = strings.ToLower(strings.TrimSpace(vendor))
		image = strings.TrimSpace(image)
		if vendor != "nvidia" && vendor != "amd" && vendor != "intel" {
			return fmt.Errorf("vendorImages keys must be nvidia, amd, or intel")
		}
		if image != "" {
			images[vendor] = image
		}
	}
	request.VendorImages = images
	return nil
}

func allFabricNodesRDMA(nodes []ClusterNode, selected map[string]bool) bool {
	seen := false
	for _, node := range nodes {
		if !selected[node.Name] {
			continue
		}
		seen = true
		if !node.Fabric.RDMA {
			return false
		}
	}
	return seen
}

func allFabricNodesBackend(nodes []ClusterNode, selected map[string]bool, backend string) bool {
	seen := false
	for _, node := range nodes {
		if !selected[node.Name] {
			continue
		}
		seen = true
		available := false
		for _, candidate := range node.Fabric.Backends {
			if candidate == backend {
				available = true
				break
			}
		}
		if !available {
			return false
		}
	}
	return seen
}

func nativeBackend(runtime string) string {
	switch runtime {
	case "cuda":
		return "nccl"
	case "rocm":
		return "rccl"
	case "oneapi":
		return "oneccl"
	default:
		return "gloo"
	}
}

func buildExecutionGroups(nodes []ClusterNode, request ExecutionPlanRequest, profiles []FabricBenchmarkProfile) ([]ExecutionGroup, []string) {
	filters := map[string]bool{}
	for _, vendor := range request.Vendors {
		filters[strings.ToLower(strings.TrimSpace(vendor))] = true
	}
	grouped := map[fabricGroupKey]*ExecutionGroup{}
	warnings := []string{}
	remaining := request.MaxDevices
	for _, node := range nodes {
		if remaining <= 0 || !node.Ready || !node.Schedulable {
			continue
		}
		for _, accelerator := range node.Accelerators {
			if remaining <= 0 || accelerator.Available <= 0 {
				continue
			}
			vendor := strings.ToLower(accelerator.Vendor)
			if len(filters) > 0 && !filters[vendor] {
				continue
			}
			count := int(accelerator.Available)
			if count > remaining {
				count = remaining
			}
			key := fabricGroupKey{Vendor: vendor, Runtime: accelerator.Runtime, Model: accelerator.Model, Resource: accelerator.Resource}
			group := grouped[key]
			if group == nil {
				group = &ExecutionGroup{ID: fmt.Sprintf("group-%d", len(grouped)+1), Vendor: vendor, Runtime: accelerator.Runtime, Model: accelerator.Model, Resource: accelerator.Resource, NativeBackend: nativeBackend(accelerator.Runtime), Nodes: []string{}, Devices: 0}
				grouped[key] = group
			}
			group.Devices += count
			group.Nodes = append(group.Nodes, node.Name)
			remaining -= count
		}
	}
	groups := make([]ExecutionGroup, 0, len(grouped))
	for _, group := range grouped {
		profile, ok := profileForGroup(*group, profiles, request.Precision)
		if ok {
			group.ThroughputWeight = profile.TokensPerSecond
			if group.ThroughputWeight <= 0 && profile.LayerMilliseconds > 0 {
				group.ThroughputWeight = 1000 / profile.LayerMilliseconds
			}
			group.MemoryGiB = profile.MemoryGiB
			group.PowerWatts = profile.PowerWatts
			group.ProfileSource = profile.Source
		} else {
			group.ThroughputWeight = 1
			group.MemoryGiB = request.DefaultMemoryGiB
			group.ProfileSource = "conservative-default"
			warnings = append(warnings, fmt.Sprintf("%s %s has no matching benchmark profile; equal conservative weight is used", group.Vendor, group.Model))
		}
		if group.ThroughputWeight <= 0 {
			group.ThroughputWeight = 1
		}
		group.Image = request.VendorImages[group.Vendor]
		groups = append(groups, *group)
	}
	sort.Slice(groups, func(i, j int) bool {
		if groups[i].Vendor != groups[j].Vendor {
			return groups[i].Vendor < groups[j].Vendor
		}
		return groups[i].Model < groups[j].Model
	})
	return groups, warnings
}

func distributeInteger(total int, weights []float64) []int {
	result := make([]int, len(weights))
	if total <= 0 || len(weights) == 0 {
		return result
	}
	sum := 0.0
	for _, weight := range weights {
		if weight > 0 {
			sum += weight
		}
	}
	if sum == 0 {
		sum = float64(len(weights))
		for index := range weights {
			weights[index] = 1
		}
	}
	assigned := 0
	fractions := make([]float64, len(weights))
	for index, weight := range weights {
		exact := float64(total) * weight / sum
		result[index] = int(math.Floor(exact))
		fractions[index] = exact - float64(result[index])
		assigned += result[index]
	}
	for assigned < total {
		best := 0
		for index := 1; index < len(fractions); index++ {
			if fractions[index] > fractions[best] {
				best = index
			}
		}
		result[best]++
		fractions[best] = -1
		assigned++
	}
	return result
}

func selectExecutionTransport(request ExecutionPlanRequest, groups []ExecutionGroup, nodes []ClusterNode) (string, string, []string, []string) {
	vendors, selectedNodes := map[string]bool{}, map[string]bool{}
	for _, group := range groups {
		vendors[group.Vendor] = true
		for _, node := range group.Nodes {
			selectedNodes[node] = true
		}
	}
	if len(vendors) <= 1 {
		return groups[0].NativeBackend, "ready", nil, nil
	}
	preferred := request.PreferredTransport
	if preferred == "" || preferred == "auto" {
		if allFabricNodesBackend(nodes, selectedNodes, "hetccl") {
			preferred = "hetccl"
		} else if allFabricNodesBackend(nodes, selectedNodes, "device-direct") {
			preferred = "device-direct"
		} else if request.AllowCPUFallback {
			preferred = "gloo"
		} else {
			preferred = "hetccl"
		}
	}
	switch preferred {
	case "hetccl", "device-direct":
		missing := []string{}
		if !allFabricNodesBackend(nodes, selectedNodes, preferred) {
			missing = append(missing, preferred+" runtime adapter on every selected node")
		}
		if !allFabricNodesRDMA(nodes, selectedNodes) {
			missing = append(missing, "RDMA on every selected node")
		}
		if len(missing) > 0 {
			return preferred, "blocked", missing, []string{"direct cross-vendor execution is blocked until the runtime and RDMA prerequisites are advertised by inventory"}
		}
		return preferred, "ready", nil, nil
	case "hetccl-tcp":
		if request.RequireRDMA {
			return preferred, "blocked", []string{"device-direct HetCCL or another qualified RDMA adapter"}, []string{"HetCCL TCP host staging cannot satisfy requireRdma=true"}
		}
		return preferred, "ready", nil, []string{"HetCCL TCP centralizes reductions and stages tensors through host memory; use it for compatibility and qualification, not as a device-direct performance claim"}
	case "gloo", "cpu-forwarding":
		if request.RequireRDMA {
			return "gloo", "blocked", []string{"RDMA cross-vendor runtime adapter"}, []string{"CPU forwarding cannot satisfy requireRdma=true"}
		}
		return "gloo", "ready", nil, []string{"cross-vendor tensors are staged through host memory; benchmark before production use"}
	default:
		return preferred, "blocked", []string{"supported transport: hetccl, hetccl-tcp, device-direct, or gloo"}, nil
	}
}

func buildExecutionPlan(id string, request ExecutionPlanRequest, nodes []ClusterNode, profiles []FabricBenchmarkProfile) (ExecutionPlan, error) {
	if err := validateExecutionRequest(&request); err != nil {
		return ExecutionPlan{}, err
	}
	groups, warnings := buildExecutionGroups(nodes, request, profiles)
	if len(groups) == 0 {
		return ExecutionPlan{}, fmt.Errorf("no ready accelerator capacity satisfies this execution request")
	}
	transport, transportStatus, required, transportWarnings := selectExecutionTransport(request, groups, nodes)
	warnings = append(warnings, transportWarnings...)
	training := request.WorkloadKind != "inference"
	modelStateGiB := request.ParametersB * 1e9 * bytesPerParameter(request.Precision, training) / gibibyte
	if training && request.ZeroStage > 0 {
		devices := 0
		for _, group := range groups {
			devices += group.Devices
		}
		if devices > 0 {
			modelStateGiB /= float64(devices)
		}
	}
	groups, parallel, estimate, optimization, optimizationWarnings := compileMyceliumOptimization(request, groups, transport, modelStateGiB)
	warnings = append(warnings, optimizationWarnings...)
	executable := transportStatus == "ready"
	status := "ready"
	if !executable {
		status = "blocked"
	}
	plan := ExecutionPlan{
		ID: id, Name: request.Name, ClusterID: request.ClusterID, Model: request.Model, Status: status, Executable: executable,
		Transport: transport, TransportStatus: transportStatus, RequiredAdapters: required, Groups: groups, Parallel: parallel,
		Estimate: estimate, Optimization: optimization,
		Environment: map[string]string{
			"OPENMYCELIUM_EXECUTION_PLAN_ID": id, "OPENMYCELIUM_TRANSPORT": transport, "OPENMYCELIUM_PARALLEL_MODE": parallel.Mode,
			"OPENMYCELIUM_ALGORITHM": myceliumAlgorithmName, "OPENMYCELIUM_ALGORITHM_VERSION": myceliumAlgorithmVersion,
			"OPENMYCELIUM_PIPELINE_PARALLEL": fmt.Sprint(parallel.Pipeline), "OPENMYCELIUM_TENSOR_PARALLEL": fmt.Sprint(parallel.Tensor),
			"OPENMYCELIUM_DATA_PARALLEL": fmt.Sprint(parallel.Data), "OPENMYCELIUM_ZERO_STAGE": fmt.Sprint(parallel.Zero),
		}, Warnings: warnings, CreatedAt: time.Now().UTC(),
	}
	plan.Warnings = append(plan.Warnings, "the execution plan coordinates separate device memory domains and does not claim cross-vendor pointer coherence")
	return plan, nil
}

func loadExecutionPlan(ctx context.Context, id string) (ExecutionPlan, error) {
	var payload []byte
	if err := database.QueryRow(ctx, `SELECT plan FROM execution_plans WHERE id=$1`, id).Scan(&payload); err != nil {
		return ExecutionPlan{}, err
	}
	var plan ExecutionPlan
	if err := json.Unmarshal(payload, &plan); err != nil {
		return ExecutionPlan{}, err
	}
	return plan, nil
}

func hydrateExecutionContract(ctx context.Context, workload *Workload) error {
	if workload.ExecutionPlanID == "" {
		return nil
	}
	if database == nil {
		return fmt.Errorf("PostgreSQL is required to load execution plan %s", workload.ExecutionPlanID)
	}
	plan, err := loadExecutionPlan(ctx, workload.ExecutionPlanID)
	if err != nil {
		return fmt.Errorf("load execution plan %s: %w", workload.ExecutionPlanID, err)
	}
	if plan.ClusterID != workload.ClusterID {
		return fmt.Errorf("execution plan belongs to a different cluster")
	}
	if !plan.Executable {
		return fmt.Errorf("execution plan is blocked: missing %s", strings.Join(plan.RequiredAdapters, ", "))
	}
	if len(plan.Groups) > 1 && !workloadUsesJob(workload.Kind) {
		return fmt.Errorf("multi-group execution plans currently require a training, finetuning, or batch Job; heterogeneous pipeline serving requires a serving-runtime adapter")
	}
	payload, err := json.Marshal(plan)
	if err != nil {
		return err
	}
	workload.ExecutionPlan = string(payload)
	workload.ExecutionTransport = plan.Transport
	return nil
}

func executionGroupsFromWorkload(workload Workload) ([]ExecutionGroup, error) {
	if workload.ExecutionPlanID == "" {
		return nil, nil
	}
	var plan ExecutionPlan
	if err := json.Unmarshal([]byte(workload.ExecutionPlan), &plan); err != nil {
		return nil, fmt.Errorf("decode execution plan contract: %w", err)
	}
	if !plan.Executable {
		return nil, fmt.Errorf("execution plan %s is not executable", plan.ID)
	}
	return plan.Groups, nil
}

func fabricQualificationHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	clusterID := strings.TrimSpace(r.URL.Query().Get("clusterId"))
	if clusterID == "" {
		writeJSON(w, 400, map[string]string{"error": "clusterId is required"})
		return
	}
	nodes, err := loadClusterNodes(r.Context(), clusterID)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot load cluster inventory"})
		return
	}
	profiles, _ := loadFabricProfiles(r.Context(), clusterID)
	rdma, qualified, accelerators := 0, 0, 0
	backends := map[string]int{}
	for _, node := range nodes {
		if node.Fabric.RDMA {
			rdma++
		}
		if node.Fabric.Qualified {
			qualified++
		}
		for _, accelerator := range node.Accelerators {
			accelerators += int(accelerator.Available)
		}
		for _, backend := range node.Fabric.Backends {
			backends[backend]++
		}
	}
	writeJSON(w, 200, map[string]any{"clusterId": clusterID, "nodes": nodes, "summary": map[string]any{"nodes": len(nodes), "qualifiedNodes": qualified, "rdmaNodes": rdma, "availableAccelerators": accelerators, "profiles": len(profiles)}, "backends": backends, "qualification": "inventory capabilities must be confirmed by measured profiles before production admission"})
}

func fabricProfilesHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		profiles, err := loadFabricProfiles(r.Context(), r.URL.Query().Get("clusterId"))
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list fabric profiles"})
			return
		}
		writeJSON(w, 200, map[string]any{"profiles": profiles})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var profile FabricBenchmarkProfile
	if err := decode(r, &profile); err != nil {
		writeJSON(w, 400, map[string]string{"error": "valid profile JSON is required"})
		return
	}
	if err := normalizeProfile(&profile); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	profile.ID = "profile_" + randomToken(9)
	profile.CreatedAt, profile.UpdatedAt = time.Now().UTC(), time.Now().UTC()
	payload, _ := json.Marshal(profile)
	if _, err := database.Exec(r.Context(), `INSERT INTO fabric_profiles(id,name,cluster_id,profile,source,status,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, profile.ID, profile.Name, profile.ClusterID, payload, profile.Source, profile.Status, profile.CreatedAt, profile.UpdatedAt); err != nil {
		writeJSON(w, 409, map[string]string{"error": "fabric profile could not be stored"})
		return
	}
	auditRequest(r, "fabric.profile_created", map[string]any{"profile_id": profile.ID, "cluster_id": profile.ClusterID, "vendor": profile.Vendor, "source": profile.Source})
	publishEvent("fabric.profile_created", profile)
	writeJSON(w, 201, profile)
}

func fabricProfileResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/fabric/profiles/"), "/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "fabric profile not found"})
		return
	}
	if r.Method != http.MethodDelete {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	result, err := database.Exec(r.Context(), `DELETE FROM fabric_profiles WHERE id=$1`, id)
	if err != nil || result.RowsAffected() == 0 {
		writeJSON(w, 404, map[string]string{"error": "fabric profile not found"})
		return
	}
	auditRequest(r, "fabric.profile_deleted", map[string]any{"profile_id": id})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func executionPlansHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		query, args := `SELECT plan FROM execution_plans`, []any{}
		if clusterID := strings.TrimSpace(r.URL.Query().Get("clusterId")); clusterID != "" {
			query += ` WHERE cluster_id=$1`
			args = append(args, clusterID)
		}
		query += ` ORDER BY created_at DESC`
		rows, err := database.Query(r.Context(), query, args...)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list execution plans"})
			return
		}
		defer rows.Close()
		plans := []ExecutionPlan{}
		for rows.Next() {
			var payload []byte
			var plan ExecutionPlan
			if rows.Scan(&payload) == nil && json.Unmarshal(payload, &plan) == nil {
				plans = append(plans, plan)
			}
		}
		writeJSON(w, 200, map[string]any{"plans": plans})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	var request ExecutionPlanRequest
	if decode(r, &request) != nil {
		writeJSON(w, 400, map[string]string{"error": "valid execution plan JSON is required"})
		return
	}
	if err := validateExecutionRequest(&request); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	nodes, err := loadClusterNodes(r.Context(), request.ClusterID)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot load cluster inventory"})
		return
	}
	profiles, err := loadFabricProfiles(r.Context(), request.ClusterID)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot load fabric profiles"})
		return
	}
	plan, err := buildExecutionPlan("exec_"+randomToken(9), request, nodes, profiles)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": err.Error()})
		return
	}
	requestPayload, _ := json.Marshal(request)
	planPayload, _ := json.Marshal(plan)
	if _, err = database.Exec(r.Context(), `INSERT INTO execution_plans(id,name,cluster_id,request,plan,status,executable,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, plan.ID, plan.Name, plan.ClusterID, requestPayload, planPayload, plan.Status, plan.Executable, plan.CreatedAt); err != nil {
		writeJSON(w, 409, map[string]string{"error": "execution plan could not be stored"})
		return
	}
	auditRequest(r, "fabric.execution_plan_created", map[string]any{"execution_plan_id": plan.ID, "cluster_id": plan.ClusterID, "transport": plan.Transport, "executable": plan.Executable})
	publishEvent("fabric.execution_plan_created", plan)
	writeJSON(w, 201, plan)
}

func executionPlanResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/fabric/execution-plans/"), "/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "execution plan not found"})
		return
	}
	if r.Method == http.MethodGet {
		plan, err := loadExecutionPlan(r.Context(), id)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "execution plan not found"})
			return
		}
		writeJSON(w, 200, plan)
		return
	}
	if r.Method == http.MethodDelete {
		state.RLock()
		for _, workload := range state.Workloads {
			if workload.ExecutionPlanID == id {
				state.RUnlock()
				writeJSON(w, 409, map[string]string{"error": "delete workloads using this execution plan first"})
				return
			}
		}
		state.RUnlock()
		result, err := database.Exec(r.Context(), `DELETE FROM execution_plans WHERE id=$1`, id)
		if err != nil || result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "execution plan not found"})
			return
		}
		auditRequest(r, "fabric.execution_plan_deleted", map[string]any{"execution_plan_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
}
