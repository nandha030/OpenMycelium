package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

type metricDimension struct {
	Label string `json:"label"`
	Value int    `json:"value"`
}

type mlopsModelUsage struct {
	ID                string   `json:"id"`
	Name              string   `json:"name"`
	Versions          int      `json:"versions"`
	SizeBytes         int64    `json:"sizeBytes"`
	Runtimes          []string `json:"runtimes"`
	Deployments       int      `json:"deployments"`
	ActiveDeployments int      `json:"activeDeployments"`
}

type mlopsRelease struct {
	ID            string    `json:"id"`
	Name          string    `json:"name"`
	WorkspaceName string    `json:"workspaceName"`
	SourceType    string    `json:"sourceType"`
	Status        string    `json:"status"`
	CreatedAt     time.Time `json:"createdAt"`
}

type mlopsSummary struct {
	Summary        map[string]any    `json:"summary"`
	WorkloadStatus []metricDimension `json:"workloadStatus"`
	WorkloadKinds  []metricDimension `json:"workloadKinds"`
	Runtimes       []metricDimension `json:"runtimes"`
	Models         []mlopsModelUsage `json:"models"`
	Workloads      []Workload        `json:"workloads"`
	Releases       []mlopsRelease    `json:"releases"`
	Timestamp      time.Time         `json:"timestamp"`
}

type aiopsCluster struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Status       string    `json:"status"`
	Nodes        int       `json:"nodes"`
	ReadyNodes   int       `json:"readyNodes"`
	Accelerators int       `json:"accelerators"`
	UpdatedAt    time.Time `json:"updatedAt"`
}

type aiopsUserUsage struct {
	ID             string     `json:"id"`
	Identity       string     `json:"identity"`
	Role           string     `json:"role"`
	Active         bool       `json:"active"`
	ActiveSessions int        `json:"activeSessions"`
	Actions24h     int        `json:"actions24h"`
	LastActionAt   *time.Time `json:"lastActionAt,omitempty"`
}

type aiopsActivity struct {
	Subject   string    `json:"subject"`
	Actor     string    `json:"actor"`
	CreatedAt time.Time `json:"createdAt"`
}

type aiopsAlert struct {
	Severity string `json:"severity"`
	Title    string `json:"title"`
	Detail   string `json:"detail"`
	Resource string `json:"resource"`
}

type aiopsSummary struct {
	Summary   map[string]any    `json:"summary"`
	Services  map[string]any    `json:"services"`
	Clusters  []aiopsCluster    `json:"clusters"`
	Users     []aiopsUserUsage  `json:"users"`
	Activity  []aiopsActivity   `json:"activity"`
	Alerts    []aiopsAlert      `json:"alerts"`
	Timestamp time.Time         `json:"timestamp"`
	Links     map[string]string `json:"links"`
}

func dimensions(values map[string]int) []metricDimension {
	items := make([]metricDimension, 0, len(values))
	for label, value := range values {
		items = append(items, metricDimension{Label: label, Value: value})
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Value == items[j].Value {
			return items[i].Label < items[j].Label
		}
		return items[i].Value > items[j].Value
	})
	return items
}

func workloadActive(status string) bool {
	switch strings.ToLower(status) {
	case "running", "ready", "pending", "queued":
		return true
	default:
		return false
	}
}

