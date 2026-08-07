# Unit tests for authz.rego. Run with:
#
#   make test                                   # if opa is in PATH
#   make test OPA=/path/to/opa
#
# Tests build realistic `input` documents for the opa-docker-authz plugin,
# including the BindMounts array the plugin enriches the request with
# (only "/"-prefixed binds are extracted, mirroring listBindMounts).

package docker.authz

import rego.v1

# --- helpers ----------------------------------------------------------------

base_create := {
	"PathPlain": "/v1.40/containers/create",
	"Method": "POST",
	"Headers": {"Content-Type": "application/json"},
	"Query": {},
	"BindMounts": [],
	"Body": {"Image": "alpine", "HostConfig": {}},
}

input_create(hc, binds) := object.union(base_create, {
	"BindMounts": [bm |
		some b in binds
		startswith(b, "/")
		src := split(b, ":")[0]
		ro := endswith(b, ":ro")
		bm := {"Source": src, "ReadOnly": ro}
	],
	"Body": {"Image": "alpine", "HostConfig": object.union(hc, {"Binds": binds, "Mounts": []})},
})

# --- baseline (no mounts) ---------------------------------------------------

test_plain_container_run_allowed if {
	allow with input as base_create
}

test_image_pull_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/images/create",
		"Method": "POST",
		"Headers": {},
		"Query": {"fromImage": ["alpine"], "tag": ["latest"]},
		"BindMounts": [],
	}
}

test_named_volume_allowed if {
	allow with input as input_create({}, ["myvol:/data"])
}

test_anonymous_volume_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/containers/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Image": "postgres", "HostConfig": {"Volumes": {"/var/lib/postgresql": {}}}},
	}
}

test_tmpfs_allowed if {
	allow with input as input_create({"Tmpfs": {"/run": "rw"}}, [])
}

test_network_create_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/networks/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Name": "kind", "Driver": "bridge"},
	}
}

test_normal_exec_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/containers/abc123/exec",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Cmd": ["/bin/sh"], "Privileged": false},
	}
}

# --- mounts: deny by default ------------------------------------------------

test_mount_denied_by_default if {
	not allow with input as input_create({}, ["/home/tsaarni/work/demo:/app"])
}

test_mount_etc_denied if {
	not allow with input as input_create({}, ["/etc:/hostetc"])
}

test_mount_var_denied if {
	not allow with input as input_create({}, ["/var/lib:/hostvar"])
}

test_mount_root_denied if {
	not allow with input as input_create({}, ["/:/host"])
}

test_mount_tmp_denied if {
	not allow with input as input_create({}, ["/tmp/data:/data"])
}

test_mount_home_dotfile_ro_denied if {
	not allow with input as input_create({}, ["/home/tsaarni/.ssh:/ssh:ro"])
}

test_mount_socket_denied if {
	not allow with input as input_create({}, ["/var/run/docker.sock:/var/run/docker.sock"])
}

test_mount_double_slash_denied if {
	not allow with input as input_create({}, ["//etc:/hostetc"])
}

# --- mounts: allowlists -----------------------------------------------------

