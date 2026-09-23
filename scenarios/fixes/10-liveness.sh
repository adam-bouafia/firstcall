kubectl -n firstcall-demo patch deploy search-api --type=json \
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds","value":30}]'
