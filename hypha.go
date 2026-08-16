package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"k8s.io/apimachinery/pkg/api/resource"
)

const gibibyte = float64(1 << 30)

type FabricPlanRequest struct {
	Name                   string   `json:"name"`
	ClusterID              string   `json:"clusterId"`
	TensorName             string   `json:"tensorName"`
	TensorGiB              float64  `json:"tensorGiB"`
	Strategy               string   `json:"strategy"`
	Consistency            string   `json:"consistency"`
	ReservePercent         float64  `json:"reservePercent"`
	DefaultDeviceMemoryGiB float64  `json:"defaultDeviceMemoryGiB"`
	MaxDevices             int      `json:"maxDevices"`
	IncludeHostMemory      bool     `json:"includeHostMemory"`
	Vendors                []string `json:"vendors,omitempty"`
}

type FabricDevice struct {
	ID           string  `json:"id"`
	ClusterID    string  `json:"clusterId"`
	ClusterName  string  `json:"clusterName"`
	Node         string  `json:"node"`
	Vendor       string  `json:"vendor"`
	Runtime      string  `json:"runtime"`
	Model        string  `json:"model"`
	Resource     string  `json:"resource"`
	Ordinal      int64   `json:"ordinal"`
	Tier         string  `json:"tier"`
	MemoryGiB    float64 `json:"memoryGiB"`
	AvailableGiB float64 `json:"availableGiB"`
	MemorySource string  `json:"memorySource"`
}

type FabricPlacement struct {
	DeviceID     string  `json:"deviceId"`
	Node         string  `json:"node"`
	Vendor       string  `json:"vendor"`
	Runtime      string  `json:"runtime"`
	Model        string  `json:"model"`
	Tier         string  `json:"tier"`
	OffsetGiB    float64 `json:"offsetGiB"`
	LengthGiB    float64 `json:"lengthGiB"`
	Replica      int     `json:"replica"`
	TransferMode string  `json:"transferMode"`
}

type FabricPlan struct {
	ID                   string            `json:"id"`
	Name                 string            `json:"name"`
	ClusterID            string            `json:"clusterId"`
	TensorName           string            `json:"tensorName"`
	LogicalAddress       string            `json:"logicalAddress"`
	TensorGiB            float64           `json:"tensorGiB"`
	Strategy             string            `json:"strategy"`
	Consistency          string            `json:"consistency"`
	Status               string            `json:"status"`
	HardwareCoherent     bool              `json:"hardwareCoherent"`
	SelectedDevices      int               `json:"selectedDevices"`
	UsableCapacityGiB    float64           `json:"usableCapacityGiB"`
	PhysicalFootprintGiB float64           `json:"physicalFootprintGiB"`
	Placements           []FabricPlacement `json:"placements"`
	Warnings             []string          `json:"warnings"`
	CreatedAt            time.Time         `json:"createdAt"`
}

func roundGiB(value float64) float64 { return math.Round(value*1000) / 1000 }

func memoryGiB(value string) float64 {
	quantity, err := resource.ParseQuantity(strings.TrimSpace(value))
	if err != nil {
		return 0
	}
	return roundGiB(float64(quantity.Value()) / gibibyte)
}

func labelMemoryGiB(node ClusterNode) (float64, string) {
	for _, key := range []string{"accelerator.openmycelium.io/memory-gib", "openmycelium.io/gpu-memory-gib"} {
		if value, err := strconv.ParseFloat(strings.TrimSpace(node.Labels[key]), 64); err == nil && value > 0 {
			return roundGiB(value), "node-label:" + key
		}
	}
	return 0, "unknown"
}

