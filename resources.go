package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"golang.org/x/crypto/bcrypt"
)

type Cluster struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Endpoint     string    `json:"endpoint"`
	Type         string    `json:"type"`
	Status       string    `json:"status"`
	Nodes        int       `json:"nodes"`
	Accelerators int       `json:"accelerators"`
	CreatedAt    time.Time `json:"createdAt"`
	UpdatedAt    time.Time `json:"updatedAt"`
}

type ManagedPool struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Vendor    string    `json:"vendor"`
	Runtime   string    `json:"runtime"`
	Policy    string    `json:"policy"`
	Selector  string    `json:"selector"`
	Enabled   bool      `json:"enabled"`
	CreatedAt time.Time `json:"createdAt"`
}

type Queue struct {
	ID                string    `json:"id"`
	Name              string    `json:"name"`
	Priority          int       `json:"priority"`
	AcceleratorQuota  int       `json:"acceleratorQuota"`
	MemoryQuotaGB     int       `json:"memoryQuotaGB"`
	PreemptionEnabled bool      `json:"preemptionEnabled"`
	CreatedAt         time.Time `json:"createdAt"`
}

type Integration struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Type      string    `json:"type"`
	Target    string    `json:"target"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"createdAt"`
}

var resourceSchema = []string{
	`CREATE TABLE IF NOT EXISTS clusters (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, endpoint TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL, nodes INTEGER NOT NULL DEFAULT 0, accelerators INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS accelerator_pools (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, vendor TEXT NOT NULL, runtime TEXT NOT NULL, policy TEXT NOT NULL, selector TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS queues (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, priority INTEGER NOT NULL, accelerator_quota INTEGER NOT NULL, memory_quota_gb INTEGER NOT NULL, preemption_enabled BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS integrations (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, type TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'configured', created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
}

func requireDatabase(w http.ResponseWriter) bool {
	if database == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "PostgreSQL is required for this resource"})
		return false
	}
	return true
}

func requestActor(r *http.Request) string {
	if user, ok := currentUser(r); ok {
		return user.Email
	}
	return "system"
}

func auditRequest(r *http.Request, subject string, payload map[string]any) {
	payload["actor"] = requestActor(r)
	audit(subject, payload)
}

func clustersHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,endpoint,type,status,nodes,accelerators,created_at,updated_at FROM clusters ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list clusters"})
			return
		}
		defer rows.Close()
		items := []Cluster{}
		for rows.Next() {
			var item Cluster
			if rows.Scan(&item.ID, &item.Name, &item.Endpoint, &item.Type, &item.Status, &item.Nodes, &item.Accelerators, &item.CreatedAt, &item.UpdatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"clusters": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct {
		Name     string `json:"name"`
		Endpoint string `json:"endpoint"`
		Type     string `json:"type"`
	}
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.Endpoint) == "" {
		writeJSON(w, 400, map[string]string{"error": "name and endpoint are required"})
		return
	}
	if input.Type == "" {
		input.Type = "kubernetes"
	}
	item := Cluster{ID: "clu_" + randomToken(9), Name: strings.TrimSpace(input.Name), Endpoint: strings.TrimSpace(input.Endpoint), Type: input.Type, Status: "pending", CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	_, err := database.Exec(r.Context(), `INSERT INTO clusters(id,name,endpoint,type,status,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, item.ID, item.Name, item.Endpoint, item.Type, item.Status, item.CreatedAt, item.UpdatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a cluster with that name already exists"})
		return
	}
	auditRequest(r, "cluster.created", map[string]any{"cluster_id": item.ID, "name": item.Name})
	publishEvent("cluster.created", item)
	writeJSON(w, 201, item)
}

func clusterResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/clusters/")
	if id == "" {
		writeJSON(w, 404, map[string]string{"error": "cluster not found"})
		return
	}
	if r.Method == http.MethodDelete {
		result, err := database.Exec(r.Context(), `DELETE FROM clusters WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete cluster"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "cluster not found"})
			return
		}
		auditRequest(r, "cluster.deleted", map[string]any{"cluster_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func managedPoolsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,vendor,runtime,policy,selector,enabled,created_at FROM accelerator_pools ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list pools"})
			return
		}
		defer rows.Close()
		items := []ManagedPool{}
		for rows.Next() {
			var item ManagedPool
			if rows.Scan(&item.ID, &item.Name, &item.Vendor, &item.Runtime, &item.Policy, &item.Selector, &item.Enabled, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"pools": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input ManagedPool
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.Runtime) == "" {
		writeJSON(w, 400, map[string]string{"error": "name and runtime are required"})
		return
	}
	if input.Vendor == "" {
		input.Vendor = "mixed"
	}
	if input.Policy == "" {
		input.Policy = "compatible-runtime"
	}
	input.ID = "pool_" + randomToken(9)
	input.Enabled = true
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO accelerator_pools(id,name,vendor,runtime,policy,selector,enabled,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, input.ID, input.Name, input.Vendor, input.Runtime, input.Policy, input.Selector, input.Enabled, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a pool with that name already exists"})
		return
	}
	auditRequest(r, "pool.created", map[string]any{"pool_id": input.ID, "name": input.Name})
	publishEvent("pool.created", input)
	writeJSON(w, 201, input)
}

func poolResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/pools/")
	if r.Method == http.MethodDelete {
		result, err := database.Exec(r.Context(), `DELETE FROM accelerator_pools WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete pool"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "pool not found"})
			return
		}
		auditRequest(r, "pool.deleted", map[string]any{"pool_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func queuesHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,priority,accelerator_quota,memory_quota_gb,preemption_enabled,created_at FROM queues ORDER BY priority DESC,name`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list queues"})
			return
		}
		defer rows.Close()
		items := []Queue{}
		for rows.Next() {
			var item Queue
			if rows.Scan(&item.ID, &item.Name, &item.Priority, &item.AcceleratorQuota, &item.MemoryQuotaGB, &item.PreemptionEnabled, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"queues": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input Queue
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || input.Priority < 0 || input.Priority > 100 || input.AcceleratorQuota < 0 || input.MemoryQuotaGB < 0 {
		writeJSON(w, 400, map[string]string{"error": "valid name, priority (0-100), and non-negative quotas are required"})
		return
	}
	input.ID = "que_" + randomToken(9)
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO queues(id,name,priority,accelerator_quota,memory_quota_gb,preemption_enabled,created_at) VALUES($1,$2,$3,$4,$5,$6,$7)`, input.ID, input.Name, input.Priority, input.AcceleratorQuota, input.MemoryQuotaGB, input.PreemptionEnabled, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a queue with that name already exists"})
		return
	}
	auditRequest(r, "queue.created", map[string]any{"queue_id": input.ID, "name": input.Name})
	publishEvent("queue.created", input)
	writeJSON(w, 201, input)
}

func queueResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/queues/")
	if r.Method == http.MethodDelete {
		result, err := database.Exec(r.Context(), `DELETE FROM queues WHERE id=$1`, id)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete queue"})
			return
		}
		if result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "queue not found"})
			return
		}
		auditRequest(r, "queue.deleted", map[string]any{"queue_id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func integrationsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,type,target,status,created_at FROM integrations ORDER BY created_at DESC`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list integrations"})
			return
		}
		defer rows.Close()
		items := []Integration{}
		for rows.Next() {
			var item Integration
			if rows.Scan(&item.ID, &item.Name, &item.Type, &item.Target, &item.Status, &item.CreatedAt) == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"integrations": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input Integration
	if decode(r, &input) != nil || strings.TrimSpace(input.Name) == "" || strings.TrimSpace(input.Target) == "" {
		writeJSON(w, 400, map[string]string{"error": "name and target are required"})
		return
	}
	if input.Type == "" {
		input.Type = "mcp"
	}
	input.ID = "int_" + randomToken(9)
	input.Status = "configured"
	input.CreatedAt = time.Now().UTC()
	_, err := database.Exec(r.Context(), `INSERT INTO integrations(id,name,type,target,status,created_at) VALUES($1,$2,$3,$4,$5,$6)`, input.ID, input.Name, input.Type, input.Target, input.Status, input.CreatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "an integration with that name already exists"})
		return
	}
	auditRequest(r, "integration.created", map[string]any{"integration_id": input.ID, "name": input.Name})
	writeJSON(w, 201, input)
}

func integrationResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/integrations/")
	if r.Method != http.MethodDelete {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	result, err := database.Exec(r.Context(), `DELETE FROM integrations WHERE id=$1`, id)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot delete integration"})
		return
	}
	if result.RowsAffected() == 0 {
		writeJSON(w, 404, map[string]string{"error": "integration not found"})
		return
	}
	auditRequest(r, "integration.deleted", map[string]any{"integration_id": id})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func usersHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodPost {
		var input struct {
			Email    string `json:"email"`
			Password string `json:"password"`
			Role     string `json:"role"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid request"})
			return
		}
		input.Email = strings.ToLower(strings.TrimSpace(input.Email))
		input.Role = strings.TrimSpace(input.Role)
		allowed := map[string]bool{"platform_admin": true, "operator": true, "viewer": true}
		if !strings.Contains(input.Email, "@") {
			writeJSON(w, 400, map[string]string{"error": "a valid email address is required"})
			return
		}
		if len(input.Password) < 12 {
			writeJSON(w, 400, map[string]string{"error": "password must contain at least 12 characters"})
			return
		}
		if !allowed[input.Role] {
			writeJSON(w, 400, map[string]string{"error": "supported roles: platform_admin, operator, viewer"})
			return
		}
		hash, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot secure account password"})
			return
		}
		item := map[string]any{
			"id":        "usr_" + randomToken(12),
			"email":     input.Email,
			"role":      input.Role,
			"active":    true,
			"createdAt": time.Now().UTC(),
		}
		_, err = database.Exec(r.Context(), `INSERT INTO users(id,email,password_hash,role,active,created_at) VALUES($1,$2,$3,$4,true,$5)`, item["id"], item["email"], string(hash), item["role"], item["createdAt"])
		if err != nil {
			writeJSON(w, 409, map[string]string{"error": "an account with that email address already exists"})
			return
		}
		auditRequest(r, "user.created", map[string]any{"user_id": item["id"], "email": input.Email, "role": input.Role})
		publishEvent("user.created", item)
		writeJSON(w, 201, item)
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	rows, err := database.Query(r.Context(), `SELECT id,email,role,active,created_at FROM users ORDER BY created_at`)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot list users"})
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id, email, role string
		var active bool
		var created time.Time
		if rows.Scan(&id, &email, &role, &active, &created) == nil {
			items = append(items, map[string]any{"id": id, "email": email, "role": role, "active": active, "createdAt": created})
		}
	}
	writeJSON(w, 200, map[string]any{"users": items})
}

func userResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodPatch && r.Method != http.MethodDelete {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/users/")
	if id == "" || strings.Contains(id, "/") {
		writeJSON(w, 404, map[string]string{"error": "user not found"})
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	var targetRole string
	var targetActive bool
	if err := database.QueryRow(ctx, `SELECT role,active FROM users WHERE id=$1`, id).Scan(&targetRole, &targetActive); err != nil {
		writeJSON(w, 404, map[string]string{"error": "user not found"})
		return
	}
	actor, _ := currentUser(r)
	if r.Method == http.MethodDelete {
		if actor.ID == id {
			writeJSON(w, 409, map[string]string{"error": "you cannot delete your own active administrator account"})
			return
		}
		if targetRole == "platform_admin" && targetActive {
			var administrators int
			if err := database.QueryRow(ctx, `SELECT count(*) FROM users WHERE role='platform_admin' AND active=true`).Scan(&administrators); err != nil || administrators <= 1 {
				writeJSON(w, 409, map[string]string{"error": "at least one active platform administrator is required"})
				return
			}
		}
		if _, err := database.Exec(ctx, `DELETE FROM users WHERE id=$1`, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot delete account"})
			return
		}
		auditRequest(r, "user.deleted", map[string]any{"user_id": id, "role": targetRole})
		publishEvent("user.deleted", map[string]string{"id": id})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	var input struct {
		Role   string `json:"role"`
		Active *bool  `json:"active"`
	}
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid request"})
		return
	}
	allowed := map[string]bool{"platform_admin": true, "operator": true, "viewer": true}
	if input.Role != "" && !allowed[input.Role] {
		writeJSON(w, 400, map[string]string{"error": "supported roles: platform_admin, operator, viewer"})
		return
	}
	disabling := input.Active != nil && !*input.Active
	demoting := input.Role != "" && input.Role != "platform_admin"
	if actor.ID == id && (disabling || demoting) {
		writeJSON(w, 409, map[string]string{"error": "you cannot disable or demote your own active administrator account"})
		return
	}
	if targetRole == "platform_admin" && targetActive && (disabling || demoting) {
		var administrators int
		if err := database.QueryRow(ctx, `SELECT count(*) FROM users WHERE role='platform_admin' AND active=true`).Scan(&administrators); err != nil || administrators <= 1 {
			writeJSON(w, 409, map[string]string{"error": "at least one active platform administrator is required"})
			return
		}
	}
	if input.Role != "" {
		if _, err := database.Exec(ctx, `UPDATE users SET role=$1 WHERE id=$2`, input.Role, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot update role"})
			return
		}
	}
	if input.Active != nil {
		if _, err := database.Exec(ctx, `UPDATE users SET active=$1 WHERE id=$2`, *input.Active, id); err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot update account"})
			return
		}
	}
	auditRequest(r, "user.updated", map[string]any{"user_id": id, "role": input.Role})
	writeJSON(w, 200, map[string]bool{"ok": true})
}

func auditEventsHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	rows, err := database.Query(r.Context(), `SELECT id,subject,payload,created_at FROM audit_events ORDER BY created_at DESC LIMIT 100`)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot list audit events"})
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id int64
		var subject string
		var payload []byte
		var created time.Time
		if rows.Scan(&id, &subject, &payload, &created) == nil {
			var body any
			_ = json.Unmarshal(payload, &body)
			items = append(items, map[string]any{"id": id, "subject": subject, "payload": body, "createdAt": created})
		}
	}
	writeJSON(w, 200, map[string]any{"events": items})
}

func observabilityHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	state.RLock()
	host := state.Host
	workloads := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	running, queued, failed := 0, 0, 0
	for _, item := range workloads {
		switch item.Status {
		case "running":
			running++
		case "queued":
			queued++
		case "failed":
			failed++
		}
	}
	counts := map[string]int{"clusters": 0, "pools": 0, "queues": 0, "users": 0}
	if database != nil {
		for table := range counts {
			queryTable := table
			if table == "pools" {
				queryTable = "accelerator_pools"
			}
			var count int
			if err := database.QueryRow(r.Context(), `SELECT count(*) FROM `+queryTable).Scan(&count); err == nil {
				counts[table] = count
			}
		}
	}
	natsReady := eventBus != nil && eventBus.IsConnected()
	writeJSON(w, 200, map[string]any{"services": map[string]any{"api": "healthy", "postgres": database != nil, "nats": natsReady, "ollama": ollamaBaseURL()}, "inventory": map[string]any{"host": host.Name, "accelerators": len(host.Accelerators), "clusters": counts["clusters"], "pools": counts["pools"]}, "workloads": map[string]int{"total": len(workloads), "running": running, "queued": queued, "failed": failed}, "governance": map[string]int{"users": counts["users"], "queues": counts["queues"]}, "timestamp": time.Now().UTC()})
}

func metricsHandler(w http.ResponseWriter, r *http.Request) {
	token := os.Getenv("METRICS_TOKEN")
	if token != "" {
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if len(provided) != len(token) || subtle.ConstantTimeCompare([]byte(provided), []byte(token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "valid metrics bearer token required"})
			return
		}
	} else if _, ok := currentUser(r); !ok {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "authentication required"})
		return
	}

	state.RLock()
	host := state.Host
	workloads := append([]Workload(nil), state.Workloads...)
	state.RUnlock()
	statuses := map[string]int{"running": 0, "queued": 0, "stopped": 0, "failed": 0}
	for _, workload := range workloads {
		statuses[workload.Status]++
	}

	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintln(w, "# HELP openmycelium_up Whether the control plane is serving requests.")
	fmt.Fprintln(w, "# TYPE openmycelium_up gauge")
	fmt.Fprintln(w, "openmycelium_up 1")
	fmt.Fprintln(w, "# HELP openmycelium_accelerators Number of accelerators reported by the host agent.")
	fmt.Fprintln(w, "# TYPE openmycelium_accelerators gauge")
	fmt.Fprintf(w, "openmycelium_accelerators %d\n", len(host.Accelerators))
	fmt.Fprintln(w, "# HELP openmycelium_workloads Workloads by lifecycle status.")
	fmt.Fprintln(w, "# TYPE openmycelium_workloads gauge")
	for _, status := range []string{"running", "queued", "stopped", "failed"} {
		fmt.Fprintf(w, "openmycelium_workloads{status=%q} %d\n", status, statuses[status])
	}
}
