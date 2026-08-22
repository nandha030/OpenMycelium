package main

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/remotecommand"
	"sigs.k8s.io/yaml"
)

const (
	defaultWorkloadNamespace = "openmycelium-workloads"
	workloadLabel            = "openmycelium.io/workload-id"
	managedByLabel           = "app.kubernetes.io/managed-by"
)

type kubernetesClusterRecord struct {
	Cluster
	Kubeconfig   []byte
	Namespace    string
	StorageClass string
}

type kubernetesWorkloadInput struct {
	ClusterID    string
	Namespace    string
	Runtime      string
	Model        string
	Command      string
	CPU          string
	Memory       string
	StorageGB    int
	StorageClass string
	ServiceType  string
	Port         int32
	Replicas     int32
}

func clusterCredentialKey() ([]byte, error) {
	value := strings.TrimSpace(os.Getenv("CLUSTER_CREDENTIAL_KEY"))
	if value == "" {
		value = strings.TrimSpace(os.Getenv("JWT_SECRET"))
	}
	if value == "" {
		return nil, errors.New("CLUSTER_CREDENTIAL_KEY or JWT_SECRET is required to encrypt Kubernetes credentials")
	}
	if decoded, err := base64.StdEncoding.DecodeString(value); err == nil && len(decoded) == 32 {
		return decoded, nil
	}
	if len(value) < 32 {
		return nil, errors.New("cluster credential encryption key must contain at least 32 characters")
	}
	digest := sha256.Sum256([]byte(value))
	return digest[:], nil
}

func encryptClusterCredential(plain []byte) ([]byte, error) {
	key, err := clusterCredentialKey()
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	return gcm.Seal(nonce, nonce, plain, nil), nil
}

func decryptClusterCredential(sealed []byte) ([]byte, error) {
	key, err := clusterCredentialKey()
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(sealed) < gcm.NonceSize() {
		return nil, errors.New("stored Kubernetes credential is invalid")
	}
	nonce, ciphertext := sealed[:gcm.NonceSize()], sealed[gcm.NonceSize():]
	return gcm.Open(nil, nonce, ciphertext, nil)
}

func restConfigFromKubeconfig(kubeconfig []byte, endpoint string) (*rest.Config, error) {
	config, err := clientcmd.RESTConfigFromKubeConfig(kubeconfig)
	if err != nil {
		return nil, fmt.Errorf("invalid kubeconfig: %w", err)
	}
	if strings.TrimSpace(endpoint) != "" {
		config.Host = strings.TrimSpace(endpoint)
	}
	config.Timeout = 15 * time.Second
	config.QPS = 25
	config.Burst = 50
	config.UserAgent = "OpenMycelium/1.0"
	return config, nil
}

func probeKubernetesCluster(ctx context.Context, kubeconfig []byte, endpoint string) (Cluster, error) {
	probe, err := probeKubernetesClusterInventory(ctx, kubeconfig, endpoint, "", "")
	return probe.Cluster, err
}

func probeKubernetesClusterInventory(ctx context.Context, kubeconfig []byte, endpoint, clusterID, clusterName string) (clusterProbe, error) {
	config, err := restConfigFromKubeconfig(kubeconfig, endpoint)
	if err != nil {
		return clusterProbe{}, err
	}
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return clusterProbe{}, fmt.Errorf("create Kubernetes client: %w", err)
	}
	version, err := client.Discovery().ServerVersion()
	if err != nil {
		return clusterProbe{}, fmt.Errorf("authenticate with Kubernetes API %s: %w", config.Host, err)
	}
	nodes, err := collectClusterNodes(ctx, client, clusterID, clusterName)
	if err != nil {
		return clusterProbe{}, err
	}
	ready, accelerators := 0, 0
	for _, node := range nodes {
		if node.Ready {
			ready++
		}
		for _, accelerator := range node.Accelerators {
			accelerators += int(accelerator.Capacity)
		}
	}
	status := "degraded"
	if len(nodes) > 0 && ready == len(nodes) {
		status = "ready"
	} else if len(nodes) == 0 {
		status = "pending"
	}
	return clusterProbe{Cluster: Cluster{Endpoint: config.Host, Status: status, Nodes: len(nodes), ReadyNodes: ready, Accelerators: accelerators, Version: version.GitVersion, UpdatedAt: time.Now().UTC()}, Nodes: nodes}, nil
}

func loadKubernetesCluster(ctx context.Context, id string) (kubernetesClusterRecord, error) {
	if database == nil {
		return kubernetesClusterRecord{}, errors.New("PostgreSQL is required")
	}
	query := `SELECT id,name,endpoint,type,status,nodes,ready_nodes,accelerators,version,created_at,updated_at,kubeconfig,namespace,storage_class FROM clusters`
	args := []any{}
	if id != "" {
		query += ` WHERE id=$1`
		args = append(args, id)
	} else {
		query += ` WHERE type='kubernetes' AND octet_length(kubeconfig)>0 ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,updated_at DESC LIMIT 1`
	}
	var record kubernetesClusterRecord
	err := database.QueryRow(ctx, query, args...).Scan(&record.ID, &record.Name, &record.Endpoint, &record.Type, &record.Status, &record.Nodes, &record.ReadyNodes, &record.Accelerators, &record.Version, &record.CreatedAt, &record.UpdatedAt, &record.Kubeconfig, &record.Namespace, &record.StorageClass)
	if err != nil {
		return record, fmt.Errorf("connected Kubernetes cluster not found: %w", err)
	}
	if len(record.Kubeconfig) == 0 {
		return record, errors.New("cluster has no Kubernetes credentials; reconnect it with a kubeconfig")
	}
	return record, nil
}

func kubernetesClientForCluster(ctx context.Context, id string) (kubernetes.Interface, *rest.Config, kubernetesClusterRecord, error) {
	record, err := loadKubernetesCluster(ctx, id)
	if err != nil {
		return nil, nil, record, err
	}
	plain, err := decryptClusterCredential(record.Kubeconfig)
	if err != nil {
		return nil, nil, record, fmt.Errorf("decrypt cluster credentials: %w", err)
	}
	config, err := restConfigFromKubeconfig(plain, record.Endpoint)
	if err != nil {
		return nil, nil, record, err
	}
	client, err := kubernetes.NewForConfig(config)
	return client, config, record, err
}

func nodeReady(node corev1.Node) bool {
	for _, condition := range node.Status.Conditions {
		if condition.Type == corev1.NodeReady {
			return condition.Status == corev1.ConditionTrue
		}
	}
	return false
}

func acceleratorResource(name string) bool {
	return name == "nvidia.com/gpu" || strings.HasPrefix(name, "nvidia.com/mig-") || name == "amd.com/gpu" || name == "gpu.intel.com/i915" || name == "gpu.intel.com/xe" || name == "habana.ai/gaudi" || name == "google.com/tpu" || strings.Contains(name, "tpu")
}

func workloadResourceName(id, name string) string {
	value := strings.ToLower(name)
	var output strings.Builder
	lastDash := false
	for _, char := range value {
		valid := char >= 'a' && char <= 'z' || char >= '0' && char <= '9'
		if valid {
			output.WriteRune(char)
			lastDash = false
		} else if !lastDash && output.Len() > 0 {
			output.WriteByte('-')
			lastDash = true
		}
	}
	clean := strings.Trim(output.String(), "-")
	if clean == "" {
		clean = "workload"
	}
	suffix := strings.TrimPrefix(id, "job-")
	if len(suffix) > 8 {
		suffix = suffix[len(suffix)-8:]
	}
	if len(clean) > 45 {
		clean = clean[:45]
	}
	return strings.Trim(clean+"-"+suffix, "-")
}

func parseNodeSelector(selector string) (map[string]string, error) {
	result := map[string]string{}
	if strings.TrimSpace(selector) == "" {
		return result, nil
	}
	for _, pair := range strings.Split(selector, ",") {
		parts := strings.SplitN(strings.TrimSpace(pair), "=", 2)
		if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" || strings.TrimSpace(parts[1]) == "" {
			return nil, fmt.Errorf("invalid node selector %q; use key=value pairs separated by commas", pair)
		}
		result[strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
	}
	return result, nil
}

func poolForWorkload(ctx context.Context, name string) (ManagedPool, error) {
	if name == "" || name == "cpu-local" {
		return ManagedPool{Name: "cpu", Vendor: "CPU", Runtime: "cpu", Enabled: true}, nil
	}
	var pool ManagedPool
	err := database.QueryRow(ctx, `SELECT id,name,vendor,runtime,policy,selector,resource_name,sharing_mode,slice_profile,sharing_replicas,enabled,created_at FROM accelerator_pools WHERE name=$1`, name).Scan(&pool.ID, &pool.Name, &pool.Vendor, &pool.Runtime, &pool.Policy, &pool.Selector, &pool.ResourceName, &pool.SharingMode, &pool.SliceProfile, &pool.SharingReplicas, &pool.Enabled, &pool.CreatedAt)
	if err != nil {
		return pool, fmt.Errorf("accelerator pool %q not found", name)
	}
	if !pool.Enabled {
		return pool, fmt.Errorf("accelerator pool %q is disabled", name)
	}
	return pool, nil
}

func gpuResourceForPool(pool ManagedPool) corev1.ResourceName {
	if strings.TrimSpace(pool.ResourceName) != "" {
		return corev1.ResourceName(strings.TrimSpace(pool.ResourceName))
	}
	value := strings.ToLower(pool.Vendor + " " + pool.Runtime)
	switch {
	case strings.Contains(value, "nvidia") || strings.Contains(value, "cuda"):
		return "nvidia.com/gpu"
	case strings.Contains(value, "amd") || strings.Contains(value, "rocm"):
		return "amd.com/gpu"
	case strings.Contains(value, "intel") || strings.Contains(value, "oneapi"):
		return "gpu.intel.com/i915"
	default:
		return ""
	}
}

