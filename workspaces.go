package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	workspaceLabel = "openmycelium.io/workspace-id"
	releaseLabel   = "openmycelium.io/release-id"
)

type Workspace struct {
	ID               string    `json:"id"`
	Name             string    `json:"name"`
	Organization     string    `json:"organization"`
	ClusterID        string    `json:"clusterId"`
	ClusterName      string    `json:"clusterName"`
	Namespace        string    `json:"namespace"`
	Queue            string    `json:"queue"`
	StorageClass     string    `json:"storageClass"`
	CPUQuota         string    `json:"cpuQuota"`
	MemoryQuotaGB    int       `json:"memoryQuotaGB"`
	AcceleratorQuota int       `json:"acceleratorQuota"`
	NetworkPolicy    string    `json:"networkPolicy"`
	CreatedBy        string    `json:"createdBy"`
	CreatedAt        time.Time `json:"createdAt"`
	UpdatedAt        time.Time `json:"updatedAt"`
}

type WorkspaceRelease struct {
	ID          string         `json:"id"`
	WorkspaceID string         `json:"workspaceId"`
	Name        string         `json:"name"`
	SourceType  string         `json:"sourceType"`
	SourceRef   string         `json:"sourceRef"`
	Status      string         `json:"status"`
	Summary     map[string]any `json:"summary"`
	CreatedAt   time.Time      `json:"createdAt"`
	UpdatedAt   time.Time      `json:"updatedAt"`
}

type workspacePod struct {
	Name       string            `json:"name"`
	Phase      string            `json:"phase"`
	Node       string            `json:"node"`
	PodIP      string            `json:"podIp"`
	Ready      int               `json:"ready"`
	Containers int               `json:"containers"`
	Restarts   int32             `json:"restarts"`
	Images     []string          `json:"images"`
	Labels     map[string]string `json:"labels"`
	CreatedAt  time.Time         `json:"createdAt"`
}

type workspaceController struct {
	Kind      string    `json:"kind"`
	Name      string    `json:"name"`
	Ready     int32     `json:"ready"`
	Desired   int32     `json:"desired"`
	Status    string    `json:"status"`
	ReleaseID string    `json:"releaseId,omitempty"`
	CreatedAt time.Time `json:"createdAt"`
}

type workspaceService struct {
	Name        string               `json:"name"`
	Type        string               `json:"type"`
	ClusterIP   string               `json:"clusterIp"`
	Ports       []corev1.ServicePort `json:"ports"`
	ExternalURL string               `json:"externalUrl,omitempty"`
	GatewayURL  string               `json:"gatewayUrl"`
	CreatedAt   time.Time            `json:"createdAt"`
}

type workspaceStorage struct {
	Name         string    `json:"name"`
	Phase        string    `json:"phase"`
	StorageClass string    `json:"storageClass"`
	Requested    string    `json:"requested"`
	Capacity     string    `json:"capacity"`
	CreatedAt    time.Time `json:"createdAt"`
}

type workspaceConfiguration struct {
	Kind      string    `json:"kind"`
	Name      string    `json:"name"`
	Type      string    `json:"type,omitempty"`
	CreatedAt time.Time `json:"createdAt"`
}

type workspaceEvent struct {
	Type      string    `json:"type"`
	Reason    string    `json:"reason"`
	Object    string    `json:"object"`
	Message   string    `json:"message"`
	Count     int32     `json:"count"`
	Timestamp time.Time `json:"timestamp"`
}

type workspaceInventory struct {
	Workspace     Workspace                `json:"workspace"`
	Nodes         []ClusterNode            `json:"nodes"`
	Workloads     []Workload               `json:"workloads"`
	Controllers   []workspaceController    `json:"controllers"`
	Pods          []workspacePod           `json:"pods"`
	Services      []workspaceService       `json:"services"`
	Storage       []workspaceStorage       `json:"storage"`
	Configuration []workspaceConfiguration `json:"configuration"`
	Events        []workspaceEvent         `json:"events"`
	Releases      []WorkspaceRelease       `json:"releases"`
	UpdatedAt     time.Time                `json:"updatedAt"`
}

