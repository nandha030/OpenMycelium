package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func resetWorkloadState(t *testing.T, workloads []Workload) {
	t.Helper()
	t.Setenv("STATE_PATH", t.TempDir()+"/state.json")
	state.Lock()
	previous := append([]Workload(nil), state.Workloads...)
	state.Workloads = append([]Workload(nil), workloads...)
	state.Unlock()
	t.Cleanup(func() {
		state.Lock()
		state.Workloads = previous
		state.Unlock()
	})
}

func TestWorkloadSSHCommand(t *testing.T) {
	resetWorkloadState(t, []Workload{{ID: "job-1", Name: "trainer", Status: "running", SSHHost: "worker.example.com", SSHPort: 2222, SSHUser: "ubuntu"}})
	request := httptest.NewRequest(http.MethodGet, "/api/v1/workloads/job-1/ssh", nil)
	response := httptest.NewRecorder()

	workloadActionHandler(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", response.Code, response.Body.String())
	}
	var payload struct {
		Command string `json:"command"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Command != "ssh -p 2222 ubuntu@worker.example.com" {
		t.Fatalf("unexpected SSH command %q", payload.Command)
	}
}

func TestWorkloadDelete(t *testing.T) {
	resetWorkloadState(t, []Workload{{ID: "job-1", Name: "trainer", Status: "stopped"}})
	request := httptest.NewRequest(http.MethodDelete, "/api/v1/workloads/job-1", nil)
	response := httptest.NewRecorder()

	workloadActionHandler(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", response.Code, response.Body.String())
	}
	state.RLock()
	defer state.RUnlock()
	if len(state.Workloads) != 0 {
		t.Fatalf("expected workload to be deleted, found %d", len(state.Workloads))
	}
}

func TestConfigureWorkloadSSH(t *testing.T) {
	resetWorkloadState(t, []Workload{{ID: "job-1", Name: "trainer", Status: "queued"}})
	body := `{"host":"worker.example.com","port":22,"user":"ubuntu"}`
	request := httptest.NewRequest(http.MethodPatch, "/api/v1/workloads/job-1/ssh", strings.NewReader(body))
	response := httptest.NewRecorder()

	workloadActionHandler(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", response.Code, response.Body.String())
	}
	state.RLock()
	defer state.RUnlock()
	if state.Workloads[0].SSHHost != "worker.example.com" || state.Workloads[0].SSHUser != "ubuntu" {
		t.Fatalf("SSH coordinates were not persisted: %+v", state.Workloads[0])
	}
}

func TestWorkloadRejectsUnsafeSSHCoordinates(t *testing.T) {
	resetWorkloadState(t, nil)
	body := `{"name":"trainer","kind":"training","sshHost":"worker.example.com;shutdown","sshPort":22,"sshUser":"ubuntu"}`
	request := httptest.NewRequest(http.MethodPost, "/api/v1/workloads", strings.NewReader(body))
	response := httptest.NewRecorder()

	workloadsHandler(response, request)

	if response.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d: %s", response.Code, response.Body.String())
	}
}
