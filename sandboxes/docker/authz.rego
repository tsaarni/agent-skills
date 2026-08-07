# METADATA
# description: |
#   This policy if for authorizing Docker API requests using the OPA Docker
#   authorization plugin (openpolicyagent/opa-docker-authz v2).
#
#   The policy refuses by default. It permits only:
#     - a bind mount of a path in allow_rw_mounts or allow_ro_mounts
#     - a capability grant of a capability in allowed_capabilities
#     - a privileged container that matches a role in privileged_containers
#       (for example a kind cluster node)
#   It refuses host namespaces (PID, IPC, network, user, cgroup, UTS),
#   device access, mount propagation, volume driver options with a device,
#   swarm services, and Docker plugin management. Named volumes and tmpfs
#   mounts are not checked.
#
#   data.sandbox in data.yaml is the configuration.
#   Without data, every bind mount, every capability grant, and every
#   privileged container is refused.
package docker.authz

import rego.v1

# ---------------------------------------------------------------------------
# Configuration
#
# data.sandbox in data.yaml is the only configuration.
# Without data, the policy refuses every bind mount, every capability grant,
# and every privileged container (fail closed).
# ---------------------------------------------------------------------------

default sandbox := {}

sandbox := data.sandbox if is_object(data.sandbox)

cfg := sandbox if count(sandbox) > 0

# true when data.sandbox is not loaded
no_config := count(sandbox) == 0

# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

# METADATA
# description: |
#   allow is true unless a deny rule fires. The plugin refuses the request
#   when allow is false or undefined. A permitted operation must keep
#   allow true.
# entrypoint: true
default allow := true

allow := false if {
	count(deny) > 0
}

# deny: one message per refused request. The plugin writes the messages
# to its log.
deny contains msg if {
	msg := "JSON endpoints require Content-Type: application/json"
	json_endpoint
	object.get(input, ["Headers", "Content-Type"], "") != "application/json"
}

deny contains msg if {
	msg := "swarm management is not allowed"
	input.Method != "GET"
	api_resource in {"swarm", "nodes", "services", "tasks", "secrets", "configs"}
}

deny contains msg if {
	msg := "docker plugin management is not allowed"
	input.Method != "GET"
	api_resource == "plugins"
}

deny contains msg if {
	msg := "privileged containers are only allowed for privileged-by-design roles"
	is_create
	hc.Privileged == true
	not role_grants("privileged")
}

deny contains msg if {
	msg := "seccomp/apparmor unconfined is only allowed for privileged-by-design roles"
	is_create
	not role_grants("unconfined_security_opts")
	some opt in security_opts
	opt in {"seccomp=unconfined", "apparmor=unconfined"}
}

deny contains msg if {
	msg := "host PID namespace is not allowed"
	is_create
	object.get(hc, ["PidMode"], "") == "host"
}

deny contains msg if {
	msg := "host IPC namespace is not allowed"
	is_create
	object.get(hc, ["IpcMode"], "") == "host"
}

deny contains msg if {
	msg := "host UTS namespace is not allowed"
	is_create
	object.get(hc, ["UTSMode"], "") == "host"
}

deny contains msg if {
	msg := "host network is not allowed"
	is_create
	object.get(hc, ["NetworkMode"], "") == "host"
}

deny contains msg if {
	msg := "host cgroup namespace is not allowed"
	is_create
	object.get(hc, ["CgroupnsMode"], "") == "host"
}

deny contains msg if {
	msg := "host user namespace is not allowed"
	is_create
	object.get(hc, ["UsernsMode"], "") == "host"
	not role_grants("host_userns") # for example, kind uses --userns=host
}

deny contains msg if {
	msg := "capability grants are not allowed (no data.sandbox configuration)"
	is_create
	no_config
	count(cap_add) > 0
}

deny contains msg if {
	is_create
	some cap in cap_add
	not cap_allowed(cap)
	msg := sprintf("capability %q is not allowed (not in allowed_capabilities)", [cap])
}

