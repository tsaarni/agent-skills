# OPA authorization policy for the opa-docker-authz plugin.
#
# Requires `opa` in PATH. Override with:  make test OPA=/path/to/opa

OPA ?= opa

REGOS := authz.rego authz_test.rego

.PHONY: test check fmt eval

test: ## run the Rego unit tests
	$(OPA) test $(REGOS)

check: ## syntax / type check
	$(OPA) check $(REGOS)

fmt: ## check formatting (fails if files are not opa-fmt clean)
	$(OPA) fmt --diff $(REGOS)

# Run one request through the policy and print the decision (allow + deny
# reasons), simulating the plugin's evaluation of data.docker.authz.allow.
#   make eval INPUT=request.json                 # no config (fail-closed)
#   make eval INPUT=request.json DATA=data.example.yaml  # with bundle data
eval:
	$(OPA) eval --format pretty $(if $(DATA),-d $(DATA),) -d authz.rego -i $(INPUT) '{ "allow": data.docker.authz.allow, "deny": data.docker.authz.deny }'
