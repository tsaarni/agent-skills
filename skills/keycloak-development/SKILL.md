---
name: keycloak-development
description: Development environment, building, debugging, testing, Kubernetes deployment, and admin REST API for managing realms, users, clients, LDAP federation, identity providers, OAuth2/OIDC, authorization services and UMA.
---

# Keycloak Development

## Important Rules You MUST Follow

- Follow the commands and instructions carefully, unless you are asked to do otherwise, or you have specific reason to do otherwise.
- Always search the documentation before diverging from the instructions.
- Assume Keycloak is already running; check for it or start it only when explicitly asked to do so.
- Infer names and IDs from the context when possible. For example, if a realm name is required and there is only one realm, infer that realm name. If there are multiple realms, ask for clarification or use `master` as default.
- Use `http://keycloak.127-0-0-1.nip.io:8080` as the default base URL for API requests if the user has not specified a different URL for Keycloak.
- Replace `<ADMIN_TOKEN>` with the actual token value obtained from the token endpoint using command `$(curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/master/protocol/openid-connect/token -d 'username=admin&password=admin&grant_type=password&client_id=admin-cli' | jq -r .access_token)`.
- Maven/mvnd builds produce huge output. Always use `-l <logfile>` to redirect build output to a file, then `tail` the log to check the result:
  ```bash
  mvnd -l /tmp/build.log clean install ...; tail -20 /tmp/build.log  # success: tail -20, failure: tail -80
  ```

## Building Keycloak

### Full Build
```bash
mvnd -l /tmp/kc-build.log clean install -DskipTestsuite -DskipExamples -DskipTests; tail -20 /tmp/kc-build.log # Parallel build with mvnd (faster)
mvn -l /tmp/kc-build.log clean install -DskipTestsuite -DskipExamples -DskipTests; tail -20 /tmp/kc-build.log # Regular maven build (slower but use if you have issues with mvnd)
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
runagent delete keycloak >/dev/null 2>&1
runagent run -n keycloak -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
  -Dkc.config.built=true -Dquarkus.args="start-dev --db=dev-mem" \
  -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://localhost:8080/realms/master 2>/dev/null && { echo "ready after ${i}s"; break; }; sleep 1; done
```

With PostgreSQL (requires running docker compose with PostgreSQL container):
```bash
runagent delete keycloak >/dev/null 2>&1
runagent run -n keycloak -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
  -Dkc.config.built=true \
  -Dquarkus.args="start-dev -Dkc.db=postgres -Dkc.db-url=jdbc:postgresql://localhost/keycloak -Dkc.db-username=keycloak -Dkc.db-password=keycloak" \
  -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin
for i in $(seq 1 90); do curl -s -o /dev/null -m 2 http://localhost:8080/realms/master 2>/dev/null && { echo "ready after ${i}s"; break; }; sleep 1; done
```

### Per-Category Debug Logging
```bash
runagent delete keycloak >/dev/null 2>&1
runagent run -n keycloak -- ./mvnw -f quarkus/server/pom.xml compile quarkus:dev \
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

Use provided docker compose wrapper [./scripts/docker-compose-wrapper.sh](./scripts/docker-compose-wrapper.sh) to start test server containers.

### PostgreSQL

```bash
<SKILL_ROOT>./scripts/docker-compose-wrapper.sh up -d postgres

# Connect to PostgreSQL CLI
docker exec -it keycloak-postgres-1 psql --username=keycloak
```

### OpenLDAP

```bash
<SKILL_ROOT>./scripts/docker-compose-wrapper.sh up -d openldap
```

## Admin REST API

### URL Structure

Admin API URLs follow the pattern:
- `http://keycloak-host:port/admin/realms/{realm-name}/{resource}`
- Example: `http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users`

The `{realm-name}` in the URL specifies which realm you're managing. Use `master` for the master realm or your custom realm name (e.g., `my-realm`, `example-realm`).

### Realm Management

Get realm:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Create realm:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/ \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "id": "example-realm",
    "realm": "example-realm",
    "enabled": true,
    "adminEventsEnabled": true
  }'
```

Update realm settings:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "ssoSessionIdleTimeout": 86400,
    "accessTokenLifespan": 86400
  }'
```

Delete realm:

```bash
curl -s -X DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm \
  -H "Authorization: bearer <ADMIN_TOKEN>"
```

Export realm configuration:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/partial-export \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

### User Management