deny contains msg if {
	msg := "device access is not allowed"
	is_create
	count(devices) > 0
}

deny contains msg if {
	msg := "device cgroup rules are not allowed"
	is_create
	count(device_rules) > 0
}

deny contains msg if {
	msg := "GPU / CDI device requests are not allowed"
	is_create
	count(device_requests) > 0
}

deny contains msg if {
	is_create
	some m in input.BindMounts
	not mount_allowed(m)
	msg := sprintf("bind mount %q is not allowed (not in allow_rw_mounts or allow_ro_mounts)", [m.Source])
}

deny contains msg if {
	is_create
	some m in mounts
	bad_propagation(m.BindPropagation)
	msg := sprintf("mount propagation %q is not allowed", [m.BindPropagation])
}

deny contains msg if {
	is_create
	some b in binds
	parts := split(b, ":")
	some i, opt in parts
	i >= 2
	bad_propagation(opt)
	msg := sprintf("bind propagation %q is not allowed", [opt])
}

deny contains msg if {
	msg := "volume mounts with host-binding driver options are not allowed"
	is_create
	some m in mounts
	m.Type == "volume"
	bind_device_opts(object.get(m, ["VolumeOptions", "DriverConfig", "Options"], {}))
}

deny contains msg if {
	msg := "volume driver options with a device are not allowed"
	is_volume_create
	bind_device_opts(object.get(input, ["Body", "DriverOpts"], {}))
}

deny contains msg if {
	msg := "swarm-scoped networks are not allowed"
	is_network_create
	object.get(input, ["Body", "Scope"], "") == "swarm"
}

deny contains msg if {
	msg := "docker build with host network is not allowed"
	is_build
	input.Query.networkmode[0] == "host"
}

deny contains msg if {
	msg := "privileged exec is not allowed"
	is_exec_create
	object.get(input, ["Body", "Privileged"], false) == true
}

# ---------------------------------------------------------------------------
# Request classification
#
# The plugin sends the request path with every request. These helpers
# identify the request type from the path.
# ---------------------------------------------------------------------------

is_create := glob.match("/**/containers/create", ["/"], input.PathPlain)
is_exec_create := glob.match("/**/containers/*/exec", ["/"], input.PathPlain)
is_volume_create := glob.match("/**/volumes/create", ["/"], input.PathPlain)
is_network_create := glob.match("/**/networks/create", ["/"], input.PathPlain)
is_build := glob.match("/**/build", ["/"], input.PathPlain)

json_endpoint if is_create
json_endpoint if is_exec_create
json_endpoint if is_volume_create
json_endpoint if is_network_create

# api_resource: the resource name in the path, for example "containers"
# in "/v1.40/containers/create". Versioned and plain paths are accepted.
api_resource := input.PathArr[2] if {
	count(input.PathArr) > 2
	startswith(input.PathArr[1], "v")
}

api_resource := input.PathArr[1] if {
	count(input.PathArr) > 1
	not startswith(input.PathArr[1], "v")
}

# ---------------------------------------------------------------------------
# Body helpers
#
# The plugin leaves Body empty for a request without the header
# Content-Type: application/json. The Content-Type guard above refuses such
# a request. These helpers return safe defaults for the Body fields.
# ---------------------------------------------------------------------------

hc := object.get(input, ["Body", "HostConfig"], {})
binds := object.get(hc, ["Binds"], [])
mounts := object.get(hc, ["Mounts"], [])
security_opts := object.get(hc, ["SecurityOpt"], [])
cap_add := object.get(hc, ["CapAdd"], [])

# cap_allowed: true when the capability is in allowed_capabilities.
# A missing key or a missing configuration permits no capability
# (fail closed). Rego note: do not write `not cap in cfg.allowed_capabilities`.
# A missing key makes `in` undefined. In Rego, `not` of undefined is
# undefined, not true. The count pattern below always returns a value.
cap_allowed(cap) := count([1 |
	some c in object.get(cfg, ["allowed_capabilities"], [])
	c == cap
]) > 0

