# Login flows with SimpleBrowser

All examples assume the session from SKILL.md: `kc = Keycloak(...)`, `realm`, `A = f"/admin/realms/{realm}"`, `T = f"/realms/{realm}/protocol/openid-connect"`, and `c = {"client_id": ..., "client_secret": ...}` for the client used in token requests.

## Login Flows in the Browser

Keycloak login pages and the form fields that the authenticators read:

| Page id | Fields |
|---|---|
| `login-login` | `username`, `password` (only `password` when re-authenticating with an SSO session) |
| `login-login-otp` | `otp`, `selectedCredentialId` (hidden, one per OTP device) |
| `login-login-config-totp` | `totp`, `totpSecret` (hidden, the raw secret), `userLabel` |
| `login-login-update-profile` | `email`, `firstName`, `lastName`, ... |
| `login-select-authenticator` | one form per method with `authenticationExecution`; click its title |
| `login-login-recovery-authn-code-config` | `generatedRecoveryAuthnCodes` (hidden, comma separated) |
| `login-login-recovery-authn-code-input` | `recoveryCodeInput` (asks for a specific code, e.g. "Recovery code #1") |
| `login-login-reset-password` | `username` |
| `login-delete-credential` | confirm (first button) or `cancel-aia` |

Password login and code exchange:

```python
b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret")  # <Page login-login 200 ...>
b.submit(username="joe", password="bad")                       # messages=['Invalid username or password.']
b.submit(username="joe", password="joe")                       # <Callback http://localhost/cb {... 'code': ...}>
t = b.tokens(); decode(t)
print(b.auth)                                                  # the authorization request
b.auth.matches(b.callback)                                     # state echoed back
b.authorize(realm, "example-client", secret="example-secret")  # SSO: callback without a login page
b.authorize(realm, "example-client", secret="example-secret", prompt="login")  # "Please re-authenticate to continue"
```

The client needs `redirectUris` containing the `redirect_uri` (default `http://localhost/cb`; it is never requested).

PKCE and request parameter tests:

```python
b.authorize(realm, "example-client", secret="example-secret", pkce=False)       # no code_challenge
b.authorize(realm, "example-client", secret="example-secret", state="fixed", scope="openid email")
b.tokens(code_verifier="wrong")       # 400 "PKCE verification failed"; the code is then used up
```

Application-initiated actions (`kc_action`) on an existing session; the callback has `kc_action_status` `success` or `cancelled`:

```python
b.authorize(realm, "example-client", secret="example-secret", kc_action="UPDATE_PASSWORD")
b.click("cancel-aia")                                         # cancel instead of completing
b.authorize(realm, "example-client", secret="example-secret", kc_action=f"delete_credential:{cred_id}")
b.submit()                                                    # confirm
```

Update profile (users without required fields, e.g. LDAP users):

```python
b.expect("login-login-update-profile")
b.submit(email="user1@example.com", firstName="User", lastName="One")
```

Reset password: the realm needs `"resetPasswordAllowed": True`. The flow sends an email, so it ends with "You should receive an email shortly" unless an SMTP server is configured.

```python
b.authorize(realm, "example-client", secret="example-secret")
b.click("Forgot Password?")
b.submit(username="joe")
```

## OTP

The realm OTP policy fields are set as a group: a realm `PUT` with only some of them (e.g. only `otpPolicyType`) resets the others (algorithm null, digits 0), and enrolment then fails with 500 "null algorithm name". Send all of them:

```python
kc.put(A, json={"otpPolicyType": "totp", "otpPolicyAlgorithm": "HmacSHA1", "otpPolicyDigits": 6,
                "otpPolicyPeriod": 30, "otpPolicyLookAheadWindow": 1, "otpPolicyInitialCounter": 0,
                "otpPolicyCodeReusable": False}).expect(204)
```

Enrol TOTP in the browser (required action or `kc_action`), then log in with it:

```python
b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret", kc_action="CONFIGURE_TOTP")  # or requiredActions=["CONFIGURE_TOTP"]
b.submit(username="joe", password="joe")
otp = TOTP(b.page["totpSecret"])
b.submit(totp=otp.now(), userLabel="phone")

b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret")
b.submit(username="joe", password="joe")
b.expect("login-login-otp")
b.submit(otp=otp.now())                                       # or b.submit(otp=..., selectedCredentialId=cred_id)
kc.post(f"{T}/token", data={"grant_type": "password", "username": "joe", "password": "joe", "totp": otp.now(), **c})
```

