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
	"strings"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	k8sschema "k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	yamlutil "k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/client-go/dynamic"
)

const (
	maximumManifestBytes   = 1 << 20
	maximumManifestObjects = 25
	manifestFieldManager   = "openmycelium-manifest"
)

type manifestRequest struct {
	ClusterID   string `json:"clusterId"`
	WorkspaceID string `json:"workspaceId"`
	Namespace   string `json:"namespace"`
	Manifest    string `json:"manifest"`
}

type manifestResource struct {
	APIVersion string   `json:"apiVersion"`
	Kind       string   `json:"kind"`
	Namespace  string   `json:"namespace"`
	Name       string   `json:"name"`
	Images     []string `json:"images"`
	DryRun     string   `json:"dryRun"`
}

type managedManifestResource struct {
	APIVersion string    `json:"apiVersion"`
	Kind       string    `json:"kind"`
	Namespace  string    `json:"namespace"`
	Name       string    `json:"name"`
	Images     []string  `json:"images"`
	Status     string    `json:"status"`
	Ready      int64     `json:"ready"`
	Desired    int64     `json:"desired"`
	CreatedAt  time.Time `json:"createdAt"`
}

type manifestRule struct {
	Resource   string
	Namespaced bool
}

var manifestAllowlist = map[k8sschema.GroupVersionKind]manifestRule{
	{Group: "", Version: "v1", Kind: "Pod"}:                                {Resource: "pods", Namespaced: true},
	{Group: "", Version: "v1", Kind: "Service"}:                            {Resource: "services", Namespaced: true},
	{Group: "", Version: "v1", Kind: "ConfigMap"}:                          {Resource: "configmaps", Namespaced: true},
	{Group: "", Version: "v1", Kind: "Secret"}:                             {Resource: "secrets", Namespaced: true},
	{Group: "", Version: "v1", Kind: "PersistentVolumeClaim"}:              {Resource: "persistentvolumeclaims", Namespaced: true},
	{Group: "", Version: "v1", Kind: "ServiceAccount"}:                     {Resource: "serviceaccounts", Namespaced: true},
	{Group: "apps", Version: "v1", Kind: "Deployment"}:                     {Resource: "deployments", Namespaced: true},
	{Group: "apps", Version: "v1", Kind: "StatefulSet"}:                    {Resource: "statefulsets", Namespaced: true},
	{Group: "apps", Version: "v1", Kind: "DaemonSet"}:                      {Resource: "daemonsets", Namespaced: true},
	{Group: "batch", Version: "v1", Kind: "Job"}:                           {Resource: "jobs", Namespaced: true},
	{Group: "batch", Version: "v1", Kind: "CronJob"}:                       {Resource: "cronjobs", Namespaced: true},
	{Group: "networking.k8s.io", Version: "v1", Kind: "Ingress"}:           {Resource: "ingresses", Namespaced: true},
	{Group: "networking.k8s.io", Version: "v1", Kind: "NetworkPolicy"}:     {Resource: "networkpolicies", Namespaced: true},
	{Group: "autoscaling", Version: "v2", Kind: "HorizontalPodAutoscaler"}: {Resource: "horizontalpodautoscalers", Namespaced: true},
}

type preparedManifest struct {
	Object   *unstructured.Unstructured
	Resource k8sschema.GroupVersionResource
	Summary  manifestResource
}

func parseManifestObjects(content, namespace string) ([]preparedManifest, error) {
	if strings.TrimSpace(content) == "" {
		return nil, errors.New("paste at least one Kubernetes YAML document")
	}
	if len(content) > maximumManifestBytes {
		return nil, errors.New("manifest exceeds the 1 MiB limit")
	}
	decoder := yamlutil.NewYAMLOrJSONDecoder(strings.NewReader(content), 64<<10)
	objects := []preparedManifest{}
	for {
		var value map[string]any
		err := decoder.Decode(&value)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("decode Kubernetes YAML: %w", err)
		}
		if len(value) == 0 {
			continue
		}
		if len(objects) >= maximumManifestObjects {
			return nil, fmt.Errorf("a manifest may contain at most %d resources", maximumManifestObjects)
		}
		object := &unstructured.Unstructured{Object: value}
		gvk := object.GroupVersionKind()
		rule, allowed := manifestAllowlist[gvk]
		if !allowed || !rule.Namespaced {
			return nil, fmt.Errorf("%s %s is not in the namespaced application-resource allowlist", object.GetAPIVersion(), object.GetKind())
		}
		if strings.TrimSpace(object.GetName()) == "" {
			return nil, fmt.Errorf("%s requires metadata.name", object.GetKind())
		}
		objectNamespace := strings.TrimSpace(object.GetNamespace())
		if objectNamespace != "" && objectNamespace != namespace {
			return nil, fmt.Errorf("%s/%s targets namespace %s; selected namespace is %s", object.GetKind(), object.GetName(), objectNamespace, namespace)
		}
		object.SetNamespace(namespace)
		labels := object.GetLabels()
		if labels == nil {
			labels = map[string]string{}
		}
		labels[managedByLabel] = "openmycelium"
		object.SetLabels(labels)
		images, err := validateManifestPodSecurity(object)
		if err != nil {
			return nil, fmt.Errorf("%s/%s: %w", object.GetKind(), object.GetName(), err)
		}
		objects = append(objects, preparedManifest{
			Object:   object,
			Resource: k8sschema.GroupVersionResource{Group: gvk.Group, Version: gvk.Version, Resource: rule.Resource},
			Summary:  manifestResource{APIVersion: object.GetAPIVersion(), Kind: object.GetKind(), Namespace: namespace, Name: object.GetName(), Images: images},
		})
	}
	if len(objects) == 0 {
		return nil, errors.New("manifest contains no Kubernetes resources")
	}
	return objects, nil
}