func collectMLOps(ctx context.Context) mlopsSummary {
	state.RLock()
	workloads := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	sort.Slice(workloads, func(i, j int) bool { return workloads[i].UpdatedAt.After(workloads[j].UpdatedAt) })
	statusCounts, kindCounts, runtimeCounts := map[string]int{}, map[string]int{}, map[string]int{}
	active, failed, restarts := 0, 0, int32(0)
	deploymentsByModel, activeByModel := map[string]int{}, map[string]int{}
	for _, item := range workloads {
		status := strings.ToLower(strings.TrimSpace(item.Status))
		if status == "" {
			status = "unknown"
		}
		kind := strings.ToLower(strings.TrimSpace(item.Kind))
		if kind == "" {
			kind = "unspecified"
		}
		runtime := strings.ToLower(strings.TrimSpace(item.ModelRuntime))
		if runtime == "" {
			runtime = strings.ToLower(strings.TrimSpace(item.Runtime))
		}
		if runtime == "" {
			runtime = "unknown"
		}
		statusCounts[status]++
		kindCounts[kind]++
		runtimeCounts[runtime]++
		restarts += item.Restarts
		if workloadActive(status) {
			active++
		}
		if status == "failed" {
			failed++
		}
		if item.Model != "" {
			key := strings.ToLower(item.Model)
			deploymentsByModel[key]++
			if workloadActive(status) {
				activeByModel[key]++
			}
		}
	}

	models := []mlopsModelUsage{}
	modelVersions, modelBytes, workspaceCount, releaseCount := 0, int64(0), 0, 0
	agentDefinitions, agentRuns, activeAgentRuns, agentEvaluations, pendingApprovals := 0, 0, 0, 0, 0
	if database != nil {
		rows, err := database.Query(ctx, `SELECT m.id,m.name,count(v.id),COALESCE(sum(v.size_bytes),0),COALESCE(string_agg(DISTINCT v.runtime,','),'') FROM model_artifacts m LEFT JOIN model_versions v ON v.model_id=m.id GROUP BY m.id,m.name ORDER BY m.name`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var item mlopsModelUsage
				var runtimes string
				if rows.Scan(&item.ID, &item.Name, &item.Versions, &item.SizeBytes, &runtimes) == nil {
					if runtimes != "" {
						item.Runtimes = strings.Split(runtimes, ",")
					}
					item.Deployments = deploymentsByModel[strings.ToLower(item.Name)]
					item.ActiveDeployments = activeByModel[strings.ToLower(item.Name)]
					modelVersions += item.Versions
					modelBytes += item.SizeBytes
					models = append(models, item)
				}
			}
		}
		_ = database.QueryRow(ctx, `SELECT count(*) FROM workspaces`).Scan(&workspaceCount)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM workspace_releases`).Scan(&releaseCount)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_definitions`).Scan(&agentDefinitions)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_runs`).Scan(&agentRuns)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_runs WHERE status IN ('accepted','deploying','running','waiting_approval')`).Scan(&activeAgentRuns)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_evaluations`).Scan(&agentEvaluations)
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_approvals WHERE status='pending'`).Scan(&pendingApprovals)
	}

	releases := []mlopsRelease{}
	if database != nil {
		rows, err := database.Query(ctx, `SELECT r.id,r.name,w.name,r.source_type,r.status,r.created_at FROM workspace_releases r JOIN workspaces w ON w.id=r.workspace_id ORDER BY r.created_at DESC LIMIT 50`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var item mlopsRelease
				if rows.Scan(&item.ID, &item.Name, &item.WorkspaceName, &item.SourceType, &item.Status, &item.CreatedAt) == nil {
					releases = append(releases, item)
				}
			}
		}
	}
	nonFailedPercent := 100.0
	if len(workloads) > 0 {
		nonFailedPercent = float64(len(workloads)-failed) / float64(len(workloads)) * 100
	}
	return mlopsSummary{
		Summary:        map[string]any{"workloads": len(workloads), "activeWorkloads": active, "failedWorkloads": failed, "workloadRestarts": restarts, "models": len(models), "modelVersions": modelVersions, "modelBytes": modelBytes, "workspaces": workspaceCount, "releases": releaseCount, "agents": agentDefinitions, "agentRuns": agentRuns, "activeAgentRuns": activeAgentRuns, "agentEvaluations": agentEvaluations, "pendingAgentApprovals": pendingApprovals, "nonFailedPercent": nonFailedPercent},
		WorkloadStatus: dimensions(statusCounts), WorkloadKinds: dimensions(kindCounts), Runtimes: dimensions(runtimeCounts), Models: models, Workloads: workloads, Releases: releases, Timestamp: time.Now().UTC(),
	}
}

