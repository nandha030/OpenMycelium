package main

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/kubernetes"
)

type PlacementDecision struct {
	Status        string            `json:"status,omitempty"`
	SelectedNode  string            `json:"selectedNode,omitempty"`
	SelectedNodes []string          `json:"selectedNodes,omitempty"`
	Evaluated     int               `json:"evaluated,omitempty"`
	Candidates    int               `json:"candidates,omitempty"`
	Requested     map[string]string `json:"requested,omitempty"`
	Available     map[string]string `json:"available,omitempty"`
	Reasons       []string          `json:"reasons,omitempty"`
	DecidedAt     time.Time         `json:"decidedAt,omitempty"`
}

type clusterProbe struct {
	Cluster Cluster
	Nodes   []ClusterNode
}

func acceleratorMetadata(name string) (vendor, runtime string) {
	switch {
	case strings.HasPrefix(name, "nvidia.com/gpu") || strings.HasPrefix(name, "nvidia.com/mig-"):
		return "NVIDIA", "cuda"
	case name == "amd.com/gpu":
		return "AMD", "rocm"
	case name == "gpu.intel.com/i915" || name == "gpu.intel.com/xe":
		return "Intel", "oneapi"
	case name == "habana.ai/gaudi":
		return "Intel", "synapseai"
	case name == "google.com/tpu" || strings.Contains(name, "tpu"):
		return "Google", "tpu"
	default:
		return "Unknown", "unknown"
	}
}

func acceleratorModel(node corev1.Node, vendor string) string {
	keys := []string{
		"accelerator.openmycelium.io/model",
		"nvidia.com/gpu.product",
		"amd.com/gpu.product",
		"gpu.intel.com/product",
		"cloud.google.com/gke-accelerator",
	}
	for _, key := range keys {
		if value := strings.TrimSpace(node.Labels[key]); value != "" {
			return value
		}
	}
	return vendor + " accelerator"
}

func podResourceRequests(pod corev1.Pod) corev1.ResourceList {
	summed := corev1.ResourceList{}
	for _, container := range pod.Spec.Containers {
		addResourceList(summed, container.Resources.Requests)
	}
	initMaximum := corev1.ResourceList{}
	for _, container := range pod.Spec.InitContainers {
		for name, quantity := range container.Resources.Requests {
			current := initMaximum[name]
			if current.IsZero() || quantity.Cmp(current) > 0 {
				initMaximum[name] = quantity.DeepCopy()
			}
		}
	}
	for name, quantity := range initMaximum {
		current := summed[name]
		if quantity.Cmp(current) > 0 {
			summed[name] = quantity.DeepCopy()
		}
	}
	addResourceList(summed, pod.Spec.Overhead)
	return summed
}

func addResourceList(destination, source corev1.ResourceList) {
	for name, quantity := range source {
		current := destination[name]
		current.Add(quantity)
		destination[name] = current
	}
}

func subtractResourceList(capacity, used corev1.ResourceList) corev1.ResourceList {
	result := corev1.ResourceList{}
	for name, quantity := range capacity {
		available := quantity.DeepCopy()
		if consumed, ok := used[name]; ok {
			available.Sub(consumed)
			if available.Sign() < 0 {
				available = *resource.NewQuantity(0, quantity.Format)
			}
		}
		result[name] = available
	}
	return result
}

func activePodUsage(pods []corev1.Pod) (map[string]corev1.ResourceList, map[string]int) {
	usage := map[string]corev1.ResourceList{}
	counts := map[string]int{}
	for _, pod := range pods {
		if pod.Spec.NodeName == "" || pod.Status.Phase == corev1.PodSucceeded || pod.Status.Phase == corev1.PodFailed {
			continue
		}
		if usage[pod.Spec.NodeName] == nil {
			usage[pod.Spec.NodeName] = corev1.ResourceList{}
		}
		addResourceList(usage[pod.Spec.NodeName], podResourceRequests(pod))
		counts[pod.Spec.NodeName]++
	}
	return usage, counts
}