func scanWorkspace(row interface{ Scan(...any) error }) (Workspace, error) {
	var item Workspace
	err := row.Scan(&item.ID, &item.Name, &item.Organization, &item.ClusterID, &item.ClusterName, &item.Namespace, &item.Queue, &item.StorageClass, &item.CPUQuota, &item.MemoryQuotaGB, &item.AcceleratorQuota, &item.NetworkPolicy, &item.CreatedBy, &item.CreatedAt, &item.UpdatedAt)
	return item, err
}

func loadWorkspace(ctx context.Context, id string) (Workspace, error) {
	if database == nil {
		return Workspace{}, errors.New("PostgreSQL is required")
	}
	return scanWorkspace(database.QueryRow(ctx, `SELECT w.id,w.name,w.organization,w.cluster_id,c.name,w.namespace,w.queue,w.storage_class,w.cpu_quota,w.memory_quota_gb,w.accelerator_quota,w.network_policy,w.created_by,w.created_at,w.updated_at FROM workspaces w JOIN clusters c ON c.id=w.cluster_id WHERE w.id=$1`, id))
}

func ensureDefaultWorkspaces(ctx context.Context) {
	if database == nil {
		return
	}
	rows, err := database.Query(ctx, `SELECT id,name,namespace,storage_class FROM clusters WHERE type='kubernetes' AND octet_length(kubeconfig)>0`)
	if err != nil {
		return
	}
	defer rows.Close()
	state.RLock()
	organization, queue := state.Settings.Organization, state.Settings.DefaultQueue
	state.RUnlock()
	for rows.Next() {
		var clusterID, clusterName, namespace, storageClass string
		if rows.Scan(&clusterID, &clusterName, &namespace, &storageClass) != nil {
			continue
		}
		if namespace == "" {
			namespace = defaultWorkloadNamespace
		}
		_, _ = database.Exec(ctx, `INSERT INTO workspaces(id,name,organization,cluster_id,namespace,queue,storage_class,network_policy,created_by) VALUES($1,$2,$3,$4,$5,$6,$7,'cluster-default','system') ON CONFLICT(cluster_id,namespace) DO NOTHING`, "ws_"+randomToken(9), clusterName, organization, clusterID, namespace, queue, storageClass)
	}
}

func workspacesHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		ensureDefaultWorkspaces(r.Context())
		rows, err := database.Query(r.Context(), `SELECT w.id,w.name,w.organization,w.cluster_id,c.name,w.namespace,w.queue,w.storage_class,w.cpu_quota,w.memory_quota_gb,w.accelerator_quota,w.network_policy,w.created_by,w.created_at,w.updated_at FROM workspaces w JOIN clusters c ON c.id=w.cluster_id ORDER BY w.created_at`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list workspaces"})
			return
		}
		defer rows.Close()
		items := []Workspace{}
		for rows.Next() {
			item, scanErr := scanWorkspace(rows)
			if scanErr == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"workspaces": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input Workspace
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid workspace definition"})
		return
	}
	input.Name, input.ClusterID, input.Namespace = strings.TrimSpace(input.Name), strings.TrimSpace(input.ClusterID), strings.TrimSpace(input.Namespace)
	if input.Name == "" || input.ClusterID == "" || input.Namespace == "" || len(input.Name) > 80 || len(input.Namespace) > 63 || !kubernetesObjectNamePattern.MatchString(input.Namespace) {
		writeJSON(w, 400, map[string]string{"error": "name, connected cluster, and a valid Kubernetes namespace are required"})
		return
	}
	if input.MemoryQuotaGB < 0 || input.AcceleratorQuota < 0 {
		writeJSON(w, 400, map[string]string{"error": "workspace quotas cannot be negative"})
		return
	}
	if input.NetworkPolicy == "" {
		input.NetworkPolicy = "cluster-default"
	}
	client, _, cluster, err := kubernetesClientForCluster(r.Context(), input.ClusterID)
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": err.Error()})
		return
	}
	if err = ensureNamespace(r.Context(), client, input.Namespace); err != nil {
		writeJSON(w, 502, map[string]string{"error": "prepare workspace namespace: " + err.Error()})
		return
	}
	state.RLock()
	if input.Organization == "" {
		input.Organization = state.Settings.Organization
	}
	if input.Queue == "" {
		input.Queue = state.Settings.DefaultQueue
	}
	state.RUnlock()
	if input.StorageClass == "" {
		input.StorageClass = cluster.StorageClass
	}
	input.ID, input.ClusterName, input.CreatedBy = "ws_"+randomToken(9), cluster.Name, requestActor(r)
	input.CreatedAt, input.UpdatedAt = time.Now().UTC(), time.Now().UTC()
	_, err = database.Exec(r.Context(), `INSERT INTO workspaces(id,name,organization,cluster_id,namespace,queue,storage_class,cpu_quota,memory_quota_gb,accelerator_quota,network_policy,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`, input.ID, input.Name, input.Organization, input.ClusterID, input.Namespace, input.Queue, input.StorageClass, strings.TrimSpace(input.CPUQuota), input.MemoryQuotaGB, input.AcceleratorQuota, input.NetworkPolicy, input.CreatedBy, input.CreatedAt, input.UpdatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a workspace already uses this name or cluster namespace"})
		return
	}
	auditRequest(r, "workspace.created", map[string]any{"workspace_id": input.ID, "cluster_id": input.ClusterID, "namespace": input.Namespace})
	writeJSON(w, 201, input)
}

