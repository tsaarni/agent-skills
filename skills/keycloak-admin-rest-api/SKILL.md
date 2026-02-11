---
name: keycloak-admin-rest-api
description: Interact with Keycloak admin REST API using httpie. Manage realms, users, clients, LDAP federation, identity providers, and authorization services. Use when working with Keycloak administration, user management, OAuth/OIDC configuration, or UMA authorization.
---

# Keycloak Admin REST API

Interact with Keycloak admin REST API using httpie.

## Important Rules (must follow)

- Assume Keycloak is already running. Do not check for it or start it unless explicitly asked.
- If for example realm or user name is required, and they cannot be inferred from the context, use defaults like `master` and examples like `joe` for user name.
- If user has not spcecified URL for Keycloak, use `http://keycloak.127-0-0-1.nip.io:8080` as default base URL for API requests.
- Replace `<ADMIN_TOKEN>` with `$(http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/master/protocol/openid-connect/token username=admin password=admin grant_type=password client_id=admin-cli | jq -r .access_token)`
- Use `http` command from httpie for making API requests.

## URL Structure

Admin API URLs follow the pattern:
- `http://keycloak-host:port/admin/realms/{realm-name}/{resource}`
- Example: `http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users`

The `{realm-name}` in the URL specifies which realm you're managing. Use `master` for the master realm or your custom realm name (e.g., `my-realm`, `example-realm`).


## Realm Management

Get realm:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Create realm:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/ \
  Authorization:"bearer <ADMIN_TOKEN>" \
  id=example-realm \
  realm=example-realm \
  enabled:=true \
  adminEventsEnabled:=true
```

Update realm settings:

```bash
http PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  Authorization:"bearer <ADMIN_TOKEN>" \
  ssoSessionIdleTimeout:=86400 \
  accessTokenLifespan:=86400
```

Delete realm:

```bash
http DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Export realm configuration:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/partial-export \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## User Management

List users:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Get user by username:

```bash
http GET "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users?username=joe" \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Get user by ID:

```bash
id=$(http GET "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users?username=joe" \
  Authorization:"bearer <ADMIN_TOKEN>" | jq -r '.[0].id')
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$id \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Create user with password:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  Authorization:"bearer <ADMIN_TOKEN>" \
  username=joe \
  enabled:=true \
  credentials:='[{"type": "password", "value": "joe", "temporary": false}]'
```

Create user with full details:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  Authorization:"bearer <ADMIN_TOKEN>" \
  username=joe \
  enabled:=true \
  email=joe@example.com \
  firstName=Joe \
  lastName=Average \
  emailVerified:=true \
  credentials:='[{"type":"password","value":"joe","temporary":false}]'
```

Create user with custom attributes:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users \
  Authorization:"bearer <ADMIN_TOKEN>" \
  username=ldapuser \
  enabled:=true \
  firstName=Ldap \
  lastName=User \
  attributes:='{"telephoneNumber": ["1", "2", "3"]}'
```

Delete user:

```bash
http DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$id \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Update user attributes:

```bash
http PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID \
  Authorization:"bearer <ADMIN_TOKEN>" \
  firstName=John \
  lastName=Smith
```

Update user custom attributes:

```bash
http PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID \
  Authorization:"bearer <ADMIN_TOKEN>" \
  attributes:='{"attr3":"val3"}'
```

## Role Management

Get client roles:

```bash
MASTER_REALM_ID=$(http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients \
  Authorization:"bearer <ADMIN_TOKEN>" \
| jq -r '.[] | select(.clientId=="master-realm") | .id')

http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients/$MASTER_REALM_ID/roles \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Assign client role to user:

```bash
VIEW_CLIENTS_ROLE=$(http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/clients/$MASTER_REALM_ID/roles \
  Authorization:"bearer <ADMIN_TOKEN>" \
| jq -c '.[] | select(.name=="view-clients")')

http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/role-mappings/clients/$MASTER_REALM_ID \
  Authorization:"bearer <ADMIN_TOKEN>" \
  --raw="[$VIEW_CLIENTS_ROLE]"
```

Get user's realm role mappings:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/role-mappings/realm \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Get user's groups:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/users/$USER_ID/groups \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## Client Management

List all clients:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Get client by clientId:

```bash
http GET "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients?clientId=foo" \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Get client by ID:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Create confidential client:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  Authorization:"bearer <ADMIN_TOKEN>" \
  clientId=foo \
  publicClient:=false \
  redirectUris:='["http://localhost"]' \
  serviceAccountsEnabled:=true \
  secret=mysecret
```

Create client with authorization services:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  Authorization:"bearer <ADMIN_TOKEN>" \
  clientId=example-client \
  publicClient:=false \
  secret=example-secret \
  directAccessGrantsEnabled:=true \
  rootUrl=http://localhost:18080 \
  redirectUris:='["http://localhost:18080/*"]' \
  authorizationServicesEnabled:=true \
  serviceAccountsEnabled:=true
```

Get client secret:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/client-secret \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Delete client:

