# Keycloak REST API recipes

All examples assume the session from SKILL.md: `kc = Keycloak(...)`, `realm`, `A = f"/admin/realms/{realm}"`, `T = f"/realms/{realm}/protocol/openid-connect"`, and `c = {"client_id": ..., "client_secret": ...}` for the client used in token requests.

## Realms

```python
kc.post("/admin/realms", json={"realm": realm, "enabled": True, "adminEventsEnabled": True, "eventsEnabled": True}).expect(201)
kc.get(A)                                                     # realm representation
kc.put(A, json={"ssoSessionIdleTimeout": 86400, "accessTokenLifespan": 86400}).expect(204)
kc.post(f"{A}/partial-export", params={"exportClients": True, "exportGroupsAndRoles": True})
kc.delete(A)                                                  # removes all users, clients, config
```

For a throwaway test realm, create it, run the test and delete it at the end.

## Users

```python
user_id = kc.post(f"{A}/users", json={
    "username": "joe", "enabled": True, "email": "joe@example.com", "firstName": "Joe", "lastName": "Average",
    "emailVerified": True, "credentials": [{"type": "password", "value": "joe", "temporary": False}],
}).expect(201).id
kc.get(f"{A}/users")                                          # list (max 100 by default; params={"max": ...})
user_id = kc.get(f"{A}/users", params={"username": "joe", "exact": True})[0]["id"]
kc.get(f"{A}/users/{user_id}")
u = kc.get(f"{A}/users/{user_id}").json()                     # update: GET, modify, PUT
kc.put(f"{A}/users/{user_id}", json={**u, "firstName": "John"}).expect(204)
kc.put(f"{A}/users/{user_id}/reset-password", json={"type": "password", "value": "new", "temporary": False})
kc.get(f"{A}/users/{user_id}/credentials")
kc.put(f"{A}/users/{user_id}/credentials/{cred_id}/userLabel", data="phone")    # text/plain body
kc.get(f"{A}/users/{user_id}/sessions")
kc.post(f"{A}/users/{user_id}/logout")
kc.delete(f"{A}/users/{user_id}")
```

Custom attributes are dropped unless the realm user profile allows unmanaged attributes (or declares the attribute):

```python
up = kc.get(f"{A}/users/profile").json()
kc.put(f"{A}/users/profile", json={**up, "unmanagedAttributePolicy": "ENABLED"}).expect(200)
u = kc.get(f"{A}/users/{user_id}").json()
u["attributes"] = {**u.get("attributes", {}), "telephoneNumber": ["1", "2"]}
kc.put(f"{A}/users/{user_id}", json=u).expect(204)
```

## Roles and Groups

```python
rm = kc.get(f"{A}/clients", params={"clientId": "realm-management"})[0]["id"]   # admin roles of the realm
kc.get(f"{A}/clients/{rm}/roles")
role = kc.get(f"{A}/clients/{rm}/roles/view-clients").json()
kc.post(f"{A}/users/{user_id}/role-mappings/clients/{rm}", json=[role]).expect(204)
kc.post(f"{A}/roles", json={"name": "tester"}).expect(201)                      # realm role
kc.post(f"{A}/users/{user_id}/role-mappings/realm", json=[kc.get(f"{A}/roles/tester").json()]).expect(204)
kc.get(f"{A}/users/{user_id}/role-mappings/realm")
group_id = kc.post(f"{A}/groups", json={"name": "devs"}).expect(201).id
kc.put(f"{A}/users/{user_id}/groups/{group_id}").expect(204)
kc.get(f"{A}/users/{user_id}/groups")
```

In the master realm, the admin roles of realm `x` are in client `x-realm`, and the master realm's own in `master-realm`.

## Clients and Client Scopes

```python
client_uuid = kc.post(f"{A}/clients", json={
    "clientId": "example-client", "publicClient": False, "secret": "example-secret",
    "redirectUris": ["http://localhost/cb"],
    "directAccessGrantsEnabled": True,        # password grant
    "serviceAccountsEnabled": True,           # client credentials grant
    # "authorizationServicesEnabled": True,   # authorization services / UMA
}).expect(201).id
client_uuid = kc.get(f"{A}/clients", params={"clientId": "example-client"})[0]["id"]
kc.get(f"{A}/clients/{client_uuid}/client-secret")["value"]
kc.get(f"{A}/clients/{client_uuid}/service-account-user")
kc.delete(f"{A}/clients/{client_uuid}")
kc.post(f"{A}/client-scopes", json={"name": "my-scope", "protocol": "openid-connect"}).expect(201)
```

Client scope protocols: `openid-connect`, `saml`, `docker-v2`, `oid4vc`. Every realm has built-in clients (`account`, `account-console`, `admin-cli`, `broker`, `realm-management`, `security-admin-console`); do not reuse those names.

## Tokens

```python
c = {"client_id": "example-client", "client_secret": "example-secret"}
t = kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "joe", "scope": "openid", **c})
decode(t)                                                     # claims; decode(t, "id_token")
kc.post(f"{T}/token", data={"grant_type": "client_credentials", **c})
t2 = kc.post(f"{T}/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], **c})
kc.get(f"{T}/userinfo", token=t)
kc.post(f"{T}/logout", data={"refresh_token": t2["refresh_token"], **c})   # then refresh: "Session not active"
kc.get(f"/realms/{realm}/.well-known/openid-configuration")
kc.get(f"{T}/certs")                                          # JWKS
kc.get(f"/realms/{realm}/account/credentials", token=t)       # Account REST (what the account console calls)
```

