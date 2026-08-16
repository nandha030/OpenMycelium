package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type ModelArtifact struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	SourceType  string            `json:"sourceType"`
	Framework   string            `json:"framework"`
	Format      string            `json:"format"`
	License     string            `json:"license"`
	Versions    []ModelVersion    `json:"versions"`
	Aliases     map[string]string `json:"aliases"`
	CreatedAt   time.Time         `json:"createdAt"`
	UpdatedAt   time.Time         `json:"updatedAt"`
}

type ModelVersion struct {
	ID           string         `json:"id"`
	ModelID      string         `json:"modelId"`
	Version      string         `json:"version"`
	SourceURI    string         `json:"sourceUri"`
	Digest       string         `json:"digest"`
	SizeBytes    int64          `json:"sizeBytes"`
	Quantization string         `json:"quantization"`
	ParametersB  float64        `json:"parametersB"`
	Runtime      string         `json:"runtime"`
	Status       string         `json:"status"`
	Metadata     map[string]any `json:"metadata"`
	CreatedAt    time.Time      `json:"createdAt"`
}

type modelInput struct {
	Name         string         `json:"name"`
	Description  string         `json:"description"`
	SourceType   string         `json:"sourceType"`
	SourceURI    string         `json:"sourceUri"`
	Version      string         `json:"version"`
	Framework    string         `json:"framework"`
	Format       string         `json:"format"`
	License      string         `json:"license"`
	Digest       string         `json:"digest"`
	SizeBytes    int64          `json:"sizeBytes"`
	Quantization string         `json:"quantization"`
	ParametersB  float64        `json:"parametersB"`
	Runtime      string         `json:"runtime"`
	Metadata     map[string]any `json:"metadata"`
}

var modelSourceTypes = map[string]bool{"ollama": true, "huggingface": true, "oci": true, "s3": true, "gcs": true, "azure": true, "nfs": true, "http": true, "local": true}
var modelRuntimes = map[string]bool{"ollama": true, "vllm": true, "tgi": true, "triton": true, "kserve": true, "bentoml": true, "custom": true}

func modelRuntimeImage(runtime string) string {
	switch strings.ToLower(runtime) {
	case "ollama":
		return "ollama/ollama:latest"
	case "vllm":
		return "vllm/vllm-openai:latest"
	case "tgi":
		return "ghcr.io/huggingface/text-generation-inference:latest"
	case "triton":
		return "nvcr.io/nvidia/tritonserver:24.12-py3"
	case "bentoml":
		return "bentoml/model-server:latest"
	default:
		return ""
	}
}

func validateModelInput(input *modelInput) error {
	input.Name, input.SourceType, input.SourceURI = strings.TrimSpace(input.Name), strings.ToLower(strings.TrimSpace(input.SourceType)), strings.TrimSpace(input.SourceURI)
	input.Version, input.Runtime = strings.TrimSpace(input.Version), strings.ToLower(strings.TrimSpace(input.Runtime))
	if input.Name == "" || input.SourceType == "" || input.SourceURI == "" || input.Version == "" {
		return errors.New("name, source type, source URI, and version are required")
	}
	if !modelSourceTypes[input.SourceType] {
		return errors.New("unsupported model source type")
	}
	if input.Runtime == "" {
		input.Runtime = "custom"
	}
	if !modelRuntimes[input.Runtime] {
		return errors.New("unsupported model runtime")
	}
	if input.SizeBytes < 0 || input.ParametersB < 0 {
		return errors.New("model size and parameter count cannot be negative")
	}
	if input.SourceType != "local" {
		parsed, err := url.Parse(input.SourceURI)
		if err != nil || parsed.Scheme == "" {
			return errors.New("source URI must include a scheme such as hf://, ollama://, oci://, s3://, or https://")
		}
	}
	return nil
}

