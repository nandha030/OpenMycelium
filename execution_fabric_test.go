package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func fabricTestNode(name, vendor, runtime, model, resourceName string, devices int64, fabric NodeFabricStatus) ClusterNode {
	return ClusterNode{
		ClusterID:   "cluster-1",
		Name:        name,
		Ready:       true,
		Schedulable: true,
		Accelerators: []NodeAccelerator{{
			Resource: resourceName, Vendor: vendor, Runtime: runtime, Model: model,
			Capacity: devices, Allocatable: devices, Available: devices,
		}},
		Fabric: fabric,
	}
}

func fabricTestRequest() ExecutionPlanRequest {
	return ExecutionPlanRequest{
		Name: "mixed-training", ClusterID: "cluster-1", Model: "test-12b",
		ParametersB: 12, Layers: 48, SequenceLength: 2048, GlobalBatch: 12,
		Precision: "bf16", WorkloadKind: "training", Strategy: "auto",
		MaxDevices: 8, MaxPipelineStages: 8, DynamicMicroBatch: true,
	}
}

func TestMixedVendorPlanUsesExplicitGlooFallback(t *testing.T) {
	nodes := []ClusterNode{
		fabricTestNode("nvidia-1", "NVIDIA", "cuda", "A100", "nvidia.com/gpu", 2, NodeFabricStatus{Backends: []string{"nccl"}}),
		fabricTestNode("amd-1", "AMD", "rocm", "MI250", "amd.com/gpu", 2, NodeFabricStatus{Backends: []string{"rccl"}}),
	}
	request := fabricTestRequest()
	request.AllowCPUFallback = true
	plan, err := buildExecutionPlan("exec-test", request, nodes, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Executable || plan.Transport != "gloo" || plan.Parallel.Mode != "heterogeneous-pipeline" {
		t.Fatalf("unexpected fallback plan: %#v", plan)
	}
	layers, batches := 0, 0
	for _, group := range plan.Groups {
		layers += group.Layers
		batches += group.MicroBatch
	}
	if layers != request.Layers || batches != request.GlobalBatch {
		t.Fatalf("planner lost work: layers=%d batches=%d", layers, batches)
	}
	if !strings.Contains(strings.Join(plan.Warnings, " "), "host memory") {
		t.Fatal("fallback plan must disclose host-memory staging")
	}
}

func TestMixedVendorDirectTransportFailsClosedWithoutQualification(t *testing.T) {
	nodes := []ClusterNode{
		fabricTestNode("nvidia-1", "NVIDIA", "cuda", "A100", "nvidia.com/gpu", 1, NodeFabricStatus{Backends: []string{"nccl"}}),
		fabricTestNode("amd-1", "AMD", "rocm", "MI250", "amd.com/gpu", 1, NodeFabricStatus{Backends: []string{"rccl"}}),
	}
	request := fabricTestRequest()
	request.RequireRDMA = true
	plan, err := buildExecutionPlan("exec-blocked", request, nodes, nil)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Executable || plan.Status != "blocked" || len(plan.RequiredAdapters) == 0 {
		t.Fatalf("unqualified direct transport must remain blocked: %#v", plan)
	}
}

func TestPortableHetCCLTransportIsExecutableWithoutRDMA(t *testing.T) {
	nodes := []ClusterNode{
		fabricTestNode("nvidia-1", "NVIDIA", "cuda", "A100", "nvidia.com/gpu", 1, NodeFabricStatus{Backends: []string{"nccl"}}),
		fabricTestNode("amd-1", "AMD", "rocm", "MI250", "amd.com/gpu", 1, NodeFabricStatus{Backends: []string{"rccl"}}),
	}
	request := fabricTestRequest()
	request.PreferredTransport = "hetccl-tcp"
	plan, err := buildExecutionPlan("exec-portable-hetccl", request, nodes, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Executable || plan.Transport != "hetccl-tcp" || plan.TransportStatus != "ready" {
		t.Fatalf("portable HetCCL must remain executable without direct RDMA claims: %#v", plan)
	}
	if !strings.Contains(strings.Join(plan.Warnings, " "), "host memory") {
		t.Fatalf("portable HetCCL must disclose host staging: %#v", plan.Warnings)
	}
	request.RequireRDMA = true
	blocked, err := buildExecutionPlan("exec-portable-hetccl-rdma", request, nodes, nil)
	if err != nil {
		t.Fatal(err)
	}
	if blocked.Executable || blocked.TransportStatus != "blocked" {
		t.Fatalf("portable HetCCL must not satisfy an RDMA requirement: %#v", blocked)
	}
}

func TestQualifiedHetCCLPlanAndProfileWeightedPlacement(t *testing.T) {
	qualified := NodeFabricStatus{RDMA: true, Qualified: true, Backends: []string{"hetccl", "ucx"}}
	nodes := []ClusterNode{
		fabricTestNode("nvidia-1", "NVIDIA", "cuda", "A100", "nvidia.com/gpu", 1, qualified),
		fabricTestNode("amd-1", "AMD", "rocm", "MI250", "amd.com/gpu", 1, qualified),
	}
	profiles := []FabricBenchmarkProfile{
		{Vendor: "nvidia", Runtime: "cuda", Model: "A100", Precision: "bf16", TokensPerSecond: 300, MemoryGiB: 80, Source: "measured"},
		{Vendor: "amd", Runtime: "rocm", Model: "MI250", Precision: "bf16", TokensPerSecond: 100, MemoryGiB: 128, Source: "measured"},
	}
	request := fabricTestRequest()
	request.PreferredTransport = "hetccl"
	request.RequireRDMA = true
	request.ZeroStage = 2
	request.VendorImages = map[string]string{"nvidia": "trainer:cuda", "amd": "trainer:rocm"}
	plan, err := buildExecutionPlan("exec-qualified", request, nodes, profiles)
	if err != nil {
		t.Fatal(err)
	}
	if !plan.Executable || plan.Transport != "hetccl" || plan.Parallel.Mode != "heterogeneous-zero" {
		t.Fatalf("unexpected qualified plan: %#v", plan)
	}
	placements := map[string]ExecutionGroup{}
	for _, group := range plan.Groups {
		placements[group.Vendor] = group
	}
	if placements["nvidia"].Layers <= placements["amd"].Layers {
		t.Fatalf("faster measured group should receive more layers: %#v", placements)
	}
	if placements["nvidia"].Image != "trainer:cuda" || placements["amd"].Image != "trainer:rocm" {
		t.Fatalf("vendor worker images were not retained: %#v", placements)
	}
	if plan.Estimate.Confidence != "profiled" || plan.Estimate.PredictedTokensSecond <= 0 {
		t.Fatalf("measured profiles should produce a profiled estimate: %#v", plan.Estimate)
	}
	if plan.Optimization.Algorithm != "Mycelium" || plan.Optimization.Version == "" {
		t.Fatalf("plan must carry the versioned Mycelium decision contract: %#v", plan.Optimization)
	}
	if plan.Optimization.CandidatesEvaluated < 2 || plan.Optimization.SelectedCandidate != "heterogeneous-zero" {
		t.Fatalf("expected Mycelium to compare direct-transport candidates: %#v", plan.Optimization)
	}
	if len(plan.Optimization.Decisions) < 4 {
		t.Fatalf("Mycelium plans must remain explainable: %#v", plan.Optimization.Decisions)
	}
}

func TestMyceliumReportsInfeasibleProfiledMemory(t *testing.T) {
	qualified := NodeFabricStatus{Backends: []string{"nccl"}}
	nodes := []ClusterNode{fabricTestNode("nvidia-1", "NVIDIA", "cuda", "tiny", "nvidia.com/gpu", 1, qualified)}
	profiles := []FabricBenchmarkProfile{{Vendor: "nvidia", Runtime: "cuda", Model: "tiny", Precision: "bf16", TokensPerSecond: 10, MemoryGiB: 1, Source: "measured"}}
	request := fabricTestRequest()
	request.MaxDevices = 1
	plan, err := buildExecutionPlan("exec-memory", request, nodes, profiles)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Optimization.MemoryFeasible {
		t.Fatalf("oversized model should fail the profiled memory constraint: %#v", plan.Optimization)
	}
	if !strings.Contains(strings.Join(plan.Warnings, " "), "activation checkpointing") {
		t.Fatalf("memory failure should provide remediation: %#v", plan.Warnings)
	}
}

func TestMyceliumObjectiveValidation(t *testing.T) {
	request := fabricTestRequest()
	request.Objective = "magic"
	if err := validateExecutionRequest(&request); err == nil || !strings.Contains(err.Error(), "objective") {
		t.Fatalf("unknown optimization objective should be rejected, got %v", err)
	}
}

func TestExecutionContractReachesContainerEnvironment(t *testing.T) {
	plan := ExecutionPlan{ID: "exec-contract", Transport: "gloo", Executable: true, Optimization: MyceliumOptimization{Algorithm: "Mycelium", Version: "1.0.0", Objective: "throughput"}}
	payload, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	workload := Workload{
		ID: "job-contract", Name: "fabric-job", Kind: "training", Image: "trainer:latest",
		CPU: "1", Memory: "2Gi", ExecutionPlanID: plan.ID,
		ExecutionTransport: plan.Transport, ExecutionPlan: string(payload),
	}
	container, err := workloadContainer(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"})
	if err != nil {
		t.Fatal(err)
	}
	environment := map[string]string{}
	for _, item := range container.Env {
		environment[item.Name] = item.Value
	}
	if environment["OPENMYCELIUM_EXECUTION_PLAN_ID"] != plan.ID || environment["OPENMYCELIUM_EXECUTION_TRANSPORT"] != "gloo" {
		t.Fatalf("execution contract was not propagated: %#v", environment)
	}
	if !strings.Contains(environment["OPENMYCELIUM_EXECUTION_PLAN_JSON"], "exec-contract") {
		t.Fatal("serialized execution plan is missing")
	}
	if environment["OPENMYCELIUM_ALGORITHM"] != "Mycelium" || environment["OPENMYCELIUM_ALGORITHM_VERSION"] != "1.0.0" {
		t.Fatalf("Mycelium metadata was not propagated: %#v", environment)
	}
}

func TestExecutionPlanExpandsToVendorSpecificKubernetesJobs(t *testing.T) {
	plan := ExecutionPlan{
		ID: "exec-mixed-jobs", Executable: true, Transport: "gloo",
		Groups: []ExecutionGroup{
			{ID: "group-nvidia", Vendor: "nvidia", Runtime: "cuda", Resource: "nvidia.com/gpu", Nodes: []string{"nvidia-1"}, Devices: 2, Image: "trainer:cuda"},
			{ID: "group-amd", Vendor: "amd", Runtime: "rocm", Resource: "amd.com/gpu", Nodes: []string{"amd-1"}, Devices: 1, Image: "trainer:rocm"},
		},
	}
	payload, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	workload := Workload{
		ID: "job-mixed", Name: "mixed-training", Kind: "training", Image: "trainer:latest",
		Namespace: "research", ResourceName: "mixed-training", ServiceName: "mixed-training-workers",
		CPU: "2", Memory: "8Gi", SchedulerBackend: "kubernetes", ExecutionPlanID: plan.ID,
		ExecutionTransport: plan.Transport, ExecutionPlan: string(payload),
	}
	jobs, err := buildExecutionJobs(workload)
	if err != nil {
		t.Fatal(err)
	}
	if len(jobs) != 2 {
		t.Fatalf("expected two vendor-specific Jobs, got %d", len(jobs))
	}
	resources := map[string]int64{}
	rankBases := map[string]string{}
	for _, job := range jobs {
		container := job.Spec.Template.Spec.Containers[0]
		groupID := job.Labels["fabric.openmycelium.io/execution-group"]
		if groupID == "group-nvidia" && container.Image != "trainer:cuda" || groupID == "group-amd" && container.Image != "trainer:rocm" {
			t.Fatalf("wrong vendor worker image for %s: %s", groupID, container.Image)
		}
		for resourceName, quantity := range container.Resources.Requests {
			if strings.Contains(string(resourceName), ".com/gpu") {
				resources[string(resourceName)] += quantity.Value() * int64(*job.Spec.Completions)
			}
		}
		environment := map[string]string{}
		for _, item := range container.Env {
			environment[item.Name] = item.Value
		}
		rankBases[groupID] = environment["OPENMYCELIUM_RANK_BASE"]
		if environment["OPENMYCELIUM_WORLD_SIZE"] != "3" {
			t.Fatalf("expected global world size 3, got %#v", environment)
		}
		if job.Spec.Template.Spec.Affinity == nil || job.Spec.Template.Spec.Affinity.NodeAffinity == nil {
			t.Fatal("execution group must retain its qualified node constraint")
		}
	}
	if resources["nvidia.com/gpu"] != 2 || resources["amd.com/gpu"] != 1 {
		t.Fatalf("unexpected exact resource allocation: %#v", resources)
	}
	if rankBases["group-nvidia"] != "0" || rankBases["group-amd"] != "2" {
		t.Fatalf("unexpected rank bases: %#v", rankBases)
	}
}