```bash
http DELETE http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## Client Scopes

Create client scope:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/client-scopes \
  Authorization:"bearer <ADMIN_TOKEN>" \
  name=my-scope \
  protocol=openid-connect
```

Supported protocols: `openid-connect`, `saml`, `docker-v2`, `oid4vc`

## LDAP Federation

Create LDAP user federation:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/components \
  Authorization:"bearer $ADMIN_TOKEN" << 'EOF'
{
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
}
EOF
```

Get LDAP configuration:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/components/$COMPONENT_ID \
  Authorization:"bearer <ADMIN_TOKEN>"
```

List user storage providers:

```bash
http GET "http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/components?parent=master&type=org.keycloak.storage.UserStorageProvider" \
  Authorization:"bearer <ADMIN_TOKEN>"
```

Test LDAP connection:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/testLDAPConnection \
  Authorization:"bearer <ADMIN_TOKEN>" \
  < rest-requests/test-ldap-authentication.json
```

## Identity Provider (IDP) Brokering

Create OIDC identity provider:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/identity-provider/instances \
  Authorization:"bearer <ADMIN_TOKEN>" << 'EOF'
{
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
}
EOF
```

Get IDP configuration:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/identity-provider/instances/oidc-keycloak \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## Authorization Services (UMA)

Get client ID for authorization configuration:

```bash
CLIENT_ID=$(http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients \
  Authorization:"bearer <ADMIN_TOKEN>" \
| jq -r '.[] | select(.clientId=="example-client") | .id')
```

Create protected resource:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/resource \
  Authorization:"bearer <ADMIN_TOKEN>" \
  name=example-resource \
  type=urn:resource-server:example-resource \
  uris:='["/"]' \
  scopes:='[{"name":"GET"}]'
```

Create user policy:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/policy/user \
  Authorization:"bearer <ADMIN_TOKEN>" \
  name=joe-policy \
  users:='["joe"]'
```

Create resource permission:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/permission/resource \
  Authorization:"bearer <ADMIN_TOKEN>" \
  name=example-resource-permission \
  resources:='["example-resource"]' \
  policies:='["joe-policy"]'
```

Export authorization settings:

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/example-realm/clients/$CLIENT_ID/authz/resource-server/settings \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## Token Operations

Get user token with password grant:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  grant_type=password \
  username=joe \
  password=joe \
  scope=openid \
  client_id=example-client \
  client_secret=example-secret
```

Get client token with client credentials:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  grant_type=client_credentials \
  client_id=foo \
  client_secret=mysecret
```

Exchange authorization code for token:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  grant_type=authorization_code \
  code=$AUTHORIZATION_CODE \
  client_id=example-client \
  client_secret=example-secret \
  redirect_uri=http://localhost:18080/foo
```

Refresh token:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  refresh_token=$REFRESH_TOKEN \
  grant_type=refresh_token \
  scope=openid \
  client_id=example-client \
  client_secret=example-secret
```

UMA ticket grant:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  grant_type=urn:ietf:params:oauth:grant-type:uma-ticket \
  claim_token=$ID_TOKEN \
  claim_token_format=http://openid.net/specs/openid-connect-core-1_0.html#IDToken \
  client_id=example-client \
  client_secret=example-secret \
  audience=example-client \
  permission=example-resource#GET
```

UMA ticket grant with decision response:

```bash
http --form POST http://keycloak.127-0-0-1.nip.io:8080/realms/example-realm/protocol/openid-connect/token \
  grant_type=urn:ietf:params:oauth:grant-type:uma-ticket \
  claim_token=$ID_TOKEN \
  claim_token_format=http://openid.net/specs/openid-connect-core-1_0.html#IDToken \
  client_id=example-client \
  client_secret=example-secret \
  audience=example-client \
  permission=example-resource#GET \
  response_mode=decision
```

## Discovery Endpoints

Get OpenID configuration:

```bash
http http://keycloak.127-0-0-1.nip.io:8080/realms/master/.well-known/openid-configuration
```

Get JWKS (public keys):

```bash
http http://keycloak.127-0-0-1.nip.io:8080/realms/master/protocol/openid-connect/certs
```

## Admin Events

Get admin events (requires "save admin events" enabled):

```bash
http GET http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master/admin-events \
  Authorization:"bearer <ADMIN_TOKEN>"
```

## Brute Force Protection

Create realm with brute force protection:

```bash
http POST http://keycloak.127-0-0-1.nip.io:8080/admin/realms/ \
  Authorization:"bearer <ADMIN_TOKEN>" \
  id=example-realm \
  realm=example-realm \
  enabled:=true \
  bruteForceProtected:=true
```

Update realm brute force settings:

```bash
http PUT http://keycloak.127-0-0-1.nip.io:8080/admin/realms/master \
  Authorization:"bearer <ADMIN_TOKEN>" \
  bruteForceProtected:=true \
  defaultSignatureAlgorithm=ES384
```

## References

- [Keycloak Admin REST API Documentation](https://www.keycloak.org/docs-api/latest/rest-api/index.html)
- [HTTPie Documentation](https://httpie.io/docs/cli)
