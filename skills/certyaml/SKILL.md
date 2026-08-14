---
name: certyaml
description: Use this skill when you need x509 certificates for tests. 
---

## CLI

```shell
mkdir -p certs
certyaml -d certs [path/to/certs.yaml]
```

**Output**: `<subject_common_name>.pem` (cert with chain), `<subject_common_name>-key.pem`, `certs.state` and optional CRL files `<issuers_common_name>-crl.pem`.

- If no manifest file is provided, reads from `certs.yaml` in current directory.
- If no output directory is provided, writes to current directory.
- Generates `.state` file to track certificate parameters in the output directory.
- Regenerates only changed/missing certificates on subsequent runs.
- If you need to rotate/ renew certificates, delete the certificate and key files and re-run `certyaml` to regenerate.

### YAML Manifest Format

The certificate manifest file contains multiple documents separated by `---`, each defining one certificate.

**Key fields**: `subject` (DN, must be unique), `issuer` (references earlier cert's subject), `ca` (bool), `sans` (list), `filename` (output file basename, defaults to CN), `key_type` (EC/RSA/ED25519, defaults to EC.

Run `certyaml --help-yaml` for full field reference.

**Important**: Certificate order matters - issuer must be defined before it's referenced.


Example manifest:

```yaml
subject: cn=root-ca
---
subject: cn=server
issuer: cn=root-ca
sans:
  - DNS:localhost
  - IP:127.0.0.1
ext_key_usages:
  - ServerAuth
---
subject: cn=client
issuer: cn=root-ca
ext_key_usages:
  - ClientAuth
```

### Uploading Generated Certs as Kubernetes Secrets

Upload/update certificates as Kubernetes secrets 

```shell
# TLS secret type (kubernetes.io/tls)
kubectl create secret tls echoserver-cert --dry-run=client -o yaml \
  --cert=certs/echoserver.pem --key=certs/echoserver-key.pem | kubectl apply -f -

# Generic secret with custom key names (opaque type)
kubectl create secret generic internal-root-ca --from-file=ca.crt=certs/internal-root-ca.pem --dry-run=client -o yaml | kubectl apply -f -

# Patch tls secret to inject ca.crt in secret of type kubernetes.io/tls
kubectl patch secret openldap-cert --patch-file /dev/stdin <<EOF
data:
  ca.crt: $(cat certs/client-ca.pem | base64 -w 0)
EOF
```

## Go API

Refer to source for details, you can find it locally here `go list -m -f '{{.Dir}}' github.com/tsaarni/certyaml@latest`

```go
ca := certyaml.Certificate{Subject: "cn=ca"}
server := certyaml.Certificate{Subject: "cn=server", SubjectAltNames: []string{"DNS:localhost"}, Issuer: &ca}
tlsCert, err := server.TLSCertificate()  // Use in e.g. http.Server TLSConfig
```