func workloadResources(workload Workload, pool ManagedPool) (corev1.ResourceRequirements, error) {
	cpu := workload.CPU
	if cpu == "" {
		cpu = "1"
	}
	memory := workload.Memory
	if memory == "" {
		memory = "1Gi"
	}
	cpuQuantity, err := resource.ParseQuantity(cpu)
	if err != nil {
		return corev1.ResourceRequirements{}, fmt.Errorf("invalid CPU request %q", cpu)
	}
	memoryQuantity, err := resource.ParseQuantity(memory)
	if err != nil {
		return corev1.ResourceRequirements{}, fmt.Errorf("invalid memory request %q", memory)
	}
	requests := corev1.ResourceList{corev1.ResourceCPU: cpuQuantity, corev1.ResourceMemory: memoryQuantity}
	limits := corev1.ResourceList{}
	if workload.Accelerators > 0 {
		gpuName := gpuResourceForPool(pool)
		if gpuName == "" {
			return corev1.ResourceRequirements{}, errors.New("the selected pool does not map to a Kubernetes accelerator resource")
		}
		quantity := *resource.NewQuantity(int64(workload.Accelerators), resource.DecimalSI)
		requests[gpuName] = quantity
		limits[gpuName] = quantity
	}
	return corev1.ResourceRequirements{Requests: requests, Limits: limits}, nil
}

func ensureSchedulable(ctx context.Context, client kubernetes.Interface, workload Workload, selector map[string]string, resources corev1.ResourceRequirements) (PlacementDecision, error) {
	nodes, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{LabelSelector: labels.SelectorFromSet(selector).String()})
	if err != nil {
		return PlacementDecision{}, fmt.Errorf("list candidate nodes: %w", err)
	}
	pods, err := client.CoreV1().Pods("").List(ctx, metav1.ListOptions{})
	if err != nil {
		return PlacementDecision{}, fmt.Errorf("list pod resource requests for capacity validation: %w", err)
	}
	members := int(workload.DesiredCount)
	if workload.GangMinAvailable > 0 {
		members = int(workload.GangMinAvailable)
	}
	if workloadUsesJob(workload.Kind) && members > 1 {
		return evaluateSchedulableReplicas(nodes.Items, pods.Items, workload, resources, members)
	}
	return evaluateSchedulableNodes(nodes.Items, pods.Items, workload, resources)
}

func evaluateSchedulableReplicas(nodes []corev1.Node, pods []corev1.Pod, workload Workload, resources corev1.ResourceRequirements, members int) (PlacementDecision, error) {
	decision := PlacementDecision{Status: "rejected", Requested: map[string]string{}, Available: map[string]string{}, DecidedAt: time.Now().UTC()}
	for name, quantity := range resources.Requests {
		total := quantity.DeepCopy()
		total.Mul(int64(members))
		decision.Requested[string(name)] = total.String()
	}
	usage, _ := activePodUsage(pods)
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].Name < nodes[j].Name })
	availableByNode := map[string]corev1.ResourceList{}
	for _, node := range nodes {
		decision.Evaluated++
		if !nodeReady(node) || node.Spec.Unschedulable {
			decision.Reasons = append(decision.Reasons, fmt.Sprintf("%s is not ready or is cordoned", node.Name))
			continue
		}
		blocked := false
		for _, taint := range node.Spec.Taints {
			if taint.Effect == corev1.TaintEffectNoSchedule || taint.Effect == corev1.TaintEffectNoExecute {
				decision.Reasons = append(decision.Reasons, fmt.Sprintf("%s has untolerated taint %s:%s", node.Name, taint.Key, taint.Effect))
				blocked = true
				break
			}
		}
		if !blocked {
			decision.Candidates++
			availableByNode[node.Name] = subtractResourceList(node.Status.Allocatable, usage[node.Name])
		}
	}
	for member := 0; member < members; member++ {
		placed := false
		for _, node := range nodes {
			availableResources, eligible := availableByNode[node.Name]
			if !eligible {
				continue
			}
			fits := true
			for resourceName, requested := range resources.Requests {
				available, exists := availableResources[resourceName]
				if !exists || available.Cmp(requested) < 0 {
					fits = false
					break
				}
			}
			if !fits {
				continue
			}
			for resourceName, requested := range resources.Requests {
				remaining := availableResources[resourceName]
				remaining.Sub(requested)
				availableResources[resourceName] = remaining
			}
			decision.SelectedNodes = append(decision.SelectedNodes, node.Name)
			placed = true
			break
		}
		if !placed {
			decision.Reasons = append(decision.Reasons, fmt.Sprintf("only %d of %d gang members fit simultaneously", member, members))
			return decision, fmt.Errorf("gang preflight rejected: %s", strings.Join(decision.Reasons, "; "))
		}
	}
	remainingTotal := corev1.ResourceList{}
	for _, available := range availableByNode {
		addResourceList(remainingTotal, available)
	}
	for name, quantity := range remainingTotal {
		if name == corev1.ResourceCPU || name == corev1.ResourceMemory || acceleratorResource(string(name)) {
			decision.Available[string(name)] = quantity.String()
		}
	}
	unique := []string{}
	seen := map[string]bool{}
	for _, name := range decision.SelectedNodes {
		if !seen[name] {
			unique = append(unique, name)
			seen[name] = true
		}
	}
	decision.Status = "accepted"
	decision.SelectedNode = strings.Join(unique, ", ")
	return decision, nil
}

func ensureSchedulerBackend(client kubernetes.Interface, workload Workload) error {
	backend := strings.ToLower(strings.TrimSpace(workload.SchedulerBackend))
	if backend == "" || backend == "kubernetes" {
		return nil
	}
	groups, err := client.Discovery().ServerGroups()
	if err != nil {
		return fmt.Errorf("discover scheduler APIs: %w", err)
	}
	wanted := ""
	if backend == "kueue" {
		wanted = "kueue.x-k8s.io"
	} else if backend == "volcano" {
		wanted = "scheduling.volcano.sh"
	}
	for _, group := range groups.Groups {
		if group.Name == wanted {
			return nil
		}
	}
	return fmt.Errorf("%s scheduler integration is not installed or its API is unavailable", backend)
}

func evaluateSchedulableNodes(nodes []corev1.Node, pods []corev1.Pod, workload Workload, resources corev1.ResourceRequirements) (PlacementDecision, error) {
	decision := PlacementDecision{Status: "rejected", Requested: map[string]string{}, Available: map[string]string{}, DecidedAt: time.Now().UTC()}
	for name, quantity := range resources.Requests {
		decision.Requested[string(name)] = quantity.String()
	}
	usage, _ := activePodUsage(pods)
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].Name < nodes[j].Name })
	for _, node := range nodes {
		decision.Evaluated++
		if !nodeReady(node) || node.Spec.Unschedulable {
			decision.Reasons = append(decision.Reasons, fmt.Sprintf("%s is not ready or is cordoned", node.Name))
			continue
		}
		blocked := false
		for _, taint := range node.Spec.Taints {
			if taint.Effect == corev1.TaintEffectNoSchedule || taint.Effect == corev1.TaintEffectNoExecute {
				decision.Reasons = append(decision.Reasons, fmt.Sprintf("%s has untolerated taint %s:%s", node.Name, taint.Key, taint.Effect))
				blocked = true
				break
			}
		}
		if blocked {
			continue
		}
		decision.Candidates++
		availableResources := subtractResourceList(node.Status.Allocatable, usage[node.Name])
		fits := true
		for resourceName, requested := range resources.Requests {
			available, exists := availableResources[resourceName]
			if !exists || available.Cmp(requested) < 0 {
				decision.Reasons = append(decision.Reasons, fmt.Sprintf("%s has %s %s available; %s requested", node.Name, available.String(), resourceName, requested.String()))
				fits = false
				break
			}
		}
		if fits {
			decision.Status = "accepted"
			decision.SelectedNode = node.Name
			for name, quantity := range availableResources {
				if name == corev1.ResourceCPU || name == corev1.ResourceMemory || acceleratorResource(string(name)) {
					decision.Available[string(name)] = quantity.String()
				}
			}
			return decision, nil
		}
	}
	return decision, fmt.Errorf("no currently available node satisfies the selector and requests (CPU %s, memory %s, accelerators %d): %s", workload.CPU, workload.Memory, workload.Accelerators, strings.Join(decision.Reasons, "; "))
}

func ensureNamespace(ctx context.Context, client kubernetes.Interface, namespace string) error {
	_, err := client.CoreV1().Namespaces().Get(ctx, namespace, metav1.GetOptions{})
	if err == nil {
		return nil
	}
	if !apierrors.IsNotFound(err) {
		return err
	}
	_, err = client.CoreV1().Namespaces().Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: namespace, Labels: map[string]string{managedByLabel: "openmycelium"}}}, metav1.CreateOptions{})
	return err
}

func workloadUsesJob(kind string) bool {
	return kind == "training" || kind == "finetuning" || kind == "batch"
}

func workloadUsesService(kind string) bool {
	return kind == "inference" || kind == "interactive" || kind == "agent"
}

func workloadLabels(workload Workload) map[string]string {
	labels := map[string]string{workloadLabel: workload.ID, managedByLabel: "openmycelium", "app.kubernetes.io/name": workload.ResourceName}
	if workload.AgentID != "" {
		labels["openmycelium.io/agent-id"] = workload.AgentID
	}
	if workload.AgentRunID != "" {
		labels["openmycelium.io/agent-run-id"] = workload.AgentRunID
	}
	if workload.WorkspaceID != "" {
		labels[workspaceLabel] = workload.WorkspaceID
	}
	if workload.ReleaseID != "" {
		labels[releaseLabel] = workload.ReleaseID
	}
	if workload.ExecutionGroupID != "" {
		labels["fabric.openmycelium.io/execution-group"] = workload.ExecutionGroupID
	}
	return labels
}

