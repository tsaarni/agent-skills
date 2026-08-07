# Docker Authorization Policy

This project provides an authorization policy for Docker that protects the host from the user of Docker.
The target use case is an LLM agent that is given access to Docker and can launch a container to mount a protected host paths.

This policy refuses requests that:

- mounts a host path that is not in the permitted lists
- gives the container capabilities outside allowed capabilities
- gives the container access to the host namespaces
- manages swarm services or Docker plugins

The policy permits normal container operations.
The policy also permits `kind` clusters that are privileged by design.


## Install the Plugin

The policy engine is OPA running inside the `opa-docker-authz` Docker plugin.
The daemon sends every Docker API request to the plugin, which answers allow or deny.

### Configuration

The plugin image is `openpolicyagent/opa-docker-authz-v2`.
Version `0.9` with OPA 0.59 or newer is required.

To set up:

1. Make this directory structure on the host:

   ```
   mkdir -p /etc/docker/{bundle,config}

   cp authz.rego /etc/docker/bundle/authz.rego
   cp data.example.yaml /etc/docker/bundle/data.yaml

   cat > /etc/docker/config/opa-conf.yaml <<EOF
   bundles:
      authz:
         resource: file:///opa/bundle/
   decision_logs:
      console: true
   EOF
   ```

2. Install the plugin:

   ```
   docker plugin install --alias opa-docker-authz \
      openpolicyagent/opa-docker-authz-v2:0.9 \
      opa-args="-config-file /opa/config/opa-conf.yaml"
   ```

3. Add the plugin to `/etc/docker/daemon.json`:

   ```json
   { "authorization-plugins": ["openpolicyagent/opa-docker-authz-v2:0.9"] }
   ```

4. Reload the daemon:

   `sudo systemctl reload docker`

You can run these commands on the host to verify that the plugin is working:

| Command | Result |
|---------|--------|
| `docker run --rm alpine echo ok` | Permitted |
| `docker run --rm -v /etc:/x alpine echo nope` | Refused |
| `docker run --rm --privileged alpine echo nope` | Refused |
| `kind create cluster --name test` | Permitted |
| `docker run --rm -v "$HOME/work/demo":/demo alpine echo ok` | Permitted only when configured |

### Read the logs

The plugin logs one entry per request: the request input and `result` (`false` = refused). Its output goes to the Docker daemon logs, use: `journalctl -u docker -f` (entries are tagged `plugin=<ID>`).

### Update the policy or data

OPA checks the directory every 60–120 s and loads changes by itself.
To set a polling interval to check more often:

```yaml
bundles:
   authz:
      resource: file:///opa/bundle/
      polling:
         min_delay_seconds: 1
         max_delay_seconds: 5
```

A broken bundle is not activated and OPA keeps the last good version, so run `make check` first.


## Configure the Policy

Use `data.example.yaml` as a template and change it.
Following table shows the keys.

| Key | Default | Purpose |
|-----|---------|---------|
| `allow_rw_mounts` | `[]` | Host paths that may be mounted read-write. For example `/home/tsaarni/work` (Linux) or `/Users/tsaarni/work` (macOS). An entry permits everything under it. |
| `allow_ro_mounts` | `/lib/modules`, `/etc/localtime`, and others | Host paths that may be mounted read-only only. `/lib/modules` is required by `kind`. |
| `allowed_capabilities` | Docker default set + `NET_ADMIN` | Capabilities that may be granted with `--cap-add`. Every other capability grant is refused. Docker grants its default capability set to every container regardless of this list; the list governs explicit `--cap-add` requests. Remove entries to tighten. |
| `privileged_containers` | kind and buildx multiarch roles | List of container classes that are privileged by design (`kind` and `buildx multiarch` in `data.example.yaml` are the examples). Each entry has `name`, `match` (image globs, required labels, optional network name prefix) and `allow` (which exceptions it grants: `privileged`, `unconfined_security_opts`, `host_userns`). Mounts and capabilities stay global. |

On macOS Docker Desktop or colima, the home directory has the same path in the VM as on the host (`/Users/<user>`).

## Test the Policy

Check that `opa` is in `PATH` or set `OPA=/path/to/opa`.

| Command | Purpose |
|---------|---------|
| `make test` | Run the unit tests |
| `make check` | Check the syntax |
| `make fmt` | Check the format |
| `make eval INPUT=request.json` | Run one request through the policy and print the decision (`allow` + `deny` reasons). Add `DATA=data.example.yaml` to load a bundle config. |
| `make test OPA=/path/to/opa` | Use a different `opa` binary |

Or use docker:

`docker run --rm -v "$PWD":/p openpolicyagent/opa test /p`

## Limits

1. **A privileged-by-design role member (e.g. a `kind` node) is privileged.**
   On a Linux host, a privileged container can access the host disk.
2. **Image names are not verified.**
   You bypass image name check by changing the name of an image with `docker tag`.
3. **Old containers and volumes are not checked.**
   The policy checks new requests only.
4. **Container traffic is not limited.**
   A container can use the network.
5. **Symlinks are not resolved.**
   The policy checks the mount path exactly as the client wrote it, and Docker follows symlinks at mount time. A symlink inside a permitted directory exposes whatever it points to: `ln -s /etc ~/work/etc` and mounting `~/work/etc` gives the container the host's `/etc`. The plugin cannot detect this because it runs in a container and cannot read the host filesystem.
6. **`docker buildx create` needs the `buildx multiarch` role.**
   It starts a separate BuildKit builder as a privileged container; the `buildx multiarch` role in `data.example.yaml` permits it when the image matches `moby/buildkit*`, which enables cross-platform builds (`--platform`). The match is image-only (buildx sets no labels or network), so `docker tag <any> moby/buildkit:buildx-stable-1` also matches — remove the role to refuse `docker buildx create`. `docker build` without `--platform` uses the daemon's built-in builder and needs no role.
7. **Devices are always refused.**
   A `kind` cluster on rootless Docker needs the `/dev/fuse` device and is not supported. `kind` on btrfs or zfs mounts `/dev/mapper`; add `"/dev/mapper"` to `allow_rw_mounts` for this.
8. **To remove the plugin**,
   remove `authorization-plugins` from `daemon.json` first.
   Then reload the daemon. Then remove the plugin. You cannot remove the plugin with Docker while the policy is active.