func manifestPodSpec(object *unstructured.Unstructured) (map[string]any, bool, error) {
	var path []string
	switch object.GetKind() {
	case "Pod":
		path = []string{"spec"}
	case "Deployment", "StatefulSet", "DaemonSet", "Job":
		path = []string{"spec", "template", "spec"}
	case "CronJob":
		path = []string{"spec", "jobTemplate", "spec", "template", "spec"}
	default:
		return nil, false, nil
	}
	value, found, err := unstructured.NestedMap(object.Object, path...)
	return value, found, err
}

func validateManifestPodSecurity(object *unstructured.Unstructured) ([]string, error) {
	spec, found, err := manifestPodSpec(object)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, nil
	}
	for _, field := range []string{"hostNetwork", "hostPID", "hostIPC"} {
		if enabled, _, _ := unstructured.NestedBool(spec, field); enabled {
			return nil, fmt.Errorf("spec.%s is blocked", field)
		}
	}
	volumes, _, _ := unstructured.NestedSlice(spec, "volumes")
	for _, entry := range volumes {
		volume, _ := entry.(map[string]any)
		if _, exists := volume["hostPath"]; exists {
			return nil, errors.New("hostPath volumes are blocked")
		}
	}
	images := []string{}
	for _, field := range []string{"initContainers", "containers", "ephemeralContainers"} {
		containers, _, _ := unstructured.NestedSlice(spec, field)
		for _, entry := range containers {
			container, _ := entry.(map[string]any)
			name, _ := container["name"].(string)
			image, _ := container["image"].(string)
			if strings.TrimSpace(image) == "" {
				return nil, fmt.Errorf("container %s requires an image", name)
			}
			images = append(images, image)
			security, _ := container["securityContext"].(map[string]any)
			if privileged, _ := security["privileged"].(bool); privileged {
				return nil, fmt.Errorf("container %s requests privileged mode", name)
			}
			if escalation, _ := security["allowPrivilegeEscalation"].(bool); escalation {
				return nil, fmt.Errorf("container %s enables privilege escalation", name)
			}
			if capabilities, ok := security["capabilities"].(map[string]any); ok {
				if added, ok := capabilities["add"].([]any); ok && len(added) > 0 {
					return nil, fmt.Errorf("container %s adds Linux capabilities", name)
				}
			}
			ports, _, _ := unstructured.NestedSlice(container, "ports")
			for _, portEntry := range ports {
				port, _ := portEntry.(map[string]any)
				if hostPort, ok := port["hostPort"].(int64); ok && hostPort > 0 {
					return nil, fmt.Errorf("container %s uses hostPort", name)
				}
			}
		}
	}
	return images, nil
}

func applyPreparedManifest(ctx context.Context, client dynamic.Interface, item preparedManifest, dryRun bool) (manifestResource, error) {
	payload, err := json.Marshal(item.Object.Object)
	if err != nil {
		return item.Summary, err
	}
	force := true
	options := metav1.PatchOptions{FieldManager: manifestFieldManager, Force: &force}
	if dryRun {
		options.DryRun = []string{metav1.DryRunAll}
		item.Summary.DryRun = "accepted"
	}
	_, err = client.Resource(item.Resource).Namespace(item.Summary.Namespace).Patch(ctx, item.Summary.Name, types.ApplyPatchType, payload, options)
	return item.Summary, err
}

