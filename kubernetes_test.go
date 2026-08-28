package main

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestClusterCredentialEncryptionRoundTrip(t *testing.T) {
	t.Setenv("CLUSTER_CREDENTIAL_KEY", "test-key-that-is-at-least-thirty-two-characters")
	plain := []byte("apiVersion: v1\nclusters: []\n")
	sealed, err := encryptClusterCredential(plain)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(sealed), "apiVersion") {
		t.Fatal("credential was stored as plaintext")
	}
	decrypted, err := decryptClusterCredential(sealed)
	if err != nil {
		t.Fatal(err)
	}
	if string(decrypted) != string(plain) {
		t.Fatalf("round trip mismatch: %q", decrypted)
	}
}

func TestOllamaDeploymentUsesPersistentModelCache(t *testing.T) {
	workload := Workload{ID: "job-123456789", Name: "Gemma inference", Kind: "inference", Runtime: "kubernetes", Image: "ollama/ollama:latest", Model: "gemma4:12b", Namespace: "models", ResourceName: "gemma-12345678", ServiceName: "gemma-12345678", PVCName: "gemma-12345678-models", Port: 11434, CPU: "2", Memory: "8Gi", DesiredCount: 1}
	deployment, err := buildDeployment(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, map[string]string{"workload.openmycelium.io/tier": "inference"})
	if err != nil {
		t.Fatal(err)
	}
	container := deployment.Spec.Template.Spec.Containers[0]
	if len(container.Command) != 3 || !strings.Contains(container.Command[2], "ollama pull") {
		t.Fatalf("expected Ollama pull command, got %#v", container.Command)
	}
	if len(container.VolumeMounts) != 1 || container.VolumeMounts[0].MountPath != "/root/.ollama" {
		t.Fatalf("expected persistent Ollama model cache, got %#v", container.VolumeMounts)
	}
	if deployment.Spec.Template.Spec.NodeSelector["workload.openmycelium.io/tier"] != "inference" {
		t.Fatal("pool selector was not propagated")
	}
}

