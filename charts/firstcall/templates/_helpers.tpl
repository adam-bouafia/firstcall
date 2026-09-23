{{- define "firstcall.name" -}}firstcall{{- end -}}
{{- define "firstcall.fullname" -}}{{ .Release.Name | trunc 40 | trimSuffix "-" }}{{- end -}}
{{- define "firstcall.labels" -}}
app.kubernetes.io/name: firstcall
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "firstcall.selector" -}}
app.kubernetes.io/name: firstcall
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "firstcall.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}{{ default (include "firstcall.fullname" .) .Values.serviceAccount.name }}{{- else -}}{{ default "default" .Values.serviceAccount.name }}{{- end -}}
{{- end -}}
{{- define "firstcall.secretName" -}}
{{- if .Values.existingSecret -}}{{ .Values.existingSecret }}{{- else -}}{{ include "firstcall.fullname" . }}-keys{{- end -}}
{{- end -}}
{{- define "firstcall.apiImage" -}}{{ .Values.image.api.repository }}:{{ default .Chart.AppVersion .Values.image.api.tag }}{{- end -}}
{{- define "firstcall.webImage" -}}{{ .Values.image.web.repository }}:{{ default .Chart.AppVersion .Values.image.web.tag }}{{- end -}}