func scanModelArtifact(ctx context.Context, rows interface{ Scan(...any) error }) (ModelArtifact, error) {
	var model ModelArtifact
	err := rows.Scan(&model.ID, &model.Name, &model.Description, &model.SourceType, &model.Framework, &model.Format, &model.License, &model.CreatedAt, &model.UpdatedAt)
	if err != nil {
		return model, err
	}
	model.Versions = []ModelVersion{}
	model.Aliases = map[string]string{}
	versionRows, err := database.Query(ctx, `SELECT id,model_id,version,source_uri,digest,size_bytes,quantization,parameters_b,runtime,status,metadata,created_at FROM model_versions WHERE model_id=$1 ORDER BY created_at DESC`, model.ID)
	if err != nil {
		return model, err
	}
	defer versionRows.Close()
	for versionRows.Next() {
		var item ModelVersion
		var metadata []byte
		if versionRows.Scan(&item.ID, &item.ModelID, &item.Version, &item.SourceURI, &item.Digest, &item.SizeBytes, &item.Quantization, &item.ParametersB, &item.Runtime, &item.Status, &metadata, &item.CreatedAt) == nil {
			_ = json.Unmarshal(metadata, &item.Metadata)
			if item.Metadata == nil {
				item.Metadata = map[string]any{}
			}
			model.Versions = append(model.Versions, item)
		}
	}
	aliasRows, err := database.Query(ctx, `SELECT alias,version_id FROM model_aliases WHERE model_id=$1 ORDER BY alias`, model.ID)
	if err == nil {
		defer aliasRows.Close()
		for aliasRows.Next() {
			var alias, versionID string
			if aliasRows.Scan(&alias, &versionID) == nil {
				model.Aliases[alias] = versionID
			}
		}
	}
	return model, nil
}

func loadModelArtifact(ctx context.Context, id string) (ModelArtifact, error) {
	return scanModelArtifact(ctx, database.QueryRow(ctx, `SELECT id,name,description,source_type,framework,format,license,created_at,updated_at FROM model_artifacts WHERE id=$1`, id))
}

func loadModelVersion(ctx context.Context, id string) (ModelArtifact, ModelVersion, error) {
	var version ModelVersion
	var metadata []byte
	err := database.QueryRow(ctx, `SELECT id,model_id,version,source_uri,digest,size_bytes,quantization,parameters_b,runtime,status,metadata,created_at FROM model_versions WHERE id=$1`, id).Scan(&version.ID, &version.ModelID, &version.Version, &version.SourceURI, &version.Digest, &version.SizeBytes, &version.Quantization, &version.ParametersB, &version.Runtime, &version.Status, &metadata, &version.CreatedAt)
	if err != nil {
		return ModelArtifact{}, version, err
	}
	_ = json.Unmarshal(metadata, &version.Metadata)
	model, err := loadModelArtifact(ctx, version.ModelID)
	return model, version, err
}

