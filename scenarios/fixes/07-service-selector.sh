kubectl -n firstcall-demo patch svc orders -p '{"spec":{"selector":{"app":"orders-api"}}}'
