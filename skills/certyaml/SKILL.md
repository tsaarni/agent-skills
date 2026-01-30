---
name: certyaml
description: Generate x509 certificates and PKI hierarchies for test environments using YAML manifests or Go API. Use for TLS client and server testing.
---

# Certyaml - X509 Certificate Generation

## Command Line Usage

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

**Key fields**: `subject` (DN, must be unique), `issuer` (references earlier cert's subject), `ca` (bool), `sans` (list), `filename` (output file basename, defaults to CN), `key_type` (EC/RSA/ED25519), `key_size`, `expires` (duration), `not_before`/`not_after` (RFC3339), `key_usages`, `ext_key_usages`, `crl_distribution_points`, `revoked`.

**Important**: Certificate order matters - issuer must be defined before it's referenced.

**Full field reference**: https://github.com/tsaarni/certyaml

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

## Kubernetes Secrets

Upload/update certificates as Kubernetes secrets using `--dry-run=client -o yaml | kubectl apply` for idempotent operations:

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

Import: `github.com/tsaarni/certyaml`

**Main type**: `Certificate` struct with fields `Subject`, `SubjectAltNames`, `Issuer` (pointer to CA Certificate, nil for self-signed), `IsCA`, `KeyType`, `Expires`, `NotBefore`/`NotAfter`, `KeyUsage`, `ExtKeyUsage`.

**Key methods**: `PEM()`, `TLSCertificate()`, `X509Certificate()`, `WritePEM()`, `Generate()`.

**CRL type**: `CRL` struct. Methods: `Add(cert)`, `PEM()`, `WritePEM()`.

**Full API docs**: https://pkg.go.dev/github.com/tsaarni/certyaml
**Code examples**: https://github.com/tsaarni/certyaml/blob/master/examples/go-api/main.go

```go
ca := certyaml.Certificate{Subject: "cn=ca"}
server := certyaml.Certificate{Subject: "cn=server", SubjectAltNames: []string{"DNS:localhost"}, Issuer: &ca}
tlsCert, err := server.TLSCertificate()  // Use in http.Server TLSConfig
```

## Patterns

- **Mandatory field**: Only `subject` is required
- **Root CA (self-signed)**: Omit `issuer` field
- **Intermediate CA**: Set `issuer: cn=parent-ca`, `ca: true`
- **End-entity certificate**: Set `issuer: cn=ca-name`, `ca: false` (default)
- **Server certificate**: Add `ext_key_usages: [ServerAuth]`
- **Client certificate**: Add `ext_key_usages: [ClientAuth]`
- **Certificate Revocation**: Set `revoked: true` to include cert in issuer's CRL file
- **Certificate chains**: End-entity PEM files include full chain (excluding root CA)