List users:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Get user by username:

```bash
curl -s "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users?username=joe" \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Get user by ID:

```bash
id=$(curl -s "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users?username=joe" \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq -r '.[0].id')
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$id \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Create user with password:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "username": "joe",
    "enabled": true,
    "credentials": [{"type": "password", "value": "joe", "temporary": false}]
  }'
```

NOTE: This minimal example omits `email`, `firstName`, `lastName`. The default user profile in non-master realms requires these fields — without them, login fails with `"Account is not fully set up"`. The master realm is more lenient. Prefer the full example below for users that need to log in.

Create user with full details:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "username": "joe",
    "enabled": true,
    "email": "joe@example.com",
    "firstName": "Joe",
    "lastName": "Average",
    "emailVerified": true,
    "credentials": [{"type": "password", "value": "joe", "temporary": false}]
  }'
```

Create user with custom attributes:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "username": "ldapuser",
    "enabled": true,
    "firstName": "Ldap",
    "lastName": "User",
    "attributes": {"telephoneNumber": ["1", "2", "3"]}
  }'
```

Delete user:

```bash
curl -s -X DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$id \
  -H "Authorization: bearer <ADMIN_TOKEN>"
```

Update user attributes:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"firstName": "John", "lastName": "Smith"}'
```

Update user custom attributes:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"attributes": {"attr3": "val3"}}'
```

### Role Management

Get client roles:

```bash
MASTER_REALM_ID=$(curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
| jq -r '.[] | select(.clientId=="master-realm") | .id')

curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients/$MASTER_REALM_ID/roles \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Assign client role to user:

```bash
VIEW_CLIENTS_ROLE=$(curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients/$MASTER_REALM_ID/roles \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
| jq -c '.[] | select(.name=="view-clients")')

curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/role-mappings/clients/$MASTER_REALM_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "[$VIEW_CLIENTS_ROLE]"
```

Get user's realm role mappings:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/role-mappings/realm \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Get user's groups:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/groups \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

### Client Management

List all clients:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Get client by clientId:

```bash
curl -s "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients?clientId=foo" \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Get client by ID:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Create confidential client:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "clientId": "foo",
    "publicClient": false,
    "redirectUris": ["http://localhost"],
    "serviceAccountsEnabled": true,
    "secret": "mysecret"
  }'
```

Create client with authorization services:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "clientId": "example-client",
    "publicClient": false,
    "secret": "example-secret",
    "directAccessGrantsEnabled": true,
    "rootUrl": "http://localhost:18080",
    "redirectUris": ["http://localhost:18080/*"],
    "authorizationServicesEnabled": true,
    "serviceAccountsEnabled": true
  }'
```

Get client secret:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/client-secret \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Delete client:

```bash
curl -s -X DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>"
```

### Client Scopes

Create client scope:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/client-scopes \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"name": "my-scope", "protocol": "openid-connect"}'
```

Supported protocols: `openid-connect`, `saml`, `docker-v2`, `oid4vc`

### LDAP Federation

Create LDAP user federation:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/components \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "name": "ldap",
    "providerId": "ldap",
    "providerType": "org.keycloak.storage.UserStorageProvider",
    "config": {
      "connectionUrl": ["ldap://localhost:389"],
      "usersDn": ["ou=users,o=example"],
      "bindDn": ["cn=ldap-admin,ou=users,o=example"],
      "bindCredential": ["ldap-admin"],
      "authType": ["simple"],
      "editMode": ["WRITABLE"],
      "vendor": ["other"],
      "usernameLDAPAttribute": ["uid"],
      "rdnLDAPAttribute": ["uid"],
      "uuidLDAPAttribute": ["entryUUID"],
      "userObjectClasses": ["inetOrgPerson, organizationalPerson"],
      "importEnabled": ["true"],
      "syncRegistrations": ["true"]
    }
  }'
