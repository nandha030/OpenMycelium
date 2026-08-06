package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/bcrypt"
)

type authUser struct {
	ID    string `json:"id"`
	Email string `json:"email"`
	Role  string `json:"role"`
}

var authSchema = []string{
	`CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
	`CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`,
}

type loginAttempt struct {
	Failures int
	ResetAt  time.Time
}

var loginAttempts = struct {
	sync.Mutex
	entries map[string]loginAttempt
}{entries: make(map[string]loginAttempt)}

func authEnabled() bool { return strings.EqualFold(os.Getenv("AUTH_MODE"), "local") }

func initializeAuth() error {
	if !authEnabled() {
		return nil
	}
	if database == nil {
		return errors.New("local authentication requires PostgreSQL")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	for _, statement := range authSchema {
		if _, err := database.Exec(ctx, statement); err != nil {
			return err
		}
	}
	_, _ = database.Exec(ctx, `DELETE FROM sessions WHERE expires_at<=now()`)
	var count int
	if err := database.QueryRow(ctx, `SELECT count(*) FROM users`).Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return nil
	}
	email := strings.TrimSpace(strings.ToLower(os.Getenv("BOOTSTRAP_ADMIN_EMAIL")))
	password := os.Getenv("BOOTSTRAP_ADMIN_PASSWORD")
	if !strings.Contains(email, "@") || len(password) < 12 {
		return errors.New("BOOTSTRAP_ADMIN_EMAIL and a BOOTSTRAP_ADMIN_PASSWORD of at least 12 characters are required for the first local account")
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return err
	}
	id := "usr_" + randomToken(12)
	_, err = database.Exec(ctx, `INSERT INTO users(id,email,password_hash,role) VALUES($1,$2,$3,'platform_admin')`, id, email, string(hash))
	if err == nil {
		audit("auth.bootstrap_admin", map[string]any{"user_id": id, "email": email})
	}
	return err
}

func randomToken(size int) string {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		panic("cryptographic random source unavailable")
	}
	return base64.RawURLEncoding.EncodeToString(value)
}
func tokenHash(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func loginAttemptKey(r *http.Request, email string) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	return host + "|" + strings.ToLower(strings.TrimSpace(email))
}

func loginAllowed(key string) bool {
	loginAttempts.Lock()
	defer loginAttempts.Unlock()
	attempt, found := loginAttempts.entries[key]
	if !found || time.Now().After(attempt.ResetAt) {
		delete(loginAttempts.entries, key)
		return true
	}
	return attempt.Failures < 5
}

func recordLoginFailure(key string) {
	loginAttempts.Lock()
	defer loginAttempts.Unlock()
	attempt := loginAttempts.entries[key]
	if time.Now().After(attempt.ResetAt) {
		attempt = loginAttempt{ResetAt: time.Now().Add(5 * time.Minute)}
	}
	attempt.Failures++
	loginAttempts.entries[key] = attempt
}

func clearLoginFailures(key string) {
	loginAttempts.Lock()
	delete(loginAttempts.entries, key)
	loginAttempts.Unlock()
}

func audit(subject string, payload any) {
	if database == nil {
		return
	}
	data, _ := json.Marshal(payload)
	_, _ = database.Exec(context.Background(), `INSERT INTO audit_events(subject,payload) VALUES($1,$2)`, subject, data)
}

func currentUser(r *http.Request) (authUser, bool) {
	if !authEnabled() {
		return authUser{ID: "development", Email: "development@local", Role: "platform_admin"}, true
	}
	cookie, err := r.Cookie("om_session")
	if err != nil || database == nil {
		return authUser{}, false
	}
	var user authUser
	err = database.QueryRow(r.Context(), `SELECT u.id,u.email,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=$1 AND s.expires_at>now() AND u.active=true`, tokenHash(cookie.Value)).Scan(&user.ID, &user.Email, &user.Role)
	return user, err == nil
}

func loginHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	if !authEnabled() {
		writeJSON(w, 409, map[string]string{"error": "local authentication is disabled"})
		return
	}
	if database == nil {
		writeJSON(w, 503, map[string]string{"error": "authentication database is unavailable"})
		return
	}
	var input struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid request"})
		return
	}
	attemptKey := loginAttemptKey(r, input.Email)
	if !loginAllowed(attemptKey) {
		w.Header().Set("Retry-After", "300")
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "too many failed sign-in attempts; try again in five minutes"})
		return
	}
	var user authUser
	var hash string
	err := database.QueryRow(r.Context(), `SELECT id,email,role,password_hash FROM users WHERE email=$1 AND active=true`, strings.ToLower(strings.TrimSpace(input.Email))).Scan(&user.ID, &user.Email, &user.Role, &hash)
	if err != nil || bcrypt.CompareHashAndPassword([]byte(hash), []byte(input.Password)) != nil {
		recordLoginFailure(attemptKey)
		audit("auth.login_failed", map[string]any{"email": input.Email})
		writeJSON(w, 401, map[string]string{"error": "invalid email or password"})
		return
	}
	clearLoginFailures(attemptKey)
	token := randomToken(32)
	expires := time.Now().Add(12 * time.Hour)
	_, err = database.Exec(r.Context(), `INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,$3)`, tokenHash(token), user.ID, expires)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "session creation failed"})
		return
	}
	http.SetCookie(w, &http.Cookie{Name: "om_session", Value: token, Path: "/", HttpOnly: true, Secure: strings.EqualFold(os.Getenv("COOKIE_SECURE"), "true"), SameSite: http.SameSiteStrictMode, Expires: expires})
	audit("auth.login", map[string]any{"user_id": user.ID})
	writeJSON(w, 200, user)
}

func signupHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	if !authEnabled() || database == nil {
		writeJSON(w, 503, map[string]string{"error": "local registration is unavailable"})
		return
	}
	state.RLock()
	registrationOpen := state.Settings.RegistrationOpen
	state.RUnlock()
	if !registrationOpen {
		writeJSON(w, 403, map[string]string{"error": "registration is closed; ask a platform administrator to enable it in Settings"})
		return
	}
	var input struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if decode(r, &input) != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid request"})
		return
	}
	email := strings.ToLower(strings.TrimSpace(input.Email))
	if !strings.Contains(email, "@") || len(input.Password) < 12 {
		writeJSON(w, 400, map[string]string{"error": "enter a valid email and a password of at least 12 characters"})
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(input.Password), bcrypt.DefaultCost)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "account creation failed"})
		return
	}
	user := authUser{ID: "usr_" + randomToken(12), Email: email, Role: "viewer"}
	if _, err = database.Exec(r.Context(), `INSERT INTO users(id,email,password_hash,role) VALUES($1,$2,$3,$4)`, user.ID, user.Email, string(hash), user.Role); err != nil {
		writeJSON(w, 409, map[string]string{"error": "an account with that email already exists"})
		return
	}
	audit("auth.signup", map[string]any{"user_id": user.ID, "email": user.Email})
	writeJSON(w, http.StatusCreated, user)
}

func authStatusHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	configured := !authEnabled()
	if authEnabled() && database != nil {
		var count int
		configured = database.QueryRow(r.Context(), `SELECT count(*) FROM users`).Scan(&count) == nil && count > 0
	}
	state.RLock()
	registrationOpen := state.Settings.RegistrationOpen
	state.RUnlock()
	writeJSON(w, 200, map[string]any{"mode": os.Getenv("AUTH_MODE"), "configured": configured, "registrationOpen": registrationOpen})
}

func meHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	user, ok := currentUser(r)
	if !ok {
		writeJSON(w, 401, map[string]string{"error": "authentication required"})
		return
	}
	writeJSON(w, 200, user)
}
func logoutHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]string{"error": "method not allowed"})
		return
	}
	if cookie, err := r.Cookie("om_session"); err == nil && database != nil {
		_, _ = database.Exec(r.Context(), `DELETE FROM sessions WHERE token_hash=$1`, tokenHash(cookie.Value))
	}
	http.SetCookie(w, &http.Cookie{Name: "om_session", Path: "/", MaxAge: -1, HttpOnly: true, Secure: strings.EqualFold(os.Getenv("COOKIE_SECURE"), "true"), SameSite: http.SameSiteStrictMode})
	writeJSON(w, 200, map[string]bool{"ok": true})
}
func requireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if _, ok := currentUser(r); !ok {
			writeJSON(w, 401, map[string]string{"error": "authentication required"})
			return
		}
		next(w, r)
	}
}
func requireOperator(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		user, ok := currentUser(r)
		if !ok {
			writeJSON(w, 401, map[string]string{"error": "authentication required"})
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead && user.Role != "operator" && user.Role != "platform_admin" {
			writeJSON(w, 403, map[string]string{"error": "operator role required"})
			return
		}
		next(w, r)
	}
}
func requireAdmin(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		user, ok := currentUser(r)
		if !ok {
			writeJSON(w, 401, map[string]string{"error": "authentication required"})
			return
		}
		if user.Role != "platform_admin" {
			writeJSON(w, 403, map[string]string{"error": "platform administrator role required"})
			return
		}
		next(w, r)
	}
}
