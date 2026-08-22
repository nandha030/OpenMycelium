package main

import (
	"testing"
)

func TestDefaultAgentSpec(t *testing.T) {
	spec := defaultAgentSpec(AgentSpec{})
	if spec.CPU != "1" || spec.MemoryRequest != "2Gi" || spec.Pool != "cpu-local" {
		t.Fatalf("unexpected runtime defaults: %#v", spec)
	}
	if spec.Policy.TokenBudget != 100000 || spec.Policy.TimeoutSeconds != 900 || spec.Policy.MaxRetries != 2 {
		t.Fatalf("unexpected policy defaults: %#v", spec.Policy)
	}
	if spec.Memory.Mode != "run" || spec.Memory.RetentionDays != 30 {
		t.Fatalf("unexpected memory defaults: %#v", spec.Memory)
	}
}

func TestValidateAgentFlow(t *testing.T) {
	valid := AgentFlowGraph{
		Nodes: []AgentFlowNode{{ID: "input", Type: "input"}, {ID: "agent", Type: "agent"}, {ID: "approval", Type: "approval"}, {ID: "output", Type: "output"}},
		Edges: []AgentFlowEdge{{From: "input", To: "agent"}, {From: "agent", To: "approval"}, {From: "approval", To: "output", Condition: "approved"}},
	}
	if err := validateAgentFlow(valid); err != nil {
		t.Fatalf("valid flow rejected: %v", err)
	}
	invalid := valid
	invalid.Edges = append(invalid.Edges, AgentFlowEdge{From: "missing", To: "output"})
	if err := validateAgentFlow(invalid); err == nil {
		t.Fatal("flow edge referencing a missing node was accepted")
	}
}

func TestAgentPodUsesIsolatedServiceAccountAndRuntimeContract(t *testing.T) {
	workload := Workload{
		ID:             "job-agent",
		AgentID:        "agt_test",
		AgentRunID:     "run_test",
		AgentSpecJSON:  `{"policy":{"tokenBudget":1000}}`,
		AgentFlowJSON:  `{"nodes":[]}`,
		AgentToolsJSON: `[]`,
		WorkspaceID:    "ws_test",
		ResourceName:   "agent-runtime",
		Image:          "example.invalid/agent:1",
		Kind:           "agent",
		CPU:            "1",
		Memory:         "1Gi",
		Port:           8000,
	}
	template, err := workloadPodTemplate(workload, ManagedPool{Name: "cpu", Vendor: "CPU", Runtime: "cpu", Enabled: true}, map[string]string{})
	if err != nil {
		t.Fatalf("build pod template: %v", err)
	}
	if template.Spec.ServiceAccountName != workload.ResourceName {
		t.Fatalf("agent service account = %q, want %q", template.Spec.ServiceAccountName, workload.ResourceName)
	}
	if template.Spec.AutomountServiceAccountToken == nil || *template.Spec.AutomountServiceAccountToken {
		t.Fatal("agent pod must not automatically mount a Kubernetes API token")
	}
	environment := map[string]string{}
	for _, item := range template.Spec.Containers[0].Env {
		environment[item.Name] = item.Value
	}
	for _, name := range []string{"OPENMYCELIUM_AGENT_ID", "OPENMYCELIUM_AGENT_RUN_ID", "OPENMYCELIUM_AGENT_SPEC_JSON", "OPENMYCELIUM_AGENT_FLOW_JSON", "OPENMYCELIUM_AGENT_TOOLS_JSON"} {
		if environment[name] == "" {
			t.Fatalf("runtime contract is missing %s", name)
		}
	}
}