func collectAIOps(ctx context.Context, revealIdentity bool) aiopsSummary {
	mlops := collectMLOps(ctx)
	clusters, users, activity, alerts := []aiopsCluster{}, []aiopsUserUsage{}, []aiopsActivity{}, []aiopsAlert{}
	totalNodes, readyNodes, activeUsers, activeSessions, actions24h := 0, 0, 0, 0, 0
	if database != nil {
		rows, err := database.Query(ctx, `SELECT id,name,status,nodes,ready_nodes,accelerators,updated_at FROM clusters ORDER BY name`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var item aiopsCluster
				if rows.Scan(&item.ID, &item.Name, &item.Status, &item.Nodes, &item.ReadyNodes, &item.Accelerators, &item.UpdatedAt) == nil {
					clusters = append(clusters, item)
					totalNodes += item.Nodes
					readyNodes += item.ReadyNodes
					if item.Status != "ready" || item.ReadyNodes < item.Nodes {
						alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "Cluster capacity degraded", Detail: item.Name + " reports fewer ready nodes than registered nodes.", Resource: "cluster/" + item.Name})
					}
				}
			}
		}
		rows, err = database.Query(ctx, `SELECT u.id,u.email,u.role,u.active,COALESCE((SELECT count(*) FROM sessions s WHERE s.user_id=u.id AND s.expires_at>now()),0),COALESCE((SELECT count(*) FROM audit_events a WHERE a.created_at>now()-interval '24 hours' AND (a.payload->>'actor'=u.email OR a.payload->>'user_id'=u.id)),0),(SELECT max(a.created_at) FROM audit_events a WHERE a.payload->>'actor'=u.email OR a.payload->>'user_id'=u.id) FROM users u ORDER BY u.email`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var item aiopsUserUsage
				var email string
				if rows.Scan(&item.ID, &email, &item.Role, &item.Active, &item.ActiveSessions, &item.Actions24h, &item.LastActionAt) == nil {
					if revealIdentity {
						item.Identity = email
					} else {
						item.Identity = "user-" + item.ID[:min(8, len(item.ID))]
					}
					if item.Active {
						activeUsers++
					}
					activeSessions += item.ActiveSessions
					actions24h += item.Actions24h
					users = append(users, item)
				}
			}
		}
		rows, err = database.Query(ctx, `SELECT subject,COALESCE(NULLIF(payload->>'actor',''),NULLIF(payload->>'email',''),'system'),created_at FROM audit_events ORDER BY created_at DESC LIMIT 40`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var item aiopsActivity
				if rows.Scan(&item.Subject, &item.Actor, &item.CreatedAt) == nil {
					if !revealIdentity && item.Actor != "system" {
						item.Actor = "authenticated user"
					}
					activity = append(activity, item)
				}
			}
		}
	}
	for _, workload := range mlops.Workloads {
		if strings.EqualFold(workload.Status, "failed") {
			detail := workload.LastError
			if detail == "" {
				detail = workload.StatusMessage
			}
			if detail == "" {
				detail = "The workload controller reported a failed lifecycle state."
			}
			alerts = append(alerts, aiopsAlert{Severity: "critical", Title: "Workload failed", Detail: detail, Resource: "workload/" + workload.Name})
		} else if workload.Restarts >= 3 {
			alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "Repeated container restarts", Detail: workload.Name + " has restarted at least three times.", Resource: "workload/" + workload.Name})
		}
	}
	if database != nil {
		var pendingApprovals int
		_ = database.QueryRow(ctx, `SELECT count(*) FROM agent_approvals WHERE status='pending'`).Scan(&pendingApprovals)
		if pendingApprovals > 0 {
			alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "Agent approvals waiting", Detail: fmt.Sprintf("%d agent run approval request(s) require an operator decision.", pendingApprovals), Resource: "agent/approvals"})
		}
		rows, err := database.Query(ctx, `SELECT id,error FROM agent_runs WHERE status='failed' ORDER BY updated_at DESC LIMIT 10`)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var runID, detail string
				if rows.Scan(&runID, &detail) == nil {
					if detail == "" {
						detail = "The agent runtime or Kubernetes scheduler reported a failure."
					}
					alerts = append(alerts, aiopsAlert{Severity: "critical", Title: "Agent run failed", Detail: detail, Resource: "agent-run/" + runID})
				}
			}
		}
	}
	postgresReady := database != nil
	natsReady := eventBus != nil && eventBus.IsConnected()
	prometheusInternal := strings.TrimRight(strings.TrimSpace(os.Getenv("PROMETHEUS_INTERNAL_URL")), "/")
	grafanaInternal := strings.TrimRight(strings.TrimSpace(os.Getenv("GRAFANA_INTERNAL_URL")), "/")
	prometheusReady := operationsEndpointReady(ctx, prometheusInternal+"/-/ready", prometheusInternal != "")
	grafanaReady := operationsEndpointReady(ctx, grafanaInternal+"/api/health", grafanaInternal != "")
	if !postgresReady {
		alerts = append(alerts, aiopsAlert{Severity: "critical", Title: "PostgreSQL unavailable", Detail: "Persistent governance and authentication are unavailable.", Resource: "service/postgres"})
	}
	if !natsReady {
		alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "NATS unavailable", Detail: "Control-plane lifecycle events are not being published.", Resource: "service/nats"})
	}
	if prometheusInternal != "" && !prometheusReady {
		alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "Prometheus unavailable", Detail: "The provisioned metrics collector did not pass its readiness probe.", Resource: "service/prometheus"})
	}
	if grafanaInternal != "" && !grafanaReady {
		alerts = append(alerts, aiopsAlert{Severity: "warning", Title: "Grafana unavailable", Detail: "The provisioned visualization service did not pass its health probe.", Resource: "service/grafana"})
	}
	return aiopsSummary{
		Summary:  map[string]any{"alerts": len(alerts), "clusters": len(clusters), "totalNodes": totalNodes, "readyNodes": readyNodes, "workloadRestarts": mlops.Summary["workloadRestarts"], "activeUsers": activeUsers, "activeSessions": activeSessions, "actions24h": actions24h},
		Services: map[string]any{"api": true, "postgres": postgresReady, "nats": natsReady, "prometheus": prometheusReady, "grafana": grafanaReady, "ollama": ollamaBaseURL()}, Clusters: clusters, Users: users, Activity: activity, Alerts: alerts, Timestamp: time.Now().UTC(),
		Links: map[string]string{"prometheus": envOrDefault("PROMETHEUS_PUBLIC_URL", "http://127.0.0.1:9090"), "grafana": envOrDefault("GRAFANA_PUBLIC_URL", "http://127.0.0.1:3000")},
	}
}

func operationsEndpointReady(ctx context.Context, endpoint string, configured bool) bool {
	if !configured {
		return false
	}
	requestContext, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(requestContext, http.MethodGet, endpoint, nil)
	if err != nil {
		return false
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return false
	}
	defer response.Body.Close()
	return response.StatusCode >= 200 && response.StatusCode < 300
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func mlopsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	writeJSON(w, http.StatusOK, collectMLOps(r.Context()))
}

func aiopsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	user, _ := currentUser(r)
	writeJSON(w, http.StatusOK, collectAIOps(r.Context(), user.Role == "operator" || user.Role == "platform_admin"))
}
