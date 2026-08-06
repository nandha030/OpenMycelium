{{- define "openmycelium.name" -}}
openmycelium
{{- end }}

{{- define "openmycelium.fullname" -}}
{{ .Release.Name }}-openmycelium
{{- end }}

{{- define "openmycelium.labels" -}}
app.kubernetes.io/name: {{ include "openmycelium.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
