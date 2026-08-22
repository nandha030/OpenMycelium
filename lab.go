package main

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"sigs.k8s.io/yaml"
)

type LabDeviceProfile struct {
	ID            string  `json:"id"`
	Vendor        string  `json:"vendor"`
	Runtime       string  `json:"runtime"`
	Model         string  `json:"model"`
	Resource      string  `json:"resource"`
	MemoryGiB     float64 `json:"memoryGiB"`
	ComputeTFLOPS float64 `json:"computeTflops"`
	MemoryGBps    float64 `json:"memoryGBps"`
	PowerWatts    float64 `json:"powerWatts"`
	HourlyCost    float64 `json:"hourlyCost"`
	Evidence      string  `json:"evidence"`
}

type LabDeviceGroup struct {
	ProfileID     string  `json:"profileId"`
	Vendor        string  `json:"vendor"`
	Runtime       string  `json:"runtime"`
	Model         string  `json:"model"`
	Resource      string  `json:"resource"`
	Count         int     `json:"count"`
	MemoryGiB     float64 `json:"memoryGiB"`
	ComputeTFLOPS float64 `json:"computeTflops"`
	MemoryGBps    float64 `json:"memoryGBps"`
	PowerWatts    float64 `json:"powerWatts"`
	HourlyCost    float64 `json:"hourlyCost"`
	WorkerImage   string  `json:"workerImage,omitempty"`
}

type LabTopology struct {
	Name          string           `json:"name"`
	Groups        []LabDeviceGroup `json:"groups"`
	Link          string           `json:"link"`
	LinkGBps      float64          `json:"linkGBps"`
	LatencyUS     float64          `json:"latencyUs"`
	NICs          int              `json:"nics"`
	NUMADomains   int              `json:"numaDomains"`
	RDMA          bool             `json:"rdma"`
	GPUDirect     bool             `json:"gpuDirect"`
	DirectGMA     bool             `json:"directGma"`
	HetCCL        bool             `json:"hetccl"`
	DeviceDirect  bool             `json:"deviceDirect"`
	FaultScenario string           `json:"faultScenario"`
}

type LabModelRequest struct {
	Name           string  `json:"name"`
	ParametersB    float64 `json:"parametersB"`
	ActiveB        float64 `json:"activeB"`
	Layers         int     `json:"layers"`
	HiddenSize     int     `json:"hiddenSize"`
	PrecisionBits  int     `json:"precisionBits"`
	SequenceLength int     `json:"sequenceLength"`
	GlobalBatch    int     `json:"globalBatch"`
	WorkloadKind   string  `json:"workloadKind"`
	ZeroStage      int     `json:"zeroStage"`
	Objective      string  `json:"objective"`
}

type LabDatasetRequest struct {
	SizeGiB             float64 `json:"sizeGiB"`
	ReadGBps            float64 `json:"readGBps"`
	CheckpointInterval  int     `json:"checkpointInterval"`
	CheckpointRetention int     `json:"checkpointRetention"`
}

type LabSimulationRequest struct {
	Topology   LabTopology       `json:"topology"`
	Model      LabModelRequest   `json:"model"`
	Dataset    LabDatasetRequest `json:"dataset"`
	PayloadGiB float64           `json:"payloadGiB"`
	Transport  string            `json:"transport"`
	SaveResult bool              `json:"saveResult"`
	Experiment string            `json:"experiment,omitempty"`
}

type LabCommunicationEstimate struct {
	Transport      string  `json:"transport"`
	AllReduceMS    float64 `json:"allReduceMs"`
	EffectiveGBps  float64 `json:"effectiveGBps"`
	HostStagingGiB float64 `json:"hostStagingGiB"`
	Relative       float64 `json:"relative"`
	Evidence       string  `json:"evidence"`
}

type LabQualificationCheck struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail"`
}

