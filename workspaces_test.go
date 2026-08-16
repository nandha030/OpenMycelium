package main

import (
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	batchv1 "k8s.io/api/batch/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestWorkspaceControllersReconcileReadinessAndRelease(t *testing.T) {
	desired := int32(2)
	deployments := &appsv1.DeploymentList{Items: []appsv1.Deployment{{
		ObjectMeta: metav1.ObjectMeta{Name: "model-api", Labels: map[string]string{releaseLabel: "release-1"}},
		Spec:       appsv1.DeploymentSpec{Replicas: &desired},
		Status:     appsv1.DeploymentStatus{ReadyReplicas: 2},
	}}}
	items := workspaceControllers(deployments, &appsv1.StatefulSetList{}, &appsv1.DaemonSetList{}, &batchv1.JobList{}, &batchv1.CronJobList{})
	if len(items) != 1 {
		t.Fatalf("expected one controller, got %#v", items)
	}
	if items[0].Status != "Ready" || items[0].Ready != 2 || items[0].ReleaseID != "release-1" {
		t.Fatalf("unexpected reconciled controller: %#v", items[0])
	}
}