func workspaceRelease(ctx context.Context, workspaceID, releaseID, name, sourceType, sourceRef, status string, summary any) error {
	if database == nil || workspaceID == "" {
		return nil
	}
	if releaseID == "" {
		releaseID = "rel_" + randomToken(9)
	}
	payload, _ := json.Marshal(summary)
	_, err := database.Exec(ctx, `INSERT INTO workspace_releases(id,workspace_id,name,source_type,source_ref,status,summary,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,now(),now()) ON CONFLICT(id) DO UPDATE SET status=excluded.status,summary=excluded.summary,updated_at=now()`, releaseID, workspaceID, name, sourceType, sourceRef, status, payload)
	return err
}

func loadWorkspaceReleases(ctx context.Context, workspaceID string) []WorkspaceRelease {
	items := []WorkspaceRelease{}
	rows, err := database.Query(ctx, `SELECT id,workspace_id,name,source_type,source_ref,status,summary,created_at,updated_at FROM workspace_releases WHERE workspace_id=$1 ORDER BY created_at DESC`, workspaceID)
	if err != nil {
		return items
	}
	defer rows.Close()
	for rows.Next() {
		var item WorkspaceRelease
		var summary []byte
		if rows.Scan(&item.ID, &item.WorkspaceID, &item.Name, &item.SourceType, &item.SourceRef, &item.Status, &summary, &item.CreatedAt, &item.UpdatedAt) == nil {
			_ = json.Unmarshal(summary, &item.Summary)
			items = append(items, item)
		}
	}
	return items
}

func workspaceControllers(deployments *appsv1.DeploymentList, statefulSets *appsv1.StatefulSetList, daemonSets *appsv1.DaemonSetList, jobs *batchv1.JobList, cronJobs *batchv1.CronJobList) []workspaceController {
	items := []workspaceController{}
	for _, item := range deployments.Items {
		desired := int32(1)
		if item.Spec.Replicas != nil {
			desired = *item.Spec.Replicas
		}
		status := "Pending"
		if item.Status.ReadyReplicas >= desired {
			status = "Ready"
		}
		items = append(items, workspaceController{Kind: "Deployment", Name: item.Name, Ready: item.Status.ReadyReplicas, Desired: desired, Status: status, ReleaseID: item.Labels[releaseLabel], CreatedAt: item.CreationTimestamp.Time})
	}
	for _, item := range statefulSets.Items {
		desired := int32(1)
		if item.Spec.Replicas != nil {
			desired = *item.Spec.Replicas
		}
		status := "Pending"
		if item.Status.ReadyReplicas >= desired {
			status = "Ready"
		}
		items = append(items, workspaceController{Kind: "StatefulSet", Name: item.Name, Ready: item.Status.ReadyReplicas, Desired: desired, Status: status, ReleaseID: item.Labels[releaseLabel], CreatedAt: item.CreationTimestamp.Time})
	}
	for _, item := range daemonSets.Items {
		status := "Pending"
		if item.Status.DesiredNumberScheduled > 0 && item.Status.NumberReady >= item.Status.DesiredNumberScheduled {
			status = "Ready"
		}
		items = append(items, workspaceController{Kind: "DaemonSet", Name: item.Name, Ready: item.Status.NumberReady, Desired: item.Status.DesiredNumberScheduled, Status: status, ReleaseID: item.Labels[releaseLabel], CreatedAt: item.CreationTimestamp.Time})
	}
	for _, item := range jobs.Items {
		desired := int32(1)
		if item.Spec.Completions != nil {
			desired = *item.Spec.Completions
		}
		status := "Running"
		if item.Status.Failed > 0 {
			status = "Failed"
		} else if item.Status.Succeeded >= desired {
			status = "Succeeded"
		}
		items = append(items, workspaceController{Kind: "Job", Name: item.Name, Ready: item.Status.Succeeded, Desired: desired, Status: status, ReleaseID: item.Labels[releaseLabel], CreatedAt: item.CreationTimestamp.Time})
	}
	for _, item := range cronJobs.Items {
		items = append(items, workspaceController{Kind: "CronJob", Name: item.Name, Ready: int32(len(item.Status.Active)), Desired: 1, Status: "Scheduled", ReleaseID: item.Labels[releaseLabel], CreatedAt: item.CreationTimestamp.Time})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].CreatedAt.After(items[j].CreatedAt) })
	return items
}