```

Get LDAP configuration:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/components/$COMPONENT_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

List user storage providers:

```bash
curl -s "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/components?parent=master&type=org.keycloak.storage.UserStorageProvider" \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Test LDAP connection:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/testLDAPConnection \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d @rest-requests/test-ldap-authentication.json
```

### Identity Provider (IDP) Brokering

Create OIDC identity provider:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/identity-provider/instances \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "alias": "oidc-keycloak",
    "providerId": "oidc",
    "config": {
      "clientId": "my-client-id",
      "clientSecret": "my-secret",
      "authorizationUrl": "https://another-keycloak:8443/realms/other-realm/protocol/openid-connect/auth",
      "tokenUrl": "https://another-keycloak:8443/realms/other-realm/protocol/openid-connect/token",
      "userInfoUrl": "https://another-keycloak:8443/realms/other-realm/protocol/openid-connect/userinfo",
      "jwksUrl": "https://another-keycloak:8443/realms/other-realm/protocol/openid-connect/certs",
      "logoutUrl": "https://another-keycloak:8443/realms/other-realm/protocol/openid-connect/logout",
      "issuer": "https://another-keycloak:8443/realms/other-realm",
      "redirectUri": "https://keycloak.127-0-0-1.nip.io:8443/realms/example-realm/broker/oidc-keycloak/endpoint"
    }
  }'
```

Get IDP configuration:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/identity-provider/instances/oidc-keycloak \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

### Authorization Services (UMA)

Get client ID for authorization configuration:

```bash
CLIENT_ID=$(curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
| jq -r '.[] | select(.clientId=="example-client") | .id')
```

Create protected resource:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/resource \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "name": "example-resource",
    "type": "urn:resource-server:example-resource",
    "uris": ["/"],
    "scopes": [{"name": "GET"}]
  }'
```

Create user policy:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/policy/user \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"name": "joe-policy", "users": ["joe"]}'
```

Create resource permission:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/permission/resource \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "name": "example-resource-permission",
    "resources": ["example-resource"],
    "policies": ["joe-policy"]
  }'
```

Export authorization settings:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/settings \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

### Token Operations

Get user token with password grant:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d 'grant_type=password&username=joe&password=joe&scope=openid&client_id=example-client&client_secret=example-secret' | jq .
```

Get client token with client credentials:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d 'grant_type=client_credentials&client_id=foo&client_secret=mysecret' | jq .
```

Exchange authorization code for token:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d "grant_type=authorization_code&code=$AUTHORIZATION_CODE&client_id=example-client&client_secret=example-secret&redirect_uri=http://localhost:18080/foo" | jq .
```

Refresh token:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d "refresh_token=$REFRESH_TOKEN&grant_type=refresh_token&scope=openid&client_id=example-client&client_secret=example-secret" | jq .
```

UMA ticket grant:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:uma-ticket&claim_token=$ID_TOKEN&claim_token_format=http://openid.net/specs/openid-connect-core-1_0.html#IDToken&client_id=example-client&client_secret=example-secret&audience=example-client&permission=example-resource#GET" | jq .
```

UMA ticket grant with decision response:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  -d "grant_type=urn:ietf:params:oauth:grant-type:uma-ticket&claim_token=$ID_TOKEN&claim_token_format=http://openid.net/specs/openid-connect-core-1_0.html#IDToken&client_id=example-client&client_secret=example-secret&audience=example-client&permission=example-resource#GET&response_mode=decision" | jq .
```

### Discovery Endpoints

Get OpenID configuration:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/realms/master/.well-known/openid-configuration | jq .
```

Get JWKS (public keys):

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/realms/master/protocol/openid-connect/certs | jq .
```

### Admin Events

Get admin events (requires "save admin events" enabled):

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/admin-events \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

### REST API Testing Tips

#### Short-lived access tokens

Set `accessTokenLifespan` to 1 second, then test token expiry:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"accessTokenLifespan": 1}'
```

Get a token and use it immediately (200), wait 2 seconds and try again (401 with `"The access token is outside its validity period"`).

NOTE: The admin token you used to set this also gets 1-second lifespan. You must get a fresh admin token to reset it back:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"accessTokenLifespan": 300}'
```

#### Invalidate all tokens with not-before policy

Set `notBefore` to reject all tokens issued before a given time:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json "{\"notBefore\": $(date +%s)}"
```

Tokens with `iat` < `notBefore` are rejected with `"invalid_token"`. There must be a time gap between the old token's issuance and the `notBefore` value.

Reset:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"notBefore": 0}'
```

#### Brute force lockout

Enable brute force on an existing realm with a low threshold:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"bruteForceProtected": true, "failureFactor": 2}'
```

Or create a realm with brute force protection enabled:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/ \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "id": "example-realm",
    "realm": "example-realm",
    "enabled": true,
    "bruteForceProtected": true
  }'
