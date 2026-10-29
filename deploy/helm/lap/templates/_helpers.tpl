{{- define "lap.labels" -}}
app.kubernetes.io/part-of: lap
app.kubernetes.io/managed-by: {{ .Release.Service }}
lap/env: {{ .Values.env }}
{{- end -}}

{{- define "lap.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}

{{- define "lap.env" -}}
- { name: LAP_ENV, value: {{ .Values.env | quote }} }
- { name: LAP__BUS__BACKEND, value: kafka }
- { name: LAP__BUS__KAFKA__BOOTSTRAP_SERVERS, value: {{ .Values.kafka.bootstrap | quote }} }
- { name: LAP__STORAGE__BACKEND, value: clickhouse }
- { name: LAP__STORAGE__CLICKHOUSE__HOST, value: {{ .Values.clickhouse.host | quote }} }
- name: LAP__STORAGE__CLICKHOUSE__PASSWORD
  valueFrom: { secretKeyRef: { name: lap-clickhouse, key: password } }
- { name: LAP__DETECTION__REGISTRY_DIR, value: /models }
- { name: LAP__DETECTION__MLFLOW__TRACKING_URI, value: {{ .Values.mlflow.trackingUri | quote }} }
- { name: LAP__ALERTING__ALERTMANAGER_URL, value: {{ .Values.alerting.alertmanagerUrl | quote }} }
- { name: LAP_ARCHIVE_URI, value: {{ .Values.objectStore.archiveUri | quote }} }
{{- if .Values.objectStore.endpoint }}
- { name: S3_ENDPOINT, value: {{ .Values.objectStore.endpoint | quote }} }
{{- end }}
{{- end -}}