func reconcileManifestReleaseStatus(ctx context.Context, workspaceID string, controllers []workspaceController) {
	if database == nil {
		return
	}
	statuses := map[string]string{}
	for _, controller := range controllers {
		if controller.ReleaseID == "" {
			continue
		}
		status := "ready"
		switch controller.Status {
		case "Failed":
			status = "failed"
		case "Pending", "Running", "Scheduled":
			status = "pending"
		}
		if current := statuses[controller.ReleaseID]; current == "failed" || current == "pending" && status == "ready" {
			continue
		}
		statuses[controller.ReleaseID] = status
	}
	for releaseID, status := range statuses {
		_, _ = database.Exec(ctx, `UPDATE workspace_releases SET status=$1,updated_at=now() WHERE id=$2 AND workspace_id=$3 AND source_type='manifest'`, status, releaseID, workspaceID)
	}
}

func collectWorkspaceInventory(ctx context.Context, workspace Workspace) (workspaceInventory, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workspace.ClusterID)
	if err != nil {
		return workspaceInventory{}, err
	}
	deployments, err := client.AppsV1().Deployments(workspace.Namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return workspaceInventory{}, err
	}
	statefulSets, _ := client.AppsV1().StatefulSets(workspace.Namespace).List(ctx, metav1.ListOptions{})
	daemonSets, _ := client.AppsV1().DaemonSets(workspace.Namespace).List(ctx, metav1.ListOptions{})
	jobs, _ := client.BatchV1().Jobs(workspace.Namespace).List(ctx, metav1.ListOptions{})
	cronJobs, _ := client.BatchV1().CronJobs(workspace.Namespace).List(ctx, metav1.ListOptions{})
	pods, _ := client.CoreV1().Pods(workspace.Namespace).List(ctx, metav1.ListOptions{})
	services, _ := client.CoreV1().Services(workspace.Namespace).List(ctx, metav1.ListOptions{})
	claims, _ := client.CoreV1().PersistentVolumeClaims(workspace.Namespace).List(ctx, metav1.ListOptions{})
	configMaps, _ := client.CoreV1().ConfigMaps(workspace.Namespace).List(ctx, metav1.ListOptions{})
	secrets, _ := client.CoreV1().Secrets(workspace.Namespace).List(ctx, metav1.ListOptions{})
	events, _ := client.CoreV1().Events(workspace.Namespace).List(ctx, metav1.ListOptions{})
	nodes, nodeErr := collectClusterNodes(ctx, client, workspace.ClusterID, workspace.ClusterName)
	if nodeErr != nil {
		nodes, _ = loadClusterNodes(ctx, workspace.ClusterID)
	} else {
		_ = persistClusterNodes(ctx, workspace.ClusterID, workspace.ClusterName, nodes)
	}
	if statefulSets == nil {
		statefulSets = &appsv1.StatefulSetList{}
	}
	if daemonSets == nil {
		daemonSets = &appsv1.DaemonSetList{}
	}
	if jobs == nil {
		jobs = &batchv1.JobList{}
	}
	if cronJobs == nil {
		cronJobs = &batchv1.CronJobList{}
	}
	if pods == nil {
		pods = &corev1.PodList{}
	}
	if services == nil {
		services = &corev1.ServiceList{}
	}
	if claims == nil {
		claims = &corev1.PersistentVolumeClaimList{}
	}
	if configMaps == nil {
		configMaps = &corev1.ConfigMapList{}
	}
	if secrets == nil {
		secrets = &corev1.SecretList{}
	}
	if events == nil {
		events = &corev1.EventList{}
	}
	controllers := workspaceControllers(deployments, statefulSets, daemonSets, jobs, cronJobs)
	reconcileManifestReleaseStatus(ctx, workspace.ID, controllers)
	inventory := workspaceInventory{Workspace: workspace, Nodes: nodes, Controllers: controllers, Pods: []workspacePod{}, Services: []workspaceService{}, Storage: []workspaceStorage{}, Configuration: []workspaceConfiguration{}, Events: []workspaceEvent{}, Releases: loadWorkspaceReleases(ctx, workspace.ID), UpdatedAt: time.Now().UTC()}
	state.RLock()
	for _, workload := range state.Workloads {
		if workload.WorkspaceID == workspace.ID || workload.WorkspaceID == "" && workload.ClusterID == workspace.ClusterID && workload.Namespace == workspace.Namespace {
			inventory.Workloads = append(inventory.Workloads, workload)
		}
	}
	state.RUnlock()
	for _, pod := range pods.Items {
		item := workspacePod{Name: pod.Name, Phase: string(pod.Status.Phase), Node: pod.Spec.NodeName, PodIP: pod.Status.PodIP, Containers: len(pod.Spec.Containers), Labels: pod.Labels, CreatedAt: pod.CreationTimestamp.Time}
		for _, container := range pod.Spec.Containers {
			item.Images = append(item.Images, container.Image)
		}
		for _, status := range pod.Status.ContainerStatuses {
			item.Restarts += status.RestartCount
			if status.Ready {
				item.Ready++
			}
		}
		inventory.Pods = append(inventory.Pods, item)
	}
	firstNodeIP := ""
	for _, node := range nodes {
		if node.Ready && node.InternalIP != "" {
			firstNodeIP = node.InternalIP
			break
		}
	}
	for _, service := range services.Items {
		item := workspaceService{Name: service.Name, Type: string(service.Spec.Type), ClusterIP: service.Spec.ClusterIP, Ports: service.Spec.Ports, GatewayURL: "/api/v1/workspaces/" + workspace.ID + "/services/" + service.Name + "/gateway/", CreatedAt: service.CreationTimestamp.Time}
		if service.Spec.Type == corev1.ServiceTypeNodePort && firstNodeIP != "" && len(service.Spec.Ports) > 0 && service.Spec.Ports[0].NodePort > 0 {
			item.ExternalURL = servicePortScheme(service.Spec.Ports[0]) + "://" + firstNodeIP + ":" + strconv.Itoa(int(service.Spec.Ports[0].NodePort))
		} else if service.Spec.Type == corev1.ServiceTypeLoadBalancer && len(service.Status.LoadBalancer.Ingress) > 0 {
			host := service.Status.LoadBalancer.Ingress[0].Hostname
			if host == "" {
				host = service.Status.LoadBalancer.Ingress[0].IP
			}
			if host != "" {
				item.ExternalURL = servicePortScheme(service.Spec.Ports[0]) + "://" + host
			}
		}
		inventory.Services = append(inventory.Services, item)
	}
	for _, claim := range claims.Items {
		storageClass := ""
		if claim.Spec.StorageClassName != nil {
			storageClass = *claim.Spec.StorageClassName
		}
		requested, capacity := claim.Spec.Resources.Requests.Storage().String(), "pending"
		if value := claim.Status.Capacity.Storage(); value != nil {
			capacity = value.String()
		}
		inventory.Storage = append(inventory.Storage, workspaceStorage{Name: claim.Name, Phase: string(claim.Status.Phase), StorageClass: storageClass, Requested: requested, Capacity: capacity, CreatedAt: claim.CreationTimestamp.Time})
	}
	for _, item := range configMaps.Items {
		inventory.Configuration = append(inventory.Configuration, workspaceConfiguration{Kind: "ConfigMap", Name: item.Name, CreatedAt: item.CreationTimestamp.Time})
	}
	for _, item := range secrets.Items {
		inventory.Configuration = append(inventory.Configuration, workspaceConfiguration{Kind: "Secret", Name: item.Name, Type: string(item.Type), CreatedAt: item.CreationTimestamp.Time})
	}
	for _, event := range events.Items {
		timestamp := event.LastTimestamp.Time
		if timestamp.IsZero() {
			timestamp = event.EventTime.Time
		}
		inventory.Events = append(inventory.Events, workspaceEvent{Type: event.Type, Reason: event.Reason, Object: event.InvolvedObject.Kind + "/" + event.InvolvedObject.Name, Message: event.Message, Count: event.Count, Timestamp: timestamp})
	}
	sort.Slice(inventory.Events, func(i, j int) bool { return inventory.Events[i].Timestamp.After(inventory.Events[j].Timestamp) })
	if len(inventory.Events) > 100 {
		inventory.Events = inventory.Events[:100]
	}
	return inventory, nil
}