func workloadAnnotations(workload Workload) map[string]string {
	annotations := map[string]string{}
	if workload.SchedulerBackend != "" {
		annotations["openmycelium.io/scheduler-backend"] = workload.SchedulerBackend
	}
	if workload.QueueName != "" {
		annotations["openmycelium.io/queue"] = workload.QueueName
	}
	if workload.GangMinAvailable > 0 {
		annotations["openmycelium.io/gang-min-available"] = strconv.Itoa(int(workload.GangMinAvailable))
	}
	if workload.TopologyMode != "" && workload.TopologyMode != "none" {
		annotations["openmycelium.io/topology-mode"] = workload.TopologyMode
		annotations["openmycelium.io/topology-key"] = workload.TopologyKey
	}
	if workload.NetworkMode != "" {
		annotations["openmycelium.io/network-mode"] = workload.NetworkMode
	}
	if workload.SchedulerBackend == "volcano" {
		minimum := workload.GangMinAvailable
		if minimum < 1 {
			minimum = 1
		}
		annotations["scheduling.volcano.sh/group-min-member"] = strconv.Itoa(int(minimum))
		if workload.QueueName != "" {
			annotations["scheduling.volcano.sh/queue-name"] = workload.QueueName
		}
	}
	if workload.FabricPlanID != "" {
		annotations["hypha.openmycelium.io/plan-id"] = workload.FabricPlanID
		annotations["hypha.openmycelium.io/logical-address"] = workload.FabricAddress
		annotations["hypha.openmycelium.io/consistency"] = workload.FabricMode
		annotations["hypha.openmycelium.io/hardware-coherent"] = "false"
	}
	if workload.ExecutionPlanID != "" {
		annotations["fabric.openmycelium.io/execution-plan-id"] = workload.ExecutionPlanID
		annotations["fabric.openmycelium.io/transport"] = workload.ExecutionTransport
		if workload.ExecutionGroupID != "" {
			annotations["fabric.openmycelium.io/group-id"] = workload.ExecutionGroupID
			annotations["fabric.openmycelium.io/rank-base"] = strconv.Itoa(workload.ExecutionRankBase)
			annotations["fabric.openmycelium.io/group-size"] = strconv.Itoa(workload.ExecutionGroupSize)
		}
	}
	return annotations
}

func workloadContainer(workload Workload, pool ManagedPool) (corev1.Container, error) {
	resources, err := workloadResources(workload, pool)
	if err != nil {
		return corev1.Container{}, err
	}
	container := corev1.Container{
		Name:            "workload",
		Image:           workload.Image,
		ImagePullPolicy: corev1.PullIfNotPresent,
		Resources:       resources,
		SecurityContext: &corev1.SecurityContext{AllowPrivilegeEscalation: boolPointer(false)},
	}
	if workload.Accelerators > 0 {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_ACCELERATOR_RESOURCE", Value: string(gpuResourceForPool(pool))},
			corev1.EnvVar{Name: "OPENMYCELIUM_GPU_SHARING_MODE", Value: pool.SharingMode},
			corev1.EnvVar{Name: "OPENMYCELIUM_GPU_SLICE_PROFILE", Value: pool.SliceProfile},
		)
	}
	worldSize := int(workload.DesiredCount)
	if workload.ExecutionWorldSize > 0 {
		worldSize = workload.ExecutionWorldSize
	}
	if worldSize > 1 {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_WORLD_SIZE", Value: strconv.Itoa(worldSize)},
			corev1.EnvVar{Name: "OPENMYCELIUM_RENDEZVOUS_SERVICE", Value: workload.ServiceName},
			corev1.EnvVar{Name: "OPENMYCELIUM_WORKER_INDEX", ValueFrom: &corev1.EnvVarSource{FieldRef: &corev1.ObjectFieldSelector{FieldPath: "metadata.annotations['batch.kubernetes.io/job-completion-index']"}}},
		)
	}
	if workload.NetworkMode == "rdma" || workload.NetworkMode == "infiniband" {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "NCCL_IB_DISABLE", Value: "0"},
			corev1.EnvVar{Name: "UCX_TLS", Value: "rc,sm,self"},
			corev1.EnvVar{Name: "OPENMYCELIUM_NETWORK_MODE", Value: workload.NetworkMode},
		)
	}
	if workload.Command != "" {
		container.Command = []string{"/bin/sh", "-lc", workload.Command}
	}
	modelRuntime := workload.ModelRuntime
	if modelRuntime == "" && workload.Model != "" {
		modelRuntime = "ollama"
	}
	if workload.ModelVersionID != "" {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_MODEL_VERSION_ID", Value: workload.ModelVersionID},
			corev1.EnvVar{Name: "OPENMYCELIUM_MODEL", Value: workload.Model},
			corev1.EnvVar{Name: "OPENMYCELIUM_MODEL_SOURCE", Value: workload.ModelSourceURI},
			corev1.EnvVar{Name: "OPENMYCELIUM_MODEL_RUNTIME", Value: modelRuntime},
		)
	}
	if workload.AgentID != "" {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_AGENT_ID", Value: workload.AgentID},
			corev1.EnvVar{Name: "OPENMYCELIUM_AGENT_RUN_ID", Value: workload.AgentRunID},
			corev1.EnvVar{Name: "OPENMYCELIUM_WORKSPACE_ID", Value: workload.WorkspaceID},
			corev1.EnvVar{Name: "OPENMYCELIUM_AGENT_SPEC_JSON", Value: workload.AgentSpecJSON},
			corev1.EnvVar{Name: "OPENMYCELIUM_AGENT_FLOW_JSON", Value: workload.AgentFlowJSON},
			corev1.EnvVar{Name: "OPENMYCELIUM_AGENT_TOOLS_JSON", Value: workload.AgentToolsJSON},
		)
	}
	servingWorkload := workloadUsesService(workload.Kind)
	if workload.AgentID == "" && workload.Model != "" && modelRuntime == "ollama" && servingWorkload {
		container.Env = append(container.Env, corev1.EnvVar{Name: "OLLAMA_HOST", Value: "0.0.0.0:11434"}, corev1.EnvVar{Name: "OPENMYCELIUM_MODEL", Value: workload.Model})
		if workload.Command == "" {
			container.Command = []string{"/bin/sh", "-lc", "ollama serve & pid=$!; until ollama list >/dev/null 2>&1; do sleep 1; done; ollama pull \"$OPENMYCELIUM_MODEL\"; wait $pid"}
		}
		container.Ports = []corev1.ContainerPort{{Name: "http", ContainerPort: 11434}}
		container.ReadinessProbe = &corev1.Probe{ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/api/tags", Port: intstr.FromInt32(11434)}}, InitialDelaySeconds: 3, PeriodSeconds: 5, FailureThreshold: 30}
	} else if workload.AgentID == "" && workload.ModelVersionID != "" && modelRuntime == "vllm" && servingWorkload {
		port := workload.Port
		if port <= 0 {
			port = 8000
		}
		if workload.Command == "" {
			container.Command = []string{"/bin/sh", "-lc", "python3 -m vllm.entrypoints.openai.api_server --host 0.0.0.0 --port " + strconv.Itoa(int(port)) + " --model \"${OPENMYCELIUM_MODEL_SOURCE#hf://}\""}
		}
		container.Ports = []corev1.ContainerPort{{Name: "http", ContainerPort: port}}
		container.ReadinessProbe = &corev1.Probe{ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/health", Port: intstr.FromInt32(port)}}, InitialDelaySeconds: 10, PeriodSeconds: 5, FailureThreshold: 60}
	} else if workload.AgentID == "" && workload.ModelVersionID != "" && modelRuntime == "tgi" && servingWorkload {
		port := workload.Port
		if port <= 0 {
			port = 80
		}
		if workload.Command == "" {
			container.Command = []string{"/bin/sh", "-lc", "text-generation-launcher --port " + strconv.Itoa(int(port)) + " --model-id \"${OPENMYCELIUM_MODEL_SOURCE#hf://}\""}
		}
		container.Ports = []corev1.ContainerPort{{Name: "http", ContainerPort: port}}
		container.ReadinessProbe = &corev1.Probe{ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/health", Port: intstr.FromInt32(port)}}, InitialDelaySeconds: 10, PeriodSeconds: 5, FailureThreshold: 60}
	} else if workload.Port > 0 {
		container.Ports = []corev1.ContainerPort{{Name: "http", ContainerPort: workload.Port}}
	}
	if workload.AgentID != "" && workload.Port > 0 && container.ReadinessProbe == nil {
		container.ReadinessProbe = &corev1.Probe{ProbeHandler: corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/health", Port: intstr.FromInt32(workload.Port)}}, InitialDelaySeconds: 3, PeriodSeconds: 5, FailureThreshold: 30}
	}
	if workload.PVCName != "" {
		mountPath := "/models"
		if workload.Model != "" && modelRuntime == "ollama" {
			mountPath = "/root/.ollama"
		} else if workload.ModelVersionID != "" {
			container.Env = append(container.Env, corev1.EnvVar{Name: "HF_HOME", Value: "/models/huggingface"}, corev1.EnvVar{Name: "OPENMYCELIUM_MODEL_CACHE", Value: "/models"})
		}
		container.VolumeMounts = []corev1.VolumeMount{{Name: "model-storage", MountPath: mountPath}}
	}
	if workload.FabricPlanID != "" {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_FABRIC_PLAN_ID", Value: workload.FabricPlanID},
			corev1.EnvVar{Name: "OPENMYCELIUM_FABRIC_ADDRESS", Value: workload.FabricAddress},
			corev1.EnvVar{Name: "OPENMYCELIUM_FABRIC_CONSISTENCY", Value: workload.FabricMode},
			corev1.EnvVar{Name: "OPENMYCELIUM_FABRIC_PLAN_JSON", Value: workload.FabricPlan},
		)
	}
	if workload.ExecutionPlanID != "" {
		container.Env = append(container.Env,
			corev1.EnvVar{Name: "OPENMYCELIUM_EXECUTION_PLAN_ID", Value: workload.ExecutionPlanID},
			corev1.EnvVar{Name: "OPENMYCELIUM_EXECUTION_TRANSPORT", Value: workload.ExecutionTransport},
			corev1.EnvVar{Name: "OPENMYCELIUM_EXECUTION_PLAN_JSON", Value: workload.ExecutionPlan},
		)
		if workload.ExecutionTransport == "hetccl" || workload.ExecutionTransport == "hetccl-tcp" {
			coordinatorHost := strings.TrimSpace(os.Getenv("HETCCL_COORDINATOR_SERVICE"))
			if coordinatorHost == "" {
				coordinatorHost = "hetccl-coordinator.openmycelium-system.svc.cluster.local"
			}
			coordinatorPort := strings.TrimSpace(os.Getenv("HETCCL_COORDINATOR_PORT"))
			if coordinatorPort == "" {
				coordinatorPort = "29500"
			}
			container.Env = append(container.Env,
				corev1.EnvVar{Name: "HETCCL_COORDINATOR_HOST", Value: coordinatorHost},
				corev1.EnvVar{Name: "HETCCL_COORDINATOR_PORT", Value: coordinatorPort},
				corev1.EnvVar{Name: "HETCCL_GROUP", Value: workload.ExecutionPlanID},
				corev1.EnvVar{Name: "HETCCL_BACKEND", Value: "tcp"},
			)
		}
		var executionPlan ExecutionPlan
		if json.Unmarshal([]byte(workload.ExecutionPlan), &executionPlan) == nil && executionPlan.Optimization.Algorithm != "" {
			container.Env = append(container.Env,
				corev1.EnvVar{Name: "OPENMYCELIUM_ALGORITHM", Value: executionPlan.Optimization.Algorithm},
				corev1.EnvVar{Name: "OPENMYCELIUM_ALGORITHM_VERSION", Value: executionPlan.Optimization.Version},
				corev1.EnvVar{Name: "OPENMYCELIUM_OPTIMIZATION_OBJECTIVE", Value: executionPlan.Optimization.Objective},
			)
		}
		if workload.ExecutionGroupID != "" {
			container.Env = append(container.Env,
				corev1.EnvVar{Name: "OPENMYCELIUM_EXECUTION_GROUP_ID", Value: workload.ExecutionGroupID},
				corev1.EnvVar{Name: "OPENMYCELIUM_RANK_BASE", Value: strconv.Itoa(workload.ExecutionRankBase)},
				corev1.EnvVar{Name: "OPENMYCELIUM_EXECUTION_GROUP_SIZE", Value: strconv.Itoa(workload.ExecutionGroupSize)},
			)
		}
	}
	return container, nil
}

func workloadPodTemplate(workload Workload, pool ManagedPool, selector map[string]string) (corev1.PodTemplateSpec, error) {
	container, err := workloadContainer(workload, pool)
	if err != nil {
		return corev1.PodTemplateSpec{}, err
	}
	volumes := []corev1.Volume{}
	if workload.PVCName != "" {
		volumes = append(volumes, corev1.Volume{Name: "model-storage", VolumeSource: corev1.VolumeSource{PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: workload.PVCName}}})
	}
	podSpec := corev1.PodSpec{RestartPolicy: corev1.RestartPolicyAlways, NodeSelector: selector, Containers: []corev1.Container{container}, Volumes: volumes, SecurityContext: &corev1.PodSecurityContext{SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault}}, PriorityClassName: workload.PriorityClass}
	if workload.SchedulerBackend == "volcano" {
		podSpec.SchedulerName = "volcano"
	}
	if workload.TopologyMode == "spread" {
		podSpec.TopologySpreadConstraints = []corev1.TopologySpreadConstraint{{MaxSkew: 1, TopologyKey: workload.TopologyKey, WhenUnsatisfiable: corev1.DoNotSchedule, LabelSelector: &metav1.LabelSelector{MatchLabels: map[string]string{workloadLabel: workload.ID}}}}
	} else if workload.TopologyMode == "compact" {
		podSpec.Affinity = &corev1.Affinity{PodAffinity: &corev1.PodAffinity{PreferredDuringSchedulingIgnoredDuringExecution: []corev1.WeightedPodAffinityTerm{{Weight: 100, PodAffinityTerm: corev1.PodAffinityTerm{TopologyKey: workload.TopologyKey, LabelSelector: &metav1.LabelSelector{MatchLabels: map[string]string{workloadLabel: workload.ID}}}}}}}
	}
	if workload.AgentID != "" {
		podSpec.ServiceAccountName = workload.ResourceName
		podSpec.AutomountServiceAccountToken = boolPointer(false)
	}
	if workload.ImagePullSecret != "" {
		podSpec.ImagePullSecrets = []corev1.LocalObjectReference{{Name: workload.ImagePullSecret}}
	}
	return corev1.PodTemplateSpec{ObjectMeta: metav1.ObjectMeta{Labels: workloadLabels(workload), Annotations: workloadAnnotations(workload)}, Spec: podSpec}, nil
}