Failed grants return 400/401 with `error` and `error_description` in the body; the `repr` shows them. A 401 from `userinfo` or Admin REST has an empty body.

After logout, the access token still passes local validation until it expires (it is self-contained); only the refresh token and the server-side session are invalidated.

Token introspection returns `"active": true` only when the introspecting client is in the token's `aud` claim. Add an audience mapper to the client that requests the token:

```python
kc.post(f"{A}/clients", json={"clientId": "my-resource-server", "secret": "rs-secret", "serviceAccountsEnabled": True}).expect(201)
kc.post(f"{A}/clients/{client_uuid}/protocol-mappers/models", json={
    "name": "rs-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
    "config": {"included.client.audience": "my-resource-server", "access.token.claim": "true",
               "introspection.token.claim": "true"}}).expect(201)
t = kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "joe", **c})
kc.post(f"{T}/token/introspect", data={"token": t["access_token"], "client_id": "my-resource-server", "client_secret": "rs-secret"})
```

## Authorization Services (UMA)

The client needs `"authorizationServicesEnabled": True`.

```python
AZ = f"{A}/clients/{client_uuid}/authz/resource-server"
kc.post(f"{AZ}/resource", json={"name": "example-resource", "type": "urn:example:resource", "uris": ["/"], "scopes": [{"name": "GET"}]}).expect(201)
kc.post(f"{AZ}/policy/user", json={"name": "joe-policy", "users": ["joe"]}).expect(201)
kc.post(f"{AZ}/permission/resource", json={"name": "example-permission", "resources": ["example-resource"], "policies": ["joe-policy"]}).expect(201)
kc.get(f"{AZ}/settings")                                      # export authorization settings
uma = {"grant_type": "urn:ietf:params:oauth:grant-type:uma-ticket", "audience": "example-client", "permission": "example-resource#GET"}
r = kc.post(f"{T}/token", token=t, data=uma)                  # RPT: decode(r).claims["authorization"]
kc.post(f"{T}/token", token=t, data={**uma, "response_mode": "decision"})   # {"result": true}
```

## Events

```python
kc.get(f"{A}/events", params={"max": 10})                     # login events (eventsEnabled), newest first
kc.get(f"{A}/admin-events", params={"max": 10})               # admin events (adminEventsEnabled)
```

Login events show why a login failed, e.g. `LOGIN_ERROR` with `error: user_not_found` or `invalid_user_credentials`.

## Testing Tips

Short-lived access tokens (do this in a test realm: in master it also shortens the admin token, which the driver then refreshes on every call):

```python
import time
kc.put(A, json={"accessTokenLifespan": 1}).expect(204)
t = kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "joe", "scope": "openid", **c})
kc.get(f"{T}/userinfo", token=t); time.sleep(2); kc.get(f"{T}/userinfo", token=t)    # 200, then 401
kc.put(A, json={"accessTokenLifespan": 300}).expect(204)
```

Invalidate earlier tokens with not-before (tokens with `iat` < `notBefore` are rejected; leave a gap of at least a second):

```python
kc.put(A, json={"notBefore": int(time.time())}).expect(204)
kc.put(A, json={"notBefore": 0}).expect(204)                  # reset
```

Session idle timeout: with `{"ssoSessionIdleTimeout": 10}`, a refresh after 10 s of inactivity fails with `"Token is not active"`. Reset to 1800.

Brute force lockout (correct passwords are rejected while locked):

```python
kc.put(A, json={"bruteForceProtected": True, "failureFactor": 2}).expect(204)
for _ in range(2):
    kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "wrong", **c})
kc.get(f"{A}/attack-detection/brute-force/users/{user_id}")   # numFailures, disabled: True
kc.delete(f"{A}/attack-detection/brute-force/users/{user_id}") # unlock
```

Server-side call paths: attach a debugger as described in the [java-debugging skill](../java-debugging/SKILL.md) in another pysess session, set breakpoints, and run the request in the `kc` session with `pysess.bg(...)` so that it can wait at the breakpoint: `r = pysess.bg(kc.get, path, timeout=120)`, later `r.result()`.

## Multiple Nodes

Each `Keycloak` object targets one URL. Keycloak builds the token issuer and the URLs in its pages from the request URL (without a fixed `KC_HOSTNAME`), so tokens and the SSO cookie from `:8080` are rejected by `:8081` with an issuer mismatch. Use the same public `url` for every node and select the node with `connect_to`:

```python
kc0 = Keycloak("http://127.0.0.127:8080")
kc1 = Keycloak("http://127.0.0.127:8080", connect_to="127.0.0.127:8081")    # same URL, other node
for k in (kc0, kc1):
    print(k.get(f"{A}/users", params={"username": "joe"}))     # same check on each node
b = SimpleBrowser(kc0)
b.authorize(realm, "example-client", secret="example-secret"); b.submit(username="joe", password="joe")
b.kc = kc1                                                     # same cookies, other node
b.authorize(realm, "example-client", secret="example-secret")  # SSO session found on the other node
```

The `AUTH_SESSION_ID` cookie value ends with the route of the node that created the session, e.g. `.keycloak-1-28575`.