func workspacePodLogs(ctx context.Context, workspace Workspace, podName, container string, tail int64) (string, error) {
	client, _, _, err := kubernetesClientForCluster(ctx, workspace.ClusterID)
	if err != nil {
		return "", err
	}
	if _, err = client.CoreV1().Pods(workspace.Namespace).Get(ctx, podName, metav1.GetOptions{}); err != nil {
		return "", err
	}
	options := &corev1.PodLogOptions{TailLines: &tail, Timestamps: true, Container: container}
	stream, err := client.CoreV1().Pods(workspace.Namespace).GetLogs(podName, options).Stream(ctx)
	if err != nil {
		return "", err
	}
	defer stream.Close()
	content, err := io.ReadAll(io.LimitReader(stream, (2<<20)+1))
	if len(content) > 2<<20 {
		content = content[:2<<20]
	}
	return string(content), err
}

func workspaceServiceGateway(w http.ResponseWriter, r *http.Request, workspace Workspace, serviceName string, pathParts []string) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Minute)
	defer cancel()
	client, _, _, err := kubernetesClientForCluster(ctx, workspace.ClusterID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	service, err := client.CoreV1().Services(workspace.Namespace).Get(ctx, serviceName, metav1.GetOptions{})
	if err != nil || len(service.Spec.Ports) == 0 {
		http.Error(w, "workspace service or service port not found", http.StatusNotFound)
		return
	}
	servicePort := service.Spec.Ports[0]
	if requested, parseErr := strconv.ParseInt(r.URL.Query().Get("port"), 10, 32); parseErr == nil && requested > 0 {
		for _, candidate := range service.Spec.Ports {
			if candidate.Port == int32(requested) {
				servicePort = candidate
			}
		}
	}
	allowedMethods := map[string]bool{http.MethodGet: true, http.MethodHead: true, http.MethodPost: true, http.MethodPut: true, http.MethodPatch: true, http.MethodDelete: true, http.MethodOptions: true}
	if !allowedMethods[r.Method] {
		http.Error(w, "workspace gateway method is not supported", http.StatusMethodNotAllowed)
		return
	}
	request := client.CoreV1().RESTClient().Verb(r.Method).Namespace(workspace.Namespace).Resource("services").Name(kubernetesServiceProxyName(service.Name, servicePort)).SubResource("proxy")
	for _, part := range pathParts {
		if part == ".." {
			http.Error(w, "invalid service path", http.StatusBadRequest)
			return
		}
		if part != "" {
			request = request.Suffix(part)
		}
	}
	for key, values := range r.URL.Query() {
		if key == "port" {
			continue
		}
		for _, value := range values {
			request.Param(key, value)
		}
	}
	if r.Body != nil && r.Method != http.MethodGet && r.Method != http.MethodHead {
		content, readErr := io.ReadAll(io.LimitReader(r.Body, (16<<20)+1))
		if readErr != nil || len(content) > 16<<20 {
			http.Error(w, "workspace gateway request body exceeds 16 MiB", http.StatusRequestEntityTooLarge)
			return
		}
		request = request.Body(content)
	}
	if value := r.Header.Get("Content-Type"); value != "" {
		request = request.SetHeader("Content-Type", value)
	}
	if value := r.Header.Get("Accept"); value != "" {
		request = request.SetHeader("Accept", value)
	}
	statusCode, contentType := http.StatusOK, ""
	content, err := request.Do(ctx).StatusCode(&statusCode).ContentType(&contentType).Raw()
	if err != nil {
		http.Error(w, "workspace service returned an error: "+err.Error(), http.StatusBadGateway)
		return
	}
	if contentType == "" {
		contentType = http.DetectContentType(content)
	}
	if strings.Contains(contentType, "text/html") {
		base := "/api/v1/workspaces/" + workspace.ID + "/services/" + service.Name + "/gateway/"
		content = []byte(strings.Replace(string(content), "<head>", "<head><base href=\""+base+"\">", 1))
		w.Header().Set("Content-Security-Policy", "sandbox allow-forms allow-scripts allow-modals allow-downloads; default-src 'self' data: blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:")
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(statusCode)
	_, _ = w.Write(content)
}

func workspaceResourceHandler(w http.ResponseWriter, r *http.Request) {
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/workspaces/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeJSON(w, 404, map[string]string{"error": "workspace not found"})
		return
	}
	workspace, err := loadWorkspace(r.Context(), parts[0])
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": "workspace not found"})
		return
	}
	if len(parts) == 1 && r.Method == http.MethodGet {
		writeJSON(w, 200, workspace)
		return
	}
	if len(parts) == 1 && r.Method == http.MethodDelete {
		var count int
		_ = database.QueryRow(r.Context(), `SELECT count(*) FROM workspace_releases WHERE workspace_id=$1`, workspace.ID).Scan(&count)
		if count > 0 {
			writeJSON(w, 409, map[string]string{"error": "delete workspace releases before deleting the workspace"})
			return
		}
		_, _ = database.Exec(r.Context(), `DELETE FROM workspaces WHERE id=$1`, workspace.ID)
		auditRequest(r, "workspace.deleted", map[string]any{"workspace_id": workspace.ID})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	if len(parts) == 2 && parts[1] == "inventory" && r.Method == http.MethodGet {
		ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
		defer cancel()
		inventory, collectErr := collectWorkspaceInventory(ctx, workspace)
		if collectErr != nil {
			writeJSON(w, 502, map[string]string{"error": collectErr.Error()})
			return
		}
		writeJSON(w, 200, inventory)
		return
	}
	if len(parts) == 4 && parts[1] == "pods" && parts[3] == "logs" && r.Method == http.MethodGet {
		tail := int64(500)
		if value, parseErr := strconv.ParseInt(r.URL.Query().Get("tail"), 10, 64); parseErr == nil && value > 0 && value <= 5000 {
			tail = value
		}
		content, logErr := workspacePodLogs(r.Context(), workspace, parts[2], r.URL.Query().Get("container"), tail)
		if logErr != nil {
			writeJSON(w, 502, map[string]string{"error": logErr.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"pod": parts[2], "logs": content})
		return
	}
	if len(parts) == 4 && parts[1] == "pods" && parts[3] == "exec" && r.Method == http.MethodPost {
		var input struct {
			Container string `json:"container"`
			Command   string `json:"command"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid command request"})
			return
		}
		content, execErr := kubernetesWorkloadExec(r.Context(), Workload{ID: "workspace:" + workspace.ID, ClusterID: workspace.ClusterID, Namespace: workspace.Namespace, PodName: parts[2]}, strings.TrimSpace(input.Container), input.Command)
		hash := sha256.Sum256([]byte(input.Command))
		auditRequest(r, "workspace.pod_exec", map[string]any{"workspace_id": workspace.ID, "pod": parts[2], "container": strings.TrimSpace(input.Container), "command_sha256": fmt.Sprintf("%x", hash[:])})
		if execErr != nil && content == nil {
			writeJSON(w, 502, map[string]string{"error": execErr.Error()})
			return
		}
		writeJSON(w, 200, content)
		return
	}
	if len(parts) >= 4 && parts[1] == "services" && parts[3] == "gateway" {
		workspaceServiceGateway(w, r, workspace, parts[2], parts[4:])
		return
	}
	writeJSON(w, 405, map[string]string{"error": "workspace operation is not available"})
}