func quantityString(resources corev1.ResourceList, name corev1.ResourceName) string {
	if quantity, ok := resources[name]; ok {
		return quantity.String()
	}
	return "0"
}

func nodeRoles(node corev1.Node) []string {
	roles := []string{}
	for key := range node.Labels {
		if strings.HasPrefix(key, "node-role.kubernetes.io/") {
			role := strings.TrimPrefix(key, "node-role.kubernetes.io/")
			if role != "" {
				roles = append(roles, role)
			}
		}
	}
	if len(roles) == 0 {
		roles = append(roles, "worker")
	}
	sort.Strings(roles)
	return roles
}

func collectClusterNodes(ctx context.Context, client kubernetes.Interface, clusterID, clusterName string) ([]ClusterNode, error) {
	nodeList, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list Kubernetes nodes: %w", err)
	}
	podList, podErr := client.CoreV1().Pods("").List(ctx, metav1.ListOptions{})
	pods := []corev1.Pod{}
	if podErr == nil {
		pods = podList.Items
	}
	return buildClusterNodeInventory(nodeList.Items, pods, podErr, clusterID, clusterName), nil
}

func buildClusterNodeInventory(nodeItems []corev1.Node, podItems []corev1.Pod, podErr error, clusterID, clusterName string) []ClusterNode {
	usage, runningPods := map[string]corev1.ResourceList{}, map[string]int{}
	warning := ""
	if podErr == nil {
		usage, runningPods = activePodUsage(podItems)
	} else {
		warning = "pod requests unavailable: " + podErr.Error()
	}
	nodes := make([]ClusterNode, 0, len(nodeItems))
	for _, node := range nodeItems {
		available := subtractResourceList(node.Status.Allocatable, usage[node.Name])
		item := ClusterNode{
			ClusterID: clusterID, ClusterName: clusterName, Name: node.Name,
			Ready: nodeReady(node), Schedulable: !node.Spec.Unschedulable, Roles: nodeRoles(node),
			OS: node.Status.NodeInfo.OperatingSystem, Architecture: node.Status.NodeInfo.Architecture,
			KernelVersion: node.Status.NodeInfo.KernelVersion, ContainerRuntime: node.Status.NodeInfo.ContainerRuntimeVersion,
			KubeletVersion:    node.Status.NodeInfo.KubeletVersion,
			CPUCapacity:       quantityString(node.Status.Capacity, corev1.ResourceCPU),
			CPUAllocatable:    quantityString(node.Status.Allocatable, corev1.ResourceCPU),
			CPUAvailable:      quantityString(available, corev1.ResourceCPU),
			MemoryCapacity:    quantityString(node.Status.Capacity, corev1.ResourceMemory),
			MemoryAllocatable: quantityString(node.Status.Allocatable, corev1.ResourceMemory),
			MemoryAvailable:   quantityString(available, corev1.ResourceMemory),
			PodCapacity:       node.Status.Capacity.Pods().Value(), RunningPods: runningPods[node.Name],
			Labels: node.Labels, Accelerators: []NodeAccelerator{}, InventoryWarning: warning, UpdatedAt: time.Now().UTC(),
		}
		for _, address := range node.Status.Addresses {
			if address.Type == corev1.NodeInternalIP {
				item.InternalIP = address.Address
				break
			}
		}
		for _, taint := range node.Spec.Taints {
			item.Taints = append(item.Taints, fmt.Sprintf("%s=%s:%s", taint.Key, taint.Value, taint.Effect))
		}
		for name, capacity := range node.Status.Capacity {
			if !acceleratorResource(string(name)) {
				continue
			}
			vendor, runtime := acceleratorMetadata(string(name))
			allocatable := node.Status.Allocatable[name]
			availableQuantity := available[name]
			item.Accelerators = append(item.Accelerators, NodeAccelerator{
				Resource: string(name), Vendor: vendor, Runtime: runtime, Model: acceleratorModel(node, vendor),
				Capacity: capacity.Value(), Allocatable: allocatable.Value(), Available: availableQuantity.Value(),
			})
		}
		sort.Slice(item.Accelerators, func(i, j int) bool { return item.Accelerators[i].Resource < item.Accelerators[j].Resource })
		nodes = append(nodes, item)
	}
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].Name < nodes[j].Name })
	return nodes
}

