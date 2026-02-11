---
name: keycloak-development
description: Setup and work with Keycloak development environment including building, debugging, testing, and working with databases, LDAP and Kubernetes.
---

# Keycloak Development Environment

## Building Keycloak

### Full Build
```bash
# Parallel build with mvnd
mvnd clean install -DskipTestsuite -DskipExamples -DskipTests
```

### Build Single Module
```bash
# After editing code, rebuild only affected module
mvn install -DskipTests -pl federation/ldap/
mvn -f model/pom.xml install -DskipTests
mvn -f js/pom.xml install
mvn -f themes/pom.xml install
```

## Running Keycloak on Command Line

### Development Mode
```bash
./mvnw -f quarkus/server/pom.xml compile quarkus:dev -Dkc.config.built=true -Dquarkus.args="start-dev -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin"
./mvnw -f quarkus/server/pom.xml compile quarkus:dev -Dkc.config.built=true -Dquarkus.args="start-dev -Dkc.bootstrap-admin-username=admin -Dkc.bootstrap-admin-password=admin -Dkc.db=postgres -Dkc.db-url=jdbc:postgresql://localhost/keycloak -Dkc.db-username=keycloak -Dkc.db-password=keycloak"
```

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
mvn clean install -DskipTests
(cd distribution; mvn clean install)

# Run specific test
mvn clean install -Pauth-server-quarkus-f testsuite/integration-arquillian/pom.xml \
  -Dtest=org.keycloak.testsuite.federation.storage.UserStorageDirtyDeletionUnsyncedImportTest#testMembersWhenCachedUsersRemovedFromBackend \
  -Dkeycloak.logging.level=debug

# Run all tests in package (recursively)
mvn clean install -Pauth-server-quarkus -Dtest=org.keycloak.testsuite.federation.ldap.** -Dkeycloak.logging.level=debug
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
mvn -f tests/pom.xml test -Dtest=SMTPConnectionVaultTest
mvn -f tests/pom.xml test -Dtest=ClientVaultTest
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

### PostgreSQL
```bash
scripts/docker-compose-wrapper.sh up -d postgres

# Connect to PostgreSQL CLI
docker exec -it keycloak-postgres-1 psql --username=keycloak
```

### OpenLDAP

```bash
scripts/docker-compose-wrapper.sh up -d openldap
```


## Kubernetes Deployment

### Setup Kind Cluster
```bash
# Create cluster
kind delete cluster --name keycloak
kind create cluster --config $HOME/work/devenvs/keycloak/configs/kind-cluster-config.yaml --name keycloak

# Install Contour ingress
kubectl apply -f https://projectcontour.io/quickstart/contour.yaml

# Deploy PostgreSQL and Keycloak
kubectl apply -f  $HOME/work/devenvs/keycloak/manifests/postgresql.yaml
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


### Network Debugging
```bash
# Capture LDAP traffic from OpenLDAP container
sudo nsenter --target $(pidof slapd) --net wireshark -f "port 389 or port 636" -k

# Capture from Keycloak
sudo nsenter --target $(pgrep -f quarkus) --net wireshark -f "port 8080" -Y http -k
```

## Documentation

### Build Documentation
```bash
./mvnw clean install -am -pl docs/documentation/dist -Pdocumentation
kde-open ./docs/documentation/server_admin/target/generated-docs/index.html
```

## Useful URLs

- Dev console: http://keycloak.127-0-0-1.nip.io:8080/q/dev/
- Keycloak: http://keycloak.127-0-0-1.nip.io:8080/
- HTTPS: https://keycloak.127-0-0-1.nip.io:8443/
- Admin console: http://lkeycloak.127-0-0-1.nip.io:8080/admin/master/console/
- Well-known config: http://keycloak.127-0-0-1.nip.io:8080/realms/master/.well-known/openid-configuration

## References

- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [Server Configuration](https://www.keycloak.org/server/all-config)
- [Admin REST API](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
- [Quarkus Guides](https://quarkus.io/guides/)
