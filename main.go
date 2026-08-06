// OpenMycelium Developer Edition control plane.
package main

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
)

//go:embed index.html login.html app.js styles.css premium.css
var ui embed.FS

type Accelerator struct {
	ID        string `json:"id"`
	Vendor    string `json:"vendor"`
	Model     string `json:"model"`
	Runtime   string `json:"runtime"`
	Memory    string `json:"memory"`
	Health    string `json:"health"`
	Simulated bool   `json:"simulated"`
}
type HostProfile struct {
	Name         string        `json:"name"`
	OS           string        `json:"os"`
	CPU          string        `json:"cpu"`
	LogicalCores int           `json:"logicalCores"`
	MemoryGB     float64       `json:"memoryGB"`
	Accelerators []Accelerator `json:"accelerators"`
	ReportedAt   time.Time     `json:"reportedAt"`
}
type Pool struct {
	ID, Name, Policy string
	Members          []Accelerator `json:"members"`
}
type Workload struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Kind         string    `json:"kind"`
	Pool         string    `json:"pool"`
	Image        string    `json:"image"`
	Accelerators int       `json:"accelerators"`
	Status       string    `json:"status"`
	Checkpoint   string    `json:"checkpoint"`
	SSHHost      string    `json:"sshHost,omitempty"`
	SSHPort      int       `json:"sshPort,omitempty"`
	SSHUser      string    `json:"sshUser,omitempty"`
	CreatedAt    time.Time `json:"createdAt"`
}