func buildPVC(workload Workload) *corev1.PersistentVolumeClaim {
	if workload.StorageGB <= 0 || workload.PVCName == "" {
		return nil
	}
	quantity := resource.MustParse(strconv.Itoa(workload.StorageGB) + "Gi")
	pvc := &corev1.PersistentVolumeClaim{TypeMeta: metav1.TypeMeta{APIVersion: "v1", Kind: "PersistentVolumeClaim"}, ObjectMeta: metav1.ObjectMeta{Name: workload.PVCName, Namespace: workload.Namespace, Labels: workloadLabels(workload)}, Spec: corev1.PersistentVolumeClaimSpec{AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce}, Resources: corev1.VolumeResourceRequirements{Requests: corev1.ResourceList{corev1.ResourceStorage: quantity}}}}
	if workload.StorageClass != "" {
		pvc.Spec.StorageClassName = &workload.StorageClass
	}
	return pvc
}

func buildDeployment(workload Workload, pool ManagedPool, selector map[string]string) (*appsv1.Deployment, error) {
	template, err := workloadPodTemplate(workload, pool, selector)
	if err != nil {
		return nil, err
	}
	replicas := workload.DesiredCount
	if replicas < 1 {
		replicas = 1
	}
	return &appsv1.Deployment{TypeMeta: metav1.TypeMeta{APIVersion: "apps/v1", Kind: "Deployment"}, ObjectMeta: metav1.ObjectMeta{Name: workload.ResourceName, Namespace: workload.Namespace, Labels: workloadLabels(workload), Annotations: workloadAnnotations(workload)}, Spec: appsv1.DeploymentSpec{Replicas: &replicas, Selector: &metav1.LabelSelector{MatchLabels: map[string]string{workloadLabel: workload.ID}}, Template: template}}, nil
}

func buildJob(workload Workload, pool ManagedPool, selector map[string]string) (*batchv1.Job, error) {
	template, err := workloadPodTemplate(workload, pool, selector)
	if err != nil {
		return nil, err
	}
	template.Spec.RestartPolicy = corev1.RestartPolicyNever
	backoff := int32(2)
	replicas := workload.DesiredCount
	if replicas < 1 {
		replicas = 1
	}
	jobLabels := workloadLabels(workload)
	job := &batchv1.Job{TypeMeta: metav1.TypeMeta{APIVersion: "batch/v1", Kind: "Job"}, ObjectMeta: metav1.ObjectMeta{Name: workload.ResourceName, Namespace: workload.Namespace, Labels: jobLabels, Annotations: workloadAnnotations(workload)}, Spec: batchv1.JobSpec{BackoffLimit: &backoff, Parallelism: &replicas, Completions: &replicas, Template: template}}
	if replicas > 1 {
		completionMode := batchv1.IndexedCompletion
		job.Spec.CompletionMode = &completionMode
	}
	if workload.SchedulerBackend == "kueue" {
		job.Labels["kueue.x-k8s.io/queue-name"] = workload.QueueName
		suspended := true
		job.Spec.Suspend = &suspended
	}
	return job, nil
}

func buildExecutionJobs(workload Workload) ([]*batchv1.Job, error) {
	groups, err := executionGroupsFromWorkload(workload)
	if err != nil || len(groups) == 0 {
		return nil, err
	}
	totalDevices := 0
	for _, group := range groups {
		if group.Devices < 1 || !validExtendedResourceName(group.Resource) {
			return nil, fmt.Errorf("execution group %s has invalid accelerator resource %q", group.ID, group.Resource)
		}
		totalDevices += group.Devices
	}
	jobs := make([]*batchv1.Job, 0, len(groups))
	rankBase := 0
	for index, group := range groups {
		groupWorkload := workload
		if group.Image != "" {
			groupWorkload.Image = group.Image
		}
		groupWorkload.ResourceName = workloadResourceName(fmt.Sprintf("%s-%d", workload.ID, index), workload.Name+"-"+group.ID)
		groupWorkload.DesiredCount = int32(group.Devices)
		groupWorkload.Accelerators = 1
		groupWorkload.ExecutionGroupID = group.ID
		groupWorkload.ExecutionRankBase = rankBase
		groupWorkload.ExecutionGroupSize = group.Devices
		groupWorkload.ExecutionWorldSize = totalDevices
		groupWorkload.GangMinAvailable = int32(group.Devices)
		pool := ManagedPool{Name: group.ID, Vendor: group.Vendor, Runtime: group.Runtime, ResourceName: group.Resource, SharingMode: "exclusive", Enabled: true}
		job, buildErr := buildJob(groupWorkload, pool, nil)
		if buildErr != nil {
			return nil, buildErr
		}
		if len(group.Nodes) > 0 {
			if job.Spec.Template.Spec.Affinity == nil {
				job.Spec.Template.Spec.Affinity = &corev1.Affinity{}
			}
			job.Spec.Template.Spec.Affinity.NodeAffinity = &corev1.NodeAffinity{RequiredDuringSchedulingIgnoredDuringExecution: &corev1.NodeSelector{NodeSelectorTerms: []corev1.NodeSelectorTerm{{MatchFields: []corev1.NodeSelectorRequirement{{Key: "metadata.name", Operator: corev1.NodeSelectorOpIn, Values: group.Nodes}}}}}}
		}
		jobs = append(jobs, job)
		rankBase += group.Devices
	}
	return jobs, nil
}

