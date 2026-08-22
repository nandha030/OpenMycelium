package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"golang.org/x/crypto/bcrypt"
	"k8s.io/apimachinery/pkg/util/validation"
)

type Cluster struct {
	ID            string    `json:"id"`
	Name          string    `json:"name"`
	Endpoint      string    `json:"endpoint"`
	Type          string    `json:"type"`
	Status        string    `json:"status"`
	Nodes         int       `json:"nodes"`
	ReadyNodes    int       `json:"readyNodes"`
	Accelerators  int       `json:"accelerators"`
	Version       string    `json:"version"`
	Namespace     string    `json:"namespace,omitempty"`
	StorageClass  string    `json:"storageClass,omitempty"`
	Authenticated bool      `json:"authenticated"`
	CreatedAt     time.Time `json:"createdAt"`
	UpdatedAt     time.Time `json:"updatedAt"`
}

type ManagedPool struct {
	ID              string    `json:"id"`
	Name            string    `json:"name"`
	Vendor          string    `json:"vendor"`
	Runtime         string    `json:"runtime"`
	Policy          string    `json:"policy"`
	Selector        string    `json:"selector"`
	ResourceName    string    `json:"resourceName,omitempty"`
	SharingMode     string    `json:"sharingMode"`
	SliceProfile    string    `json:"sliceProfile,omitempty"`
	SharingReplicas int       `json:"sharingReplicas"`
	Enabled         bool      `json:"enabled"`
	NodeCount       int       `json:"nodeCount"`
	ClusterCount    int       `json:"clusterCount"`
	Capacity        int64     `json:"capacity"`
	Allocatable     int64     `json:"allocatable"`
	Available       int64     `json:"available"`
	Status          string    `json:"status"`
	CreatedAt       time.Time `json:"createdAt"`
}

type NodeAccelerator struct {
	Resource    string `json:"resource"`
	Vendor      string `json:"vendor"`
	Runtime     string `json:"runtime"`
	Model       string `json:"model,omitempty"`
	Capacity    int64  `json:"capacity"`
	Allocatable int64  `json:"allocatable"`
	Available   int64  `json:"available"`
}

type ClusterNode struct {
	ClusterID         string            `json:"clusterId"`
	ClusterName       string            `json:"clusterName"`
	Name              string            `json:"name"`
	Ready             bool              `json:"ready"`
	Schedulable       bool              `json:"schedulable"`
	Roles             []string          `json:"roles"`
	InternalIP        string            `json:"internalIp"`
	OS                string            `json:"os"`
	Architecture      string            `json:"architecture"`
	KernelVersion     string            `json:"kernelVersion"`
	ContainerRuntime  string            `json:"containerRuntime"`
	KubeletVersion    string            `json:"kubeletVersion"`
	CPUCapacity       string            `json:"cpuCapacity"`
	CPUAllocatable    string            `json:"cpuAllocatable"`
	CPUAvailable      string            `json:"cpuAvailable"`
	MemoryCapacity    string            `json:"memoryCapacity"`
	MemoryAllocatable string            `json:"memoryAllocatable"`
	MemoryAvailable   string            `json:"memoryAvailable"`
	PodCapacity       int64             `json:"podCapacity"`
	RunningPods       int               `json:"runningPods"`
	Accelerators      []NodeAccelerator `json:"accelerators"`
	Fabric            NodeFabricStatus  `json:"fabric"`
	Labels            map[string]string `json:"labels"`
	Taints            []string          `json:"taints"`
	InventoryWarning  string            `json:"inventoryWarning,omitempty"`
	UpdatedAt         time.Time         `json:"updatedAt"`
}

type Queue struct {
	ID                string    `json:"id"`
	Name              string    `json:"name"`
	Priority          int       `json:"priority"`
	AcceleratorQuota  int       `json:"acceleratorQuota"`
	MemoryQuotaGB     int       `json:"memoryQuotaGB"`
	PreemptionEnabled bool      `json:"preemptionEnabled"`
	CreatedAt         time.Time `json:"createdAt"`
}

type Integration struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Type      string    `json:"type"`
	Target    string    `json:"target"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"createdAt"`
}

func validExtendedResourceName(value string) bool {
	parts := strings.SplitN(value, "/", 2)
	return len(parts) == 2 && parts[0] != "" && parts[1] != "" && parts[0] != "kubernetes.io" && !strings.HasSuffix(parts[0], ".kubernetes.io") && len(validation.IsQualifiedName(value)) == 0
}

func validKubernetesQualifiedName(value string) bool {
	return value != "" && len(validation.IsQualifiedName(value)) == 0
}