Or create the OTP credential with the user:

```python
otp, hotp = TOTP("12345678901234567890"), HOTP("abcdefghijabcdefghij")
kc.post(f"{A}/users", json={"username": "ann", "enabled": True, "email": "ann@example.com", "firstName": "Ann", "lastName": "Lee",
        "credentials": [{"type": "password", "value": "ann", "temporary": False}, otp.credential("phone"), hotp.credential("token")]}).expect(201)
```

Keycloak rejects a reused code (cluster-wide). `TOTP.now()` tracks the codes it returned, so a new `TOTP` object for the same secret may return a code that was already used.

Recovery codes as an alternative to OTP. The default browser flow has the recovery code form disabled; enable it in a copy of the flow:

```python
F = f"{A}/authentication/flows"
kc.post(f"{F}/browser/copy", json={"newName": "browser-rc"}).expect(201)
rc = next(e for e in kc.get(f"{F}/browser-rc/executions").json() if e.get("providerId") == "auth-recovery-authn-code-form")
kc.put(f"{F}/browser-rc/executions", json={**rc, "requirement": "ALTERNATIVE"}).expect(204)
kc.put(A, json={"browserFlow": "browser-rc"}).expect(204)

b.authorize(realm, "example-client", secret="example-secret", kc_action="CONFIGURE_RECOVERY_AUTHN_CODES")  # logged-in b
codes = b.page["generatedRecoveryAuthnCodes"].split(",")
b.submit()                                                    # "Complete setup"

b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret")
b.submit(username="joe", password="joe")                       # OTP page
b.click("Try Another Way")
b.click("Recovery Authentication Code")
b.submit(recoveryCodeInput=codes[0])                           # the page asks for a specific code: "Recovery code #1"
```

Credentials through Account REST (what the account console shows):

```python
creds = kc.get(f"/realms/{realm}/account/credentials", token=t).json()
kc.put(f"/realms/{realm}/account/credentials/{cred_id}/label", json="new label", token=t)   # JSON string body
kc.delete(f"/realms/{realm}/account/credentials/{cred_id}", token=t)
```

## Identity Provider Brokering

OIDC broker from `example-realm` to realm `upstream` on the same server. The browser follows `authorizationUrl`; Keycloak itself calls `tokenUrl` and `jwksUrl`, so in Kind use a URL that the pod can reach (e.g. `http://keycloak-0:8080`):

```python
U = "/admin/realms/upstream"
kc.post("/admin/realms", json={"realm": "upstream", "enabled": True}).expect(201)
kc.post(f"{U}/clients", json={"clientId": "idp-client", "secret": "idp-secret",
        "redirectUris": [f"{kc.url}/realms/{realm}/broker/up/endpoint"]}).expect(201)
kc.post(f"{U}/users", json={"username": "bob", "enabled": True, "email": "bob@example.com", "firstName": "Bob",
        "lastName": "Up", "credentials": [{"type": "password", "value": "bob", "temporary": False}]}).expect(201)
browser_url = f"{kc.url}/realms/upstream/protocol/openid-connect"
server_url = browser_url                                       # Kind: "http://keycloak-0:8080/realms/upstream/protocol/openid-connect"
kc.post(f"{A}/identity-provider/instances", json={"alias": "up", "providerId": "oidc", "config": {
    "clientId": "idp-client", "clientSecret": "idp-secret", "clientAuthMethod": "client_secret_post",
    "authorizationUrl": f"{browser_url}/auth", "tokenUrl": f"{server_url}/token",
    "jwksUrl": f"{server_url}/certs", "useJwksUrl": "true", "validateSignature": "true", "syncMode": "IMPORT"}}).expect(201)
kc.get(f"{A}/identity-provider/instances/up")

b = SimpleBrowser(kc)
b.authorize(realm, "example-client", secret="example-secret")
b.click("up")                                                  # "Sign in with up" (or authorize(..., kc_idp_hint="up"))
b.submit(username="bob", password="bob")                       # upstream login page -> first broker login -> callback
b.history                                                      # every hop across both realms
```