func deleteJobsByWorkload(ctx context.Context, client kubernetes.Interface, workload Workload) error {
	selector := labels.Set{workloadLabel: workload.ID}.String()
	policy := metav1.DeletePropagationBackground
	return client.BatchV1().Jobs(workload.Namespace).DeleteCollection(ctx, metav1.DeleteOptions{PropagationPolicy: &policy}, metav1.ListOptions{LabelSelector: selector})
}

func buildService(workload Workload) *corev1.Service {
	if workloadUsesJob(workload.Kind) && workload.DesiredCount > 1 && workload.ServiceName != "" {
		return &corev1.Service{TypeMeta: metav1.TypeMeta{APIVersion: "v1", Kind: "Service"}, ObjectMeta: metav1.ObjectMeta{Name: workload.ServiceName, Namespace: workload.Namespace, Labels: workloadLabels(workload)}, Spec: corev1.ServiceSpec{ClusterIP: corev1.ClusterIPNone, PublishNotReadyAddresses: true, Selector: map[string]string{workloadLabel: workload.ID}, Ports: []corev1.ServicePort{{Name: "rendezvous", Port: 29500, TargetPort: intstr.FromInt32(29500)}}}}
	}
	if !workloadUsesService(workload.Kind) {
		return nil
	}
	port := workload.Port
	if workload.Model != "" && (workload.ModelRuntime == "ollama" || workload.ModelRuntime == "") {
		port = 11434
	}
	if port <= 0 {
		return nil
	}
	serviceType := corev1.ServiceTypeClusterIP
	if workload.ServiceType == string(corev1.ServiceTypeNodePort) {
		serviceType = corev1.ServiceTypeNodePort
	} else if workload.ServiceType == string(corev1.ServiceTypeLoadBalancer) {
		serviceType = corev1.ServiceTypeLoadBalancer
	}
	return &corev1.Service{TypeMeta: metav1.TypeMeta{APIVersion: "v1", Kind: "Service"}, ObjectMeta: metav1.ObjectMeta{Name: workload.ServiceName, Namespace: workload.Namespace, Labels: workloadLabels(workload)}, Spec: corev1.ServiceSpec{Type: serviceType, Selector: map[string]string{workloadLabel: workload.ID}, Ports: []corev1.ServicePort{{Name: "http", Port: port, TargetPort: intstr.FromInt32(port)}}}}
}

func servicePortScheme(port corev1.ServicePort) string {
	if port.AppProtocol != nil && strings.EqualFold(strings.TrimSpace(*port.AppProtocol), "https") {
		return "https"
	}
	if strings.EqualFold(port.Name, "https") || port.Port == 443 {
		return "https"
	}
	return "http"
}

func kubernetesServiceProxyName(serviceName string, port corev1.ServicePort) string {
	return fmt.Sprintf("%s:%s:%d", servicePortScheme(port), serviceName, port.Port)
}

func waitForControllerDeletion(ctx context.Context, client kubernetes.Interface, workload Workload) error {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		var err error
		if workloadUsesJob(workload.Kind) {
			if workload.ExecutionPlanID != "" {
				var jobs *batchv1.JobList
				jobs, err = client.BatchV1().Jobs(workload.Namespace).List(ctx, metav1.ListOptions{LabelSelector: labels.Set{workloadLabel: workload.ID}.String()})
				if err == nil && len(jobs.Items) == 0 {
					return nil
				}
			} else {
				_, err = client.BatchV1().Jobs(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
			}
		} else {
			_, err = client.AppsV1().Deployments(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
		}
		if apierrors.IsNotFound(err) {
			return nil
		}
		if err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return errors.New("timed out waiting for the previous Kubernetes controller to terminate")
		case <-ticker.C:
		}
	}
}

func waitForServiceDeletion(ctx context.Context, client kubernetes.Interface, workload Workload) error {
	if workload.ServiceName == "" {
		return nil
	}
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		_, err := client.CoreV1().Services(workload.Namespace).Get(ctx, workload.ServiceName, metav1.GetOptions{})
		if apierrors.IsNotFound(err) {
			return nil
		}
		if err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return errors.New("timed out waiting for the previous Kubernetes Service to terminate")
		case <-ticker.C:
		}
	}
}

func deployKubernetesWorkload(ctx context.Context, workload *Workload) error {
	if err := hydrateFabricContract(ctx, workload); err != nil {
		return err
	}
	if err := hydrateExecutionContract(ctx, workload); err != nil {
		return err
	}
	if workload.SchedulerBackend == "" {
		workload.SchedulerBackend = "kubernetes"
	}
	client, _, cluster, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return err
	}
	workload.ClusterID = cluster.ID
	workload.ClusterName = cluster.Name
	if err = ensureSchedulerBackend(client, *workload); err != nil {
		return err
	}
	if workload.Namespace == "" {
		workload.Namespace = cluster.Namespace
		if workload.Namespace == "" {
			workload.Namespace = defaultWorkloadNamespace
		}
	}
	if workload.StorageClass == "" {
		workload.StorageClass = cluster.StorageClass
	}
	pool, err := poolForWorkload(ctx, workload.Pool)
	if err != nil {
		return err
	}
	selector, err := parseNodeSelector(pool.Selector)
	if err != nil {
		return err
	}
	resources, err := workloadResources(*workload, pool)
	if err != nil {
		return err
	}
	decision, schedulingErr := ensureSchedulable(ctx, client, *workload, selector, resources)
	workload.Placement = decision
	if schedulingErr != nil {
		return schedulingErr
	}
	if err = ensureNamespace(ctx, client, workload.Namespace); err != nil {
		return fmt.Errorf("ensure namespace: %w", err)
	}
	if workload.AgentID != "" {
		account := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: workload.ResourceName, Namespace: workload.Namespace, Labels: workloadLabels(*workload)}, AutomountServiceAccountToken: boolPointer(false)}
		if _, err = client.CoreV1().ServiceAccounts(workload.Namespace).Create(ctx, account, metav1.CreateOptions{}); err != nil && !apierrors.IsAlreadyExists(err) {
			return fmt.Errorf("create agent service account: %w", err)
		}
	}
	if pvc := buildPVC(*workload); pvc != nil {
		if _, err = client.CoreV1().PersistentVolumeClaims(workload.Namespace).Create(ctx, pvc, metav1.CreateOptions{}); err != nil && !apierrors.IsAlreadyExists(err) {
			if workload.AgentID != "" {
				_ = client.CoreV1().ServiceAccounts(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{})
			}
			return fmt.Errorf("create model storage: %w", err)
		}
	}
	if workloadUsesJob(workload.Kind) {
		executionJobs, buildErr := buildExecutionJobs(*workload)
		if buildErr != nil {
			return buildErr
		}
		if len(executionJobs) > 0 {
			devices := 0
			for _, job := range executionJobs {
				devices += int(*job.Spec.Completions)
				if _, err = client.BatchV1().Jobs(workload.Namespace).Create(ctx, job, metav1.CreateOptions{}); err != nil {
					_ = deleteJobsByWorkload(ctx, client, *workload)
					return fmt.Errorf("create heterogeneous execution Job %s: %w", job.Name, err)
				}
			}
			workload.DesiredCount = int32(devices)
			workload.Accelerators = 1
		} else {
			job, jobErr := buildJob(*workload, pool, selector)
			if jobErr != nil {
				return jobErr
			}
			if _, err = client.BatchV1().Jobs(workload.Namespace).Create(ctx, job, metav1.CreateOptions{}); err != nil {
				if workload.AgentID != "" {
					_ = client.CoreV1().ServiceAccounts(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{})
				}
				return fmt.Errorf("create Kubernetes Job: %w", err)
			}
		}
	} else {
		deployment, buildErr := buildDeployment(*workload, pool, selector)
		if buildErr != nil {
			return buildErr
		}
		if _, err = client.AppsV1().Deployments(workload.Namespace).Create(ctx, deployment, metav1.CreateOptions{}); err != nil {
			if workload.AgentID != "" {
				_ = client.CoreV1().ServiceAccounts(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{})
			}
			return fmt.Errorf("create Kubernetes Deployment: %w", err)
		}
	}
	if service := buildService(*workload); service != nil {
		if _, err = client.CoreV1().Services(workload.Namespace).Create(ctx, service, metav1.CreateOptions{}); err != nil {
			_ = deleteKubernetesResources(ctx, client, *workload, false)
			return fmt.Errorf("create Kubernetes Service: %w", err)
		}
	}
	workload.Status = "pending"
	workload.PodName = ""
	workload.NodeName = ""
	workload.Endpoint = ""
	workload.LastError = ""
	workload.PodPhase = ""
	workload.StatusReason = "Scheduled"
	workload.StatusMessage = fmt.Sprintf("Capacity preflight accepted on %s; %s controls final placement and admission.", decision.SelectedNode, workload.SchedulerBackend)
	workload.Restarts = 0
	workload.UpdatedAt = time.Now().UTC()
	return nil
}