func TestGenericContainerRetainsImageCapabilities(t *testing.T) {
	workload := Workload{ID: "job-123", Name: "nginx", Kind: "inference", Runtime: "kubernetes", Image: "nginx:alpine", Namespace: "models", ResourceName: "nginx-123", Port: 80, CPU: "100m", Memory: "128Mi", DesiredCount: 1}
	deployment, err := buildDeployment(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	security := deployment.Spec.Template.Spec.Containers[0].SecurityContext
	if security == nil || security.AllowPrivilegeEscalation == nil || *security.AllowPrivilegeEscalation {
		t.Fatal("generic containers must disable privilege escalation")
	}
	if security.Capabilities != nil && len(security.Capabilities.Drop) > 0 {
		t.Fatal("generic containers must retain image capabilities required by their entrypoint")
	}
}

func TestVendorPoolsMapToExtendedResources(t *testing.T) {
	tests := []struct {
		pool ManagedPool
		want string
	}{
		{ManagedPool{Vendor: "NVIDIA", Runtime: "cuda"}, "nvidia.com/gpu"},
		{ManagedPool{Vendor: "AMD", Runtime: "rocm"}, "amd.com/gpu"},
		{ManagedPool{Vendor: "Intel", Runtime: "oneapi"}, "gpu.intel.com/i915"},
	}
	for _, test := range tests {
		if got := string(gpuResourceForPool(test.pool)); got != test.want {
			t.Fatalf("expected %s, got %s", test.want, got)
		}
	}
}

func TestSlicedPoolUsesAdvertisedExtendedResource(t *testing.T) {
	pool := ManagedPool{Vendor: "NVIDIA", Runtime: "cuda", SharingMode: "mig", SliceProfile: "1g.10gb", ResourceName: "nvidia.com/mig-1g.10gb"}
	workload := Workload{CPU: "1", Memory: "2Gi", Accelerators: 1}
	resources, err := workloadResources(workload, pool)
	if err != nil {
		t.Fatal(err)
	}
	requested, ok := resources.Requests["nvidia.com/mig-1g.10gb"]
	if !ok || requested.Value() != 1 {
		t.Fatalf("expected one MIG slice request, got %#v", resources.Requests)
	}
	if _, exists := resources.Requests["nvidia.com/gpu"]; exists {
		t.Fatal("a sliced pool must not request the whole-device resource")
	}
}

func TestKubernetesServiceProxyNameIncludesSchemeAndPort(t *testing.T) {
	httpPort := corev1.ServicePort{Name: "web", Port: 80}
	if got := kubernetesServiceProxyName("internet-test", httpPort); got != "http:internet-test:80" {
		t.Fatalf("unexpected HTTP service proxy name: %s", got)
	}
	protocol := "https"
	httpsPort := corev1.ServicePort{Name: "web", Port: 8443, AppProtocol: &protocol}
	if got := kubernetesServiceProxyName("model-api", httpsPort); got != "https:model-api:8443" {
		t.Fatalf("unexpected HTTPS service proxy name: %s", got)
	}
}

func TestAcceleratorResourceNameValidation(t *testing.T) {
	for _, value := range []string{"nvidia.com/gpu.shared", "nvidia.com/mig-1g.10gb", "amd.com/gpu"} {
		if !validExtendedResourceName(value) {
			t.Fatalf("expected %q to be a valid extended resource", value)
		}
	}
	for _, value := range []string{"gpu", "kubernetes.io/gpu", "bad resource/gpu"} {
		if validExtendedResourceName(value) {
			t.Fatalf("expected %q to be rejected", value)
		}
	}
}

func TestKueueParallelJobIsIndexedAndSuspendedForAdmission(t *testing.T) {
	workload := Workload{ID: "job-kueue", Kind: "training", Image: "trainer:latest", Namespace: "research", ResourceName: "trainer-kueue", ServiceName: "trainer-kueue-workers", CPU: "2", Memory: "8Gi", DesiredCount: 4, SchedulerBackend: "kueue", QueueName: "research"}
	job, err := buildJob(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if job.Spec.Parallelism == nil || *job.Spec.Parallelism != 4 || job.Spec.Completions == nil || *job.Spec.Completions != 4 {
		t.Fatalf("expected four parallel completions, got %#v", job.Spec)
	}
	if job.Spec.CompletionMode == nil || *job.Spec.CompletionMode != batchv1.IndexedCompletion {
		t.Fatal("distributed jobs must use indexed completion mode")
	}
	if job.Spec.Suspend == nil || !*job.Spec.Suspend || job.Labels["kueue.x-k8s.io/queue-name"] != "research" {
		t.Fatalf("expected Kueue admission contract, got labels=%#v suspend=%#v", job.Labels, job.Spec.Suspend)
	}
	service := buildService(workload)
	if service == nil || service.Spec.ClusterIP != corev1.ClusterIPNone || !service.Spec.PublishNotReadyAddresses {
		t.Fatalf("expected a headless rendezvous service, got %#v", service)
	}
}

func TestVolcanoGangAndTopologyContractsReachPodTemplate(t *testing.T) {
	workload := Workload{ID: "job-volcano", Kind: "finetuning", Image: "trainer:latest", Namespace: "research", ResourceName: "trainer-volcano", CPU: "2", Memory: "8Gi", DesiredCount: 3, SchedulerBackend: "volcano", QueueName: "gpu-research", GangMinAvailable: 3, TopologyMode: "spread", TopologyKey: "topology.kubernetes.io/zone", NetworkMode: "rdma", PriorityClass: "high-priority"}
	job, err := buildJob(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if job.Annotations["scheduling.volcano.sh/group-min-member"] != "3" || job.Spec.Template.Spec.SchedulerName != "volcano" {
		t.Fatalf("expected Volcano gang metadata, got annotations=%#v scheduler=%q", job.Annotations, job.Spec.Template.Spec.SchedulerName)
	}
	if job.Spec.Template.Spec.PriorityClassName != "high-priority" || len(job.Spec.Template.Spec.TopologySpreadConstraints) != 1 {
		t.Fatalf("expected priority and topology policy, got %#v", job.Spec.Template.Spec)
	}
	environment := map[string]string{}
	for _, item := range job.Spec.Template.Spec.Containers[0].Env {
		environment[item.Name] = item.Value
	}
	if environment["NCCL_IB_DISABLE"] != "0" || environment["UCX_TLS"] == "" {
		t.Fatalf("expected RDMA runtime contract, got %#v", environment)
	}
}

func TestMCCLExecutionContractIncludesCoordinator(t *testing.T) {
	t.Setenv("MCCL_COORDINATOR_SERVICE", "mccl.test.svc")
	t.Setenv("MCCL_COORDINATOR_PORT", "31000")
	workload := Workload{
		ID: "job-mccl", Kind: "training", Image: "trainer:latest", Namespace: "research",
		ResourceName: "trainer-mccl", ServiceName: "trainer-mccl-workers", CPU: "2", Memory: "8Gi",
		DesiredCount: 2, ExecutionPlanID: "plan-mccl", ExecutionTransport: "mccl-tcp",
	}
	job, err := buildJob(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	environment := map[string]string{}
	for _, item := range job.Spec.Template.Spec.Containers[0].Env {
		environment[item.Name] = item.Value
	}
	if environment["MCCL_COORDINATOR_HOST"] != "mccl.test.svc" || environment["MCCL_COORDINATOR_PORT"] != "31000" {
		t.Fatalf("expected configured MCCL coordinator, got %#v", environment)
	}
	if environment["MCCL_GROUP"] != "plan-mccl" || environment["MCCL_BACKEND"] != "tcp" {
		t.Fatalf("expected plan-scoped portable MCCL contract, got %#v", environment)
	}
}

func TestCeliumWorkloadKindsSelectTheCorrectController(t *testing.T) {
	for _, kind := range []string{"training", "finetuning", "batch"} {
		if !workloadUsesJob(kind) {
			t.Fatalf("%s must run as a Kubernetes Job", kind)
		}
	}
	for _, kind := range []string{"inference", "interactive", "agent"} {
		if workloadUsesJob(kind) || !workloadUsesService(kind) {
			t.Fatalf("%s must run as a service-backed Deployment", kind)
		}
	}
}

func TestInvalidPoolSelectorIsRejected(t *testing.T) {
	if _, err := parseNodeSelector("runtime"); err == nil {
		t.Fatal("expected invalid selector to fail")
	}
}

func testReadyNode(name string) *corev1.Node {
	return &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{Name: name, Labels: map[string]string{"node-role.kubernetes.io/worker": "", "nvidia.com/gpu.product": "RTX-Test"}},
		Status: corev1.NodeStatus{
			Capacity: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("4"), corev1.ResourceMemory: resource.MustParse("8Gi"),
				corev1.ResourcePods: resource.MustParse("110"), "nvidia.com/gpu": resource.MustParse("1"),
			},
			Allocatable: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("4"), corev1.ResourceMemory: resource.MustParse("8Gi"),
				corev1.ResourcePods: resource.MustParse("110"), "nvidia.com/gpu": resource.MustParse("1"),
			},
			Conditions: []corev1.NodeCondition{{Type: corev1.NodeReady, Status: corev1.ConditionTrue}},
			Addresses:  []corev1.NodeAddress{{Type: corev1.NodeInternalIP, Address: "10.0.0.12"}},
			NodeInfo:   corev1.NodeSystemInfo{OperatingSystem: "linux", Architecture: "amd64", KubeletVersion: "v1.30.0", ContainerRuntimeVersion: "containerd://2"},
		},
	}
}

func TestClusterNodeInventoryIncludesAvailableCapacityAndAccelerators(t *testing.T) {
	node := testReadyNode("worker-1")
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "existing", Namespace: "default"},
		Spec: corev1.PodSpec{NodeName: node.Name, Containers: []corev1.Container{{Name: "work", Resources: corev1.ResourceRequirements{Requests: corev1.ResourceList{
			corev1.ResourceCPU: resource.MustParse("1500m"), corev1.ResourceMemory: resource.MustParse("2Gi"), "nvidia.com/gpu": resource.MustParse("1"),
		}}}}},
		Status: corev1.PodStatus{Phase: corev1.PodRunning},
	}
	nodes := buildClusterNodeInventory([]corev1.Node{*node}, []corev1.Pod{*pod}, nil, "cluster-1", "research")
	if len(nodes) != 1 || nodes[0].CPUAvailable != "2500m" || nodes[0].MemoryAvailable != "6Gi" {
		t.Fatalf("unexpected node capacity: %#v", nodes)
	}
	if len(nodes[0].Accelerators) != 1 || nodes[0].Accelerators[0].Vendor != "NVIDIA" || nodes[0].Accelerators[0].Available != 0 {
		t.Fatalf("unexpected accelerator inventory: %#v", nodes[0].Accelerators)
	}
}