devices := object.get(hc, ["Devices"], [])
device_rules := object.get(hc, ["DeviceCgroupRules"], [])
device_requests := object.get(hc, ["DeviceRequests"], [])

# ---------------------------------------------------------------------------
# Mount checks
#
# A bind mount is refused by default. It is permitted only when its source
# path is in allow_rw_mounts or allow_ro_mounts. An entry permits the path
# and everything below it. Named volumes and tmpfs mounts are not checked.
# ---------------------------------------------------------------------------

# norm: remove extra leading slashes. "//etc" cannot slip past "/etc".
norm(p) := concat("", ["/", trim_left(p, "/")])

# with_trailing_slash: add a trailing slash. Rego has no "+" for strings.
with_trailing_slash(p) := concat("", [p, "/"])

# matches: true when the path equals the prefix or is below it.
matches(path, prefix) if {
	norm(path) == norm(prefix)
}

matches(path, prefix) if {
	startswith(norm(path), with_trailing_slash(norm(prefix)))
}

# allowed_reason: a path may be mounted when it is in allow_rw_mounts
# (read-write or read-only), or when it is read-only and in allow_ro_mounts.
allowed_reason(path, ro) if {
	some p in cfg.allow_rw_mounts
	matches(path, p)
}

allowed_reason(path, ro) if {
	ro
	some p in cfg.allow_ro_mounts
	matches(path, p)
}

path_allowed(path, ro) := count([1 | allowed_reason(path, ro)]) > 0

mount_allowed(m) := count([1 | mount_ok(m)]) > 0

mount_ok(m) if {
	path_allowed(m.Source, m.ReadOnly)
}

# bad_propagation: these modes share a mount with the host. They are refused.
bad_propagation(p) := p in {"shared", "rshared", "slave", "rslave"}

# bind_device_opts: volume driver options with a device can mount a host
# path without a bind mount. They are refused, except the tmpfs device.
bind_device_opts(opts) if {
	opts.device
	not opts.type == "tmpfs"
}

bind_device_opts(opts) if {
	opts.device
	not opts.device == "tmpfs"
}

# ---------------------------------------------------------------------------
# Privileged-by-design roles
#
# A role names a class of containers that is privileged by design, for
# example a kind cluster node. A container belongs to a role when all
# conditions hold:
#   - the image matches one of the role's image globs
#   - every required label is present and matches its value glob
#     ("*" = any value)
#   - the network mode starts with the role's network name prefix, when
#     the role has one
#
# A role grants only the exceptions in its allow object. Mounts,
# capabilities, and devices are never role-specific. They apply to a role
# member as to any other container.
# ---------------------------------------------------------------------------

image_ref := object.get(input, ["Body", "Image"], "")
container_labels := object.get(input, ["Body", "Labels"], {})

role_matches(role) if {
	some img in object.get(role, ["match", "images"], [])
	glob.match(img, null, image_ref)
	labels_match(object.get(role, ["match", "require_labels"], {}))
	network_matches(object.get(role, ["match", "network"], {}))
}

# labels_match: every required label must be present and match its glob.
labels_match(req) if {
	count(req) == count([1 |
		some k, v in req
		labels_match_one(k, v)
	])
}

# labels_match_one: the label must exist (direct lookup) and its value
# must match the glob. The delimiters are null. "*" also matches "."
# and ":", as in image tags and label values.
labels_match_one(k, v) if {
	glob.match(v, null, container_labels[k])
}

# network_matches without a network entry: any network is accepted.
network_matches(net) if {
	count(net) == 0
}

network_matches(net) if {
	startswith(object.get(hc, ["NetworkMode"], ""), net.name_prefix)
}

# role_grants: true when the container matches the role and the role's
# allow value for the feature is true.
role_grants(feature) if {
	some role in cfg.privileged_containers
	role_matches(role)
	object.get(role, ["allow", feature], false) == true
}