func deleteKubernetesResources(ctx context.Context, client kubernetes.Interface, workload Workload, deleteStorage bool) error {
	policy := metav1.DeletePropagationBackground
	var errs []string
	if workloadUsesJob(workload.Kind) {
		if workload.ExecutionPlanID != "" {
			if err := deleteJobsByWorkload(ctx, client, workload); err != nil && !apierrors.IsNotFound(err) {
				errs = append(errs, err.Error())
			}
		} else if err := client.BatchV1().Jobs(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{PropagationPolicy: &policy}); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, err.Error())
		}
	} else {
		if err := client.AppsV1().Deployments(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{PropagationPolicy: &policy}); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, err.Error())
		}
	}
	if workload.ServiceName != "" {
		if err := client.CoreV1().Services(workload.Namespace).Delete(ctx, workload.ServiceName, metav1.DeleteOptions{}); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, err.Error())
		}
	}
	if workload.AgentID != "" {
		if err := client.CoreV1().ServiceAccounts(workload.Namespace).Delete(ctx, workload.ResourceName, metav1.DeleteOptions{}); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, err.Error())
		}
	}
	if deleteStorage && workload.PVCName != "" {
		if err := client.CoreV1().PersistentVolumeClaims(workload.Namespace).Delete(ctx, workload.PVCName, metav1.DeleteOptions{}); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, err.Error())
		}
	}
	if len(errs) > 0 {
		return errors.New(strings.Join(errs, "; "))
	}
	return nil
}

func deleteKubernetesWorkload(ctx context.Context, workload Workload) error {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return err
	}
	return deleteKubernetesResources(ctx, client, workload, true)
}

func actOnKubernetesWorkload(ctx context.Context, workload *Workload, action string) error {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return err
	}
	switch action {
	case "stop":
		if workloadUsesJob(workload.Kind) {
			if err = deleteKubernetesResources(ctx, client, *workload, false); err != nil {
				return err
			}
		} else {
			zero := int32(0)
			deployment, getErr := client.AppsV1().Deployments(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
			if getErr != nil {
				return getErr
			}
			deployment.Spec.Replicas = &zero
			if _, err = client.AppsV1().Deployments(workload.Namespace).Update(ctx, deployment, metav1.UpdateOptions{}); err != nil {
				return err
			}
		}
		workload.Status = "stopped"
	case "start":
		if workloadUsesJob(workload.Kind) {
			if err = waitForControllerDeletion(ctx, client, *workload); err != nil {
				return err
			}
			workload.Status = "pending"
			return deployKubernetesWorkload(ctx, workload)
		}
		replicas := workload.DesiredCount
		if replicas < 1 {
			replicas = 1
		}
		deployment, getErr := client.AppsV1().Deployments(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
		if getErr != nil {
			return getErr
		}
		deployment.Spec.Replicas = &replicas
		if _, err = client.AppsV1().Deployments(workload.Namespace).Update(ctx, deployment, metav1.UpdateOptions{}); err != nil {
			return err
		}
		workload.Status = "pending"
	case "redeploy":
		if err = deleteKubernetesResources(ctx, client, *workload, false); err != nil {
			return err
		}
		if err = waitForControllerDeletion(ctx, client, *workload); err != nil {
			return err
		}
		if err = waitForServiceDeletion(ctx, client, *workload); err != nil {
			return err
		}
		workload.Status = "pending"
		return deployKubernetesWorkload(ctx, workload)
	default:
		return errors.New("supported Kubernetes actions: start, stop, redeploy")
	}
	workload.LastError = ""
	workload.UpdatedAt = time.Now().UTC()
	return nil
}

func reconcileKubernetesWorkload(ctx context.Context, workload *Workload) error {
	if workload.Status == "stopped" {
		return nil
	}
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return err
	}
	workload.LastError = ""
	workload.StatusReason = ""
	workload.StatusMessage = ""
	workload.Restarts = 0
	if workloadUsesJob(workload.Kind) {
		if workload.ExecutionPlanID != "" {
			jobList, listErr := client.BatchV1().Jobs(workload.Namespace).List(ctx, metav1.ListOptions{LabelSelector: labels.Set{workloadLabel: workload.ID}.String()})
			if listErr != nil {
				return listErr
			}
			if len(jobList.Items) == 0 {
				return fmt.Errorf("no execution group Jobs found for workload %s", workload.Name)
			}
			active, failed, succeeded, expected := int32(0), int32(0), int32(0), int32(0)
			for _, job := range jobList.Items {
				active += job.Status.Active
				failed += job.Status.Failed
				succeeded += job.Status.Succeeded
				if job.Spec.Completions != nil {
					expected += *job.Spec.Completions
				}
			}
			switch {
			case failed > 0:
				workload.Status = "failed"
			case expected > 0 && succeeded >= expected:
				workload.Status = "succeeded"
			case active > 0:
				workload.Status = "running"
			default:
				workload.Status = "pending"
			}
			workload.StatusMessage = fmt.Sprintf("%d execution groups / %d active / %d succeeded / %d failed", len(jobList.Items), active, succeeded, failed)
		} else {
			job, getErr := client.BatchV1().Jobs(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
			if getErr != nil {
				return getErr
			}
			switch {
			case job.Status.Succeeded > 0:
				workload.Status = "succeeded"
			case job.Status.Failed > 0:
				workload.Status = "failed"
			case job.Status.Active > 0:
				workload.Status = "running"
			default:
				workload.Status = "pending"
			}
		}
	} else {
		deployment, getErr := client.AppsV1().Deployments(workload.Namespace).Get(ctx, workload.ResourceName, metav1.GetOptions{})
		if getErr != nil {
			return getErr
		}
		if deployment.Status.ReadyReplicas > 0 && deployment.Status.ReadyReplicas == deployment.Status.Replicas {
			workload.Status = "running"
		} else {
			workload.Status = "pending"
		}
	}
	pods, err := client.CoreV1().Pods(workload.Namespace).List(ctx, metav1.ListOptions{LabelSelector: labels.Set{workloadLabel: workload.ID}.String()})
	if err == nil && len(pods.Items) > 0 {
		sort.SliceStable(pods.Items, func(i, j int) bool {
			return pods.Items[i].CreationTimestamp.After(pods.Items[j].CreationTimestamp.Time)
		})
		pod := pods.Items[0]
		workload.PodName = pod.Name
		workload.NodeName = pod.Spec.NodeName
		workload.PodPhase = string(pod.Status.Phase)
		for _, condition := range pod.Status.Conditions {
			if condition.Status == corev1.ConditionFalse && (condition.Type == corev1.PodScheduled || condition.Type == corev1.PodReady || condition.Type == corev1.ContainersReady) {
				workload.StatusReason = condition.Reason
				workload.StatusMessage = condition.Message
			}
		}
		if pod.Status.Phase == corev1.PodFailed {
			workload.Status = "failed"
			workload.StatusReason = pod.Status.Reason
			workload.StatusMessage = pod.Status.Message
			workload.LastError = strings.TrimSpace(strings.TrimSpace(pod.Status.Reason) + ": " + strings.TrimSpace(pod.Status.Message))
		}
		for _, containerStatus := range pod.Status.ContainerStatuses {
			workload.Restarts += containerStatus.RestartCount
			if waiting := containerStatus.State.Waiting; waiting != nil {
				workload.StatusReason = waiting.Reason
				workload.StatusMessage = waiting.Message
				switch waiting.Reason {
				case "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CreateContainerError", "InvalidImageName":
					workload.Status = "failed"
					workload.LastError = strings.TrimSpace(waiting.Reason + ": " + waiting.Message)
				}
			}
			if terminated := containerStatus.State.Terminated; terminated != nil && terminated.ExitCode != 0 {
				workload.Status = "failed"
				workload.StatusReason = terminated.Reason
				workload.StatusMessage = terminated.Message
				workload.LastError = fmt.Sprintf("%s exited with code %d: %s", containerStatus.Name, terminated.ExitCode, strings.TrimSpace(terminated.Message))
			} else if terminated := containerStatus.LastTerminationState.Terminated; terminated != nil && terminated.ExitCode != 0 && containerStatus.RestartCount > 0 {
				workload.Status = "failed"
				workload.StatusReason = "Restarting"
				workload.StatusMessage = terminated.Message
				workload.LastError = fmt.Sprintf("%s is restarting after exit code %d (%s)", containerStatus.Name, terminated.ExitCode, terminated.Reason)
			}
		}
	}
	if workload.ServiceName != "" {
		service, serviceErr := client.CoreV1().Services(workload.Namespace).Get(ctx, workload.ServiceName, metav1.GetOptions{})
		if serviceErr == nil {
			workload.Endpoint = serviceEndpoint(ctx, client, *workload, service)
		}
	}
	workload.UpdatedAt = time.Now().UTC()
	return nil
}

func serviceEndpoint(ctx context.Context, client kubernetes.Interface, workload Workload, service *corev1.Service) string {
	if len(service.Spec.Ports) == 0 {
		return ""
	}
	port := service.Spec.Ports[0]
	scheme := servicePortScheme(port)
	switch service.Spec.Type {
	case corev1.ServiceTypeNodePort:
		if workload.NodeName == "" || port.NodePort == 0 {
			return fmt.Sprintf("%s://<node-ip>:%d", scheme, port.NodePort)
		}
		node, err := client.CoreV1().Nodes().Get(ctx, workload.NodeName, metav1.GetOptions{})
		if err == nil {
			for _, address := range node.Status.Addresses {
				if address.Type == corev1.NodeInternalIP {
					return fmt.Sprintf("%s://%s:%d", scheme, address.Address, port.NodePort)
				}
			}
		}
	case corev1.ServiceTypeLoadBalancer:
		if len(service.Status.LoadBalancer.Ingress) > 0 {
			host := service.Status.LoadBalancer.Ingress[0].IP
			if host == "" {
				host = service.Status.LoadBalancer.Ingress[0].Hostname
			}
			return fmt.Sprintf("%s://%s:%d", scheme, host, port.Port)
		}
	default:
		return fmt.Sprintf("%s://%s.%s.svc.cluster.local:%d", scheme, service.Name, workload.Namespace, port.Port)
	}
	return "pending"
}

func reconcileAllKubernetesWorkloads(ctx context.Context) {
	state.RLock()
	snapshot := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	changed := false
	for i := range snapshot {
		if snapshot[i].Runtime != "kubernetes" {
			continue
		}
		statusContext, cancel := context.WithTimeout(ctx, 10*time.Second)
		err := reconcileKubernetesWorkload(statusContext, &snapshot[i])
		cancel()
		if err != nil && !apierrors.IsNotFound(err) {
			snapshot[i].LastError = err.Error()
			snapshot[i].UpdatedAt = time.Now().UTC()
		}
		if snapshot[i].WorkspaceID != "" && snapshot[i].ReleaseID != "" {
			sourceRef := snapshot[i].Image
			if snapshot[i].ModelSourceURI != "" {
				sourceRef = snapshot[i].ModelSourceURI
			}
			_ = workspaceRelease(ctx, snapshot[i].WorkspaceID, snapshot[i].ReleaseID, snapshot[i].Name, "celium-ai", sourceRef, snapshot[i].Status, snapshot[i])
		}
		changed = true
	}
	if changed {
		state.Lock()
		for i := range state.Workloads {
			for j := range snapshot {
				if state.Workloads[i].ID == snapshot[j].ID {
					state.Workloads[i] = snapshot[j]
					break
				}
			}
		}
		persistStateLocked()
		state.Unlock()
	}
}

func startKubernetesReconciler(ctx context.Context) {
	go func() {
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				reconcileAllKubernetesWorkloads(ctx)
			case <-ctx.Done():
				return
			}
		}
	}()
}