func TestClusterNodeInventoryDiscoversFabricCapabilities(t *testing.T) {
	node := testReadyNode("worker-fabric")
	node.Labels["openmycelium.io/rdma"] = "ready"
	node.Labels["openmycelium.io/gpudirect"] = "true"
	node.Labels["openmycelium.io/mccl"] = "enabled"
	node.Status.Capacity["rdma/rdma_shared_device_a"] = resource.MustParse("1")
	nodes := buildClusterNodeInventory([]corev1.Node{*node}, nil, nil, "cluster-1", "research")
	if len(nodes) != 1 {
		t.Fatalf("expected one node, got %d", len(nodes))
	}
	fabric := nodes[0].Fabric
	if !fabric.RDMA || !fabric.GPUDirect || !fabric.Qualified {
		t.Fatalf("expected qualified RDMA and GPUDirect inventory: %#v", fabric)
	}
	backends := strings.Join(fabric.Backends, ",")
	if !strings.Contains(backends, "mccl") || !strings.Contains(backends, "nccl") || !strings.Contains(backends, "ucx") {
		t.Fatalf("expected native and fabric backends, got %s", backends)
	}
}

func TestSchedulerPreflightSubtractsExistingPodRequests(t *testing.T) {
	node := testReadyNode("worker-1")
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "existing", Namespace: "default"},
		Spec: corev1.PodSpec{NodeName: node.Name, Containers: []corev1.Container{{Name: "work", Resources: corev1.ResourceRequirements{Requests: corev1.ResourceList{
			corev1.ResourceCPU: resource.MustParse("3"), corev1.ResourceMemory: resource.MustParse("7Gi"),
		}}}}},
		Status: corev1.PodStatus{Phase: corev1.PodRunning},
	}
	workload := Workload{CPU: "2", Memory: "2Gi"}
	requests := corev1.ResourceRequirements{Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("2"), corev1.ResourceMemory: resource.MustParse("2Gi")}}
	decision, err := evaluateSchedulableNodes([]corev1.Node{*node}, []corev1.Pod{*pod}, workload, requests)
	if err == nil || decision.Status != "rejected" {
		t.Fatalf("expected capacity rejection, got decision=%#v err=%v", decision, err)
	}
	requests.Requests[corev1.ResourceCPU] = resource.MustParse("500m")
	requests.Requests[corev1.ResourceMemory] = resource.MustParse("512Mi")
	decision, err = evaluateSchedulableNodes([]corev1.Node{*node}, []corev1.Pod{*pod}, workload, requests)
	if err != nil || decision.SelectedNode != "worker-1" || decision.Available["cpu"] != "1" {
		t.Fatalf("expected accepted placement with one CPU free, got decision=%#v err=%v", decision, err)
	}
}