var resourceSchema = []string{
	`CREATE TABLE IF NOT EXISTS clusters (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, endpoint TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL, nodes INTEGER NOT NULL DEFAULT 0, accelerators INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`ALTER TABLE clusters ADD COLUMN IF NOT EXISTS ready_nodes INTEGER NOT NULL DEFAULT 0`,
	`ALTER TABLE clusters ADD COLUMN IF NOT EXISTS version TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE clusters ADD COLUMN IF NOT EXISTS kubeconfig BYTEA NOT NULL DEFAULT ''::bytea`,
	`ALTER TABLE clusters ADD COLUMN IF NOT EXISTS namespace TEXT NOT NULL DEFAULT 'openmycelium-workloads'`,
	`ALTER TABLE clusters ADD COLUMN IF NOT EXISTS storage_class TEXT NOT NULL DEFAULT ''`,
	`CREATE TABLE IF NOT EXISTS cluster_nodes (cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE, name TEXT NOT NULL, inventory JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(cluster_id,name))`,
	`CREATE INDEX IF NOT EXISTS cluster_nodes_updated_idx ON cluster_nodes(updated_at DESC)`,
	`CREATE TABLE IF NOT EXISTS accelerator_pools (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, vendor TEXT NOT NULL, runtime TEXT NOT NULL, policy TEXT NOT NULL, selector TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`ALTER TABLE accelerator_pools ADD COLUMN IF NOT EXISTS resource_name TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE accelerator_pools ADD COLUMN IF NOT EXISTS sharing_mode TEXT NOT NULL DEFAULT 'exclusive'`,
	`ALTER TABLE accelerator_pools ADD COLUMN IF NOT EXISTS slice_profile TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE accelerator_pools ADD COLUMN IF NOT EXISTS sharing_replicas INTEGER NOT NULL DEFAULT 1`,
	`CREATE TABLE IF NOT EXISTS queues (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, priority INTEGER NOT NULL, accelerator_quota INTEGER NOT NULL, memory_quota_gb INTEGER NOT NULL, preemption_enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS integrations (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, type TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'configured', created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS fabric_plans (id TEXT PRIMARY KEY, name TEXT NOT NULL, cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE, request JSONB NOT NULL, plan JSONB NOT NULL, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS fabric_plans_cluster_idx ON fabric_plans(cluster_id,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS fabric_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE, profile JSONB NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS fabric_profiles_cluster_idx ON fabric_profiles(cluster_id,updated_at DESC)`,
	`CREATE TABLE IF NOT EXISTS execution_plans (id TEXT PRIMARY KEY, name TEXT NOT NULL, cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE, request JSONB NOT NULL, plan JSONB NOT NULL, status TEXT NOT NULL, executable BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS execution_plans_cluster_idx ON execution_plans(cluster_id,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS lab_assets (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, payload JSONB NOT NULL, created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(kind,name))`,
	`CREATE INDEX IF NOT EXISTS lab_assets_kind_idx ON lab_assets(kind,updated_at DESC)`,
	`CREATE TABLE IF NOT EXISTS model_artifacts (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL, framework TEXT NOT NULL DEFAULT '', format TEXT NOT NULL DEFAULT '', license TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS model_versions (id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES model_artifacts(id) ON DELETE CASCADE, version TEXT NOT NULL, source_uri TEXT NOT NULL, digest TEXT NOT NULL DEFAULT '', size_bytes BIGINT NOT NULL DEFAULT 0, quantization TEXT NOT NULL DEFAULT '', parameters_b DOUBLE PRECISION NOT NULL DEFAULT 0, runtime TEXT NOT NULL DEFAULT 'custom', status TEXT NOT NULL DEFAULT 'registered', metadata JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(model_id,version))`,
	`CREATE INDEX IF NOT EXISTS model_versions_model_idx ON model_versions(model_id,created_at DESC)`,
	`CREATE TABLE IF NOT EXISTS model_aliases (model_id TEXT NOT NULL REFERENCES model_artifacts(id) ON DELETE CASCADE, alias TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(model_id,alias))`,
	`CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, organization TEXT NOT NULL, cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE, namespace TEXT NOT NULL, queue TEXT NOT NULL DEFAULT 'default', storage_class TEXT NOT NULL DEFAULT '', cpu_quota TEXT NOT NULL DEFAULT '', memory_quota_gb INTEGER NOT NULL DEFAULT 0, accelerator_quota INTEGER NOT NULL DEFAULT 0, network_policy TEXT NOT NULL DEFAULT 'cluster-default', created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(cluster_id,namespace))`,
	`CREATE INDEX IF NOT EXISTS workspaces_cluster_idx ON workspaces(cluster_id,namespace)`,
	`CREATE TABLE IF NOT EXISTS workspace_releases (id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE, name TEXT NOT NULL, source_type TEXT NOT NULL, source_ref TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, summary JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE INDEX IF NOT EXISTS workspace_releases_workspace_idx ON workspace_releases(workspace_id,created_at DESC)`,
}

func requireDatabase(w http.ResponseWriter) bool {
	if database == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "PostgreSQL is required for this resource"})
		return false
	}
	return true
}

func requestActor(r *http.Request) string {
	if user, ok := currentUser(r); ok {
		return user.Email
	}
	return "system"
}

func auditRequest(r *http.Request, subject string, payload map[string]any) {
	payload["actor"] = requestActor(r)
	audit(subject, payload)
}

func clustersHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,endpoint,type,status,nodes,ready_nodes,accelerators,version,namespace,storage_class,octet_length(kubeconfig)>0,created_at,updated_at FROM clusters ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list clusters"})
			return
		}
		defer rows.Close()
		items := []Cluster{}
		for rows.Next() {
			var item Cluster
			if rows.Scan(&item.ID, &item.Name, &item.Endpoint, &item.Type, &item.Status, &item.Nodes, &item.ReadyNodes, &item.Accelerators, &item.Version, &item.Namespace, &item.StorageClass, &item.Authenticated, &item.CreatedAt, &item.UpdatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"clusters": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct {
		Name         string `json:"name"`
		Endpoint     string `json:"endpoint"`
		Type         string `json:"type"`
		Kubeconfig   string `json:"kubeconfig"`
		Namespace    string `json:"namespace"`
		StorageClass string `json:"storageClass"`
	}
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" {
		writeJSON(w, 400, map[string]string{"error": "cluster name is required"})
		return
	}
	if input.Type == "" {
		input.Type = "kubernetes"
	}
	input.Endpoint = strings.TrimSpace(input.Endpoint)
	input.Namespace = strings.TrimSpace(input.Namespace)
	if input.Namespace == "" {
		input.Namespace = defaultWorkloadNamespace
	}
	var credential []byte
	var discoveredNodes []ClusterNode
	item := Cluster{ID: "clu_" + randomToken(9), Name: strings.TrimSpace(input.Name), Endpoint: input.Endpoint, Type: input.Type, Status: "pending", Namespace: input.Namespace, StorageClass: strings.TrimSpace(input.StorageClass), CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	if input.Type == "kubernetes" {
		if strings.TrimSpace(input.Kubeconfig) == "" {
			writeJSON(w, 400, map[string]string{"error": "kubeconfig is required for an authenticated Kubernetes connection"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
		result, probeErr := probeKubernetesClusterInventory(ctx, []byte(input.Kubeconfig), input.Endpoint, item.ID, item.Name)
		cancel()
		if probeErr != nil {
			writeJSON(w, 400, map[string]string{"error": probeErr.Error()})
			return
		}
		credential, probeErr = encryptClusterCredential([]byte(input.Kubeconfig))
		if probeErr != nil {
			writeJSON(w, 500, map[string]string{"error": probeErr.Error()})
			return
		}
		probe := result.Cluster
		item.Endpoint, item.Status, item.Nodes, item.ReadyNodes, item.Accelerators, item.Version, item.Authenticated = probe.Endpoint, probe.Status, probe.Nodes, probe.ReadyNodes, probe.Accelerators, probe.Version, true
		discoveredNodes = result.Nodes
	} else if item.Endpoint == "" {
		writeJSON(w, 400, map[string]string{"error": "endpoint is required"})
		return
	}
	_, err := database.Exec(r.Context(), `INSERT INTO clusters(id,name,endpoint,type,status,nodes,ready_nodes,accelerators,version,kubeconfig,namespace,storage_class,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, item.ID, item.Name, item.Endpoint, item.Type, item.Status, item.Nodes, item.ReadyNodes, item.Accelerators, item.Version, credential, item.Namespace, item.StorageClass, item.CreatedAt, item.UpdatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a cluster with that name already exists"})
		return
	}
	if err = persistClusterNodes(r.Context(), item.ID, item.Name, discoveredNodes); err != nil {
		_, _ = database.Exec(r.Context(), `DELETE FROM clusters WHERE id=$1`, item.ID)
		writeJSON(w, 500, map[string]string{"error": "cluster connected but node inventory could not be stored"})
		return
	}
	auditRequest(r, "cluster.created", map[string]any{"cluster_id": item.ID, "name": item.Name, "authenticated": item.Authenticated})
	publishEvent("cluster.created", item)
	writeJSON(w, 201, item)
}

func clusterResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/clusters/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" || len(parts) > 2 {
		writeJSON(w, 404, map[string]string{"error": "cluster not found"})
		return
	}
	id := parts[0]
	if len(parts) == 2 && parts[1] == "refresh" && clusterCredentialsStatusHandler(w, r, id) {
		return
	}
	if len(parts) == 2 && parts[1] == "nodes" && r.Method == http.MethodGet {
		items, err := loadClusterNodes(r.Context(), id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot load cluster node inventory"})
			return
		}
		writeJSON(w, 200, map[string]any{"nodes": items})
		return
	}
	if len(parts) == 1 && r.Method == http.MethodDelete {
		state.RLock()
		for _, workload := range state.Workloads {
			if workload.ClusterID == id {
				state.RUnlock()
				writeJSON(w, 409, map[string]string{"error": "delete workloads assigned to this cluster before removing the cluster connection"})
				return
			}
		}
		state.RUnlock()
		result, err := database.Exec(r.Context(), `DELETE FROM clusters WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete cluster"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "cluster not found"})
			return
		}
		auditRequest(r, "cluster.deleted", map[string]any{"cluster_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	if len(parts) == 2 && parts[1] == "inventory" && r.Method == http.MethodPatch {
		var input struct {
			Nodes        int    `json:"nodes"`
			ReadyNodes   int    `json:"readyNodes"`
			Accelerators int    `json:"accelerators"`
			Version      string `json:"version"`
		}
		if decode(r, &input) != nil || input.Nodes < 0 || input.ReadyNodes < 0 || input.ReadyNodes > input.Nodes || input.Accelerators < 0 {
			writeJSON(w, 400, map[string]string{"error": "valid node, ready-node, and accelerator counts are required"})
			return
		}
		status := "pending"
		if input.Nodes > 0 && input.ReadyNodes == input.Nodes {
			status = "ready"
		} else if input.Nodes > 0 {
			status = "degraded"
		}
		var item Cluster
		err := database.QueryRow(r.Context(), `UPDATE clusters SET status=$1,nodes=$2,ready_nodes=$3,accelerators=$4,version=$5,updated_at=now() WHERE id=$6 RETURNING id,name,endpoint,type,status,nodes,ready_nodes,accelerators,version,namespace,storage_class,octet_length(kubeconfig)>0,created_at,updated_at`, status, input.Nodes, input.ReadyNodes, input.Accelerators, strings.TrimSpace(input.Version), id).Scan(&item.ID, &item.Name, &item.Endpoint, &item.Type, &item.Status, &item.Nodes, &item.ReadyNodes, &item.Accelerators, &item.Version, &item.Namespace, &item.StorageClass, &item.Authenticated, &item.CreatedAt, &item.UpdatedAt)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "cluster not found"})
			return
		}
		auditRequest(r, "cluster.inventory_reported", map[string]any{"cluster_id": item.ID, "nodes": item.Nodes, "ready_nodes": item.ReadyNodes, "accelerators": item.Accelerators, "version": item.Version})
		publishEvent("cluster.inventory_reported", item)
		writeJSON(w, 200, item)
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func managedPoolsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,vendor,runtime,policy,selector,resource_name,sharing_mode,slice_profile,sharing_replicas,enabled,created_at FROM accelerator_pools ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list pools"})
			return
		}
		defer rows.Close()
		items := []ManagedPool{}
		for rows.Next() {
			var item ManagedPool
			if rows.Scan(&item.ID, &item.Name, &item.Vendor, &item.Runtime, &item.Policy, &item.Selector, &item.ResourceName, &item.SharingMode, &item.SliceProfile, &item.SharingReplicas, &item.Enabled, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"pools": enrichPoolCapacities(r.Context(), items)})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input ManagedPool
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.Runtime) == "" {
		writeJSON(w, 400, map[string]string{"error": "name and runtime are required"})
		return
	}
	if input.Vendor == "" {
		input.Vendor = "mixed"
	}
	if input.Policy == "" {
		input.Policy = "compatible-runtime"
	}
	input.ResourceName = strings.TrimSpace(input.ResourceName)
	input.SharingMode = strings.ToLower(strings.TrimSpace(input.SharingMode))
	if input.SharingMode == "" {
		input.SharingMode = "exclusive"
	}
	if input.SharingMode != "exclusive" && input.SharingMode != "mig" && input.SharingMode != "time-slicing" && input.SharingMode != "mps" && input.SharingMode != "device-plugin" {
		writeJSON(w, 400, map[string]string{"error": "sharingMode must be exclusive, mig, time-slicing, mps, or device-plugin"})
		return
	}
	if input.ResourceName != "" && !validExtendedResourceName(input.ResourceName) {
		writeJSON(w, 400, map[string]string{"error": "resourceName must be a valid Kubernetes extended resource name"})
		return
	}
	if input.SharingMode != "exclusive" && input.ResourceName == "" {
		writeJSON(w, 400, map[string]string{"error": "shared and sliced pools require the exact resource name advertised by the device plugin"})
		return
	}
	if input.SharingMode == "mig" && !strings.HasPrefix(input.ResourceName, "nvidia.com/mig-") {
		writeJSON(w, 400, map[string]string{"error": "MIG pools require an nvidia.com/mig-* resource name"})
		return
	}
	if input.SharingReplicas < 1 {
		input.SharingReplicas = 1
	}
	if input.SharingReplicas > 128 {
		writeJSON(w, 400, map[string]string{"error": "sharingReplicas cannot exceed 128"})
		return
	}
	input.SliceProfile = strings.TrimSpace(input.SliceProfile)
	input.ID = "pool_" + randomToken(9)
	input.Enabled = true
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO accelerator_pools(id,name,vendor,runtime,policy,selector,resource_name,sharing_mode,slice_profile,sharing_replicas,enabled,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, input.ID, input.Name, input.Vendor, input.Runtime, input.Policy, input.Selector, input.ResourceName, input.SharingMode, input.SliceProfile, input.SharingReplicas, input.Enabled, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a pool with that name already exists"})
		return
	}
	auditRequest(r, "pool.created", map[string]any{"pool_id": input.ID, "name": input.Name})
	publishEvent("pool.created", input)
	writeJSON(w, 201, input)
}

func poolResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/pools/")
	if r.Method == http.MethodDelete {
		result, err := database.Exec(r.Context(), `DELETE FROM accelerator_pools WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete pool"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "pool not found"})
			return
		}
		auditRequest(r, "pool.deleted", map[string]any{"pool_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func queuesHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,priority,accelerator_quota,memory_quota_gb,preemption_enabled,created_at FROM queues ORDER BY priority DESC,name`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list queues"})
			return
		}
		defer rows.Close()
		items := []Queue{}
		for rows.Next() {
			var item Queue
			if rows.Scan(&item.ID, &item.Name, &item.Priority, &item.AcceleratorQuota, &item.MemoryQuotaGB, &item.PreemptionEnabled, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"queues": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input Queue
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || input.Priority < 0 || input.Priority > 100 || input.AcceleratorQuota < 0 || input.MemoryQuotaGB < 0 {
		writeJSON(w, 400, map[string]string{"error": "valid name, priority (0-100), and non-negative quotas are required"})
		return
	}
	input.ID = "que_" + randomToken(9)
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO queues(id,name,priority,accelerator_quota,memory_quota_gb,preemption_enabled,created_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, input.ID, input.Name, input.Priority, input.AcceleratorQuota, input.MemoryQuotaGB, input.PreemptionEnabled, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a queue with that name already exists"})
		return
	}
	auditRequest(r, "queue.created", map[string]any{"queue_id": input.ID, "name": input.Name})
	publishEvent("queue.created", input)
	writeJSON(w, 201, input)
}

func queueResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/queues/")
	if r.Method == http.MethodDelete {
		result, err := database.Exec(r.Context(), `DELETE FROM queues WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete queue"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "queue not found"})
			return
		}
		auditRequest(r, "queue.deleted", map[string]any{"queue_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func integrationsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,type,target,status,created_at FROM integrations ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list integrations"})
			return
		}
		defer rows.Close()
		items := []Integration{}
		for rows.Next() {
			var item Integration
			if rows.Scan(&item.ID, &item.Name, &item.Type, &item.Target, &item.Status, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"integrations": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input Integration
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.Target) == "" {
		writeJSON(w, 400, map[string]string{"error": "name and target are required"})
		return
	}
	if input.Type == "" {
		input.Type = "mcp"
	}
	input.ID = "int_" + randomToken(9)
	input.Status = "configured"
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO integrations(id,name,type,target,status,created_at) VALUES($1,$2,$3,$4,$5,$6)`, input.ID, input.Name, input.Type, input.Target, input.Status, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "an integration with that name already exists"})
		return
	}
	auditRequest(r, "integration.created", map[string]any{"integration_id": input.ID, "name": input.Name})
	writeJSON(w, 201, input)
}

func integrationResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/integrations/")
	if r.Method != http.MethodDelete {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	result, err := database.Exec(r.Context(), `DELETE FROM integrations WHERE id=$1`, id)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot delete integration"})
		return
	}
	if result.RowsAffected() == 0 {
		writeJSON(w, 404, map[string]string{"error": "integration not found"})
		return
	}
	auditRequest(r, "integration.deleted", map[string]any{"integration_id": id})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func usersHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodPost {
		var input struct {
			Email    string `json:"email"`
			Password string `json:"password"`
			Role     string `json:"role"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid request"})
			return
		}
		input.Email = strings.ToLower(strings.TrimSpace(input.Email))
		input.Role = strings.TrimSpace(input.Role)
		allowed := map[string]bool{"platform_admin": true, "operator": true, "viewer": true}
		if !strings.Contains(input.Email, "@") {
			writeJSON(w, 400, map[string]string{"error": "a valid email address is required"})
			return
		}
		if len(input.Password) < 12 {
			writeJSON(w, 400, map[string]string{"error": "password must contain at least 12 characters"})
			return
		}
		if !allowed[input.Role] {
			writeJSON(w, 400, map[string]string{"error": "supported roles: platform_admin, operator, viewer"})
			return
		}
		hash, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot secure account password"})
			return
		}
		item := map[string]any{
			"id":        "usr_" + randomToken(12),
			"email":     input.Email,
			"role":      input.Role,
			"active":    true,
			"createdAt": time.Now().UTC(),
		}
		_, err = database.Exec(r.Context(), `INSERT INTO users(id,email,password_hash,role,active,created_at) VALUES($1,$2,$3,$4,true,$5)`, item["id"], item["email"], string(hash), item["role"], item["createdAt"])
		if err != nil {
			writeJSON(w, 409, map[string]string{"error": "an account with that email address already exists"})
			return
		}
		auditRequest(r, "user.created", map[string]any{"user_id": item["id"], "email": input.Email, "role": input.Role})
		publishEvent("user.created", item)
		writeJSON(w, 201, item)
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	rows, err := database.Query(r.Context(), `SELECT id,email,role,active,created_at FROM users ORDER BY created_at`)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot list users"})
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id, email, role string
		var active bool
		var created time.Time
		if rows.Scan(&id, &email, &role, &active, &created) == nil {
			items = append(items, map[string]any{"id": id, "email": email, "role": role, "active": active, "createdAt": created})
		}
	}
	writeJSON(w, 200, map[string]any{"users": items})
}

func userResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodPatch && r.Method != http.MethodDelete {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/users/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "user not found"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	var targetRole string
	var targetActive bool
	if err := database.QueryRow(ctx, `SELECT role,active FROM users WHERE id=$1`, id).Scan(&targetRole, &targetActive); err != nil {
		writeJSON(w, 404, map[string]string{"error": "user not found"})
		return
	}
	actor, _ := currentUser(r)
	if r.Method == http.MethodDelete {
		if actor.ID == id {
			writeJSON(w, 409, map[string]string{"error": "you cannot delete your own active administrator account"})
			return
		}
		if targetRole == "platform_admin" && targetActive {
			var administrators int
			if err := database.QueryRow(ctx, `SELECT count(*) FROM users WHERE role='platform_admin' AND active=true`).Scan(&administrators); err != nil || administrators <= 1 {
				writeJSON(w, 409, map[string]string{"error": "at least one active platform administrator is required"})
				return
			}
		}
		if _, err := database.Exec(ctx, `DELETE FROM users WHERE id=$1`, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete account"})
			return
		}
		auditRequest(r, "user.deleted", map[string]any{"user_id": id, "role": targetRole})
		publishEvent("user.deleted", map[string]string{"id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	var input struct {
		Role   string `json:"role"`
		Active *bool  `json:"active"`
	}
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid request"})
		return
	}
	allowed := map[string]bool{"platform_admin": true, "operator": true, "viewer": true}
	if input.Role != "" && !allowed[input.Role] {
		writeJSON(w, 400, map[string]string{"error": "supported roles: platform_admin, operator, viewer"})
		return
	}
	disabling := input.Active != nil && !*input.Active
	demoting := input.Role != "" && input.Role != "platform_admin"
	if actor.ID == id && (disabling || demoting) {
		writeJSON(w, 409, map[string]string{"error": "you cannot disable or demote your own active administrator account"})
		return
	}
	if targetRole == "platform_admin" && targetActive && (disabling || demoting) {
		var administrators int
		if err := database.QueryRow(ctx, `SELECT count(*) FROM users WHERE role='platform_admin' AND active=true`).Scan(&administrators); err != nil || administrators <= 1 {
			writeJSON(w, 409, map[string]string{"error": "at least one active platform administrator is required"})
			return
		}
	}
	if input.Role != "" {
		if _, err := database.Exec(ctx, `UPDATE users SET role=$1 WHERE id=$2`, input.Role, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot update role"})
			return
		}
	}
	if input.Active != nil {
		if _, err := database.Exec(ctx, `UPDATE users SET active=$1 WHERE id=$2`, *input.Active, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot update account"})
			return
		}
	}
	auditRequest(r, "user.updated", map[string]any{"user_id": id, "role": input.Role})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func auditEventsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	rows, err := database.Query(r.Context(), `SELECT id,subject,payload,created_at FROM audit_events ORDER BY created_at DESC LIMIT 100`)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot list audit events"})
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id int64
		var subject string
		var payload []byte
		var created time.Time
		if rows.Scan(&id, &subject, &payload, &created) == nil {
			var body any
			_ = json.Unmarshal(payload, &body)
			items = append(items, map[string]any{"id": id, "subject": subject, "payload": body, "createdAt": created})
		}
	}
	writeJSON(w, 200, map[string]any{"events": items})
}

func observabilityHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	state.RLock()
	host := state.Host
	workloads := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	running, queued, failed := 0, 0, 0
	for _, item := range workloads {
		switch item.Status {
		case "running":
			running++
		case "queued":
			queued++
		case "failed":
			failed++
		}
	}
	counts := map[string]int{"clusters": 0, "pools": 0, "queues": 0, "users": 0}
	if database != nil {
		for table := range counts {
			queryTable := table
			if table == "pools" {
				queryTable = "accelerator_pools"
			}
			var count int
			if err := database.QueryRow(r.Context(), `SELECT count(*) FROM `+queryTable).Scan(&count); err == nil {
				counts[table] = count
			}
		}
	}
	natsReady := eventBus != nil && eventBus.IsConnected()
	writeJSON(w, 200, map[string]any{"services": map[string]any{"api": "healthy", "postgres": database != nil, "nats": natsReady, "ollama": ollamaBaseURL()}, "inventory": map[string]any{"host": host.Name, "accelerators": len(host.Accelerators), "clusters": counts["clusters"], "pools": counts["pools"]}, "workloads": map[string]int{"total": len(workloads), "running": running, "queued": queued, "failed": failed}, "governance": map[string]int{"users": counts["users"], "queues": counts["queues"]}, "timestamp": time.Now().UTC()})
}

func metricsHandler(w http.ResponseWriter, r *http.Request) {
	token := os.Getenv("METRICS_TOKEN")
	if token != "" {
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if len(provided) != len(token) || subtle.ConstantTimeCompare([]byte(provided), []byte(token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "valid metrics bearer token required"})
			return
		}
	} else if _, ok := currentUser(r); !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "authentication required"})
		return
	}

	state.RLock()
	host := state.Host
	workloads := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	mlops := collectMLOps(r.Context())
	schedulerCounts := map[string]int{}
	gangWorkloads := 0
	parallelWorkloads := 0
	multiAcceleratorWorkloads := 0
	for _, workload := range workloads {
		if workload.Runtime != "kubernetes" {
			continue
		}
		backend := workload.SchedulerBackend
		if backend == "" {
			backend = "kubernetes"
		}
		schedulerCounts[backend]++
		if workload.GangMinAvailable > 1 {
			gangWorkloads++
		}
		if workload.DesiredCount > 1 {
			parallelWorkloads++
		}
		if int32(workload.Accelerators)*workload.DesiredCount > 1 {
			multiAcceleratorWorkloads++
		}
	}

	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintln(w, "# HELP openmycelium_up Whether the control plane is serving requests.")
	fmt.Fprintln(w, "# TYPE openmycelium_up gauge")
	fmt.Fprintln(w, "openmycelium_up 1")
	fmt.Fprintln(w, "# HELP openmycelium_accelerators Number of accelerators reported by the host agent.")
	fmt.Fprintln(w, "# TYPE openmycelium_accelerators gauge")
	fmt.Fprintf(w, "openmycelium_accelerators %d\n", len(host.Accelerators))
	fmt.Fprintln(w, "# HELP openmycelium_workloads Workloads by lifecycle status.")
	fmt.Fprintln(w, "# TYPE openmycelium_workloads gauge")
	for _, item := range mlops.WorkloadStatus {
		fmt.Fprintf(w, "openmycelium_workloads{status=\"%s\"} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_workload_kinds Workloads by execution kind.")
	fmt.Fprintln(w, "# TYPE openmycelium_workload_kinds gauge")
	for _, item := range mlops.WorkloadKinds {
		fmt.Fprintf(w, "openmycelium_workload_kinds{kind=\"%s\"} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_workload_runtimes Workloads by model or execution runtime.")
	fmt.Fprintln(w, "# TYPE openmycelium_workload_runtimes gauge")
	for _, item := range mlops.Runtimes {
		fmt.Fprintf(w, "openmycelium_workload_runtimes{runtime=\"%s\"} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_workload_schedulers Kubernetes workloads by scheduler backend.")
	fmt.Fprintln(w, "# TYPE openmycelium_workload_schedulers gauge")
	for _, item := range dimensions(schedulerCounts) {
		fmt.Fprintf(w, "openmycelium_workload_schedulers{backend=\"%s\"} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_gang_workloads Kubernetes workloads requiring simultaneous gang admission.")
	fmt.Fprintln(w, "# TYPE openmycelium_gang_workloads gauge")
	fmt.Fprintf(w, "openmycelium_gang_workloads %d\n", gangWorkloads)
	fmt.Fprintln(w, "# HELP openmycelium_parallel_workloads Kubernetes workloads with more than one replica or worker.")
	fmt.Fprintln(w, "# TYPE openmycelium_parallel_workloads gauge")
	fmt.Fprintf(w, "openmycelium_parallel_workloads %d\n", parallelWorkloads)
	fmt.Fprintln(w, "# HELP openmycelium_multi_accelerator_workloads Kubernetes workloads requesting more than one accelerator in aggregate.")
	fmt.Fprintln(w, "# TYPE openmycelium_multi_accelerator_workloads gauge")
	fmt.Fprintf(w, "openmycelium_multi_accelerator_workloads %d\n", multiAcceleratorWorkloads)
	fmt.Fprintln(w, "# HELP openmycelium_workload_restarts Current cumulative container restart count across managed workloads.")
	fmt.Fprintln(w, "# TYPE openmycelium_workload_restarts gauge")
	fmt.Fprintf(w, "openmycelium_workload_restarts %v\n", mlops.Summary["workloadRestarts"])
	fmt.Fprintln(w, "# HELP openmycelium_models Governed model artifacts.")
	fmt.Fprintln(w, "# TYPE openmycelium_models gauge")
	fmt.Fprintf(w, "openmycelium_models %v\n", mlops.Summary["models"])
	fmt.Fprintln(w, "# HELP openmycelium_model_versions Governed model versions.")
	fmt.Fprintln(w, "# TYPE openmycelium_model_versions gauge")
	fmt.Fprintf(w, "openmycelium_model_versions %v\n", mlops.Summary["modelVersions"])
	fmt.Fprintln(w, "# HELP openmycelium_model_storage_bytes Declared model artifact storage in bytes.")
	fmt.Fprintln(w, "# TYPE openmycelium_model_storage_bytes gauge")
	fmt.Fprintf(w, "openmycelium_model_storage_bytes %v\n", mlops.Summary["modelBytes"])
	fmt.Fprintln(w, "# HELP openmycelium_workspaces Governed Kubernetes workspaces.")
	fmt.Fprintln(w, "# TYPE openmycelium_workspaces gauge")
	fmt.Fprintf(w, "openmycelium_workspaces %v\n", mlops.Summary["workspaces"])
	fmt.Fprintln(w, "# HELP openmycelium_agents Governed agent definitions.")
	fmt.Fprintln(w, "# TYPE openmycelium_agents gauge")
	fmt.Fprintf(w, "openmycelium_agents %v\n", mlops.Summary["agents"])
	fmt.Fprintln(w, "# HELP openmycelium_agent_runs Agent runs by lifecycle scope.")
	fmt.Fprintln(w, "# TYPE openmycelium_agent_runs gauge")
	fmt.Fprintf(w, "openmycelium_agent_runs{state=%q} %v\n", "all", mlops.Summary["agentRuns"])
	fmt.Fprintf(w, "openmycelium_agent_runs{state=%q} %v\n", "active", mlops.Summary["activeAgentRuns"])
	fmt.Fprintln(w, "# HELP openmycelium_agent_approvals_pending Agent runs waiting for human approval.")
	fmt.Fprintln(w, "# TYPE openmycelium_agent_approvals_pending gauge")
	fmt.Fprintf(w, "openmycelium_agent_approvals_pending %v\n", mlops.Summary["pendingAgentApprovals"])
	fmt.Fprintln(w, "# HELP openmycelium_agent_evaluations Persisted agent evaluation results.")
	fmt.Fprintln(w, "# TYPE openmycelium_agent_evaluations gauge")
	fmt.Fprintf(w, "openmycelium_agent_evaluations %v\n", mlops.Summary["agentEvaluations"])

	releaseCounts := map[string]int{}
	fabricProfileCounts := map[string]int{}
	executionPlanCounts := map[string]int{}
	executionTransportCounts := map[string]int{}
	myceliumObjectiveCounts := map[string]int{}
	labAssetCounts := map[string]int{}
	fabricNodeCounts := map[string]int{"qualified": 0, "rdma": 0, "total": 0}
	userCounts := map[string]int{"active": 0, "inactive": 0}
	activeSessions, auditEvents := 0, int64(0)
	clusters := []aiopsCluster{}
	if database != nil {
		rows, err := database.Query(r.Context(), `SELECT status,count(*) FROM workspace_releases GROUP BY status`)
		if err == nil {
			for rows.Next() {
				var status string
				var count int
				if rows.Scan(&status, &count) == nil {
					releaseCounts[status] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT active,count(*) FROM users GROUP BY active`)
		if err == nil {
			for rows.Next() {
				var active bool
				var count int
				if rows.Scan(&active, &count) == nil {
					if active {
						userCounts["active"] = count
					} else {
						userCounts["inactive"] = count
					}
				}
			}
			rows.Close()
		}
		_ = database.QueryRow(r.Context(), `SELECT count(*) FROM sessions WHERE expires_at>now()`).Scan(&activeSessions)
		_ = database.QueryRow(r.Context(), `SELECT count(*) FROM audit_events`).Scan(&auditEvents)
		rows, err = database.Query(r.Context(), `SELECT id,name,status,nodes,ready_nodes,accelerators,updated_at FROM clusters ORDER BY name`)
		if err == nil {
			for rows.Next() {
				var item aiopsCluster
				if rows.Scan(&item.ID, &item.Name, &item.Status, &item.Nodes, &item.ReadyNodes, &item.Accelerators, &item.UpdatedAt) == nil {
					clusters = append(clusters, item)
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT source,count(*) FROM fabric_profiles GROUP BY source`)
		if err == nil {
			for rows.Next() {
				var label string
				var count int
				if rows.Scan(&label, &count) == nil {
					fabricProfileCounts[label] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT status,count(*) FROM execution_plans GROUP BY status`)
		if err == nil {
			for rows.Next() {
				var label string
				var count int
				if rows.Scan(&label, &count) == nil {
					executionPlanCounts[label] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT COALESCE(plan->>'transport','unknown'),count(*) FROM execution_plans GROUP BY plan->>'transport'`)
		if err == nil {
			for rows.Next() {
				var label string
				var count int
				if rows.Scan(&label, &count) == nil {
					executionTransportCounts[label] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT COALESCE(plan#>>'{optimization,objective}','unknown'),count(*) FROM execution_plans GROUP BY plan#>>'{optimization,objective}'`)
		if err == nil {
			for rows.Next() {
				var label string
				var count int
				if rows.Scan(&label, &count) == nil {
					myceliumObjectiveCounts[label] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT kind,count(*) FROM lab_assets GROUP BY kind`)
		if err == nil {
			for rows.Next() {
				var label string
				var count int
				if rows.Scan(&label, &count) == nil {
					labAssetCounts[label] = count
				}
			}
			rows.Close()
		}
		rows, err = database.Query(r.Context(), `SELECT inventory FROM cluster_nodes`)
		if err == nil {
			for rows.Next() {
				var payload []byte
				var node ClusterNode
				if rows.Scan(&payload) == nil && json.Unmarshal(payload, &node) == nil {
					fabricNodeCounts["total"]++
					if node.Fabric.Qualified {
						fabricNodeCounts["qualified"]++
					}
					if node.Fabric.RDMA {
						fabricNodeCounts["rdma"]++
					}
				}
			}
			rows.Close()
		}
	}
	fmt.Fprintln(w, "# HELP openmycelium_workspace_releases Workspace releases by reconciled state.")
	fmt.Fprintln(w, "# TYPE openmycelium_workspace_releases gauge")
	for _, item := range dimensions(releaseCounts) {
		fmt.Fprintf(w, "openmycelium_workspace_releases{status=\"%s\"} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_users Platform users by account state.")
	fmt.Fprintln(w, "# TYPE openmycelium_users gauge")
	for _, status := range []string{"active", "inactive"} {
		fmt.Fprintf(w, "openmycelium_users{status=%q} %d\n", status, userCounts[status])
	}
	fmt.Fprintln(w, "# HELP openmycelium_active_sessions Current unexpired browser sessions.")
	fmt.Fprintln(w, "# TYPE openmycelium_active_sessions gauge")
	fmt.Fprintf(w, "openmycelium_active_sessions %d\n", activeSessions)
	fmt.Fprintln(w, "# HELP openmycelium_audit_events_total Persisted audit events.")
	fmt.Fprintln(w, "# TYPE openmycelium_audit_events_total counter")
	fmt.Fprintf(w, "openmycelium_audit_events_total %d\n", auditEvents)
	fmt.Fprintln(w, "# HELP openmycelium_cluster_nodes Kubernetes nodes by readiness.")
	fmt.Fprintln(w, "# TYPE openmycelium_cluster_nodes gauge")
	for _, cluster := range clusters {
		fmt.Fprintf(w, "openmycelium_cluster_nodes{cluster=\"%s\",state=%q} %d\n", prometheusLabel(cluster.Name), "ready", cluster.ReadyNodes)
		fmt.Fprintf(w, "openmycelium_cluster_nodes{cluster=\"%s\",state=%q} %d\n", prometheusLabel(cluster.Name), "not_ready", cluster.Nodes-cluster.ReadyNodes)
	}
	fmt.Fprintln(w, "# HELP openmycelium_fabric_nodes Persisted cluster nodes by communication-fabric capability.")
	fmt.Fprintln(w, "# TYPE openmycelium_fabric_nodes gauge")
	for _, capability := range []string{"total", "qualified", "rdma"} {
		fmt.Fprintf(w, "openmycelium_fabric_nodes{capability=%q} %d\n", capability, fabricNodeCounts[capability])
	}
	fmt.Fprintln(w, "# HELP openmycelium_fabric_profiles Persisted heterogeneous accelerator benchmark profiles by source.")
	fmt.Fprintln(w, "# TYPE openmycelium_fabric_profiles gauge")
	for _, item := range dimensions(fabricProfileCounts) {
		fmt.Fprintf(w, "openmycelium_fabric_profiles{source=%q} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_execution_plans Heterogeneous execution plans by admission status.")
	fmt.Fprintln(w, "# TYPE openmycelium_execution_plans gauge")
	for _, item := range dimensions(executionPlanCounts) {
		fmt.Fprintf(w, "openmycelium_execution_plans{status=%q} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_execution_plan_transports Heterogeneous execution plans by selected transport.")
	fmt.Fprintln(w, "# TYPE openmycelium_execution_plan_transports gauge")
	for _, item := range dimensions(executionTransportCounts) {
		fmt.Fprintf(w, "openmycelium_execution_plan_transports{transport=%q} %d\n", prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_mycelium_plans Mycelium execution plans by optimization objective.")
	fmt.Fprintln(w, "# TYPE openmycelium_mycelium_plans gauge")
	for _, item := range dimensions(myceliumObjectiveCounts) {
		fmt.Fprintf(w, "openmycelium_mycelium_plans{algorithm=%q,version=%q,objective=%q} %d\n", myceliumAlgorithmName, myceliumAlgorithmVersion, prometheusLabel(item.Label), item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_lab_assets Saved virtual-lab evidence by artifact kind.")
	fmt.Fprintln(w, "# TYPE openmycelium_lab_assets gauge")
	for _, item := range dimensions(labAssetCounts) {
		fmt.Fprintf(w, "openmycelium_lab_assets{kind=%q,evidence=%q} %d\n", prometheusLabel(item.Label), "simulated", item.Value)
	}
	fmt.Fprintln(w, "# HELP openmycelium_dependency_up Control-plane dependency connectivity.")
	fmt.Fprintln(w, "# TYPE openmycelium_dependency_up gauge")
	fmt.Fprintf(w, "openmycelium_dependency_up{dependency=%q} %d\n", "postgres", boolMetric(database != nil))
	fmt.Fprintf(w, "openmycelium_dependency_up{dependency=%q} %d\n", "nats", boolMetric(eventBus != nil && eventBus.IsConnected()))
}

func prometheusLabel(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, "\n", `\n`)
	return strings.ReplaceAll(value, `"`, `\"`)
}

func boolMetric(value bool) int {
	if value {
		return 1
	}
	return 0
}