func kubernetesWorkloadLogs(ctx context.Context, workload Workload, tail int64) (string, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return "", err
	}
	if workload.PodName == "" {
		_ = reconcileKubernetesWorkload(ctx, &workload)
	}
	if workload.PodName == "" {
		return "", errors.New("no pod has been scheduled for this workload")
	}
	options := &corev1.PodLogOptions{TailLines: &tail, Timestamps: true}
	request := client.CoreV1().Pods(workload.Namespace).GetLogs(workload.PodName, options)
	stream, err := request.Stream(ctx)
	if err != nil {
		options.Previous = true
		stream, err = client.CoreV1().Pods(workload.Namespace).GetLogs(workload.PodName, options).Stream(ctx)
	}
	if err != nil {
		return "", err
	}
	defer stream.Close()
	content, err := io.ReadAll(io.LimitReader(stream, 2<<20))
	return string(content), err
}

type cappedBuffer struct {
	bytes.Buffer
	limit int
}

func (buffer *cappedBuffer) Write(value []byte) (int, error) {
	original := len(value)
	remaining := buffer.limit - buffer.Len()
	if remaining > 0 {
		if len(value) > remaining {
			value = value[:remaining]
		}
		_, _ = buffer.Buffer.Write(value)
	}
	return original, nil
}

func kubernetesWorkloadExec(ctx context.Context, workload Workload, containerName, command string) (map[string]any, error) {
	client, config, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return nil, err
	}
	if workload.PodName == "" {
		_ = reconcileKubernetesWorkload(ctx, &workload)
	}
	if workload.PodName == "" {
		return nil, errors.New("no pod has been scheduled for this workload")
	}
	pod, err := client.CoreV1().Pods(workload.Namespace).Get(ctx, workload.PodName, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	if containerName == "" && len(pod.Spec.Containers) > 0 {
		containerName = pod.Spec.Containers[0].Name
	}
	validContainer := false
	for _, container := range pod.Spec.Containers {
		if container.Name == containerName {
			validContainer = true
			break
		}
	}
	if !validContainer {
		return nil, errors.New("container does not exist in the selected pod")
	}
	command = strings.TrimSpace(command)
	if command == "" || len(command) > 4096 || strings.ContainsRune(command, '\x00') {
		return nil, errors.New("a command of at most 4096 characters is required")
	}
	req := client.CoreV1().RESTClient().Post().Resource("pods").Name(workload.PodName).Namespace(workload.Namespace).SubResource("exec").VersionedParams(&corev1.PodExecOptions{Container: containerName, Command: []string{"/bin/sh", "-lc", command}, Stdout: true, Stderr: true}, scheme.ParameterCodec)
	executor, err := remotecommand.NewSPDYExecutor(config, http.MethodPost, req.URL())
	if err != nil {
		return nil, err
	}
	stdout, stderr := &cappedBuffer{limit: 2 << 20}, &cappedBuffer{limit: 2 << 20}
	err = executor.StreamWithContext(ctx, remotecommand.StreamOptions{Stdout: stdout, Stderr: stderr, Tty: false})
	result := map[string]any{"workloadId": workload.ID, "pod": workload.PodName, "container": containerName, "stdout": stdout.String(), "stderr": stderr.String(), "truncated": stdout.Len() >= stdout.limit || stderr.Len() >= stderr.limit, "succeeded": err == nil}
	if err != nil {
		result["error"] = err.Error()
	}
	return result, err
}

func kubernetesWorkloadProbe(ctx context.Context, workload Workload, method, path, body string) (map[string]any, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return nil, err
	}
	if workload.ServiceName == "" {
		return nil, errors.New("this workload does not expose a Kubernetes Service")
	}
	if workload.Port <= 0 {
		return nil, errors.New("workload service port is not configured")
	}
	method = strings.ToUpper(strings.TrimSpace(method))
	if method != http.MethodGet && method != http.MethodPost {
		return nil, errors.New("service probe supports GET and POST")
	}
	path = "/" + strings.TrimLeft(strings.TrimSpace(path), "/")
	if len(path) > 2048 || strings.Contains(path, "..") {
		return nil, errors.New("invalid service path")
	}
	if len(body) > 1<<20 {
		return nil, errors.New("request body exceeds 1 MiB")
	}
	restClient := client.CoreV1().RESTClient()
	var request *rest.Request
	if method == http.MethodPost {
		request = restClient.Post().Body([]byte(body)).SetHeader("Content-Type", "application/json")
	} else {
		request = restClient.Get()
	}
	service, err := client.CoreV1().Services(workload.Namespace).Get(ctx, workload.ServiceName, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	var servicePort *corev1.ServicePort
	for index := range service.Spec.Ports {
		candidate := &service.Spec.Ports[index]
		if candidate.Port == workload.Port {
			servicePort = candidate
			break
		}
	}
	if servicePort == nil {
		return nil, fmt.Errorf("service %s does not expose port %d", workload.ServiceName, workload.Port)
	}
	request = request.Namespace(workload.Namespace).Resource("services").Name(kubernetesServiceProxyName(workload.ServiceName, *servicePort)).SubResource("proxy")
	if suffix := strings.TrimPrefix(path, "/"); suffix != "" {
		request = request.Suffix(strings.Split(suffix, "/")...)
	}
	stream, err := request.Stream(ctx)
	if err != nil {
		return nil, err
	}
	defer stream.Close()
	content, err := io.ReadAll(io.LimitReader(stream, (2<<20)+1))
	truncated := len(content) > 2<<20
	if truncated {
		content = content[:2<<20]
	}
	return map[string]any{"workloadId": workload.ID, "service": workload.ServiceName, "method": method, "path": path, "body": string(content), "truncated": truncated}, err
}

func kubernetesWorkloadEvents(ctx context.Context, workload Workload) ([]map[string]any, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return nil, err
	}
	if workload.PodName == "" {
		_ = reconcileKubernetesWorkload(ctx, &workload)
	}
	if workload.PodName == "" {
		return []map[string]any{}, nil
	}
	items, err := client.CoreV1().Events(workload.Namespace).List(ctx, metav1.ListOptions{FieldSelector: fields.OneTermEqualSelector("involvedObject.name", workload.PodName).String()})
	if err != nil {
		return nil, err
	}
	result := make([]map[string]any, 0, len(items.Items))
	for _, item := range items.Items {
		result = append(result, map[string]any{"type": item.Type, "reason": item.Reason, "message": item.Message, "count": item.Count, "timestamp": item.LastTimestamp.Time})
	}
	return result, nil
}

func kubernetesWorkloadManifest(ctx context.Context, workload Workload) (string, error) {
	pool, err := poolForWorkload(ctx, workload.Pool)
	if err != nil {
		return "", err
	}
	selector, err := parseNodeSelector(pool.Selector)
	if err != nil {
		return "", err
	}
	objects := []any{}
	if pvc := buildPVC(workload); pvc != nil {
		objects = append(objects, pvc)
	}
	if workloadUsesJob(workload.Kind) {
		executionJobs, buildErr := buildExecutionJobs(workload)
		if buildErr != nil {
			return "", buildErr
		}
		if len(executionJobs) > 0 {
			workload.DesiredCount = 0
			for _, job := range executionJobs {
				objects = append(objects, job)
				if job.Spec.Completions != nil {
					workload.DesiredCount += *job.Spec.Completions
				}
			}
		} else {
			job, jobErr := buildJob(workload, pool, selector)
			if jobErr != nil {
				return "", jobErr
			}
			objects = append(objects, job)
		}
	} else {
		deployment, buildErr := buildDeployment(workload, pool, selector)
		if buildErr != nil {
			return "", buildErr
		}
		objects = append(objects, deployment)
	}
	if service := buildService(workload); service != nil {
		objects = append(objects, service)
	}
	parts := make([]string, 0, len(objects))
	for _, object := range objects {
		content, marshalErr := yaml.Marshal(object)
		if marshalErr != nil {
			return "", marshalErr
		}
		parts = append(parts, string(content))
	}
	return strings.Join(parts, "---\n"), nil
}

