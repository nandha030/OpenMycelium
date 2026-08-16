package main

import (
	"strings"
	"testing"
)

func fabricTestNodes() []ClusterNode {
	return []ClusterNode{
		{
			ClusterID: "cluster-1", ClusterName: "test", Name: "nvidia-node", Ready: true, Schedulable: true,
			Labels: map[string]string{"accelerator.openmycelium.io/memory-gib": "24"}, MemoryAvailable: "48Gi",
			Accelerators: []NodeAccelerator{{Resource: "nvidia.com/gpu", Vendor: "NVIDIA", Runtime: "cuda", Model: "L4", Capacity: 1, Allocatable: 1, Available: 1}},
		},
		{
			ClusterID: "cluster-1", ClusterName: "test", Name: "amd-node", Ready: true, Schedulable: true,
			Labels: map[string]string{"accelerator.openmycelium.io/memory-gib": "48"}, MemoryAvailable: "96Gi",
			Accelerators: []NodeAccelerator{{Resource: "amd.com/gpu", Vendor: "AMD", Runtime: "rocm", Model: "MI210", Capacity: 1, Allocatable: 1, Available: 1}},
		},
	}
}

func TestFabricAutoSelectsSingleDevice(t *testing.T) {
	plan, err := buildFabricPlan("fabric-test", FabricPlanRequest{Name: "weights", ClusterID: "cluster-1", TensorName: "model", TensorGiB: 30, Strategy: "auto", Consistency: "immutable", ReservePercent: 10}, fabricTestNodes())
	if err != nil {
		t.Fatal(err)
	}
	if plan.Strategy != "single-device" || len(plan.Placements) != 1 || plan.Placements[0].Vendor != "AMD" {
		t.Fatalf("unexpected placement: %#v", plan)
	}
	if plan.HardwareCoherent {
		t.Fatal("fabric plan must not claim hardware coherence")
	}
}

func TestFabricShardsAcrossVendors(t *testing.T) {
	plan, err := buildFabricPlan("fabric-test", FabricPlanRequest{Name: "weights", ClusterID: "cluster-1", TensorName: "model", TensorGiB: 60, Strategy: "shard", Consistency: "immutable", ReservePercent: 10}, fabricTestNodes())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Placements) != 2 || plan.PhysicalFootprintGiB != 60 {
		t.Fatalf("expected two placements totaling 60 GiB, got %#v", plan)
	}
	if plan.Placements[0].OffsetGiB != 0 || plan.Placements[1].OffsetGiB <= 0 {
		t.Fatalf("shards do not have contiguous offsets: %#v", plan.Placements)
	}
	foundWarning := false
	for _, warning := range plan.Warnings {
		foundWarning = foundWarning || strings.Contains(warning, "mixed-vendor")
	}
	if !foundWarning {
		t.Fatal("expected a mixed-vendor transfer warning")
	}
}

func TestFabricReplicationCountsPhysicalFootprint(t *testing.T) {
	plan, err := buildFabricPlan("fabric-test", FabricPlanRequest{Name: "cache", ClusterID: "cluster-1", TensorName: "lookup", TensorGiB: 20, Strategy: "replicate", Consistency: "immutable", ReservePercent: 10}, fabricTestNodes())
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Placements) != 2 || plan.PhysicalFootprintGiB != 40 {
		t.Fatalf("expected two 20 GiB replicas, got %#v", plan)
	}
}

func TestFabricRejectsInsufficientCapacity(t *testing.T) {
	_, err := buildFabricPlan("fabric-test", FabricPlanRequest{Name: "too-large", ClusterID: "cluster-1", TensorName: "model", TensorGiB: 100, Strategy: "shard", Consistency: "immutable", ReservePercent: 10}, fabricTestNodes())
	if err == nil || !strings.Contains(err.Error(), "usable") {
		t.Fatalf("expected usable-capacity error, got %v", err)
	}
}

func TestFabricCanUseHostSpillTier(t *testing.T) {
	nodes := []ClusterNode{{ClusterID: "cluster-1", ClusterName: "test", Name: "cpu-node", Ready: true, Schedulable: true, Architecture: "amd64", MemoryAvailable: "64Gi", Labels: map[string]string{}}}
	plan, err := buildFabricPlan("fabric-host", FabricPlanRequest{Name: "cpu-model", ClusterID: "cluster-1", TensorName: "weights", TensorGiB: 50, Strategy: "auto", Consistency: "single-writer", ReservePercent: 10, IncludeHostMemory: true}, nodes)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Placements) != 1 || plan.Placements[0].Tier != "host-memory" {
		t.Fatalf("expected host-memory placement, got %#v", plan)
	}
}

func TestWorkloadFabricAnnotations(t *testing.T) {
	workload := Workload{FabricPlanID: "fabric-1", FabricAddress: "hypha://fabric-1/weights", FabricMode: "immutable"}
	annotations := workloadAnnotations(workload)
	if annotations["hypha.openmycelium.io/plan-id"] != "fabric-1" || annotations["hypha.openmycelium.io/hardware-coherent"] != "false" {
		t.Fatalf("unexpected annotations: %#v", annotations)
	}
}

func TestWorkloadContainerReceivesFabricContract(t *testing.T) {
	workload := Workload{Image: "example/workload:latest", CPU: "1", Memory: "1Gi", FabricPlanID: "fabric-1", FabricAddress: "hypha://fabric-1/weights", FabricMode: "immutable", FabricPlan: `{"id":"fabric-1"}`}
	container, err := workloadContainer(workload, ManagedPool{Runtime: "cpu"})
	if err != nil {
		t.Fatal(err)
	}
	values := map[string]string{}
	for _, item := range container.Env {
		values[item.Name] = item.Value
	}
	if values["OPENMYCELIUM_FABRIC_PLAN_JSON"] != `{"id":"fabric-1"}` || values["OPENMYCELIUM_FABRIC_ADDRESS"] != workload.FabricAddress {
		t.Fatalf("fabric execution contract missing from container: %#v", values)
	}
}
