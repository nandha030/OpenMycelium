package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTokenHash(t *testing.T) {
	first := tokenHash("one")
	if first != tokenHash("one") || first == tokenHash("two") {
		t.Fatal("session token hashes must be stable and unique per token")
	}
}

func TestLoginRequiresDatabase(t *testing.T) {
	t.Setenv("AUTH_MODE", "local")
	database = nil
	request := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", strings.NewReader(`{"email":"admin@example.com","password":"long-enough-password"}`))
	response := httptest.NewRecorder()
	loginHandler(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 without PostgreSQL, got %d", response.Code)
	}
}

func TestDashboardRedirectsWithoutSession(t *testing.T) {
	t.Setenv("AUTH_MODE", "local")
	database = nil
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	response := httptest.NewRecorder()
	staticHandler(response, request)
	if response.Code != http.StatusSeeOther || response.Header().Get("Location") != "/login.html" {
		t.Fatalf("expected login redirect, got %d %q", response.Code, response.Header().Get("Location"))
	}
}

func TestDevelopmentModeAllowsRequests(t *testing.T) {
	t.Setenv("AUTH_MODE", "")
	called := false
	handler := requireOperator(func(w http.ResponseWriter, r *http.Request) { called = true; w.WriteHeader(http.StatusNoContent) })
	response := httptest.NewRecorder()
	handler(response, httptest.NewRequest(http.MethodPost, "/api/v1/workloads", nil))
	if !called || response.Code != http.StatusNoContent {
		t.Fatalf("development mode request was unexpectedly blocked: %d", response.Code)
	}
}

func TestHostImportRequiresAgentAuthentication(t *testing.T) {
	t.Setenv("AUTH_MODE", "local")
	t.Setenv("AGENT_TOKEN", "test-agent-token-that-is-long-and-random")
	t.Setenv("STATE_PATH", t.TempDir()+"/state.json")
	database = nil

	handler := requireAgentOrOperator(importHostHandler)
	body := `{"name":"test-host","cpu":"test-cpu","logicalCores":4,"memoryGB":8}`
	unauthorized := httptest.NewRecorder()
	handler(unauthorized, httptest.NewRequest(http.MethodPost, "/api/v1/discovery/import", strings.NewReader(body)))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("expected unauthenticated import to return 401, got %d", unauthorized.Code)
	}

	authorizedRequest := httptest.NewRequest(http.MethodPost, "/api/v1/discovery/import", strings.NewReader(body))
	authorizedRequest.Header.Set("Authorization", "Bearer test-agent-token-that-is-long-and-random")
	authorized := httptest.NewRecorder()
	handler(authorized, authorizedRequest)
	if authorized.Code != http.StatusCreated {
		t.Fatalf("expected agent import to return 201, got %d: %s", authorized.Code, authorized.Body.String())
	}
}