func kubernetesWorkloadDiagnostics(ctx context.Context, workload Workload) (map[string]any, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
	if err != nil {
		return nil, err
	}
	_ = reconcileKubernetesWorkload(ctx, &workload)
	diagnostics := map[string]any{
		"workloadId": workload.ID, "status": workload.Status, "podPhase": workload.PodPhase,
		"reason": workload.StatusReason, "message": workload.StatusMessage, "lastError": workload.LastError,
		"pod": workload.PodName, "node": workload.NodeName, "restarts": workload.Restarts,
		"placement": workload.Placement, "endpoint": workload.Endpoint,
		"controller": map[string]any{"kind": map[bool]string{true: "Job", false: "Deployment"}[workloadUsesJob(workload.Kind)], "name": workload.ResourceName},
	}
	if workload.ExecutionPlanID != "" && workloadUsesJob(workload.Kind) {
		if jobs, listErr := client.BatchV1().Jobs(workload.Namespace).List(ctx, metav1.ListOptions{LabelSelector: labels.Set{workloadLabel: workload.ID}.String()}); listErr == nil {
			controllers := make([]map[string]any, 0, len(jobs.Items))
			for _, job := range jobs.Items {
				controllers = append(controllers, map[string]any{"kind": "Job", "name": job.Name, "group": job.Labels["fabric.openmycelium.io/execution-group"], "active": job.Status.Active, "succeeded": job.Status.Succeeded, "failed": job.Status.Failed})
			}
			diagnostics["controllers"] = controllers
		}
	}
	if workload.PodName != "" {
		pod, podErr := client.CoreV1().Pods(workload.Namespace).Get(ctx, workload.PodName, metav1.GetOptions{})
		if podErr == nil {
			containers := []map[string]any{}
			for _, status := range pod.Status.ContainerStatuses {
				item := map[string]any{"name": status.Name, "ready": status.Ready, "restarts": status.RestartCount}
				if status.State.Waiting != nil {
					item["state"], item["reason"], item["message"] = "waiting", status.State.Waiting.Reason, status.State.Waiting.Message
				} else if status.State.Running != nil {
					item["state"], item["startedAt"] = "running", status.State.Running.StartedAt.Time
				} else if status.State.Terminated != nil {
					item["state"], item["reason"], item["exitCode"] = "terminated", status.State.Terminated.Reason, status.State.Terminated.ExitCode
				}
				containers = append(containers, item)
			}
			diagnostics["containers"] = containers
		}
	}
	pods, podListErr := client.CoreV1().Pods(workload.Namespace).List(ctx, metav1.ListOptions{LabelSelector: labels.Set{workloadLabel: workload.ID}.String()})
	if podListErr == nil {
		podItems := make([]map[string]any, 0, len(pods.Items))
		for _, pod := range pods.Items {
			containers := make([]map[string]any, 0, len(pod.Spec.Containers))
			for _, container := range pod.Spec.Containers {
				containers = append(containers, map[string]any{"name": container.Name, "image": container.Image, "cpu": container.Resources.Requests.Cpu().String(), "memory": container.Resources.Requests.Memory().String()})
			}
			podItems = append(podItems, map[string]any{"name": pod.Name, "phase": pod.Status.Phase, "node": pod.Spec.NodeName, "podIp": pod.Status.PodIP, "createdAt": pod.CreationTimestamp.Time, "containers": containers})
		}
		diagnostics["pods"] = podItems
	}
	if workload.ServiceName != "" {
		if service, serviceErr := client.CoreV1().Services(workload.Namespace).Get(ctx, workload.ServiceName, metav1.GetOptions{}); serviceErr == nil {
			diagnostics["service"] = map[string]any{"name": service.Name, "type": service.Spec.Type, "clusterIp": service.Spec.ClusterIP, "endpoint": workload.Endpoint, "ports": service.Spec.Ports}
		}
	}
	if workload.PVCName != "" {
		if pvc, pvcErr := client.CoreV1().PersistentVolumeClaims(workload.Namespace).Get(ctx, workload.PVCName, metav1.GetOptions{}); pvcErr == nil {
			diagnostics["storage"] = map[string]any{"name": pvc.Name, "phase": pvc.Status.Phase, "storageClass": workload.StorageClass, "requested": pvc.Spec.Resources.Requests.Storage().String()}
		}
	}
	events, eventErr := kubernetesWorkloadEvents(ctx, workload)
	if eventErr == nil {
		diagnostics["events"] = events
	}
	return diagnostics, nil
}

func kubernetesWorkloadSubresourceHandler(w http.ResponseWriter, r *http.Request, workload Workload, resourceName string) bool {
	if workload.Runtime != "kubernetes" {
		return false
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	switch resourceName {
	case "diagnostics":
		content, err := kubernetesWorkloadDiagnostics(ctx, workload)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
		} else {
			writeJSON(w, 200, content)
		}
	case "logs":
		tail := int64(300)
		if value, err := strconv.ParseInt(r.URL.Query().Get("tail"), 10, 64); err == nil && value > 0 && value <= 5000 {
			tail = value
		}
		content, err := kubernetesWorkloadLogs(ctx, workload, tail)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
		} else {
			writeJSON(w, 200, map[string]any{"workloadId": workload.ID, "pod": workload.PodName, "logs": content})
		}
	case "events":
		items, err := kubernetesWorkloadEvents(ctx, workload)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
		} else {
			writeJSON(w, 200, map[string]any{"events": items})
		}
	case "service":
		_ = reconcileKubernetesWorkload(ctx, &workload)
		writeJSON(w, 200, map[string]any{"name": workload.ServiceName, "namespace": workload.Namespace, "endpoint": workload.Endpoint, "type": workload.ServiceType, "port": workload.Port})
	case "manifest":
		content, err := kubernetesWorkloadManifest(ctx, workload)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
		} else {
			writeJSON(w, 200, map[string]string{"manifest": content})
		}
	case "storage":
		client, _, _, err := kubernetesClientForCluster(ctx, workload.ClusterID)
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
			break
		}
		if workload.PVCName == "" {
			writeJSON(w, 200, map[string]any{"configured": false})
			break
		}
		pvc, err := client.CoreV1().PersistentVolumeClaims(workload.Namespace).Get(ctx, workload.PVCName, metav1.GetOptions{})
		if err != nil {
			writeJSON(w, 502, map[string]string{"error": err.Error()})
		} else {
			capacity := "pending"
			if quantity := pvc.Status.Capacity.Storage(); quantity != nil {
				capacity = quantity.String()
			}
			writeJSON(w, 200, map[string]any{"configured": true, "name": pvc.Name, "phase": pvc.Status.Phase, "capacity": capacity, "storageClass": workload.StorageClass})
		}
	case "exec":
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]string{"error": "method not allowed"})
			break
		}
		var input struct {
			Container string `json:"container"`
			Command   string `json:"command"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "container and command payload is invalid"})
			break
		}
		content, execErr := kubernetesWorkloadExec(ctx, workload, strings.TrimSpace(input.Container), input.Command)
		hash := sha256.Sum256([]byte(input.Command))
		auditRequest(r, "workload.container_exec", map[string]any{"workload_id": workload.ID, "pod": workload.PodName, "container": strings.TrimSpace(input.Container), "command_sha256": fmt.Sprintf("%x", hash[:])})
		if content == nil {
			writeJSON(w, 502, map[string]string{"error": execErr.Error()})
		} else {
			writeJSON(w, 200, content)
		}
	case "probe":
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]string{"error": "method not allowed"})
			break
		}
		var input struct {
			Method string `json:"method"`
			Path   string `json:"path"`
			Body   string `json:"body"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "service probe payload is invalid"})
			break
		}
		content, probeErr := kubernetesWorkloadProbe(ctx, workload, input.Method, input.Path, input.Body)
		auditRequest(r, "workload.service_probed", map[string]any{"workload_id": workload.ID, "service": workload.ServiceName, "method": strings.ToUpper(input.Method), "path": input.Path})
		if probeErr != nil {
			writeJSON(w, 502, map[string]string{"error": probeErr.Error()})
		} else {
			writeJSON(w, 200, content)
		}
	default:
		return false
	}
	return true
}

func boolPointer(value bool) *bool { return &value }

func refreshClusterFromCredentials(ctx context.Context, id string) (Cluster, error) {
	record, err := loadKubernetesCluster(ctx, id)
	if err != nil {
		return Cluster{}, err
	}
	plain, err := decryptClusterCredential(record.Kubeconfig)
	if err != nil {
		return Cluster{}, err
	}
	result, err := probeKubernetesClusterInventory(ctx, plain, record.Endpoint, record.ID, record.Name)
	if err != nil {
		_, _ = database.Exec(ctx, `UPDATE clusters SET status='degraded',updated_at=now() WHERE id=$1`, id)
		return Cluster{}, err
	}
	probe := result.Cluster
	probe.ID, probe.Name, probe.Type, probe.CreatedAt, probe.Namespace, probe.StorageClass, probe.Authenticated = record.ID, record.Name, record.Type, record.CreatedAt, record.Namespace, record.StorageClass, true
	_, err = database.Exec(ctx, `UPDATE clusters SET endpoint=$1,status=$2,nodes=$3,ready_nodes=$4,accelerators=$5,version=$6,updated_at=now() WHERE id=$7`, probe.Endpoint, probe.Status, probe.Nodes, probe.ReadyNodes, probe.Accelerators, probe.Version, id)
	if err == nil {
		err = persistClusterNodes(ctx, record.ID, record.Name, result.Nodes)
	}
	return probe, err
}

func clusterCredentialsStatusHandler(w http.ResponseWriter, r *http.Request, id string) bool {
	if !strings.HasSuffix(strings.Trim(r.URL.Path, "/"), "/refresh") || r.Method != http.MethodPost {
		return false
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	cluster, err := refreshClusterFromCredentials(ctx, id)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return true
	}
	auditRequest(r, "cluster.refreshed", map[string]any{"cluster_id": id, "nodes": cluster.Nodes, "ready_nodes": cluster.ReadyNodes})
	writeJSON(w, 200, cluster)
	return true
}
