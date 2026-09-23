kubectl -n firstcall-demo patch deploy report-worker --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/args/0","value":"echo streaming dataset in chunks; sleep 3600"}]'