func labelPreparedManifests(items []preparedManifest, workspaceID, releaseID string) {
	if workspaceID == "" {
		return
	}
	for index := range items {
		object := items[index].Object
		labels := object.GetLabels()
		if labels == nil {
			labels = map[string]string{}
		}
		labels[workspaceLabel] = workspaceID
		if releaseID != "" {
			labels[releaseLabel] = releaseID
		}
		object.SetLabels(labels)
		var templatePath []string
		switch object.GetKind() {
		case "Deployment", "StatefulSet", "DaemonSet", "Job":
			templatePath = []string{"spec", "template", "metadata", "labels"}
		case "CronJob":
			templatePath = []string{"spec", "jobTemplate", "spec", "template", "metadata", "labels"}
		}
		if len(templatePath) > 0 {
			templateLabels, _, _ := unstructured.NestedStringMap(object.Object, templatePath...)
			if templateLabels == nil {
				templateLabels = map[string]string{}
			}
			templateLabels[workspaceLabel] = workspaceID
			if releaseID != "" {
				templateLabels[releaseLabel] = releaseID
			}
			_ = unstructured.SetNestedStringMap(object.Object, templateLabels, templatePath...)
		}
	}
}

func managedResourceStatus(object *unstructured.Unstructured) (string, int64, int64) {
	desired, ready := int64(0), int64(0)
	status := "Applied"
	switch object.GetKind() {
	case "Deployment", "StatefulSet":
		desired, _, _ = unstructured.NestedInt64(object.Object, "spec", "replicas")
		if desired == 0 {
			desired = 1
		}
		ready, _, _ = unstructured.NestedInt64(object.Object, "status", "readyReplicas")
		status = "Pending"
		if ready >= desired {
			status = "Ready"
		}
	case "DaemonSet":
		desired, _, _ = unstructured.NestedInt64(object.Object, "status", "desiredNumberScheduled")
		ready, _, _ = unstructured.NestedInt64(object.Object, "status", "numberReady")
		status = "Pending"
		if desired > 0 && ready >= desired {
			status = "Ready"
		}
	case "Pod":
		desired = 1
		status, _, _ = unstructured.NestedString(object.Object, "status", "phase")
		if status == "Running" || status == "Succeeded" {
			ready = 1
		}
		if status == "" {
			status = "Pending"
		}
	case "Job":
		desired, _, _ = unstructured.NestedInt64(object.Object, "spec", "completions")
		if desired == 0 {
			desired = 1
		}
		ready, _, _ = unstructured.NestedInt64(object.Object, "status", "succeeded")
		failed, _, _ := unstructured.NestedInt64(object.Object, "status", "failed")
		status = "Running"
		if failed > 0 {
			status = "Failed"
		} else if ready >= desired {
			status = "Succeeded"
		}
	case "PersistentVolumeClaim":
		status, _, _ = unstructured.NestedString(object.Object, "status", "phase")
		if status == "" {
			status = "Pending"
		}
	case "Service":
		status = "Ready"
	}
	return status, ready, desired
}

func listManagedManifestResources(ctx context.Context, client dynamic.Interface, namespace string) ([]managedManifestResource, error) {
	items := []managedManifestResource{}
	seen := map[k8sschema.GroupVersionResource]bool{}
	var firstErr error
	for gvk, rule := range manifestAllowlist {
		gvr := k8sschema.GroupVersionResource{Group: gvk.Group, Version: gvk.Version, Resource: rule.Resource}
		if seen[gvr] {
			continue
		}
		seen[gvr] = true
		list, err := client.Resource(gvr).Namespace(namespace).List(ctx, metav1.ListOptions{LabelSelector: managedByLabel + "=openmycelium"})
		if err != nil {
			if firstErr == nil && !apierrors.IsNotFound(err) && !apierrors.IsForbidden(err) {
				firstErr = err
			}
			continue
		}
		for index := range list.Items {
			object := &list.Items[index]
			if object.GetKind() == "" {
				object.SetGroupVersionKind(gvk)
			}
			status, ready, desired := managedResourceStatus(object)
			images, _ := validateManifestPodSecurity(object)
			items = append(items, managedManifestResource{APIVersion: object.GetAPIVersion(), Kind: object.GetKind(), Namespace: namespace, Name: object.GetName(), Images: images, Status: status, Ready: ready, Desired: desired, CreatedAt: object.GetCreationTimestamp().Time})
		}
	}
	if len(items) == 0 && firstErr != nil {
		return nil, firstErr
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Kind == items[j].Kind {
			return items[i].Name < items[j].Name
		}
		return items[i].Kind < items[j].Kind
	})
	return items, nil
}