test_allow_rw_workspace_allowed if {
	allow with input as input_create({}, ["/home/tsaarni/work/demo:/app"]) with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_allow_rw_prefix_covers_subdirs if {
	allow with input as input_create({}, ["/home/tsaarni/work/a/b/c:/x"]) with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_allow_rw_ro_mount_allowed if {
	allow with input as input_create({}, ["/home/tsaarni/work/demo:/app:ro"]) with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_outside_allowlist_denied if {
	not allow with input as input_create({}, ["/home/tsaarni/other:/x"]) with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_allow_ro_path_ro_allowed if {
	allow with input as input_create({}, ["/etc/localtime:/etc/localtime:ro"]) with data.sandbox as {"allow_ro_mounts": ["/etc/localtime"]}
}

test_allow_ro_path_rw_denied if {
	not allow with input as input_create({}, ["/etc/localtime:/etc/localtime"]) with data.sandbox as {"allow_ro_mounts": ["/etc/localtime"]}
}

# --- privileges -------------------------------------------------------------

test_privileged_denied if {
	not allow with input as input_create({"Privileged": true}, [])
}

test_seccomp_unconfined_denied if {
	not allow with input as input_create({"SecurityOpt": ["seccomp=unconfined"]}, [])
}

test_privileged_exec_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/containers/abc123/exec",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Cmd": ["/bin/sh"], "Privileged": true},
	}
}

test_cap_not_in_allowlist_denied if {
	not allow with input as input_create({"CapAdd": ["SYS_ADMIN"]}, []) with data.sandbox as {"allowed_capabilities": ["NET_ADMIN"]}
}

test_cap_all_denied if {
	not allow with input as input_create({"CapAdd": ["ALL"]}, []) with data.sandbox as {"allowed_capabilities": ["NET_ADMIN"]}
}

test_benign_cap_allowed if {
	allow with input as input_create({"CapAdd": ["NET_ADMIN"]}, []) with data.sandbox as {"allowed_capabilities": ["NET_ADMIN"]}
}

test_cap_add_denied_without_config if {
	not allow with input as input_create({"CapAdd": ["NET_ADMIN"]}, [])
}

test_cap_add_denied_when_allowlist_missing if {
	# fail closed: a config without allowed_capabilities denies all CapAdd
	not allow with input as input_create({"CapAdd": ["NET_ADMIN"]}, []) with data.sandbox as {"allow_ro_mounts": ["/lib/modules"]}
}

test_cap_mixed_add_denied if {
	# one cap outside the allowlist denies the whole request
	not allow with input as input_create({"CapAdd": ["NET_ADMIN", "SYS_NICE"]}, []) with data.sandbox as {"allowed_capabilities": ["NET_ADMIN"]}
}

test_device_denied if {
	not allow with input as input_create({"Devices": [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}]}, [])
}

test_device_cgroup_rule_denied if {
	not allow with input as input_create({"DeviceCgroupRules": ["c 1:* rmw"]}, [])
}

test_gpu_request_denied if {
	not allow with input as input_create({"DeviceRequests": [{"Driver": "nvidia", "Count": 1}]}, [])
}

# --- namespaces -------------------------------------------------------------

test_pid_host_denied if {
	not allow with input as input_create({"PidMode": "host"}, [])
}

test_network_host_denied if {
	not allow with input as input_create({"NetworkMode": "host"}, [])
}

test_ipc_host_denied if {
	not allow with input as input_create({"IpcMode": "host"}, [])
}

test_uts_host_denied if {
	not allow with input as input_create({"UTSMode": "host"}, [])
}

test_userns_host_denied if {
	not allow with input as input_create({"UsernsMode": "host"}, [])
}

test_cgroupns_host_denied if {
	not allow with input as input_create({"CgroupnsMode": "host"}, [])
}

# --- mounts (modern API), propagation, volume driver opts -------------------

test_propagation_shared_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/containers/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [{"Source": "/home/tsaarni/work/demo", "ReadOnly": false}],
		"Body": {"Image": "alpine", "HostConfig": {"Binds": [], "Mounts": [
			{"Type": "bind", "Source": "/home/tsaarni/work/demo", "Target": "/demo", "BindPropagation": "rshared"},
		]}},
	}
		with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_bind_string_propagation_denied if {
	not allow with input as input_create({}, ["/home/tsaarni/work/demo:/demo:shared"]) with data.sandbox as {"allow_rw_mounts": ["/home/tsaarni/work"]}
}

test_volume_with_device_opts_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/containers/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Image": "alpine", "HostConfig": {"Binds": [], "Mounts": [
			{"Type": "volume", "Source": "v", "Target": "/data", "VolumeOptions": {"DriverConfig": {"Options": {"type": "none", "device": "/etc", "o": "bind"}}}},
		]}},
	}
}

test_volume_create_with_device_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/volumes/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Name": "v", "Driver": "local", "DriverOpts": {"type": "none", "device": "/etc", "o": "bind"}},
	}
}

test_volume_create_plain_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/volumes/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {"Name": "v"},
	}
}

# --- build ------------------------------------------------------------------

test_build_host_network_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/build",
		"Method": "POST",
		"Headers": {},
		"Query": {"networkmode": ["host"]},
		"BindMounts": [],
	}
}

test_build_default_network_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/build",
		"Method": "POST",
		"Headers": {},
		"Query": {},
		"BindMounts": [],
	}
}

# --- management endpoints ---------------------------------------------------

test_swarm_service_create_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/services/create",
		"PathArr": ["", "v1.40", "services", "create"],
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [],
		"Body": {},
	}
}

test_swarm_inspect_get_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/swarm",
		"PathArr": ["", "v1.40", "swarm"],
		"Method": "GET",
		"Headers": {},
		"Query": {},
		"BindMounts": [],
	}
}