func fabricDevices(nodes []ClusterNode, request FabricPlanRequest) ([]FabricDevice, []string) {
	devices := []FabricDevice{}
	warnings := []string{}
	vendorFilter := map[string]bool{}
	for _, vendor := range request.Vendors {
		vendorFilter[strings.ToLower(strings.TrimSpace(vendor))] = true
	}
	for _, node := range nodes {
		if request.ClusterID != "" && node.ClusterID != request.ClusterID {
			continue
		}
		if !node.Ready || !node.Schedulable {
			continue
		}
		for _, accelerator := range node.Accelerators {
			if len(vendorFilter) > 0 && !vendorFilter[strings.ToLower(accelerator.Vendor)] {
				continue
			}
			perDeviceGiB, source := labelMemoryGiB(node)
			if perDeviceGiB <= 0 && request.DefaultDeviceMemoryGiB > 0 {
				perDeviceGiB, source = request.DefaultDeviceMemoryGiB, "operator-assumption"
			}
			if perDeviceGiB <= 0 {
				warnings = append(warnings, fmt.Sprintf("%s on %s has no reported per-device memory; provide defaultDeviceMemoryGiB or the accelerator memory label", accelerator.Model, node.Name))
				continue
			}
			for ordinal := int64(0); ordinal < accelerator.Available; ordinal++ {
				devices = append(devices, FabricDevice{
					ID: fmt.Sprintf("%s/%s/%s/%d", node.ClusterID, node.Name, accelerator.Resource, ordinal), ClusterID: node.ClusterID,
					ClusterName: node.ClusterName, Node: node.Name, Vendor: accelerator.Vendor, Runtime: accelerator.Runtime,
					Model: accelerator.Model, Resource: accelerator.Resource, Ordinal: ordinal, Tier: "device-vram",
					MemoryGiB: roundGiB(perDeviceGiB), AvailableGiB: roundGiB(perDeviceGiB), MemorySource: source,
				})
			}
		}
		if request.IncludeHostMemory {
			available := memoryGiB(node.MemoryAvailable)
			if available > 0 {
				devices = append(devices, FabricDevice{ID: node.ClusterID + "/" + node.Name + "/host", ClusterID: node.ClusterID, ClusterName: node.ClusterName, Node: node.Name, Vendor: "CPU", Runtime: "host", Model: node.Architecture + " host memory", Resource: "memory", Tier: "host-memory", MemoryGiB: available, AvailableGiB: available, MemorySource: "kubernetes-available"})
			}
		}
	}
	sort.Slice(devices, func(i, j int) bool {
		if devices[i].Tier != devices[j].Tier {
			return devices[i].Tier == "device-vram"
		}
		if devices[i].AvailableGiB != devices[j].AvailableGiB {
			return devices[i].AvailableGiB > devices[j].AvailableGiB
		}
		return devices[i].ID < devices[j].ID
	})
	return devices, warnings
}

func transferMode(device FabricDevice) string {
	switch device.Runtime {
	case "cuda":
		return "CUDA copy / GPUDirect when available"
	case "rocm":
		return "HIP copy / ROCm peer transfer when available"
	case "oneapi":
		return "Level Zero copy"
	case "metal":
		return "Metal blit"
	default:
		return "host memcpy"
	}
}

func validateFabricRequest(request *FabricPlanRequest) error {
	request.Name, request.ClusterID, request.TensorName = strings.TrimSpace(request.Name), strings.TrimSpace(request.ClusterID), strings.TrimSpace(request.TensorName)
	request.Strategy, request.Consistency = strings.ToLower(strings.TrimSpace(request.Strategy)), strings.ToLower(strings.TrimSpace(request.Consistency))
	if request.Name == "" || request.ClusterID == "" || request.TensorName == "" || request.TensorGiB <= 0 {
		return fmt.Errorf("name, clusterId, tensorName, and a positive tensorGiB are required")
	}
	if request.Strategy == "" {
		request.Strategy = "auto"
	}
	if request.Strategy != "auto" && request.Strategy != "shard" && request.Strategy != "replicate" {
		return fmt.Errorf("strategy must be auto, shard, or replicate")
	}
	if request.Consistency == "" {
		request.Consistency = "immutable"
	}
	if request.Consistency != "immutable" && request.Consistency != "single-writer" && request.Consistency != "reduce" && request.Consistency != "transactional" {
		return fmt.Errorf("consistency must be immutable, single-writer, reduce, or transactional")
	}
	if request.ReservePercent < 0 || request.ReservePercent >= 80 {
		return fmt.Errorf("reservePercent must be from 0 through 79")
	}
	if request.DefaultDeviceMemoryGiB < 0 {
		return fmt.Errorf("defaultDeviceMemoryGiB cannot be negative")
	}
	if request.MaxDevices == 0 {
		request.MaxDevices = 16
	}
	if request.MaxDevices < 1 || request.MaxDevices > 1024 {
		return fmt.Errorf("maxDevices must be from 1 through 1024")
	}
	return nil
}