```

Fail login twice (user must exist):

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/master/protocol/openid-connect/token \
  -d 'grant_type=password&username=joe&password=wrong&client_id=admin-cli'
```

Check brute force status:

```bash
curl -s http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/attack-detection/brute-force/users/$USER_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq .
```

Response shows `"numFailures"`, `"disabled": true`. Even correct password is rejected while locked.

Unlock user:

```bash
curl -s -X DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/attack-detection/brute-force/users/$USER_ID \
  -H "Authorization: bearer <ADMIN_TOKEN>"
```

#### Throwaway test realm

Create a realm, run tests, delete it:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/ \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"id": "test-realm", "realm": "test-realm", "enabled": true}'
```

NOTE: When creating users in a new realm, include `email`, `firstName`, `lastName` — the default user profile requires them. Without these fields, login fails with `{"error":"invalid_grant","error_description":"Account is not fully set up"}`.

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/test-realm/users \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "username": "testuser",
    "enabled": true,
    "email": "test@example.com",
    "firstName": "Test",
    "lastName": "User",
    "emailVerified": true,
    "credentials": [{"type": "password", "value": "testpass", "temporary": false}]
  }'
```

Delete the realm when done (removes all users, clients, config):

```bash
curl -s -X DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/test-realm \
  -H "Authorization: bearer <ADMIN_TOKEN>"
```

#### Token introspection

Token introspection requires the introspecting client to be in the token's `aud` claim. Use an audience protocol mapper to add the resource server client to the token audience.

Create the app client and resource server client:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "clientId": "my-app",
    "publicClient": false,
    "secret": "my-app-secret",
    "directAccessGrantsEnabled": true,
    "serviceAccountsEnabled": true
  }'

curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "clientId": "my-resource-server",
    "publicClient": false,
    "secret": "rs-secret",
    "serviceAccountsEnabled": true
  }'
```

Add audience mapper to the app client:

```bash
APP_UUID=$(curl -s "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients?clientId=my-app" \
  -H "Authorization: bearer <ADMIN_TOKEN>" | jq -r '.[0].id')

curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$APP_UUID/protocol-mappers/models \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{
    "name": "resource-server-audience",
    "protocol": "openid-connect",
    "protocolMapper": "oidc-audience-mapper",
    "config": {
      "included.client.audience": "my-resource-server",
      "id.token.claim": "false",
      "access.token.claim": "true",
      "introspection.token": "true"
    }
  }'
```

Introspect a token using the resource server client:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token/introspect \
  -d "token=$ACCESS_TOKEN&client_id=my-resource-server&client_secret=rs-secret" | jq .
```

Returns `"active": true` for valid tokens, `"active": false` for expired or invalid tokens.

NOTE: If introspection returns `{"active":false}` for a valid token, check that the introspecting client is in the token's `aud` claim.

#### Decode tokens offline

Decode JWT payload with `jq`:

```bash
echo $ACCESS_TOKEN | jq -R 'split(".")[1] | @base64d | fromjson'
```

Decode JWT header:

```bash
echo $ACCESS_TOKEN | jq -R 'split(".")[0] | @base64d | fromjson'
```

Check if a token is expired:

```bash
echo $ACCESS_TOKEN | jq -R 'split(".")[1] | @base64d | fromjson | .exp - now | if . > 0 then "Expires in \(. | round) seconds" else "Expired \((. * -1) | round) seconds ago" end'
```

#### Session idle timeout

Set `ssoSessionIdleTimeout` to a short value:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"ssoSessionIdleTimeout": 10}'
```

After 10 seconds of inactivity, refresh token fails with `{"error":"invalid_grant","error_description":"Token is not active"}`.

Reset:

```bash
curl -s -X PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm \
  -H "Authorization: bearer <ADMIN_TOKEN>" \
  --json '{"ssoSessionIdleTimeout": 1800}'
```

#### Logout and verify

Logout using the refresh token:

```bash
curl -s -X POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/logout \
  -d "refresh_token=$REFRESH_TOKEN&client_id=example-client&client_secret=example-secret"
```

After logout, refresh token is rejected with `{"error":"invalid_grant","error_description":"Session not active"}`.

NOTE: The access token (JWT) may still pass local validation until it expires, since it is self-contained. Only the refresh token and server-side session are invalidated immediately.

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
- [HTTPie Documentation](https://httpie.io/docs/cli)
- [Quarkus Guides](https://quarkus.io/guides/)