test_plugin_install_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/plugins/pull",
		"PathArr": ["", "v1.40", "plugins", "pull"],
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {"name": ["evil/plugin"]},
		"BindMounts": [],
		"Body": {},
	}
}

test_plugin_list_get_allowed if {
	allow with input as {
		"PathPlain": "/v1.40/plugins",
		"PathArr": ["", "v1.40", "plugins"],
		"Method": "GET",
		"Headers": {},
		"Query": {},
		"BindMounts": [],
	}
}

# --- content type guard -----------------------------------------------------

test_create_without_json_content_type_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/containers/create",
		"Method": "POST",
		"Headers": {},
		"Query": {},
		"BindMounts": [],
		"Body": {"Image": "alpine", "HostConfig": {}},
	}
}

# --- kind whitelist ---------------------------------------------------------

# Complete data.sandbox configuration for the kind tests (there are no
# defaults in the policy; data.example.yaml is the real-world template).
kind_data := {
	"allow_ro_mounts": ["/lib/modules"],
	"allowed_capabilities": ["NET_ADMIN"],
	"privileged_containers": [
		{
			"name": "kind",
			"match": {
				"images": ["kindest/node*", "docker.io/kindest/node*"],
				"require_labels": {"io.x-k8s.kind.cluster": "*"},
				"network": {"name_prefix": "kind"},
			},
			"allow": {
				"privileged": true,
				"unconfined_security_opts": true,
				"host_userns": true,
			},
		},
	],
}

# kind role without a network requirement (was require_kind_network: false)
kind_data_no_network := {
	"allow_ro_mounts": ["/lib/modules"],
	"allowed_capabilities": ["NET_ADMIN"],
	"privileged_containers": [
		{
			"name": "kind",
			"match": {
				"images": ["kindest/node*", "docker.io/kindest/node*"],
				"require_labels": {"io.x-k8s.kind.cluster": "*"},
			},
			"allow": {
				"privileged": true,
				"unconfined_security_opts": true,
				"host_userns": true,
			},
		},
	],
}

# Second, hypothetical role: a worker image on any network, matched by a label
# value, granting only the privileged exception. Proves allow entries are
# independent opt-ins and that no network entry means any network is accepted.
worker_data := {
	"allow_ro_mounts": ["/lib/modules"],
	"allowed_capabilities": ["NET_ADMIN"],
	"privileged_containers": [
		{
			"name": "worker",
			"match": {
				"images": ["registry.example.com/worker*"],
				"require_labels": {"node.role": "worker"},
			},
			"allow": {"privileged": true},
		},
	],
}

worker_input := {
	"PathPlain": "/v1.40/containers/create",
	"Method": "POST",
	"Headers": {"Content-Type": "application/json"},
	"Query": {},
	"BindMounts": [],
	"Body": {
		"Image": "registry.example.com/worker:v1",
		"Labels": {"node.role": "worker"},
		"HostConfig": {
			"Privileged": true,
			"NetworkMode": "bridge",
			"Binds": [],
			"Mounts": [],
		},
	},
}

kind_node_input := {
	"PathPlain": "/v1.40/containers/create",
	"Method": "POST",
	"Headers": {"Content-Type": "application/json"},
	"Query": {},
	"BindMounts": [{"Source": "/lib/modules", "ReadOnly": true}],
	"Body": {
		"Image": "kindest/node:v1.30.0",
		"Labels": {"io.x-k8s.kind.cluster": "kind", "io.x-k8s.kind.role": "control-plane"},
		"HostConfig": {
			"Privileged": true,
			"SecurityOpt": ["seccomp=unconfined", "apparmor=unconfined"],
			"NetworkMode": "kind",
			"CgroupnsMode": "private",
			"Binds": ["/lib/modules:/lib/modules:ro"],
			"Mounts": [],
		},
	},
}

test_kind_node_allowed if {
	allow with input as kind_node_input with data.sandbox as kind_data
}

test_kind_denied_without_config if {
	not allow with input as kind_node_input
}

test_kind_node_with_userns_host_allowed if {
	allow with input as object.union(kind_node_input, {"Body": object.union(kind_node_input.Body, {
		"HostConfig": object.union(kind_node_input.Body.HostConfig, {"UsernsMode": "host"}),
	})})
		with data.sandbox as kind_data
}