type LabSimulationResult struct {
	Mode               string                     `json:"mode"`
	GeneratedAt        time.Time                  `json:"generatedAt"`
	Devices            int                        `json:"devices"`
	Vendors            int                        `json:"vendors"`
	TotalMemoryGiB     float64                    `json:"totalMemoryGiB"`
	TotalComputeTFLOPS float64                    `json:"totalComputeTflops"`
	PowerWatts         float64                    `json:"powerWatts"`
	HourlyCost         float64                    `json:"hourlyCost"`
	ModelWeightsGiB    float64                    `json:"modelWeightsGiB"`
	ActiveWeightsGiB   float64                    `json:"activeWeightsGiB"`
	KVCacheGiB         float64                    `json:"kvCacheGiB"`
	TrainingStateGiB   float64                    `json:"trainingStateGiB"`
	ActivationGiB      float64                    `json:"activationGiB"`
	StepTFLOPs         float64                    `json:"stepTflops"`
	PredictedStepMS    float64                    `json:"predictedStepMs"`
	CheckpointGiB      float64                    `json:"checkpointGiB"`
	CheckpointStoreGiB float64                    `json:"checkpointStoreGiB"`
	DatasetReadSeconds float64                    `json:"datasetReadSeconds"`
	Communications     []LabCommunicationEstimate `json:"communications"`
	Qualification      []LabQualificationCheck    `json:"qualification"`
	Plan               ExecutionPlan              `json:"plan"`
	Deployment         LabDeploymentPreview       `json:"deployment"`
	Warnings           []string                   `json:"warnings"`
}

type LabDeploymentPreview struct {
	Ready    bool     `json:"ready"`
	Command  string   `json:"command"`
	Manifest string   `json:"manifest"`
	Missing  []string `json:"missing,omitempty"`
}

type LabAsset struct {
	ID        string          `json:"id"`
	Kind      string          `json:"kind"`
	Name      string          `json:"name"`
	Status    string          `json:"status"`
	Payload   json.RawMessage `json:"payload"`
	CreatedBy string          `json:"createdBy,omitempty"`
	CreatedAt time.Time       `json:"createdAt"`
	UpdatedAt time.Time       `json:"updatedAt"`
}

var labDeviceCatalog = []LabDeviceProfile{
	{ID: "nvidia-h100-80", Vendor: "nvidia", Runtime: "cuda", Model: "H100 80GB", Resource: "nvidia.com/gpu", MemoryGiB: 80, ComputeTFLOPS: 989, MemoryGBps: 3350, PowerWatts: 700, HourlyCost: 3.50, Evidence: "planning-envelope"},
	{ID: "nvidia-l40s-48", Vendor: "nvidia", Runtime: "cuda", Model: "L40S 48GB", Resource: "nvidia.com/gpu", MemoryGiB: 48, ComputeTFLOPS: 362, MemoryGBps: 864, PowerWatts: 350, HourlyCost: 1.40, Evidence: "planning-envelope"},
	{ID: "amd-mi300x-192", Vendor: "amd", Runtime: "rocm", Model: "MI300X 192GB", Resource: "amd.com/gpu", MemoryGiB: 192, ComputeTFLOPS: 1307, MemoryGBps: 5300, PowerWatts: 750, HourlyCost: 3.00, Evidence: "planning-envelope"},
	{ID: "amd-mi250-128", Vendor: "amd", Runtime: "rocm", Model: "MI250 128GB", Resource: "amd.com/gpu", MemoryGiB: 128, ComputeTFLOPS: 383, MemoryGBps: 3276, PowerWatts: 560, HourlyCost: 2.10, Evidence: "planning-envelope"},
	{ID: "intel-max-1550", Vendor: "intel", Runtime: "oneapi", Model: "Data Center GPU Max 1550", Resource: "gpu.intel.com/i915", MemoryGiB: 128, ComputeTFLOPS: 419, MemoryGBps: 3276, PowerWatts: 600, HourlyCost: 2.20, Evidence: "planning-envelope"},
	{ID: "apple-m4-max-128", Vendor: "apple", Runtime: "metal", Model: "M4 Max unified", Resource: "openmycelium.io/apple-gpu", MemoryGiB: 128, ComputeTFLOPS: 28, MemoryGBps: 546, PowerWatts: 120, HourlyCost: 0, Evidence: "planning-envelope"},
	{ID: "cpu-64", Vendor: "cpu", Runtime: "gloo", Model: "CPU 64GB", Resource: "openmycelium.io/cpu-worker", MemoryGiB: 64, ComputeTFLOPS: 4, MemoryGBps: 100, PowerWatts: 120, HourlyCost: 0.20, Evidence: "planning-envelope"},
}

func labCatalogHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"devices": labDeviceCatalog,
		"templates": []map[string]any{
			{"id": "mixed-research", "name": "Mixed research pod", "groups": []map[string]any{{"profileId": "nvidia-h100-80", "count": 2}, {"profileId": "amd-mi300x-192", "count": 2}}, "link": "RoCE 100GbE", "linkGBps": 12.5, "latencyUs": 12, "nics": 1},
			{"id": "cpu-validation", "name": "CPU validation cluster", "groups": []map[string]any{{"profileId": "cpu-64", "count": 3}}, "link": "Ethernet 10GbE", "linkGBps": 1.25, "latencyUs": 80, "nics": 1},
			{"id": "scale-lab", "name": "Eight accelerator scale lab", "groups": []map[string]any{{"profileId": "nvidia-h100-80", "count": 4}, {"profileId": "amd-mi300x-192", "count": 4}}, "link": "InfiniBand 400G", "linkGBps": 50, "latencyUs": 3, "nics": 2},
		},
		"evidence": "catalog values are planning envelopes and are never merged into discovered physical capacity",
	})
}

func normalizeLabAsset(asset *LabAsset) error {
	asset.Kind = strings.ToLower(strings.TrimSpace(asset.Kind))
	asset.Name = strings.TrimSpace(asset.Name)
	asset.Status = strings.ToLower(strings.TrimSpace(asset.Status))
	if asset.Name == "" || len(asset.Name) > 120 {
		return fmt.Errorf("name is required and must not exceed 120 characters")
	}
	allowed := map[string]bool{"topology": true, "runtime-image": true, "experiment": true, "benchmark-import": true}
	if !allowed[asset.Kind] {
		return fmt.Errorf("kind must be topology, runtime-image, experiment, or benchmark-import")
	}
	if len(asset.Payload) == 0 || !json.Valid(asset.Payload) {
		return fmt.Errorf("valid JSON payload is required")
	}
	if asset.Status == "" {
		asset.Status = "simulated"
	}
	if asset.Status != "simulated" && asset.Status != "draft" && asset.Status != "passed" && asset.Status != "failed" && asset.Status != "awaiting-qualification" {
		return fmt.Errorf("unsupported lab asset status")
	}
	return nil
}

func labAssetsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		query, args := `SELECT id,kind,name,status,payload,created_by,created_at,updated_at FROM lab_assets`, []any{}
		if kind := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("kind"))); kind != "" {
			query += ` WHERE kind=$1`
			args = append(args, kind)
		}
		query += ` ORDER BY updated_at DESC`
		rows, err := database.Query(r.Context(), query, args...)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list lab assets"})
			return
		}
		defer rows.Close()
		assets := []LabAsset{}
		for rows.Next() {
			var asset LabAsset
			if rows.Scan(&asset.ID, &asset.Kind, &asset.Name, &asset.Status, &asset.Payload, &asset.CreatedBy, &asset.CreatedAt, &asset.UpdatedAt) == nil {
				assets = append(assets, asset)
			}
		}
		writeJSON(w, 200, map[string]any{"assets": assets})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var asset LabAsset
	if decode(r, &asset) != nil {
		writeJSON(w, 400, map[string]string{"error": "valid lab asset JSON is required"})
		return
	}
	if err := normalizeLabAsset(&asset); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	asset.ID = "lab_" + randomToken(9)
	asset.CreatedBy = requestActor(r)
	asset.CreatedAt = time.Now().UTC()
	asset.UpdatedAt = asset.CreatedAt
	err := database.QueryRow(r.Context(), `INSERT INTO lab_assets(id,kind,name,status,payload,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(kind,name) DO UPDATE SET payload=EXCLUDED.payload,status=EXCLUDED.status,created_by=EXCLUDED.created_by,updated_at=EXCLUDED.updated_at RETURNING id,created_at,updated_at`, asset.ID, asset.Kind, asset.Name, asset.Status, asset.Payload, asset.CreatedBy, asset.CreatedAt, asset.UpdatedAt).Scan(&asset.ID, &asset.CreatedAt, &asset.UpdatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a lab asset with this kind and name already exists"})
		return
	}
	auditRequest(r, "lab.asset_created", map[string]any{"asset_id": asset.ID, "kind": asset.Kind, "status": asset.Status})
	publishEvent("lab.asset_created", asset)
	writeJSON(w, 201, asset)
}

func labAssetResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/lab/assets/"), "/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "lab asset not found"})
		return
	}
	if r.Method != http.MethodDelete {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	result, err := database.Exec(r.Context(), `DELETE FROM lab_assets WHERE id=$1`, id)
	if err != nil || result.RowsAffected() == 0 {
		writeJSON(w, 404, map[string]string{"error": "lab asset not found"})
		return
	}
	auditRequest(r, "lab.asset_deleted", map[string]any{"asset_id": id})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func validateLabSimulation(request *LabSimulationRequest) error {
	request.Topology.Name = strings.TrimSpace(request.Topology.Name)
	if request.Topology.Name == "" || len(request.Topology.Groups) == 0 {
		return fmt.Errorf("topology name and at least one device group are required")
	}
	if len(request.Topology.Groups) > 16 {
		return fmt.Errorf("a topology supports at most 16 device groups")
	}
	if request.Topology.LinkGBps <= 0 || request.Topology.LatencyUS < 0 {
		return fmt.Errorf("positive link bandwidth and non-negative latency are required")
	}
	if request.Topology.NICs < 1 || request.Topology.NICs > 16 {
		return fmt.Errorf("NIC count must be from 1 through 16")
	}
	for i := range request.Topology.Groups {
		group := &request.Topology.Groups[i]
		group.Vendor = strings.ToLower(strings.TrimSpace(group.Vendor))
		group.Runtime = strings.ToLower(strings.TrimSpace(group.Runtime))
		if group.Count < 1 || group.Count > 1024 || group.MemoryGiB <= 0 || group.ComputeTFLOPS <= 0 || group.MemoryGBps <= 0 {
			return fmt.Errorf("each device group requires valid count, memory, compute, and bandwidth")
		}
		if group.Resource == "" {
			group.Resource = "openmycelium.io/virtual-" + group.Vendor
		}
	}
	if request.Model.Name == "" || request.Model.ParametersB <= 0 || request.Model.Layers < 1 {
		return fmt.Errorf("model name, parameters, and layers are required")
	}
	if request.Model.ActiveB <= 0 || request.Model.ActiveB > request.Model.ParametersB {
		request.Model.ActiveB = request.Model.ParametersB
	}
	if request.Model.PrecisionBits != 4 && request.Model.PrecisionBits != 8 && request.Model.PrecisionBits != 16 && request.Model.PrecisionBits != 32 {
		return fmt.Errorf("precisionBits must be 4, 8, 16, or 32")
	}
	if request.Model.SequenceLength < 1 {
		request.Model.SequenceLength = 2048
	}
	if request.Model.GlobalBatch < 1 {
		request.Model.GlobalBatch = 1
	}
	if request.Model.HiddenSize < 1 {
		request.Model.HiddenSize = 4096
	}
	if request.Model.WorkloadKind == "" {
		request.Model.WorkloadKind = "training"
	}
	if request.Model.Objective == "" {
		request.Model.Objective = "throughput"
	}
	if request.PayloadGiB <= 0 {
		request.PayloadGiB = 1
	}
	if request.Dataset.CheckpointRetention < 1 {
		request.Dataset.CheckpointRetention = 2
	}
	if request.Dataset.ReadGBps <= 0 {
		request.Dataset.ReadGBps = 1
	}
	return nil
}

func labPrecision(bits int) string {
	if bits == 32 {
		return "fp32"
	}
	if bits == 8 {
		return "int8"
	}
	if bits == 4 {
		return "int4"
	}
	return "bf16"
}

func simulateLab(request LabSimulationRequest) (LabSimulationResult, error) {
	if err := validateLabSimulation(&request); err != nil {
		return LabSimulationResult{}, err
	}
	nodes := []ClusterNode{}
	profiles := []FabricBenchmarkProfile{}
	vendorImages := map[string]string{}
	devices, totalMemory, totalCompute, power, cost := 0, 0.0, 0.0, 0.0, 0.0
	vendors := map[string]bool{}
	for index, group := range request.Topology.Groups {
		fabric := NodeFabricStatus{RDMA: request.Topology.RDMA, GPUDirect: request.Topology.GPUDirect, DirectGMA: request.Topology.DirectGMA, Qualified: false, Qualification: "simulated topology; hardware evidence required"}
		fabric.Backends = []string{nativeBackend(group.Runtime)}
		if request.Topology.HetCCL {
			fabric.Backends = append(fabric.Backends, "hetccl")
		}
		if request.Topology.DeviceDirect {
			fabric.Backends = append(fabric.Backends, "device-direct")
		}
		nodes = append(nodes, ClusterNode{ClusterID: "virtual-lab", Name: fmt.Sprintf("virtual-%s-%d", group.Vendor, index+1), Ready: true, Schedulable: true, Accelerators: []NodeAccelerator{{Resource: group.Resource, Vendor: group.Vendor, Runtime: group.Runtime, Model: group.Model, Capacity: int64(group.Count), Allocatable: int64(group.Count), Available: int64(group.Count)}}, Fabric: fabric})
		throughput := math.Max(1, math.Min(group.ComputeTFLOPS/2, group.MemoryGBps/10))
		profiles = append(profiles, FabricBenchmarkProfile{Vendor: group.Vendor, Runtime: group.Runtime, Model: group.Model, Precision: labPrecision(request.Model.PrecisionBits), TokensPerSecond: throughput, MemoryGiB: group.MemoryGiB, PowerWatts: group.PowerWatts, Source: "imported"})
		if group.WorkerImage != "" && group.Vendor != "cpu" {
			vendorImages[group.Vendor] = group.WorkerImage
		}
		devices += group.Count
		vendors[group.Vendor] = true
		totalMemory += group.MemoryGiB * float64(group.Count)
		totalCompute += group.ComputeTFLOPS * float64(group.Count)
		power += group.PowerWatts * float64(group.Count)
		cost += group.HourlyCost * float64(group.Count)
	}
	precisionBytes := float64(request.Model.PrecisionBits) / 8
	weights := request.Model.ParametersB * 1e9 * precisionBytes / gibibyte
	activeWeights := request.Model.ActiveB * 1e9 * precisionBytes / gibibyte
	training := request.Model.WorkloadKind != "inference"
	trainingState := 0.0
	if training {
		trainingState = request.Model.ParametersB * 1e9 * 12 / gibibyte
	}
	activation := float64(request.Model.Layers*request.Model.SequenceLength*request.Model.GlobalBatch*request.Model.HiddenSize) * 2 / gibibyte
	kvCache := float64(2*request.Model.Layers*request.Model.SequenceLength*request.Model.GlobalBatch*request.Model.HiddenSize) * precisionBytes / gibibyte
	flopFactor := 2.0
	if training {
		flopFactor = 6
	}
	stepTFLOPs := flopFactor * request.Model.ActiveB * 1e9 * float64(request.Model.SequenceLength*request.Model.GlobalBatch) / 1e12
	predictedStepMS := stepTFLOPs / math.Max(0.001, totalCompute*.35) * 1000
	checkpoint := weights
	if training {
		checkpoint += trainingState
	}
	ringFactor := 2 * float64(max(1, devices-1)) / float64(max(1, devices))
	rails := float64(request.Topology.NICs)
	effective := request.Topology.LinkGBps * rails
	comm := []LabCommunicationEstimate{}
	addComm := func(name string, efficiency, staging float64, evidence string) {
		gbps := effective * efficiency
		seconds := request.PayloadGiB*ringFactor/math.Max(.001, gbps) + request.Topology.LatencyUS/1e6
		comm = append(comm, LabCommunicationEstimate{Transport: name, AllReduceMS: math.Round(seconds*100000) / 100, EffectiveGBps: math.Round(gbps*100) / 100, HostStagingGiB: staging, Relative: 0, Evidence: evidence})
	}
	addComm("cpu-forwarding", .55, request.PayloadGiB*2, "simulated")
	addComm("hetccl", .82, 0, "awaiting-qualification")
	addComm("device-direct", .90, 0, "awaiting-qualification")
	baseline := comm[0].AllReduceMS
	for i := range comm {
		comm[i].Relative = math.Round(baseline/math.Max(.001, comm[i].AllReduceMS)*100) / 100
	}
	preferred := strings.ToLower(strings.TrimSpace(request.Transport))
	if preferred == "" {
		preferred = "gloo"
	}
	allowFallback := preferred == "gloo" || preferred == "cpu-forwarding"
	planRequest := ExecutionPlanRequest{Name: request.Topology.Name + " / " + request.Model.Name, ClusterID: "virtual-lab", Model: request.Model.Name, ParametersB: request.Model.ParametersB, Layers: request.Model.Layers, SequenceLength: request.Model.SequenceLength, GlobalBatch: request.Model.GlobalBatch, Precision: labPrecision(request.Model.PrecisionBits), WorkloadKind: request.Model.WorkloadKind, Strategy: "auto", ZeroStage: request.Model.ZeroStage, MaxDevices: devices, MaxPipelineStages: 16, RequireRDMA: preferred != "gloo", AllowCPUFallback: allowFallback, DynamicMicroBatch: true, PreferredTransport: preferred, DefaultMemoryGiB: 0, Objective: request.Model.Objective, VendorImages: vendorImages}
	plan, err := buildExecutionPlan("sim_"+randomToken(6), planRequest, nodes, profiles)
	if err != nil {
		return LabSimulationResult{}, err
	}
	if preferred != "gloo" && preferred != "cpu-forwarding" {
		plan.Executable = false
		plan.Status = "blocked"
		plan.TransportStatus = "awaiting-qualification"
		plan.RequiredAdapters = append(plan.RequiredAdapters, "measured mixed-vendor hardware qualification")
		plan.Warnings = append(plan.Warnings, "virtual adapter labels cannot qualify a direct transport for deployment")
	}
	deployment := buildLabDeploymentPreview(request, plan)
	checks := []LabQualificationCheck{
		{Name: "Inventory isolation", Status: "passed", Detail: "virtual devices are excluded from physical scheduler capacity"},
		{Name: "Runtime images", Status: "awaiting-qualification", Detail: "build and scan vendor-specific images before hardware execution"},
		{Name: "CPU/Gloo path", Status: "simulated", Detail: "can be exercised on CPU Kubernetes workers"},
		{Name: "RDMA fabric", Status: map[bool]string{true: "simulated", false: "failed"}[request.Topology.RDMA], Detail: "requires Linux NIC and driver evidence"},
		{Name: "Cross-vendor direct", Status: "awaiting-qualification", Detail: "requires CUDA, ROCm, peer-memory, and collective correctness tests"},
	}
	warnings := append([]string{}, plan.Warnings...)
	warnings = append(warnings, "all capacity and communication results are simulated planning evidence")
	return LabSimulationResult{Mode: "simulated", GeneratedAt: time.Now().UTC(), Devices: devices, Vendors: len(vendors), TotalMemoryGiB: roundGiB(totalMemory), TotalComputeTFLOPS: math.Round(totalCompute*10) / 10, PowerWatts: math.Round(power), HourlyCost: math.Round(cost*100) / 100, ModelWeightsGiB: roundGiB(weights), ActiveWeightsGiB: roundGiB(activeWeights), KVCacheGiB: roundGiB(kvCache), TrainingStateGiB: roundGiB(trainingState), ActivationGiB: roundGiB(activation), StepTFLOPs: math.Round(stepTFLOPs*100) / 100, PredictedStepMS: math.Round(predictedStepMS*100) / 100, CheckpointGiB: roundGiB(checkpoint), CheckpointStoreGiB: roundGiB(checkpoint * float64(request.Dataset.CheckpointRetention)), DatasetReadSeconds: math.Round(request.Dataset.SizeGiB/request.Dataset.ReadGBps*100) / 100, Communications: comm, Qualification: checks, Plan: plan, Deployment: deployment, Warnings: warnings}, nil
}

func buildLabDeploymentPreview(request LabSimulationRequest, plan ExecutionPlan) LabDeploymentPreview {
	preview := LabDeploymentPreview{Command: "python -m runtime.mycelium.smoke"}
	if !plan.Executable || (plan.Transport != "gloo" && plan.Transport != "cpu-forwarding") {
		preview.Missing = append(preview.Missing, "an executable CPU/Gloo plan")
	}
	for _, group := range request.Topology.Groups {
		if strings.TrimSpace(group.WorkerImage) == "" {
			preview.Missing = append(preview.Missing, fmt.Sprintf("a qualified %s/%s worker image", group.Vendor, group.Runtime))
		}
	}
	if len(preview.Missing) > 0 {
		return preview
	}
	planJSON, err := json.Marshal(plan)
	if err != nil {
		preview.Missing = append(preview.Missing, "serializable execution plan")
		return preview
	}
	workloadID := "job-lab-preview"
	resourceName := workloadResourceName(workloadID, request.Topology.Name+"-gloo-smoke")
	totalDevices := 0
	for _, group := range plan.Groups {
		totalDevices += group.Devices
	}
	workload := Workload{
		ID:                 workloadID,
		Name:               request.Topology.Name + " Gloo smoke",
		Kind:               "training",
		Runtime:            "Mycelium/Gloo",
		Pool:               "lab-cpu",
		Image:              request.Topology.Groups[0].WorkerImage,
		Command:            preview.Command,
		CPU:                "500m",
		Memory:             "1Gi",
		Namespace:          "openmycelium-workloads",
		ResourceName:       resourceName,
		ServiceName:        resourceName + "-rendezvous",
		DesiredCount:       int32(max(2, totalDevices)),
		ExecutionPlanID:    plan.ID,
		ExecutionTransport: plan.Transport,
		ExecutionPlan:      string(planJSON),
		Status:             "DryRun",
	}
	allCPU := true
	for _, group := range request.Topology.Groups {
		if group.Vendor != "cpu" {
			allCPU = false
			break
		}
	}
	objects := []any{}
	if allCPU {
		job, buildErr := buildJob(workload, ManagedPool{Name: "lab-cpu", Vendor: "cpu", Runtime: "gloo", Enabled: true}, nil)
		if buildErr != nil {
			preview.Missing = append(preview.Missing, buildErr.Error())
			return preview
		}
		objects = append(objects, job)
	} else {
		jobs, buildErr := buildExecutionJobs(workload)
		if buildErr != nil {
			preview.Missing = append(preview.Missing, buildErr.Error())
			return preview
		}
		for _, job := range jobs {
			objects = append(objects, job)
		}
	}
	if service := buildService(workload); service != nil {
		objects = append(objects, service)
	}
	parts := make([]string, 0, len(objects))
	for _, object := range objects {
		encoded, marshalErr := yaml.Marshal(object)
		if marshalErr != nil {
			preview.Missing = append(preview.Missing, marshalErr.Error())
			return preview
		}
		parts = append(parts, strings.TrimSpace(string(encoded)))
	}
	preview.Ready = true
	preview.Manifest = strings.Join(parts, "\n---\n") + "\n"
	return preview
}

func labSimulationHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var request LabSimulationRequest
	if decode(r, &request) != nil {
		writeJSON(w, 400, map[string]string{"error": "valid simulation JSON is required"})
		return
	}
	result, err := simulateLab(request)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	if request.SaveResult && database != nil {
		payload, _ := json.Marshal(map[string]any{"request": request, "result": result})
		asset := LabAsset{ID: "lab_" + randomToken(9), Kind: "experiment", Name: strings.TrimSpace(request.Experiment), Status: "simulated", Payload: payload, CreatedBy: requestActor(r), CreatedAt: time.Now().UTC()}
		asset.UpdatedAt = asset.CreatedAt
		if asset.Name == "" {
			asset.Name = request.Topology.Name + " / " + request.Model.Name + " / " + asset.CreatedAt.Format("20060102-150405")
		}
		_, _ = database.Exec(r.Context(), `INSERT INTO lab_assets(id,kind,name,status,payload,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(kind,name) DO UPDATE SET payload=EXCLUDED.payload,status=EXCLUDED.status,updated_at=EXCLUDED.updated_at`, asset.ID, asset.Kind, asset.Name, asset.Status, asset.Payload, asset.CreatedBy, asset.CreatedAt, asset.UpdatedAt)
	}
	auditRequest(r, "lab.simulation_compiled", map[string]any{"topology": request.Topology.Name, "devices": result.Devices, "mode": "simulated"})
	writeJSON(w, 200, result)
}

func sortedLabCatalog() []LabDeviceProfile {
	result := append([]LabDeviceProfile(nil), labDeviceCatalog...)
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result
}
