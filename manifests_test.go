package main

import (
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

const validManifest = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: web
          image: nginx:alpine
---
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector:
    app: web
  ports:
    - port: 80
      targetPort: 80
`

func TestParseManifestObjectsAcceptsNamespacedApplicationResources(t *testing.T) {
	items, err := parseManifestObjects(validManifest, "research")
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 || items[0].Summary.Kind != "Deployment" || items[0].Summary.Namespace != "research" {
		t.Fatalf("unexpected manifest summary: %#v", items)
	}
	if len(items[0].Summary.Images) != 1 || items[0].Summary.Images[0] != "nginx:alpine" {
		t.Fatalf("container image was not discovered: %#v", items[0].Summary.Images)
	}
	if items[0].Object.GetLabels()[managedByLabel] != "openmycelium" {
		t.Fatal("managed-by label was not applied")
	}
}

func TestWorkspaceReleaseLabelsReachControllersAndPodTemplates(t *testing.T) {
	items, err := parseManifestObjects(validManifest, "research")
	if err != nil {
		t.Fatal(err)
	}
	labelPreparedManifests(items, "workspace-123", "release-456")
	for _, item := range items {
		if item.Object.GetLabels()[workspaceLabel] != "workspace-123" || item.Object.GetLabels()[releaseLabel] != "release-456" {
			t.Fatalf("workspace release labels missing from %s: %#v", item.Summary.Kind, item.Object.GetLabels())
		}
	}
	templateLabels, found, err := unstructured.NestedStringMap(items[0].Object.Object, "spec", "template", "metadata", "labels")
	if err != nil || !found {
		t.Fatalf("deployment pod template labels unavailable: found=%v err=%v", found, err)
	}
	if templateLabels[workspaceLabel] != "workspace-123" || templateLabels[releaseLabel] != "release-456" {
		t.Fatalf("workspace release labels missing from pod template: %#v", templateLabels)
	}
}

func TestParseManifestObjectsRejectsClusterScopedResources(t *testing.T) {
	_, err := parseManifestObjects("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: forbidden\n", "research")
	if err == nil || !strings.Contains(err.Error(), "allowlist") {
		t.Fatalf("expected cluster-scoped manifest rejection, got %v", err)
	}
}

func TestParseManifestObjectsRejectsNamespaceEscape(t *testing.T) {
	content := strings.Replace(validManifest, "name: web", "name: web\n  namespace: other", 1)
	_, err := parseManifestObjects(content, "research")
	if err == nil || !strings.Contains(err.Error(), "selected namespace") {
		t.Fatalf("expected namespace mismatch rejection, got %v", err)
	}
}

func TestParseManifestObjectsRejectsPrivilegedContainers(t *testing.T) {
	content := strings.Replace(validManifest, "image: nginx:alpine", "image: nginx:alpine\n          securityContext:\n            privileged: true", 1)
	_, err := parseManifestObjects(content, "research")
	if err == nil || !strings.Contains(err.Error(), "privileged") {
		t.Fatalf("expected privileged container rejection, got %v", err)
	}
}

func TestWorkloadPodTemplateUsesImagePullSecret(t *testing.T) {
	workload := Workload{ID: "job-private", Name: "private", Kind: "inference", Runtime: "kubernetes", Image: "registry.example.com/team/app:1", ImagePullSecret: "registry-credentials", Port: 8080, CPU: "100m", Memory: "128Mi", DesiredCount: 1}
	template, err := workloadPodTemplate(workload, ManagedPool{Vendor: "CPU", Runtime: "cpu"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(template.Spec.ImagePullSecrets) != 1 || template.Spec.ImagePullSecrets[0].Name != "registry-credentials" {
		t.Fatalf("image pull secret missing from pod spec: %#v", template.Spec.ImagePullSecrets)
	}
}

func TestManagedResourceStatusReportsDeploymentReadiness(t *testing.T) {
	object := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"spec":       map[string]any{"replicas": int64(2)},
		"status":     map[string]any{"readyReplicas": int64(1)},
	}}
	status, ready, desired := managedResourceStatus(object)
	if status != "Pending" || ready != 1 || desired != 2 {
		t.Fatalf("unexpected deployment status: %s %d/%d", status, ready, desired)
	}
	_ = unstructured.SetNestedField(object.Object, int64(2), "status", "readyReplicas")
	status, ready, desired = managedResourceStatus(object)
	if status != "Ready" || ready != 2 || desired != 2 {
		t.Fatalf("expected ready deployment status: %s %d/%d", status, ready, desired)
	}
}
