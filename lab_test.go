package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func testLabRequest() LabSimulationRequest {
	return LabSimulationRequest{
		Topology: LabTopology{Name: "mixed-lab", Link: "RoCE", LinkGBps: 25, LatencyUS: 8, NICs: 2, NUMADomains: 2, RDMA: true, GPUDirect: true, DirectGMA: true, HetCCL: true, Groups: []LabDeviceGroup{
			{Vendor: "nvidia", Runtime: "cuda", Model: "H100", Resource: "nvidia.com/gpu", Count: 2, MemoryGiB: 80, ComputeTFLOPS: 989, MemoryGBps: 3350, PowerWatts: 700, WorkerImage: "trainer:cuda"},
			{Vendor: "amd", Runtime: "rocm", Model: "MI300X", Resource: "amd.com/gpu", Count: 2, MemoryGiB: 192, ComputeTFLOPS: 1307, MemoryGBps: 5300, PowerWatts: 750, WorkerImage: "trainer:rocm"},
		}},
		Model:   LabModelRequest{Name: "test-8b", ParametersB: 8, ActiveB: 8, Layers: 32, HiddenSize: 4096, PrecisionBits: 16, SequenceLength: 2048, GlobalBatch: 8, WorkloadKind: "training", ZeroStage: 2, Objective: "balanced"},
		Dataset: LabDatasetRequest{SizeGiB: 100, ReadGBps: 2, CheckpointRetention: 3}, PayloadGiB: 1, Transport: "hetccl",
	}
}

func TestLabSimulationProducesIsolatedMyceliumPlan(t *testing.T) {
	result, err := simulateLab(testLabRequest())
	if err != nil {
		t.Fatal(err)
	}
	if result.Mode != "simulated" || result.Devices != 4 || result.Vendors != 2 {
		t.Fatalf("unexpected lab summary: %#v", result)
	}
	if result.Plan.Optimization.Algorithm != "Mycelium" || result.Plan.ClusterID != "virtual-lab" {
		t.Fatalf("simulation did not compile a Mycelium plan: %#v", result.Plan)
	}
	if len(result.Communications) != 3 || result.Communications[0].HostStagingGiB <= 0 {
		t.Fatalf("communication comparison missing: %#v", result.Communications)
	}
	if result.CheckpointStoreGiB <= result.CheckpointGiB || result.DatasetReadSeconds != 50 {
		t.Fatalf("storage estimates are incorrect: %#v", result)
	}
	images := map[string]string{}
	for _, group := range result.Plan.Groups {
		images[group.Vendor] = group.Image
	}
	if images["nvidia"] != "trainer:cuda" || images["amd"] != "trainer:rocm" {
		t.Fatalf("vendor images were lost: %#v", images)
	}
}

func TestLabSimulationNeverQualifiesVirtualHardware(t *testing.T) {
	result, err := simulateLab(testLabRequest())
	if err != nil {
		t.Fatal(err)
	}
	for _, check := range result.Qualification {
		if check.Name == "Cross-vendor direct" && check.Status == "passed" {
			t.Fatal("virtual evidence must not qualify direct hardware")
		}
	}
	if result.Plan.Executable || result.Deployment.Ready {
		t.Fatal("virtual direct transport must remain blocked until hardware qualification")
	}
}

func TestLabCPUGlooProducesDryRunManifest(t *testing.T) {
	request := testLabRequest()
	request.Topology.Name = "cpu-validation"
	request.Topology.RDMA = false
	request.Topology.GPUDirect = false
	request.Topology.DirectGMA = false
	request.Topology.HetCCL = false
	request.Topology.DeviceDirect = false
	request.Topology.Groups = []LabDeviceGroup{{Vendor: "cpu", Runtime: "gloo", Model: "CPU", Resource: "openmycelium.io/cpu-worker", Count: 2, MemoryGiB: 64, ComputeTFLOPS: 4, MemoryGBps: 100, PowerWatts: 120, WorkerImage: "registry.example/openmycelium-pytorch-cpu:test"}}
	request.Transport = "gloo"
	result, err := simulateLab(request)
	if err != nil {
		t.Fatal(err)
	}
	if !result.Plan.Executable || !result.Deployment.Ready {
		t.Fatalf("CPU/Gloo dry-run should be generated: %#v", result.Deployment)
	}
	if !strings.Contains(result.Deployment.Manifest, "kind: Job") || !strings.Contains(result.Deployment.Manifest, "kind: Service") || !strings.Contains(result.Deployment.Manifest, "OPENMYCELIUM_EXECUTION_PLAN_JSON") {
		t.Fatalf("generated manifest is missing the runtime contract: %s", result.Deployment.Manifest)
	}
}

func TestLabAssetValidation(t *testing.T) {
	asset := LabAsset{Kind: "topology", Name: "research", Payload: json.RawMessage(`{"groups":[]}`)}
	if err := normalizeLabAsset(&asset); err != nil {
		t.Fatal(err)
	}
	if asset.Status != "simulated" {
		t.Fatalf("expected simulated status, got %s", asset.Status)
	}
	asset.Kind = "physical-capacity"
	if err := normalizeLabAsset(&asset); err == nil {
		t.Fatal("unsupported asset kind should fail")
	}
}