func manifestDeploymentHandler(w http.ResponseWriter, r *http.Request) {
	action := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/manifests/"), "/")
	if action == "resources" && r.Method == http.MethodGet {
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
		defer cancel()
		_, config, cluster, err := kubernetesClientForCluster(ctx, strings.TrimSpace(r.URL.Query().Get("clusterId")))
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		namespace := strings.TrimSpace(r.URL.Query().Get("namespace"))
		if namespace == "" {
			namespace = cluster.Namespace
			if namespace == "" {
				namespace = defaultWorkloadNamespace
			}
		}
		dynamicClient, err := dynamic.NewForConfig(config)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "create Kubernetes dynamic client: " + err.Error()})
			return
		}
		resources, err := listManagedManifestResources(ctx, dynamicClient, namespace)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "list managed resources: " + err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"clusterId": cluster.ID, "clusterName": cluster.Name, "namespace": namespace, "resources": resources})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if action != "preview" && action != "apply" {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "manifest operation not found"})
		return
	}
	var input manifestRequest
	if decode(r, &input) != nil || strings.TrimSpace(input.Manifest) == "" || strings.TrimSpace(input.ClusterID) == "" && strings.TrimSpace(input.WorkspaceID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "workspaceId or clusterId, plus manifest, are required"})
		return
	}
	input.WorkspaceID = strings.TrimSpace(input.WorkspaceID)
	if input.WorkspaceID != "" {
		workspace, workspaceErr := loadWorkspace(r.Context(), input.WorkspaceID)
		if workspaceErr != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "selected workspace does not exist"})
			return
		}
		input.ClusterID, input.Namespace = workspace.ClusterID, workspace.Namespace
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	typedClient, config, cluster, err := kubernetesClientForCluster(ctx, strings.TrimSpace(input.ClusterID))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	namespace := strings.TrimSpace(input.Namespace)
	if namespace == "" {
		namespace = cluster.Namespace
		if namespace == "" {
			namespace = defaultWorkloadNamespace
		}
	}
	items, err := parseManifestObjects(input.Manifest, namespace)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	releaseID := ""
	if action == "apply" && input.WorkspaceID != "" {
		releaseID = "rel_" + randomToken(9)
	}
	labelPreparedManifests(items, input.WorkspaceID, releaseID)
	if action == "apply" {
		if err = ensureNamespace(ctx, typedClient, namespace); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "ensure namespace: " + err.Error()})
			return
		}
	} else if _, err = typedClient.CoreV1().Namespaces().Get(ctx, namespace, metav1.GetOptions{}); err != nil {
		if apierrors.IsNotFound(err) {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "target namespace does not exist; create it first or choose the cluster workload namespace"})
		} else {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "verify namespace: " + err.Error()})
		}
		return
	}
	dynamicClient, err := dynamic.NewForConfig(config)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "create Kubernetes dynamic client: " + err.Error()})
		return
	}
	resources := make([]manifestResource, 0, len(items))
	for _, item := range items {
		resource, applyErr := applyPreparedManifest(ctx, dynamicClient, item, true)
		if applyErr != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": fmt.Sprintf("Kubernetes dry-run rejected %s/%s: %v", item.Summary.Kind, item.Summary.Name, applyErr)})
			return
		}
		resources = append(resources, resource)
	}
	if action == "apply" {
		resources = resources[:0]
		for _, item := range items {
			resource, applyErr := applyPreparedManifest(ctx, dynamicClient, item, false)
			if applyErr != nil {
				writeJSON(w, http.StatusBadGateway, map[string]string{"error": fmt.Sprintf("apply failed at %s/%s after earlier resources may have succeeded: %v", item.Summary.Kind, item.Summary.Name, applyErr)})
				return
			}
			resource.DryRun = "applied"
			resources = append(resources, resource)
		}
		if input.WorkspaceID != "" {
			releaseName := resources[0].Name
			if len(resources) > 1 {
				releaseName += fmt.Sprintf(" + %d resources", len(resources)-1)
			}
			if err = workspaceRelease(r.Context(), input.WorkspaceID, releaseID, releaseName, "manifest", fmt.Sprintf("sha256:%x", sha256.Sum256([]byte(input.Manifest))), "applied", resources); err != nil {
				writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "resources applied but workspace release could not be recorded"})
				return
			}
		}
	}
	digest := sha256.Sum256([]byte(input.Manifest))
	auditRequest(r, "manifest."+action, map[string]any{"cluster_id": cluster.ID, "namespace": namespace, "objects": len(resources), "sha256": fmt.Sprintf("%x", digest[:])})
	writeJSON(w, http.StatusOK, map[string]any{"action": action, "workspaceId": input.WorkspaceID, "releaseId": releaseID, "clusterId": cluster.ID, "clusterName": cluster.Name, "namespace": namespace, "resources": resources})
}
