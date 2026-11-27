# Components and LDAP federation

All examples assume the session from SKILL.md: `kc = Keycloak(...)`, `realm`, `A = f"/admin/realms/{realm}"`, `T = f"/realms/{realm}/protocol/openid-connect"`, and `c = {"client_id": ..., "client_secret": ...}` for the client used in token requests.

## Components and LDAP Federation

User federation, key providers and other components are `POST {A}/components`. Config values are lists of strings.

Test the connection and bind first. The URL is the LDAP server as Keycloak reaches it:

```python
ldap_url = "ldap://ldap.example.com:389"
ldap_conn = {"connectionUrl": ldap_url, "bindDn": "cn=ldap-admin,ou=users,o=example",
             "bindCredential": "ldap-admin", "authType": "simple"}
kc.post(f"{A}/testLDAPConnection", json={"action": "testConnection", **ldap_conn})     # 204
kc.post(f"{A}/testLDAPConnection", json={"action": "testAuthentication", **ldap_conn}) # 204, or 400 AuthenticationFailure
```

Create the federation, sync users and list its mappers:

```python
ldap_id = kc.post(f"{A}/components", json={
    "name": "ldap", "providerId": "ldap", "providerType": "org.keycloak.storage.UserStorageProvider",
    "config": {
        "connectionUrl": [ldap_url], "usersDn": ["ou=users,o=example"],
        "bindDn": ["cn=ldap-admin,ou=users,o=example"], "bindCredential": ["ldap-admin"], "authType": ["simple"],
        "editMode": ["WRITABLE"],             # READ_ONLY, WRITABLE or UNSYNCED
        "vendor": ["other"], "usernameLDAPAttribute": ["uid"], "rdnLDAPAttribute": ["cn"],
        "uuidLDAPAttribute": ["entryUUID"], "userObjectClasses": ["inetOrgPerson"],
        "importEnabled": ["true"], "syncRegistrations": ["true"],
    }}).expect(201).id
kc.get(f"{A}/components", params={"parent": kc.get(A)["id"], "type": "org.keycloak.storage.UserStorageProvider"})
kc.get(f"{A}/components", params={"parent": ldap_id})         # mappers
kc.post(f"{A}/user-storage/{ldap_id}/sync", params={"action": "triggerFullSync"})   # or triggerChangedUsersSync
```

Group mapper and its sync:

```python
gm = kc.post(f"{A}/components", json={
    "name": "groups", "providerId": "group-ldap-mapper", "parentId": ldap_id,
    "providerType": "org.keycloak.storage.ldap.mappers.LDAPStorageMapper",
    "config": {"groups.dn": ["o=example"], "group.name.ldap.attribute": ["cn"], "group.object.classes": ["groupOfNames"],
               "membership.ldap.attribute": ["member"], "membership.attribute.type": ["DN"], "mode": ["READ_ONLY"],
               "user.roles.retrieve.strategy": ["LOAD_GROUPS_BY_MEMBER_ATTRIBUTE"]}}).expect(201).id
kc.post(f"{A}/user-storage/{ldap_id}/mappers/{gm}/sync", params={"direction": "fedToKeycloak"})
```

LDAP notes:
- The default mappers get their `read.only` flag from `editMode` when the provider is created. Changing `editMode` later does not update them: after `READ_ONLY` → `WRITABLE`, user changes stay in the Keycloak database and are not written to LDAP. Create the provider in the mode you test, or set `"read.only": ["false"]` on each mapper (GET, modify, PUT of the mapper component).
- With `READ_ONLY`, LDAP attributes are read-only in the user profile, so missing `mail`/`givenName` do not block login. With `WRITABLE`, they are editable and required: such users get the update-profile page, or `"Account is not fully set up"` from the password grant.
- `ldaps://` needs the LDAP server CA in Keycloak's truststore; otherwise the connection test returns `SSLHandshakeFailed`.
- With `WRITABLE`, password changes are written to LDAP, and LDAP password policy errors come back as 400 `"Could not modify attribute for DN ..."`.