func TestGangPreflightChecksAggregateCapacity(t *testing.T) {
	nodeOne, nodeTwo := testReadyNode("worker-1"), testReadyNode("worker-2")
	workload := Workload{Kind: "training", CPU: "2", Memory: "2Gi", Accelerators: 1, DesiredCount: 2, GangMinAvailable: 2}
	requests := corev1.ResourceRequirements{Requests: corev1.ResourceList{corev1.ResourceCPU: resource.MustParse("2"), corev1.ResourceMemory: resource.MustParse("2Gi"), "nvidia.com/gpu": resource.MustParse("1")}}
	decision, err := evaluateSchedulableReplicas([]corev1.Node{*nodeOne, *nodeTwo}, nil, workload, requests, 2)
	if err != nil || decision.Status != "accepted" || len(decision.SelectedNodes) != 2 {
		t.Fatalf("expected a two-member gang placement, got decision=%#v err=%v", decision, err)
	}
	decision, err = evaluateSchedulableReplicas([]corev1.Node{*nodeOne, *nodeTwo}, nil, workload, requests, 3)
	if err == nil || decision.Status != "rejected" {
		t.Fatalf("expected aggregate capacity rejection, got decision=%#v err=%v", decision, err)
	}
}

func TestIntegrationKubernetesInventory(t *testing.T) {
	path := os.Getenv("OPENMYCELIUM_TEST_KUBECONFIG")
	if path == "" {
		t.Skip("set OPENMYCELIUM_TEST_KUBECONFIG to verify a live cluster")
	}
	config, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	probe, err := probeKubernetesClusterInventory(ctx, config, "", "integration", "integration")
	if err != nil {
		t.Fatal(err)
	}
	if probe.Cluster.Nodes == 0 || probe.Cluster.ReadyNodes == 0 || len(probe.Nodes) != probe.Cluster.Nodes {
		t.Fatalf("unexpected live inventory: cluster=%#v nodes=%#v", probe.Cluster, probe.Nodes)
	}
	for _, node := range probe.Nodes {
		if node.Name == "" || node.CPUAllocatable == "" || node.MemoryAllocatable == "" {
			t.Fatalf("incomplete node inventory: %#v", node)
		}
	}
}
