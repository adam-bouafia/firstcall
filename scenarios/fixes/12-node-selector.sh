kubectl -n firstcall-demo patch deploy gpu-inference -p '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'