func buildFabricPlan(id string, request FabricPlanRequest, nodes []ClusterNode) (FabricPlan, error) {
	if err := validateFabricRequest(&request); err != nil {
		return FabricPlan{}, err
	}
	devices, warnings := fabricDevices(nodes, request)
	if len(devices) > request.MaxDevices {
		devices = devices[:request.MaxDevices]
	}
	if len(devices) == 0 {
		return FabricPlan{}, fmt.Errorf("no ready devices with known memory satisfy this fabric request")
	}
	reserveFactor := 1 - request.ReservePercent/100
	for index := range devices {
		devices[index].AvailableGiB = roundGiB(devices[index].AvailableGiB * reserveFactor)
	}
	strategy := request.Strategy
	if strategy == "auto" {
		strategy = "shard"
		for _, device := range devices {
			if device.AvailableGiB >= request.TensorGiB {
				strategy = "single-device"
				break
			}
		}
	}
	plan := FabricPlan{
		ID: id, Name: request.Name, ClusterID: request.ClusterID, TensorName: request.TensorName,
		LogicalAddress: "hypha://" + id + "/" + request.TensorName, TensorGiB: roundGiB(request.TensorGiB),
		Strategy: strategy, Consistency: request.Consistency, Status: "ready", HardwareCoherent: false,
		Placements: []FabricPlacement{}, Warnings: warnings, CreatedAt: time.Now().UTC(),
	}
	vendors := map[string]bool{}
	for _, device := range devices {
		vendors[device.Vendor] = true
		plan.UsableCapacityGiB += device.AvailableGiB
	}
	plan.UsableCapacityGiB = roundGiB(plan.UsableCapacityGiB)
	if len(vendors) > 1 {
		plan.Warnings = append(plan.Warnings, "mixed-vendor execution requires backend-specific kernels and explicit transfers; no direct cross-vendor pointer coherence is assumed")
	}
	if request.Consistency == "reduce" && len(vendors) > 1 {
		plan.Warnings = append(plan.Warnings, "cross-vendor reduction uses staged collectives and should be benchmarked before production training")
	}
	switch strategy {
	case "single-device":
		for _, device := range devices {
			if device.AvailableGiB >= request.TensorGiB {
				plan.Placements = append(plan.Placements, FabricPlacement{DeviceID: device.ID, Node: device.Node, Vendor: device.Vendor, Runtime: device.Runtime, Model: device.Model, Tier: device.Tier, LengthGiB: roundGiB(request.TensorGiB), TransferMode: transferMode(device)})
				break
			}
		}
	case "replicate":
		for replica, device := range devices {
			if device.AvailableGiB < request.TensorGiB {
				continue
			}
			plan.Placements = append(plan.Placements, FabricPlacement{DeviceID: device.ID, Node: device.Node, Vendor: device.Vendor, Runtime: device.Runtime, Model: device.Model, Tier: device.Tier, LengthGiB: roundGiB(request.TensorGiB), Replica: replica, TransferMode: transferMode(device)})
		}
		if len(plan.Placements) == 0 {
			return FabricPlan{}, fmt.Errorf("no device can hold one complete %.3f GiB replica after reserve", request.TensorGiB)
		}
	case "shard":
		remaining, offset := request.TensorGiB, 0.0
		for _, device := range devices {
			if remaining <= 0 {
				break
			}
			length := math.Min(remaining, device.AvailableGiB)
			if length <= 0 {
				continue
			}
			plan.Placements = append(plan.Placements, FabricPlacement{DeviceID: device.ID, Node: device.Node, Vendor: device.Vendor, Runtime: device.Runtime, Model: device.Model, Tier: device.Tier, OffsetGiB: roundGiB(offset), LengthGiB: roundGiB(length), TransferMode: transferMode(device)})
			offset, remaining = offset+length, remaining-length
		}
		if remaining > 0.0005 {
			return FabricPlan{}, fmt.Errorf("tensor requires %.3f GiB but selected fabric has only %.3f GiB usable after reserve", request.TensorGiB, request.TensorGiB-remaining)
		}
	}
	plan.SelectedDevices = len(plan.Placements)
	for _, placement := range plan.Placements {
		plan.PhysicalFootprintGiB += placement.LengthGiB
	}
	plan.PhysicalFootprintGiB = roundGiB(plan.PhysicalFootprintGiB)
	plan.Warnings = append(plan.Warnings, "Hypha provides a logical tensor address and transfer plan; it does not claim hardware-coherent cross-vendor VRAM")
	return plan, nil
}

func loadFabricPlan(ctx context.Context, id string) (FabricPlan, error) {
	var payload []byte
	if err := database.QueryRow(ctx, `SELECT plan FROM fabric_plans WHERE id=$1`, id).Scan(&payload); err != nil {
		return FabricPlan{}, err
	}
	var plan FabricPlan
	if err := json.Unmarshal(payload, &plan); err != nil {
		return FabricPlan{}, err
	}
	return plan, nil
}