test_kind_privileged_without_label_denied if {
	not allow with input as {
		"PathPlain": "/v1.40/containers/create",
		"Method": "POST",
		"Headers": {"Content-Type": "application/json"},
		"Query": {},
		"BindMounts": [{"Source": "/lib/modules", "ReadOnly": true}],
		"Body": {
			"Image": "kindest/node:v1.30.0",
			"Labels": {}, # no io.x-k8s.kind.cluster label
			"HostConfig": {
				"Privileged": true,
				"SecurityOpt": ["seccomp=unconfined"],
				"NetworkMode": "kind",
				"Binds": ["/lib/modules:/lib/modules:ro"],
				"Mounts": [],
			},
		},
	}
		with data.sandbox as kind_data
}

test_kind_privileged_wrong_network_denied if {
	not allow with input as object.union(kind_node_input, {"Body": object.union(kind_node_input.Body, {
		"HostConfig": object.union(kind_node_input.Body.HostConfig, {"NetworkMode": "bridge"}),
	})})
		with data.sandbox as kind_data
}

test_kind_spoofed_image_privileged_denied if {
	not allow with input as object.union(kind_node_input, {"Body": object.union(kind_node_input.Body, {"Image": "alpine"})}) with data.sandbox as kind_data
}

test_kind_with_sensitive_extra_mount_denied if {
	not allow with input as object.union(kind_node_input, {
		"BindMounts": [
			{"Source": "/lib/modules", "ReadOnly": true},
			{"Source": "/etc", "ReadOnly": false},
		],
		"Body": object.union(kind_node_input.Body, {"HostConfig": object.union(kind_node_input.Body.HostConfig, {
			"Binds": ["/lib/modules:/lib/modules:ro", "/etc:/mnt"],
		})}),
	})
		with data.sandbox as kind_data
}

test_privileged_non_kind_image_denied if {
	not allow with input as input_create({"Privileged": true, "NetworkMode": "kind"}, [])
}

test_kind_full_image_ref_allowed if {
	allow with input as object.union(kind_node_input, {"Body": object.union(kind_node_input.Body, {"Image": "docker.io/kindest/node:v1.30.0"})}) with data.sandbox as kind_data
}

test_kind_no_network_requirement_allowed if {
	allow with input as object.union(kind_node_input, {"Body": object.union(kind_node_input.Body, {
		"HostConfig": object.union(kind_node_input.Body.HostConfig, {"NetworkMode": "custom-net"}),
	})})
		with data.sandbox as kind_data_no_network
}

test_second_role_on_any_network_allowed if {
	allow with input as worker_input with data.sandbox as worker_data
}

test_role_label_value_mismatch_denied if {
	not allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {"Labels": {"node.role": "other"}})}) with data.sandbox as worker_data
}

test_role_image_mismatch_denied if {
	not allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {"Image": "alpine"})}) with data.sandbox as worker_data
}

test_role_ungranted_security_opt_denied if {
	# worker role grants privileged but not unconfined_security_opts
	not allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {
		"HostConfig": object.union(worker_input.Body.HostConfig, {"SecurityOpt": ["seccomp=unconfined"]}),
	})})
		with data.sandbox as worker_data
}

test_role_ungranted_host_userns_denied if {
	not allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {
		"HostConfig": object.union(worker_input.Body.HostConfig, {"UsernsMode": "host"}),
	})})
		with data.sandbox as worker_data
}

test_role_member_extra_mount_denied if {
	# mounts are global: a role grants no mount paths beyond the allowlists
	not allow with input as object.union(worker_input, {
		"BindMounts": [{"Source": "/etc", "ReadOnly": false}],
		"Body": object.union(worker_input.Body, {"HostConfig": object.union(worker_input.Body.HostConfig, {
			"Binds": ["/etc:/mnt"],
		})}),
	})
		with data.sandbox as worker_data
}

test_role_member_cap_not_allowed_denied if {
	# capabilities are global: the allowlist applies to role members too
	not allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {
		"HostConfig": object.union(worker_input.Body.HostConfig, {"CapAdd": ["SYS_ADMIN"]}),
	})})
		with data.sandbox as worker_data
}

test_role_member_allowed_cap_allowed if {
	allow with input as object.union(worker_input, {"Body": object.union(worker_input.Body, {
		"HostConfig": object.union(worker_input.Body.HostConfig, {"CapAdd": ["NET_ADMIN"]}),
	})})
		with data.sandbox as worker_data
}