func persistClusterNodes(ctx context.Context, clusterID, clusterName string, nodes []ClusterNode) error {
	if database == nil {
		return nil
	}
	tx, err := database.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `DELETE FROM cluster_nodes WHERE cluster_id=$1`, clusterID); err != nil {
		return err
	}
	for i := range nodes {
		nodes[i].ClusterID, nodes[i].ClusterName = clusterID, clusterName
		payload, marshalErr := json.Marshal(nodes[i])
		if marshalErr != nil {
			return marshalErr
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cluster_nodes(cluster_id,name,inventory,updated_at) VALUES($1,$2,$3,now())`, clusterID, nodes[i].Name, payload); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func loadClusterNodes(ctx context.Context, clusterID string) ([]ClusterNode, error) {
	query := `SELECT inventory FROM cluster_nodes`
	args := []any{}
	if clusterID != "" {
		query += ` WHERE cluster_id=$1`
		args = append(args, clusterID)
	}
	query += ` ORDER BY cluster_id,name`
	rows, err := database.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []ClusterNode{}
	for rows.Next() {
		var payload []byte
		if rows.Scan(&payload) != nil {
			continue
		}
		var item ClusterNode
		if json.Unmarshal(payload, &item) == nil {
			items = append(items, item)
		}
	}
	return items, rows.Err()
}

func matchesPoolNode(pool ManagedPool, node ClusterNode) bool {
	selector, err := parseNodeSelector(pool.Selector)
	if err != nil || !labels.SelectorFromSet(selector).Matches(labels.Set(node.Labels)) {
		return false
	}
	return node.Ready && node.Schedulable
}

func acceleratorMatchesPool(pool ManagedPool, accelerator NodeAccelerator) bool {
	if pool.ResourceName != "" && pool.ResourceName != accelerator.Resource {
		return false
	}
	vendor := strings.ToLower(strings.TrimSpace(pool.Vendor))
	runtime := strings.ToLower(strings.TrimSpace(pool.Runtime))
	return (vendor == "" || vendor == "mixed" || vendor == strings.ToLower(accelerator.Vendor)) &&
		(runtime == "" || runtime == "compatible" || runtime == strings.ToLower(accelerator.Runtime))
}

func enrichPoolCapacities(ctx context.Context, pools []ManagedPool) []ManagedPool {
	nodes, err := loadClusterNodes(ctx, "")
	if err != nil {
		return pools
	}
	for index := range pools {
		clusters := map[string]bool{}
		for _, node := range nodes {
			if !matchesPoolNode(pools[index], node) {
				continue
			}
			matched := strings.EqualFold(pools[index].Runtime, "cpu")
			for _, accelerator := range node.Accelerators {
				if !acceleratorMatchesPool(pools[index], accelerator) {
					continue
				}
				matched = true
				pools[index].Capacity += accelerator.Capacity
				pools[index].Allocatable += accelerator.Allocatable
				pools[index].Available += accelerator.Available
			}
			if matched {
				pools[index].NodeCount++
				clusters[node.ClusterID] = true
			}
		}
		pools[index].ClusterCount = len(clusters)
		switch {
		case !pools[index].Enabled:
			pools[index].Status = "disabled"
		case pools[index].NodeCount == 0:
			pools[index].Status = "unavailable"
		case pools[index].Available == 0 && !strings.EqualFold(pools[index].Runtime, "cpu"):
			pools[index].Status = "saturated"
		default:
			pools[index].Status = "ready"
		}
	}
	return pools
}