func hydrateFabricContract(ctx context.Context, workload *Workload) error {
	if workload.FabricPlanID == "" {
		return nil
	}
	if database == nil {
		return fmt.Errorf("PostgreSQL is required to load Hypha fabric plan %s", workload.FabricPlanID)
	}
	plan, err := loadFabricPlan(ctx, workload.FabricPlanID)
	if err != nil {
		return fmt.Errorf("load Hypha fabric plan %s: %w", workload.FabricPlanID, err)
	}
	if plan.ClusterID != workload.ClusterID {
		return fmt.Errorf("Hypha fabric plan belongs to a different cluster")
	}
	payload, err := json.Marshal(plan)
	if err != nil {
		return fmt.Errorf("encode Hypha fabric plan: %w", err)
	}
	workload.FabricAddress, workload.FabricMode, workload.FabricPlan = plan.LogicalAddress, plan.Consistency, string(payload)
	return nil
}

func fabricCapabilitiesHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	defaultMemory, _ := strconv.ParseFloat(r.URL.Query().Get("defaultDeviceMemoryGiB"), 64)
	nodes, err := loadClusterNodes(r.Context(), r.URL.Query().Get("clusterId"))
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot load fabric inventory"})
		return
	}
	devices, warnings := fabricDevices(nodes, FabricPlanRequest{ClusterID: r.URL.Query().Get("clusterId"), DefaultDeviceMemoryGiB: defaultMemory, IncludeHostMemory: r.URL.Query().Get("includeHostMemory") == "true"})
	writeJSON(w, 200, map[string]any{"devices": devices, "warnings": warnings, "hardwareCoherent": false, "addressModel": "partitioned logical tensor space", "consistencyModes": []string{"immutable", "single-writer", "reduce", "transactional"}, "backends": []string{"cuda", "rocm", "oneapi", "metal", "host"}})
}

func fabricPlansHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT plan FROM fabric_plans ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list fabric plans"})
			return
		}
		defer rows.Close()
		plans := []FabricPlan{}
		for rows.Next() {
			var payload []byte
			var plan FabricPlan
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
	var request FabricPlanRequest
	if err := decode(r, &request); err != nil {
		writeJSON(w, 400, map[string]string{"error": "valid fabric plan JSON is required"})
		return
	}
	if err := validateFabricRequest(&request); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	nodes, err := loadClusterNodes(r.Context(), request.ClusterID)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot load cluster inventory"})
		return
	}
	plan, err := buildFabricPlan("fabric_"+randomToken(9), request, nodes)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": err.Error()})
		return
	}
	requestJSON, _ := json.Marshal(request)
	planJSON, _ := json.Marshal(plan)
	if _, err = database.Exec(r.Context(), `INSERT INTO fabric_plans(id,name,cluster_id,request,plan,status,created_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, plan.ID, plan.Name, plan.ClusterID, requestJSON, planJSON, plan.Status, plan.CreatedAt); err != nil {
		writeJSON(w, 409, map[string]string{"error": "fabric plan could not be stored"})
		return
	}
	auditRequest(r, "fabric.plan_created", map[string]any{"fabric_plan_id": plan.ID, "cluster_id": plan.ClusterID, "tensor_gib": plan.TensorGiB, "devices": plan.SelectedDevices})
	publishEvent("fabric.plan_created", plan)
	writeJSON(w, 201, plan)
}

func fabricPlanResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/fabric/plans/"), "/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "fabric plan not found"})
		return
	}
	if r.Method == http.MethodGet {
		plan, err := loadFabricPlan(r.Context(), id)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "fabric plan not found"})
			return
		}
		writeJSON(w, 200, plan)
		return
	}
	if r.Method == http.MethodDelete {
		state.RLock()
		for _, workload := range state.Workloads {
			if workload.FabricPlanID == id {
				state.RUnlock()
				writeJSON(w, 409, map[string]string{"error": "detach or delete workloads using this fabric plan first"})
				return
			}
		}
		state.RUnlock()
		result, err := database.Exec(r.Context(), `DELETE FROM fabric_plans WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete fabric plan"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "fabric plan not found"})
			return
		}
		auditRequest(r, "fabric.plan_deleted", map[string]any{"fabric_plan_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
}