func insertModelVersion(ctx context.Context, modelID string, input modelInput) (ModelVersion, error) {
	metadata, _ := json.Marshal(input.Metadata)
	item := ModelVersion{ID: "modv_" + randomToken(9), ModelID: modelID, Version: input.Version, SourceURI: input.SourceURI, Digest: strings.TrimSpace(input.Digest), SizeBytes: input.SizeBytes, Quantization: strings.TrimSpace(input.Quantization), ParametersB: input.ParametersB, Runtime: input.Runtime, Status: "registered", Metadata: input.Metadata, CreatedAt: time.Now().UTC()}
	_, err := database.Exec(ctx, `INSERT INTO model_versions(id,model_id,version,source_uri,digest,size_bytes,quantization,parameters_b,runtime,status,metadata,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, item.ID, item.ModelID, item.Version, item.SourceURI, item.Digest, item.SizeBytes, item.Quantization, item.ParametersB, item.Runtime, item.Status, metadata, item.CreatedAt)
	return item, err
}

func modelCatalogHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method == http.MethodGet {
		rows, err := database.Query(r.Context(), `SELECT id,name,description,source_type,framework,format,license,created_at,updated_at FROM model_artifacts ORDER BY updated_at DESC,name`)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot list model catalog"})
			return
		}
		defer rows.Close()
		items := []ModelArtifact{}
		for rows.Next() {
			item, scanErr := scanModelArtifact(r.Context(), rows)
			if scanErr == nil {
				items = append(items, item)
			}
		}
		writeJSON(w, 200, map[string]any{"models": items})
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	var input modelInput
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid model definition"})
		return
	}
	if err := validateModelInput(&input); err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	tx, err := database.Begin(r.Context())
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot create model transaction"})
		return
	}
	defer tx.Rollback(r.Context())
	model := ModelArtifact{ID: "model_" + randomToken(9), Name: input.Name, Description: strings.TrimSpace(input.Description), SourceType: input.SourceType, Framework: strings.TrimSpace(input.Framework), Format: strings.TrimSpace(input.Format), License: strings.TrimSpace(input.License), CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	_, err = tx.Exec(r.Context(), `INSERT INTO model_artifacts(id,name,description,source_type,framework,format,license,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`, model.ID, model.Name, model.Description, model.SourceType, model.Framework, model.Format, model.License, model.CreatedAt, model.UpdatedAt)
	if err != nil {
		writeJSON(w, 409, map[string]string{"error": "a model with that name already exists"})
		return
	}
	metadata, _ := json.Marshal(input.Metadata)
	version := ModelVersion{ID: "modv_" + randomToken(9), ModelID: model.ID, Version: input.Version, SourceURI: input.SourceURI, Digest: strings.TrimSpace(input.Digest), SizeBytes: input.SizeBytes, Quantization: strings.TrimSpace(input.Quantization), ParametersB: input.ParametersB, Runtime: input.Runtime, Status: "registered", Metadata: input.Metadata, CreatedAt: time.Now().UTC()}
	_, err = tx.Exec(r.Context(), `INSERT INTO model_versions(id,model_id,version,source_uri,digest,size_bytes,quantization,parameters_b,runtime,status,metadata,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`, version.ID, version.ModelID, version.Version, version.SourceURI, version.Digest, version.SizeBytes, version.Quantization, version.ParametersB, version.Runtime, version.Status, metadata, version.CreatedAt)
	if err != nil || tx.Commit(r.Context()) != nil {
		writeJSON(w, 500, map[string]string{"error": "cannot create model version"})
		return
	}
	model.Versions, model.Aliases = []ModelVersion{version}, map[string]string{}
	auditRequest(r, "model.created", map[string]any{"model_id": model.ID, "version_id": version.ID, "name": model.Name, "source": model.SourceType})
	publishEvent("model.created", model)
	writeJSON(w, 201, model)
}

func modelCatalogResourceHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	relative := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/v1/model-catalog/"), "/")
	parts := strings.Split(relative, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeJSON(w, 404, map[string]string{"error": "model not found"})
		return
	}
	modelID := parts[0]
	if len(parts) == 1 && r.Method == http.MethodGet {
		item, err := loadModelArtifact(r.Context(), modelID)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "model not found"})
			return
		}
		writeJSON(w, 200, item)
		return
	}
	if len(parts) == 1 && r.Method == http.MethodDelete {
		var referenced int
		_ = database.QueryRow(r.Context(), `SELECT count(*) FROM workloads WHERE workload->>'modelVersionId' IN (SELECT id FROM model_versions WHERE model_id=$1)`, modelID).Scan(&referenced)
		if referenced > 0 {
			writeJSON(w, 409, map[string]string{"error": "model is referenced by an existing workload"})
			return
		}
		result, err := database.Exec(r.Context(), `DELETE FROM model_artifacts WHERE id=$1`, modelID)
		if err != nil || result.RowsAffected() == 0 {
			writeJSON(w, 404, map[string]string{"error": "model not found"})
			return
		}
		auditRequest(r, "model.deleted", map[string]any{"model_id": modelID})
		writeJSON(w, 200, map[string]bool{"ok": true})
		return
	}
	if len(parts) == 2 && parts[1] == "versions" && r.Method == http.MethodPost {
		model, err := loadModelArtifact(r.Context(), modelID)
		if err != nil {
			writeJSON(w, 404, map[string]string{"error": "model not found"})
			return
		}
		var input modelInput
		if decode(r, &input) != nil {
			writeJSON(w, 400, map[string]string{"error": "invalid model version"})
			return
		}
		input.Name, input.SourceType = model.Name, model.SourceType
		if err = validateModelInput(&input); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		version, err := insertModelVersion(r.Context(), modelID, input)
		if err != nil {
			writeJSON(w, 409, map[string]string{"error": "this model version already exists"})
			return
		}
		_, _ = database.Exec(r.Context(), `UPDATE model_artifacts SET updated_at=now() WHERE id=$1`, modelID)
		auditRequest(r, "model.version_created", map[string]any{"model_id": modelID, "version_id": version.ID, "version": version.Version})
		writeJSON(w, 201, version)
		return
	}
	if len(parts) == 2 && parts[1] == "aliases" && r.Method == http.MethodPatch {
		var input struct {
			Alias     string `json:"alias"`
			VersionID string `json:"versionId"`
		}
		if decode(r, &input) != nil || strings.TrimSpace(input.Alias) == "" || strings.TrimSpace(input.VersionID) == "" {
			writeJSON(w, 400, map[string]string{"error": "alias and versionId are required"})
			return
		}
		var exists bool
		_ = database.QueryRow(r.Context(), `SELECT EXISTS(SELECT 1 FROM model_versions WHERE id=$1 AND model_id=$2)`, input.VersionID, modelID).Scan(&exists)
		if !exists {
			writeJSON(w, 400, map[string]string{"error": "version does not belong to this model"})
			return
		}
		alias := strings.ToLower(strings.TrimSpace(input.Alias))
		_, err := database.Exec(r.Context(), `INSERT INTO model_aliases(model_id,alias,version_id,updated_at) VALUES($1,$2,$3,now()) ON CONFLICT(model_id,alias) DO UPDATE SET version_id=excluded.version_id,updated_at=now()`, modelID, alias, input.VersionID)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": "cannot update model alias"})
			return
		}
		auditRequest(r, "model.alias_updated", map[string]any{"model_id": modelID, "alias": alias, "version_id": input.VersionID})
		writeJSON(w, 200, map[string]any{"alias": alias, "versionId": input.VersionID})
		return
	}
	writeJSON(w, 405, map[string]string{"error": "method not allowed"})
}

func syncOllamaCatalogHandler(w http.ResponseWriter, r *http.Request) {
	if !requireDatabase(w) {
		return
	}
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	models, err := ollamaModels()
	if err != nil {
		writeJSON(w, 502, map[string]string{"error": "cannot reach Ollama runtime"})
		return
	}
	created, updated := 0, 0
	for _, remote := range models {
		var modelID string
		err = database.QueryRow(r.Context(), `SELECT id FROM model_artifacts WHERE name=$1`, remote.Name).Scan(&modelID)
		if err != nil {
			modelID = "model_" + randomToken(9)
			_, err = database.Exec(r.Context(), `INSERT INTO model_artifacts(id,name,description,source_type,framework,format,license,created_at,updated_at) VALUES($1,$2,$3,'ollama','transformers','ollama','',now(),now())`, modelID, remote.Name, "Synchronized from the configured Ollama runtime")
			if err != nil {
				continue
			}
			created++
		}
		version := remote.Model
		if version == "" {
			version = remote.Name
		}
		metadata, _ := json.Marshal(map[string]any{"modifiedAt": remote.ModifiedAt})
		result, insertErr := database.Exec(r.Context(), `INSERT INTO model_versions(id,model_id,version,source_uri,size_bytes,runtime,status,metadata,created_at) VALUES($1,$2,$3,$4,$5,'ollama','available',$6,now()) ON CONFLICT(model_id,version) DO UPDATE SET size_bytes=excluded.size_bytes,status='available',metadata=excluded.metadata`, "modv_"+randomToken(9), modelID, version, "ollama://"+remote.Name, remote.Size, metadata)
		if insertErr == nil && result.RowsAffected() > 0 {
			updated++
		}
	}
	auditRequest(r, "model.ollama_synchronized", map[string]any{"discovered": len(models), "created": created, "updated": updated})
	writeJSON(w, 200, map[string]int{"discovered": len(models), "created": created, "updated": updated})
}
