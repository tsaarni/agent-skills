---
name: keycloak-development
description: "Keycloak: build, debug, test, deploy to Kubernetes, admin REST API, login flows and database queries (Python drivers for pysess)."
---

# Keycloak Development

## Important Rules You MUST Follow

- Follow the commands and instructions carefully, unless you are asked to do otherwise, or you have specific reason to do otherwise.
- Always search the documentation before diverging from the instructions.
- Assume Keycloak is already running; check for it or start it only when explicitly asked to do so.
- Infer names and IDs from the context when possible. For example, if a realm name is required and there is only one realm, infer that realm name. If there are multiple realms, ask for clarification or use `master` as default.
- Use `http://keycloak.127-0-0-1.nip.io:8080` as the default base URL for API requests if the user has not specified a different URL for Keycloak.
- Call REST APIs and drive login flows with the Python driver in a `pysess` session, see [REST API and Login Flows](#rest-api-and-login-flows-python-driver).
- Maven/mvnd builds produce huge output. Run builds under `runagent` and use `runagent wait` to block until completion, then check the tail of the log:
  ```bash
  runagent run -n kc-build -- mvnd clean install ...; runagent wait kc-build; runagent logs kc-build --last 20
  ```
  Run `runagent delete kc-build` once the logs are no longer needed.

## Building Keycloak

### Full Build
```bash
runagent run -n kc-build -- mvnd clean install -DskipTestsuite -DskipExamples -DskipTests; runagent wait kc-build; runagent logs kc-build --last 20 # Parallel build with mvnd (faster)
runagent run -n kc-build -- mvn clean install -DskipTestsuite -DskipExamples -DskipTests; runagent wait kc-build; runagent logs kc-build --last 20 # Regular maven build (slower but use if you have issues with mvnd)
```

Always use `-DskipTests`, not `-Dmaven.test.skip=true`. `-Dmaven.test.skip=true` skips test compilation, which also skips producing test-jars that some modules depend on.

When switching between commits or branches, stop the mvnd daemon before rebuilding to avoid stale classloader state:
```bash
mvnd --stop
```

Note: `clean` wipes `quarkus/server/target/kc/data/` which contains:
- `h2/` — H2 database files (when using `--db=dev-file` or default)
- `password-blacklists/` — password denylist bloom files
- `log/` — HTTP access logs
- `import/` — realm import files

### Extracted Distribution
Full build produces `quarkus/dist/target/keycloak-*.tar.gz`. Extract once to get `kc.sh` and client tools (`kcadm.sh`, `kcreg.sh`) for use alongside quarkus:dev:

```bash
mkdir -p /tmp/keycloak-dev-dist
tar xzf quarkus/dist/target/keycloak-*.tar.gz -C /tmp/keycloak-dev-dist --strip-components=1
```

Examples:
```bash
/tmp/keycloak-dev-dist/bin/kc.sh tools build-password-denylist /path/to/denylist.txt
/tmp/keycloak-dev-dist/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user admin --password admin
```

## Running Keycloak on Command Line

### Development Mode
```bash
runagent delete kc-dev >/dev/null 2>&1
runagent run -n kc-dev -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
  -Dkc.config.built=true -Dquarkus.args="start-dev --db=dev-mem" \
  -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://localhost:8080/realms/master 2>/dev/null && { echo "ready after ${i}s"; break; }; sleep 1; done
```

With PostgreSQL (requires running docker compose with PostgreSQL container):
```bash
runagent delete kc-dev >/dev/null 2>&1
runagent run -n kc-dev -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
  -Dkc.config.built=true \
  -Dquarkus.args="start-dev -Dkc.db=postgres -Dkc.db-url=jdbc:postgresql://localhost/keycloak -Dkc.db-username=keycloak -Dkc.db-password=keycloak" \
  -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://localhost:8080/realms/master 2>/dev/null && { echo "ready after ${i}s"; break; }; sleep 1; done
```

### Per-Category Debug Logging
```bash
runagent delete kc-dev >/dev/null 2>&1
runagent run -n kc-dev -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
  -Dkc.config.built=true \
  '-Dquarkus.args=start-dev --db=dev-mem --log-level=org.keycloak.services.resources.admin.AdminRoot:debug,org.keycloak.services.managers.AuthenticationManager:debug' \
  -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://localhost:8080/realms/master 2>/dev/null && { echo "ready after ${i}s"; break; }; sleep 1; done
```

The `--log-level` flag accepts comma-separated `category:level` pairs. This enables DEBUG for specific classes without the noise of global debug logging.

## Debugging in VS Code

### Prepare VS Code Configuration

```bash
mkdir -p .vscode
cat > .vscode/launch.json <<EOF
{
    "version": "0.2.0",
    "configurations": [
        {
            "type": "java",
            "name": "Debug Quarkus (keycloak-junit5)",
            "request": "launch",
            "mainClass": "org.keycloak.Keycloak",
            "projectName": "keycloak-junit5",

            "args": "start-dev --verbose",

            // All configuration options:
            // - https://www.keycloak.org/server/all-config
            "env": {
                "KC_HOSTNAME": "keycloak.127.0.0.1.nip.io",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": "admin",
                "KC_DB_URL": "jdbc:h2:./quarkus/dist/target/keycloakdb;NON_KEYWORDS=VALUE;AUTO_SERVER=TRUE",
                // "KC_DB_URL": "jdbc:postgresql://localhost:5432/keycloak",
                // "KC_DB_USERNAME": "keycloak",
                // "KC_DB_PASSWORD": "keycloak",
            },
        },
    ]
}
EOF

cat > .vscode/settings.json <<EOF
{
  "eslint.workingDirectories": ["js"],
  "java.compile.nullAnalysis.mode": "automatic",
  "java.debug.settings.onBuildFailureProceed": true,
  "java.jdt.ls.vmargs": "-Xmx16G -Xms100m -Djava.import.generatesMetadataFilesAtProjectRoot=false",
  "java.configuration.maven.userSettings": "${workspaceFolder}/maven-settings.xml",
  "java.import.gradle.enabled": false,
  "java.test.config": {
    "vmArgs": ["-Djava.util.logging.manager=org.jboss.logmanager.LogManager"]
  }
}
EOF
```

### Debug Workflow
1. Build with maven: `mvnd clean install -DskipTestsuite -DskipExamples -DskipTests`
2. Start VS Code and wait for build
3. Run build again without clean: `mvn install -DskipTestsuite -DskipExamples -DskipTests` to make sure the javascript resources are built.
4. Launch debug session from VS Code using `Debug Quarkus (keycloak-junit5)` configuration.


### UI Development
1. Add following environment variable to launch.json: `"KC_ADMIN_VITE_URL": "http://localhost:5174"`
2. Remove `KC_HOSTNAME` environment variable from launch.json if it exists
3. Start Keycloak in VS Code debugger using `Debug Quarkus (keycloak-junit5)` configuration.
4. Run UI separetely under Vite:
   ```bash
   cd js
   pnpm --filter keycloak-admin-ui run dev
   ```
5. Access at http://127.0.0.1:8080/

## Testing

### Running Unit Tests
```bash
# Build first
mvn -l /tmp/kc-build.log clean install -DskipTests; tail -20 /tmp/kc-build.log
(cd distribution; mvn -l /tmp/kc-dist-build.log clean install; tail -20 /tmp/kc-dist-build.log)

# Run specific test
mvn -l /tmp/kc-test.log clean install -Pauth-server-quarkus-f testsuite/integration-arquillian/pom.xml \
  -Dtest=org.keycloak.testsuite.federation.storage.UserStorageDirtyDeletionUnsyncedImportTest#testMembersWhenCachedUsersRemovedFromBackend \
  -Dkeycloak.logging.level=debug; tail -20 /tmp/kc-test.log

# Run all tests in package (recursively)
mvn -l /tmp/kc-test.log clean install -Pauth-server-quarkus -Dtest=org.keycloak.testsuite.federation.ldap.** -Dkeycloak.logging.level=debug; tail -20 /tmp/kc-test.log
```

### New JUnit5 Test Framework
```bash
# Configure logging
cat > .env.test <<EOF
KC_TEST_LOG_LEVEL=INFO
KC_TEST_CONSOLE_COLOR=true
KC_TEST_LOG_CATEGORY__MANAGED_KEYCLOAK__LEVEL=INFO
KC_TEST_LOG_CATEGORY__ORG_KEYCLOAK_VAULT__LEVEL=DEBUG
KC_TEST_LOG_CATEGORY__TESTINFO__LEVEL=DEBUG
KC_TEST_LOG_CATEGORY__ORG_APACHE_HTTP__LEVEL=DEBUG
EOF

# Run tests
mvn -l /tmp/kc-test.log -f tests/pom.xml test -Dtest=SMTPConnectionVaultTest; tail -20 /tmp/kc-test.log
mvn -l /tmp/kc-test.log -f tests/pom.xml test -Dtest=ClientVaultTest; tail -20 /tmp/kc-test.log
```

## Linting / Formatting

Java formatting is checked with Spotless:
```bash
./mvnw -Pdocs,distribution,operator spotless:check   # Check formatting
./mvnw spotless:apply                                 # Auto-fix formatting
```

JavaScript linting (from `js/` directory):
```bash
pnpm lint
```

## Database Management

### PostgreSQL

Query the Keycloak database from a `pysess` session with [scripts/psql_driver.py](./scripts/psql_driver.py), in kind, docker compose or with a local psql: [references/database.md](./references/database.md).

### H2 Database
```bash

# Launch H2 web console for Quarkus distribution
h2_version=$(find ~/.m2/repository/com/h2database/h2/ -maxdepth 1 | sort -V | tail -n 1)
test -e ./quarkus/dist/target/keycloakdb.mv.db && \
java -cp $h2_version/*.jar org.h2.tools.Console \
  -url "jdbc:h2:file:./quarkus/dist/target/keycloakdb;AUTO_SERVER=TRUE" \
  -user "" -password "" \
  -properties "h2.consoleTimeout=9999999999"

# Remove H2 database
rm ./quarkus/server/target/kc/data/h2/keycloakdb*
```

## Test Servers

### PostgreSQL

Start it with the docker compose wrapper [./scripts/docker-compose-wrapper.sh](./scripts/docker-compose-wrapper.sh):

```bash
<skill-dir>/scripts/docker-compose-wrapper.sh up -d postgres
```

Query it with the psql driver, see [references/database.md](./references/database.md).

### OpenLDAP

To run OpenLDAP in Kind or Docker (image, directory content, certificates, TLS keylog), read memory record `OpenLdapInContainer`.

## REST API and Login Flows (Python driver)

Call the Admin REST API, token endpoints and login pages from a `pysess` session (see the [pysess skill](../pysess/SKILL.md)) with [scripts/kc_driver.py](./scripts/kc_driver.py) (stdlib only). It keeps the admin token, cookies and OTP state between steps. `Keycloak` works like `requests` with Keycloak REST paths; `SimpleBrowser` posts the login pages' forms like a browser, without JavaScript.

```bash
pysess new -n kc <skill-dir>/scripts/kc_driver.py
pysess exec kc <<'EOF'
api()                                              # the driver API, one line per call; details: help(name)
kc = Keycloak("http://keycloak.127-0-0-1.nip.io:8080")   # admin/admin; paths under /admin/ get the admin token
realm = "example-realm"
A = f"/admin/realms/{realm}"                       # Admin REST base of the realm
T = f"/realms/{realm}/protocol/openid-connect"     # OIDC endpoints of the realm
c = {"client_id": "example-client", "client_secret": "example-secret"}
kc.post("/admin/realms", json={"realm": realm, "enabled": True}).expect(201)
kc.post(f"{A}/clients", json={"clientId": "example-client", "secret": "example-secret",
        "redirectUris": ["http://localhost/cb"], "directAccessGrantsEnabled": True}).expect(201)
user_id = kc.post(f"{A}/users", json={"username": "joe", "enabled": True, "email": "joe@example.com",
    "firstName": "Joe", "lastName": "Average",
    "credentials": [{"type": "password", "value": "joe", "temporary": False}]}).expect(201).id
t = kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "joe", **c})
decode(t)                                          # claims of the access token
b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret")   # <Page login-login 200 ...>
b.submit(username="joe", password="joe")           # <Callback http://localhost/cb {... 'code': ...}>
b.tokens()
EOF
```

Responses never raise on 4xx/5xx; their `repr` shows status, path and the start of the body. Check setup steps with `.expect(...)` so that a failed step stops there. After editing `kc_driver.py`, load it again with `pysess exec kc <file>` and create the objects again.

Read before writing code for:
- Realms, users, roles, groups, clients, client scopes, token grants, introspection, logout, UMA, events, token lifespan / not-before / brute force tests, several nodes: [references/rest-api.md](./references/rest-api.md)
- Login pages in the browser (page ids and their fields), PKCE and authorization request tests, required and application-initiated actions, update profile, reset password, OTP and recovery codes, IdP brokering: [references/login-flows.md](./references/login-flows.md)
- Components, LDAP federation, LDAP mappers and sync: [references/ldap.md](./references/ldap.md)
- The Keycloak database (stored credentials and OTP secrets, local copies of LDAP users, component config, SQL run during a flow) with the psql driver [scripts/psql_driver.py](./scripts/psql_driver.py), in kind, docker compose or local PostgreSQL: [references/database.md](./references/database.md)

### Gotchas

- `PUT /users/{id}` replaces the user: a partial body clears `firstName`, `email` etc. Update users and components with GET, modify, PUT. Realm `PUT` merges fields, except the OTP policy.
- A realm `PUT` with only some OTP policy fields (e.g. only `otpPolicyType`) resets the others (algorithm null, digits 0); enrolment then fails with 500 "null algorithm name". Send all `otpPolicy*` fields.
- Users in non-master realms need `email`, `firstName`, `lastName`; otherwise login fails with `"Account is not fully set up"` (update-profile page in the browser).
- Custom user attributes are silently dropped unless the realm user profile has `"unmanagedAttributePolicy": "ENABLED"` (or declares the attribute).
- Every realm has built-in clients `account`, `account-console`, `admin-cli`, `broker`, `realm-management`, `security-admin-console`; creating one with these names returns 409.
- Introspection returns `"active": false` unless the introspecting client is in the token's `aud` (add an audience mapper).
- A token or SSO cookie from one URL is rejected on another URL of the same server (issuer check). For several nodes, use the same `url` and select the node with `connect_to`.
- Keycloak rejects a reused OTP code. `TOTP.now()` tracks the codes it returned: keep one `TOTP` object per credential; a new one may repeat a used code.
- The same flow shows different pages depending on state: with `prompt=login` the login form has only `password` if an SSO session exists, and `username` + `password` otherwise. Check with `b.expect(page_id)` before submitting an assumed page.
- `b.submit(**fields)` sends the page values of fields you do not pass (an empty username is sent as empty).
- LDAP mappers get `read.only` from `editMode` when the provider is created; switching to `WRITABLE` later does not change them, and writes stay in the Keycloak database.
- A 401 from `userinfo` or Admin REST has an empty body.

## Kubernetes Deployment

### Setup Kind Cluster
```bash
# Create cluster
kind delete cluster --name keycloak
kind create cluster --config $HOME/work/devenvs/keycloak/configs/kind-cluster-config.yaml --name keycloak

# Install Contour ingress
kubectl apply -f https://projectcontour.io/quickstart/contour.yaml

# Deploy PostgreSQL and Keycloak
kubectl apply -f $HOME/work/devenvs/keycloak/manifests/postgresql.yaml
kubectl apply -f $HOME/work/devenvs/keycloak/manifests/keycloak-26.yaml

# Create secrets for certificates
kubectl create secret tls keycloak-external \
  --cert=$HOME/work/devenvs/keycloak/certs/keycloak-server.pem \
  --key=$HOME/work/devenvs/keycloak/certs/keycloak-server-key.pem \
  --dry-run=client -o yaml | kubectl apply -f -

# View logs
kubectl logs statefulset/keycloak
```

### Debugging in Kind
With `KC_DEBUG=true` in the container env, Keycloak listens for JDWP on port 8787, bound to localhost in the pod. Forward it and attach with the JDI driver from the [java-debugging skill](../java-debugging/SKILL.md):
```bash
runagent run -n kc0-jdwp -- kubectl port-forward pod/keycloak-0 5005:8787
```

### Build Custom Docker Container
```bash
cd /path/to/keycloak
mvnd clean install -DskipTestsuite -DskipExamples -DskipTests
cp ./quarkus/dist/target/keycloak-*.tar.gz quarkus/container
docker build --build-arg KEYCLOAK_DIST=keycloak-*.tar.gz -f quarkus/container/Dockerfile -t localhost/keycloak:latest quarkus/container
kind load docker-image --name keycloak localhost/keycloak:latest
```

## Network Debugging
```bash
# Capture LDAP traffic from OpenLDAP container
sudo nsenter --target $(pidof slapd) --net wireshark -f "port 389 or port 636" -k

# Capture from Keycloak
sudo nsenter --target $(pgrep -f quarkus) --net wireshark -f "port 8080" -Y http -k
```

## Documentation

### Build Documentation
```bash
./mvnw -l /tmp/kc-docs.log clean install -am -pl docs/documentation/dist -Pdocumentation; tail -20 /tmp/kc-docs.log
kde-open ./docs/documentation/server_admin/target/generated-docs/index.html
```

## Useful URLs

- Dev console: http://keycloak.127-0-0-1.nip.io:8080/q/dev/
- Keycloak: http://keycloak.127-0-0-1.nip.io:8080/
- HTTPS: https://keycloak.127-0-0-1.nip.io:8443/
- Admin console: http://keycloak.127-0-0-1.nip.io:8080/admin/master/console/
- Well-known config: http://keycloak.127-0-0-1.nip.io:8080/realms/master/.well-known/openid-configuration

## References

- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [Server Configuration](https://www.keycloak.org/server/all-config)
- [Keycloak Admin REST API Documentation](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
- [Quarkus Guides](https://quarkus.io/guides/)