var sshNamePattern = regexp.MustCompile(`^[a-zA-Z0-9._-]+$`)
var sshHostPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9.:-]*$`)

type Workflow struct {
	ID, Name, Status string
	Stages           []WorkflowStage `json:"stages"`
}
type WorkflowStage struct {
	Name, Kind, Pool, DependsOn string `json:"dependsOn"`
}
type OllamaModel struct {
	Name       string    `json:"name"`
	Model      string    `json:"model"`
	Size       int64     `json:"size"`
	ModifiedAt time.Time `json:"modified_at"`
}
type PlatformSettings struct {
	Organization      string `json:"organization"`
	DefaultQueue      string `json:"defaultQueue"`
	OIDCIssuer        string `json:"oidcIssuer"`
	TelemetryEndpoint string `json:"telemetryEndpoint"`
	RegistrationOpen  bool   `json:"registrationOpen"`
}
type State struct {
	sync.RWMutex
	Accelerators []Accelerator
	Host         HostProfile
	Settings     PlatformSettings
	Pools        []Pool
	Workloads    []Workload
	Workflows    []Workflow
}

var state = State{Settings: PlatformSettings{Organization: "OpenMycelium", DefaultQueue: "default", RegistrationOpen: false}}
var database *pgxpool.Pool
var eventBus *nats.Conn

type persistedState struct {
	Host      HostProfile      `json:"host"`
	Settings  PlatformSettings `json:"settings"`
	Workloads []Workload       `json:"workloads"`
}

func persistencePath() string {
	if value := os.Getenv("STATE_PATH"); value != "" {
		return value
	}
	return ".openmycelium-state.json"
}
func persistStateLocked() {
	saved := persistedState{Host: state.Host, Settings: state.Settings, Workloads: state.Workloads}
	snapshot, _ := json.Marshal(saved)
	path := persistencePath()
	_ = os.MkdirAll(filepath.Dir(path), 0755)
	_ = os.WriteFile(path, snapshot, 0600)
	syncDatabase(saved)
}
func restoreState() {
	data, err := os.ReadFile(persistencePath())
	if err != nil {
		return
	}
	var saved persistedState
	if json.Unmarshal(data, &saved) == nil {
		state.Host = saved.Host
		state.Settings = saved.Settings
		state.Workloads = saved.Workloads
	}
}

var schema = []string{`CREATE TABLE IF NOT EXISTS hosts (name TEXT PRIMARY KEY, profile JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS workloads (id TEXT PRIMARY KEY, workload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS platform_settings (id BOOLEAN PRIMARY KEY DEFAULT true, settings JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`, `CREATE TABLE IF NOT EXISTS audit_events (id BIGSERIAL PRIMARY KEY, subject TEXT NOT NULL, payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`}

func publishEvent(subject string, payload any) {
	if eventBus == nil {
		return
	}
	data, err := json.Marshal(payload)
	if err == nil {
		_ = eventBus.Publish("openmycelium."+subject, data)
	}
}
func syncDatabase(snapshot persistedState) {
	if database == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tx, err := database.Begin(ctx)
	if err != nil {
		return
	}
	defer tx.Rollback(ctx)
	if snapshot.Host.Name != "" {
		profile, _ := json.Marshal(snapshot.Host)
		if _, err = tx.Exec(ctx, "INSERT INTO hosts(name,profile,updated_at) VALUES($1,$2,now()) ON CONFLICT(name) DO UPDATE SET profile=excluded.profile,updated_at=now()", snapshot.Host.Name, profile); err != nil {
			return
		}
	}
	settings, _ := json.Marshal(snapshot.Settings)
	if _, err = tx.Exec(ctx, "INSERT INTO platform_settings(id,settings,updated_at) VALUES(true,$1,now()) ON CONFLICT(id) DO UPDATE SET settings=excluded.settings,updated_at=now()", settings); err != nil {
		return
	}
	if _, err = tx.Exec(ctx, "DELETE FROM workloads"); err != nil {
		return
	}
	for _, workload := range snapshot.Workloads {
		value, _ := json.Marshal(workload)
		if _, err = tx.Exec(ctx, "INSERT INTO workloads(id,workload,updated_at) VALUES($1,$2,now())", workload.ID, value); err != nil {
			return
		}
	}
	_ = tx.Commit(ctx)
}
func restoreDatabase() {
	if database == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var profile []byte
	if err := database.QueryRow(ctx, "SELECT profile FROM hosts ORDER BY updated_at DESC LIMIT 1").Scan(&profile); err == nil {
		_ = json.Unmarshal(profile, &state.Host)
	}
	var settings []byte
	if err := database.QueryRow(ctx, "SELECT settings FROM platform_settings WHERE id=true").Scan(&settings); err == nil {
		_ = json.Unmarshal(settings, &state.Settings)
	}
	rows, err := database.Query(ctx, "SELECT workload FROM workloads ORDER BY updated_at")
	if err != nil {
		return
	}
	defer rows.Close()
	restored := []Workload{}
	for rows.Next() {
		var value []byte
		if rows.Scan(&value) == nil {
			var workload Workload
			if json.Unmarshal(value, &workload) == nil {
				restored = append(restored, workload)
			}
		}
	}
	state.Workloads = restored
}
func initializeInfrastructure() {
	restoreState()
	if url := os.Getenv("DATABASE_URL"); url != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		pool, err := pgxpool.New(ctx, url)
		if err != nil {
			fmt.Printf("PostgreSQL unavailable: %v\n", err)
		} else {
			for _, statement := range append(schema, resourceSchema...) {
				_, err = pool.Exec(ctx, statement)
				if err != nil {
					break
				}
			}
			if err != nil {
				fmt.Printf("PostgreSQL migration failed: %v\n", err)
				pool.Close()
			} else {
				database = pool
				restoreDatabase()
				fmt.Println("PostgreSQL persistence ready")
			}
		}
	}
	if url := os.Getenv("NATS_URL"); url != "" {
		connection, err := nats.Connect(url, nats.Timeout(5*time.Second))
		if err != nil {
			fmt.Printf("NATS unavailable: %v\n", err)
		} else {
			eventBus = connection
			fmt.Println("NATS event bus ready")
		}
	}
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}
func decode(r *http.Request, value any) error {
	return json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(value)
}

func hostAccelerators() []Accelerator {
	found := []Accelerator{}
	if path, err := exec.LookPath("nvidia-smi"); err == nil {
		out, _ := exec.Command(path, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits").Output()
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.Split(line, ",")
			if len(parts) == 2 {
				found = append(found, Accelerator{ID: "nvidia-" + strings.ReplaceAll(strings.TrimSpace(parts[0]), " ", "-"), Vendor: "NVIDIA", Model: strings.TrimSpace(parts[0]), Runtime: "cuda", Memory: strings.TrimSpace(parts[1]) + " MiB", Health: "healthy"})
			}
		}
	}
	if path, err := exec.LookPath("rocm-smi"); err == nil {
		out, _ := exec.Command(path, "--showproductname").Output()
		for _, line := range strings.Split(string(out), "\n") {
			if strings.Contains(line, "Card series") {
				found = append(found, Accelerator{ID: "amd-" + fmt.Sprint(len(found)), Vendor: "AMD", Model: strings.TrimSpace(strings.Split(line, ":")[len(strings.Split(line, ":"))-1]), Runtime: "rocm", Memory: "reported by ROCm", Health: "healthy"})
			}
		}
	}
	if runtime.GOOS == "darwin" {
		found = append(found, Accelerator{ID: "apple-uma", Vendor: "Apple", Model: "Apple Silicon GPU / Neural Engine", Runtime: "metal,coreml", Memory: "shared unified memory", Health: "detected"})
	}
	return found
}
func scanHandler(w http.ResponseWriter, r *http.Request) {
	state.Lock()
	state.Accelerators = hostAccelerators()
	values := state.Accelerators
	profile := state.Host
	state.Unlock()
	if len(profile.Accelerators) > 0 {
		values = append(profile.Accelerators, values...)
	}
	source := "container"
	if profile.Name != "" {
		source = "host agent: " + profile.Name
	}
	writeJSON(w, http.StatusOK, map[string]any{"source": source, "host": runtime.GOOS, "hostProfile": profile, "accelerators": values, "count": len(values)})
}
func importHostHandler(w http.ResponseWriter, r *http.Request) {
	var profile HostProfile
	if err := decode(r, &profile); err != nil || profile.Name == "" || profile.CPU == "" {
		writeJSON(w, 400, map[string]string{"error": "host name and CPU are required"})
		return
	}
	profile.ReportedAt = time.Now().UTC()
	state.Lock()
	state.Host = profile
	persistStateLocked()
	state.Unlock()
	publishEvent("host.registered", profile)
	writeJSON(w, http.StatusCreated, profile)
}
func poolsHandler(w http.ResponseWriter, r *http.Request) {
	state.RLock()
	defer state.RUnlock()
	writeJSON(w, http.StatusOK, map[string]any{"pools": state.Pools})
}
func workloadsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		state.RLock()
		defer state.RUnlock()
		writeJSON(w, http.StatusOK, map[string]any{"workloads": state.Workloads})
		return
	}
	var input struct {
		Name         string `json:"name"`
		Kind         string `json:"kind"`
		Pool         string `json:"pool"`
		Image        string `json:"image"`
		Accelerators int    `json:"accelerators"`
		SSHHost      string `json:"sshHost"`
		SSHPort      int    `json:"sshPort"`
		SSHUser      string `json:"sshUser"`
	}
	if err := decode(r, &input); err != nil || strings.TrimSpace(input.Name) == "" {
		writeJSON(w, 400, map[string]string{"error": "name is required"})
		return
	}
	if input.Accelerators < 0 {
		writeJSON(w, 400, map[string]string{"error": "accelerator count cannot be negative"})
		return
	}
	input.Name = strings.TrimSpace(input.Name)
	input.SSHHost = strings.TrimSpace(input.SSHHost)
	input.SSHUser = strings.TrimSpace(input.SSHUser)
	if input.SSHHost != "" || input.SSHUser != "" || input.SSHPort != 0 {
		if input.SSHPort == 0 {
			input.SSHPort = 22
		}
		if input.SSHHost == "" || input.SSHUser == "" || input.SSHPort < 1 || input.SSHPort > 65535 || !sshHostPattern.MatchString(input.SSHHost) || !sshNamePattern.MatchString(input.SSHUser) {
			writeJSON(w, 400, map[string]string{"error": "SSH host, user, and a port from 1 to 65535 are required; credentials remain in your SSH agent"})
			return
		}
	}
	state.Lock()
	item := Workload{ID: fmt.Sprintf("job-%d", time.Now().UnixNano()), Name: input.Name, Kind: input.Kind, Pool: input.Pool, Image: input.Image, Accelerators: input.Accelerators, Status: "queued", SSHHost: input.SSHHost, SSHPort: input.SSHPort, SSHUser: input.SSHUser, CreatedAt: time.Now()}
	state.Workloads = append(state.Workloads, item)
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "workload.created", map[string]any{"workload_id": item.ID, "name": item.Name, "kind": item.Kind})
	publishEvent("workload.created", item)
	writeJSON(w, 201, item)
}
func workloadActionHandler(w http.ResponseWriter, r *http.Request) {
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/workloads/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" || len(parts) > 2 {
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	id := parts[0]
	if len(parts) == 1 && r.Method == http.MethodDelete {
		state.Lock()
		for i := range state.Workloads {
			if state.Workloads[i].ID == id {
				item := state.Workloads[i]
				state.Workloads = append(state.Workloads[:i], state.Workloads[i+1:]...)
				persistStateLocked()
				state.Unlock()
				auditRequest(r, "workload.deleted", map[string]any{"workload_id": item.ID, "name": item.Name})
				publishEvent("workload.deleted", item)
				writeJSON(w, 200, map[string]bool{"ok": true})
				return
			}
		}
		state.Unlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	if len(parts) == 2 && parts[1] == "ssh" && r.Method == http.MethodGet {
		state.RLock()
		for _, item := range state.Workloads {
			if item.ID == id {
				state.RUnlock()
				if item.SSHHost == "" || item.SSHUser == "" {
					writeJSON(w, 409, map[string]string{"error": "SSH is not configured for this workload; add host, port, and user when submitting it"})
					return
				}
				port := item.SSHPort
				if port == 0 {
					port = 22
				}
				if port < 1 || port > 65535 || !sshHostPattern.MatchString(item.SSHHost) || !sshNamePattern.MatchString(item.SSHUser) {
					writeJSON(w, 409, map[string]string{"error": "stored SSH coordinates are invalid; redeploy the workload with a valid host, port, and user"})
					return
				}
				command := fmt.Sprintf("ssh -p %d %s@%s", port, item.SSHUser, item.SSHHost)
				auditRequest(r, "workload.ssh_command_requested", map[string]any{"workload_id": item.ID, "host": item.SSHHost})
				writeJSON(w, 200, map[string]any{"workloadId": item.ID, "name": item.Name, "status": item.Status, "host": item.SSHHost, "port": port, "user": item.SSHUser, "command": command})
				return
			}
		}
		state.RUnlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	if len(parts) == 2 && parts[1] == "ssh" && r.Method == http.MethodPatch {
		var input struct {
			Host string `json:"host"`
			Port int    `json:"port"`
			User string `json:"user"`
		}
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid SSH configuration"})
			return
		}
		input.Host = strings.TrimSpace(input.Host)
		input.User = strings.TrimSpace(input.User)
		if input.Port == 0 {
			input.Port = 22
		}
		if input.Host == "" || input.User == "" || input.Port < 1 || input.Port > 65535 || !sshHostPattern.MatchString(input.Host) || !sshNamePattern.MatchString(input.User) {
			writeJSON(w, 400, map[string]string{"error": "a valid SSH host, port, and user are required"})
			return
		}
		state.Lock()
		for i := range state.Workloads {
			if state.Workloads[i].ID == id {
				state.Workloads[i].SSHHost = input.Host
				state.Workloads[i].SSHPort = input.Port
				state.Workloads[i].SSHUser = input.User
				persistStateLocked()
				item := state.Workloads[i]
				state.Unlock()
				auditRequest(r, "workload.ssh_configured", map[string]any{"workload_id": item.ID, "host": item.SSHHost, "port": item.SSHPort})
				publishEvent("workload.ssh_configured", item)
				writeJSON(w, 200, item)
				return
			}
		}
		state.Unlock()
		writeJSON(w, 404, map[string]string{"error": "workload not found"})
		return
	}
	if len(parts) != 2 || parts[1] != "action" || r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input struct{ Action string }
	if err := decode(r, &input); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid action"})
		return
	}
	state.Lock()
	for i := range state.Workloads {
		if state.Workloads[i].ID == id {
			switch input.Action {
			case "start":
				state.Workloads[i].Status = "running"
			case "stop":
				state.Workloads[i].Status = "stopped"
			case "redeploy":
				state.Workloads[i].Status = "queued"
			case "checkpoint":
				state.Workloads[i].Checkpoint = "checkpoint://local/" + id
			default:
				state.Unlock()
				writeJSON(w, 400, map[string]string{"error": "supported actions: start, stop, redeploy, checkpoint"})
				return
			}
			persistStateLocked()
			item := state.Workloads[i]
			state.Unlock()
			auditRequest(r, "workload."+input.Action, map[string]any{"workload_id": item.ID, "name": item.Name})
			publishEvent("workload."+input.Action, item)
			writeJSON(w, 200, item)
			return
		}
	}
	state.Unlock()
	writeJSON(w, 404, map[string]string{"error": "workload not found"})
}
func workflowsHandler(w http.ResponseWriter, r *http.Request) {
	state.RLock()
	defer state.RUnlock()
	writeJSON(w, 200, map[string]any{"workflows": state.Workflows})
}
func settingsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		state.RLock()
		defer state.RUnlock()
		writeJSON(w, 200, state.Settings)
		return
	}
	var settings PlatformSettings
	if err := decode(r, &settings); err != nil || settings.Organization == "" {
		writeJSON(w, 400, map[string]string{"error": "organization is required"})
		return
	}
	state.Lock()
	state.Settings = settings
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "settings.updated", map[string]any{"organization": settings.Organization, "default_queue": settings.DefaultQueue})
	publishEvent("settings.updated", settings)
	writeJSON(w, 200, settings)
}
func ollamaBaseURL() string {
	if value := os.Getenv("OLLAMA_BASE_URL"); value != "" {
		return strings.TrimRight(value, "/")
	}
	return "http://host.docker.internal:11434"
}
func ollamaModels() ([]OllamaModel, error) {
	response, err := (&http.Client{Timeout: 8 * time.Second}).Get(ollamaBaseURL() + "/api/tags")
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("Ollama returned %s", response.Status)
	}
	var payload struct {
		Models []OllamaModel `json:"models"`
	}
	err = json.NewDecoder(io.LimitReader(response.Body, 8<<20)).Decode(&payload)
	return payload.Models, err
}
func modelsHandler(w http.ResponseWriter, r *http.Request) {
	models, err := ollamaModels()
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "cannot reach Ollama at " + ollamaBaseURL() + ": " + err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"runtime": "ollama", "baseURL": ollamaBaseURL(), "models": models})
}
func deployInferenceHandler(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Model string `json:"model"`
	}
	if err := decode(r, &input); err != nil || input.Model == "" {
		writeJSON(w, 400, map[string]string{"error": "model is required"})
		return
	}
	models, err := ollamaModels()
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "cannot reach local Ollama runtime"})
		return
	}
	exists := false
	for _, model := range models {
		if model.Name == input.Model || model.Model == input.Model {
			exists = true
			break
		}
	}
	if !exists {
		writeJSON(w, 404, map[string]string{"error": "model is not installed in Ollama"})
		return
	}
	state.Lock()
	workload := Workload{ID: fmt.Sprintf("ollama-%d", time.Now().UnixNano()), Name: input.Model, Kind: "ollama-inference", Pool: "local-ollama", Status: "running", Checkpoint: ollamaBaseURL(), CreatedAt: time.Now()}
	state.Workloads = append(state.Workloads, workload)
	persistStateLocked()
	state.Unlock()
	auditRequest(r, "inference.deployed", map[string]any{"workload_id": workload.ID, "model": input.Model, "runtime": "ollama"})
	publishEvent("inference.deployed", workload)
	writeJSON(w, 201, map[string]any{"workload": workload, "endpoint": "/api/v1/inference/generate"})
}
func generateHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 16<<20))
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": "cannot read request"})
		return
	}
	var input map[string]any
	if json.Unmarshal(body, &input) != nil || input["model"] == nil || input["prompt"] == nil {
		writeJSON(w, 400, map[string]string{"error": "model and prompt are required"})
		return
	}
	model, _ := input["model"].(string)
	state.Lock()
	exists := false
	for _, workload := range state.Workloads {
		if workload.Kind == "ollama-inference" && workload.Name == model && workload.Status == "running" {
			exists = true
			break
		}
	}
	if !exists {
		state.Workloads = append(state.Workloads, Workload{ID: fmt.Sprintf("ollama-%d", time.Now().UnixNano()), Name: model, Kind: "ollama-inference", Pool: "local-ollama", Status: "running", Checkpoint: ollamaBaseURL(), CreatedAt: time.Now()})
		persistStateLocked()
	}
	state.Unlock()
	input["stream"] = false
	payload, _ := json.Marshal(input)
	response, err := (&http.Client{Timeout: 5 * time.Minute}).Post(ollamaBaseURL()+"/api/generate", "application/json", bytes.NewReader(payload))
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "Ollama generation failed: " + err.Error()})
		return
	}
	defer response.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(response.Body, 32<<20))
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(data)
}
func staticHandler(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/")
	if path == "" {
		path = "index.html"
	}
	if path != "index.html" && path != "login.html" && path != "app.js" && path != "styles.css" && path != "premium.css" {
		http.NotFound(w, r)
		return
	}
	if path == "index.html" && authEnabled() {
		if _, ok := currentUser(r); !ok {
			http.Redirect(w, r, "/login.html", http.StatusSeeOther)
			return
		}
	}
	if path == "login.html" && authEnabled() {
		if _, ok := currentUser(r); ok {
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
	}
	content, err := ui.ReadFile(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if strings.HasSuffix(path, ".js") {
		w.Header().Set("Content-Type", "application/javascript")
	} else if strings.HasSuffix(path, ".css") {
		w.Header().Set("Content-Type", "text/css")
	} else {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
	}
	w.Header().Set("Cache-Control", "no-store")
	_, _ = w.Write(content)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		next.ServeHTTP(w, r)
	})
}

func main() {
	initializeInfrastructure()
	if err := initializeAuth(); err != nil {
		fmt.Printf("Authentication initialization failed: %v\n", err)
	} else if authEnabled() {
		fmt.Println("Local authentication ready")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "healthy", "edition": "developer"})
	})
	mux.HandleFunc("/api/v1/ready", func(w http.ResponseWriter, r *http.Request) {
		if database == nil || database.Ping(r.Context()) != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not ready", "dependency": "postgres"})
			return
		}
		if eventBus == nil || !eventBus.IsConnected() {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not ready", "dependency": "nats"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("/api/v1/auth/status", authStatusHandler)
	mux.HandleFunc("/api/v1/auth/login", loginHandler)
	mux.HandleFunc("/api/v1/auth/signup", signupHandler)
	mux.HandleFunc("/api/v1/auth/me", meHandler)
	mux.HandleFunc("/api/v1/auth/logout", logoutHandler)
	mux.HandleFunc("/api/v1/discovery/scan", requireOperator(scanHandler))
	mux.HandleFunc("/api/v1/discovery/import", importHostHandler)
	mux.HandleFunc("/api/v1/clusters", requireOperator(clustersHandler))
	mux.HandleFunc("/api/v1/clusters/", requireOperator(clusterResourceHandler))
	mux.HandleFunc("/api/v1/pools", requireOperator(managedPoolsHandler))
	mux.HandleFunc("/api/v1/pools/", requireOperator(poolResourceHandler))
	mux.HandleFunc("/api/v1/queues", requireOperator(queuesHandler))
	mux.HandleFunc("/api/v1/queues/", requireOperator(queueResourceHandler))
	mux.HandleFunc("/api/v1/integrations", requireOperator(integrationsHandler))
	mux.HandleFunc("/api/v1/integrations/", requireOperator(integrationResourceHandler))
	mux.HandleFunc("/api/v1/users", requireAdmin(usersHandler))
	mux.HandleFunc("/api/v1/users/", requireAdmin(userResourceHandler))
	mux.HandleFunc("/api/v1/audit", requireAdmin(auditEventsHandler))
	mux.HandleFunc("/api/v1/observability/summary", requireAuth(observabilityHandler))
	mux.HandleFunc("/metrics", metricsHandler)
	mux.HandleFunc("/api/v1/workloads", requireOperator(workloadsHandler))
	mux.HandleFunc("/api/v1/workloads/", requireOperator(workloadActionHandler))
	mux.HandleFunc("/api/v1/workflows", requireOperator(workflowsHandler))
	mux.HandleFunc("/api/v1/settings", requireAdmin(settingsHandler))
	mux.HandleFunc("/api/v1/models", requireAuth(modelsHandler))
	mux.HandleFunc("/api/v1/inference/deploy", requireOperator(deployInferenceHandler))
	mux.HandleFunc("/api/v1/inference/generate", requireAuth(generateHandler))
	mux.HandleFunc("/", staticHandler)
	fmt.Printf("OpenMycelium Developer Edition listening on http://127.0.0.1:%s\n", port)
	server := &http.Server{Addr: ":" + port, Handler: securityHeaders(mux), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 15 * time.Minute, IdleTimeout: 60 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		panic(err)
	}
}
