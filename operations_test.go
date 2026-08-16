package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDimensionsSortByValueThenLabel(t *testing.T) {
	items := dimensions(map[string]int{"training": 2, "inference": 4, "agent": 2})
	if len(items) != 3 || items[0].Label != "inference" || items[1].Label != "agent" || items[2].Label != "training" {
		t.Fatalf("unexpected metric dimensions: %#v", items)
	}
}

func TestWorkloadActiveRecognizesInFlightStates(t *testing.T) {
	for _, status := range []string{"running", "Ready", "pending", "queued"} {
		if !workloadActive(status) {
			t.Fatalf("expected %s to be active", status)
		}
	}
	for _, status := range []string{"failed", "stopped", "succeeded"} {
		if workloadActive(status) {
			t.Fatalf("expected %s not to be active", status)
		}
	}
}

func TestOperationsEndpointReadyUsesHealthStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ready" {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
	}))
	defer server.Close()
	if !operationsEndpointReady(context.Background(), server.URL+"/ready", true) {
		t.Fatal("expected ready endpoint to pass")
	}
	if operationsEndpointReady(context.Background(), server.URL+"/down", true) {
		t.Fatal("expected unavailable endpoint to fail")
	}
}

func TestMetricsExposeMLOpsDimensions(t *testing.T) {
	t.Setenv("AUTH_MODE", "")
	t.Setenv("METRICS_TOKEN", "")
	state.Lock()
	previous := append([]Workload(nil), state.Workloads...)
	state.Workloads = []Workload{{ID: "one", Kind: "inference", Runtime: "kubernetes", ModelRuntime: "vllm", Status: "running", Restarts: 2, SchedulerBackend: "volcano", GangMinAvailable: 2, DesiredCount: 2, Accelerators: 2}}
	state.Unlock()
	t.Cleanup(func() {
		state.Lock()
		state.Workloads = previous
		state.Unlock()
	})
	recorder := httptest.NewRecorder()
	metricsHandler(recorder, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("metrics returned %d: %s", recorder.Code, recorder.Body.String())
	}
	for _, expected := range []string{`openmycelium_workloads{status="running"} 1`, `openmycelium_workload_kinds{kind="inference"} 1`, `openmycelium_workload_runtimes{runtime="vllm"} 1`, `openmycelium_workload_schedulers{backend="volcano"} 1`, `openmycelium_gang_workloads 1`, `openmycelium_parallel_workloads 1`, `openmycelium_multi_accelerator_workloads 1`, `openmycelium_workload_restarts 2`} {
		if !strings.Contains(recorder.Body.String(), expected) {
			t.Fatalf("metrics output missing %q:\n%s", expected, recorder.Body.String())
		}
	}
}

func TestPrometheusLabelEscapesTextFormatCharacters(t *testing.T) {
	got := prometheusLabel("line\npath\\\"name")
	if got != `line\npath\\\"name` {
		t.Fatalf("unexpected escaped label: %q", got)
	}
}
