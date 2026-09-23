kubectl -n firstcall-demo patch deploy auth-svc --type=json \
  -p '[{"op":"replace","path":"/spec/template/spec/containers/0/env/0/valueFrom/secretKeyRef/key","value":"db_password"}]'
