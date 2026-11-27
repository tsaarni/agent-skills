# Keycloak database (PostgreSQL)

Query and change the Keycloak database from a `pysess` session with [scripts/psql_driver.py](../scripts/psql_driver.py) (stdlib only). The driver is generic PostgreSQL; the Keycloak parts are the recipes below. It keeps one `psql` process open and writes to its stdin, so everything you would type into psql works, and session state stays between calls: transactions, `SET`, temp tables, `\c`, psql variables.

Use it for what Admin and Account REST do not show: stored credentials (OTP secret and counter, password hash parameters), local copies of LDAP users, component config including secrets (REST masks them as `**********`), and the SQL Keycloak runs during a flow.

## Start psql

```bash
pysess new -n pg <skill-dir>/scripts/psql_driver.py
pysess exec pg <<'EOF'
api()                    # the driver API; details: help(Psql.run)
db = Psql(["kubectl", "exec", "-i", "deploy/postgres", "--", "psql", "-U", "keycloak", "-d", "keycloak"])
db.query("select name, enabled from realm")
EOF
```

`Psql(cmd)` runs the command that starts psql, as you would type it. Only the command differs between environments:

| Where | cmd |
|---|---|
| Kind (`deploy/postgres` from the devenvs manifests) | `["kubectl", "exec", "-i", "deploy/postgres", "--", "psql", "-U", "keycloak", "-d", "keycloak"]` |
| Docker compose (Test Servers in SKILL.md) | `["docker", "exec", "-i", "keycloak-postgres-1", "psql", "--username=keycloak"]` |
| Local psql, e.g. quarkus:dev with the compose PostgreSQL | `["psql", "-h", "localhost", "-U", "keycloak", "-d", "keycloak"]` with `env={"PGPASSWORD": "keycloak"}` |
| Remote host | `["ssh", "host", "psql", "-d", "app"]`: the remote shell splits the arguments again, so quote them for a shell |
| Other pod or namespace | `["kubectl", "exec", "-i", "-n", "ns", "pod/name", "-c", "container", "--", "psql", ...]` |

- stdin must reach psql: pass `-i` to `kubectl exec` and `docker exec`, and never `-t`. Without `-i`, psql exits at once (`PsqlExited: ... is stdin connected?`).
- psql cannot ask for a password. Pass `env={"PGPASSWORD": ...}` to a local psql, use `~/.pgpass`, or run psql inside the database container: the official postgres image trusts local socket connections.
- The driver needs psql 13 or newer (kind has 18, the compose file has 14).
- `cmd` is kept: `db.cancel()` and `db.restart()` run it again.

## run() and query()

```python
db.run(r"\d credential")                  # anything psql accepts; print() shows psql's output
db.run("select count(*) from user_entity; select count(*) from credential;")
rows = db.query("select id, name from realm where name = :'realm'", realm="master")
rows[0]["id"], rows.columns, rows.status  # status: command tag, e.g. "UPDATE 1"; "" for SELECT
print(rows)                               # as a table
```

- `run(script)` returns `Output`, the output psql printed. The script can hold several statements, meta-commands (`\d`, `\dt`, `\x`, `\timing`, `\c`), and `COPY ... TO STDOUT`. For `COPY ... FROM STDIN`, put the data lines and `\.` in the script. SQL errors do not raise: psql goes on with the next statement. Check `o.ok` and `o.errors`, or use `o.expect()` to raise `SqlError`.
- `query(sql)` runs one statement and returns `Rows`, a list of dicts with Python values: integers → `int`, `numeric` → `Decimal`, `real`/`double precision` → `float`, `boolean` → `bool`, `json`/`jsonb` → parsed, `timestamp` → `datetime`, `date` → `date`, `bytea` → `bytes`, NULL → `None`. Other types (text, uuid, arrays, intervals) and values that don't parse (`'infinity'`) stay the text psql prints. `rows.types` has the PostgreSQL types. Cast to `::text` to get psql's text, and to `::json` to parse JSON stored in a text column. Errors raise `SqlError` (`e.sqlstate`, e.g. `42P01`). `INSERT`/`UPDATE ... RETURNING` returns the rows, and without `RETURNING`, `rows.status` and `rows.rowcount` tell what changed. If you pass several statements, all of them run and the rows come from the last one.
- Keyword arguments become psql variables for that call: `:'name'` is an SQL literal, `:"name"` an identifier, `:name` the plain text. Pass values this way instead of formatting them into the SQL. NULL cannot be passed: write `NULL` in the SQL.
- Statements autocommit, as in psql. After an error inside `begin`, statements fail with `current transaction is aborted` until `rollback`.
- Each call has a timeout (default 30 s, `Psql(..., timeout=)` or `timeout=` per call). On timeout, the driver cancels the statement with `pg_cancel_backend` from a second psql started with `cmd`. `run()` then returns an `Output` with `timed_out` set, and `query()` raises `SqlError` with sqlstate `57014`. A statement waiting for a lock held by Keycloak ends the same way. Keep the session free during long statements with `f = pysess.bg(db.query, sql, timeout=600)` and stop them with `db.cancel()`.
- psql runs inside the container for the kubectl and docker commands, so `\i`, `\copy` and `\o file` use files there.
- Do not use `\set ON_ERROR_STOP on`: psql exits at the next error (`PsqlExited`, then `db.restart()` starts a new connection). Avoid `\q`, `\watch` and interactive commands (`\password`, `\e`). An unterminated quote makes the call time out, and the driver restarts psql, so session state is lost.
- psql exits when the pysess session ends. `db.close()` ends it right away.

## Keycloak schema notes

- Table and column names are lower case. Keycloak's SQL (and the statement log) writes them in upper case, which PostgreSQL folds to lower case. `\dt` lists the tables, and `\d user_entity` shows the columns and foreign keys.
- Most tables have `realm_id`, the realm's UUID: join `realm` and filter on `realm.name`.
- `credential.created_date` and `user_entity.created_timestamp` are epoch milliseconds (`int`): `to_timestamp(created_date / 1000)` returns a `datetime`.
- Keycloak keeps JSON in `text` columns (`credential.secret_data`, `credential.credential_data`): cast with `::json` to get it parsed.
- Keycloak caches realms, clients and users. It does not see SQL changes until the cache is cleared: `kc.post(f"{A}/clear-user-cache")`, `clear-realm-cache`, `clear-keys-cache` (Admin REST). Reads served from the cache run no SQL.
- SQL writes skip Keycloak's validation and events. Use Admin REST where it can make the change, and SQL for states REST cannot create (e.g. an HOTP counter or `created_date`).

## Credentials of a user

Admin REST shows only type, label and dates. The table has the secret and parameters: an OTP secret (`secret_data.value`), `credential_data` with `subType`, `digits`, `period`, `counter` and `algorithm`, and password hash parameters.

```python
creds = db.query("""
    select c.id, c.type, c.user_label, c.priority, to_timestamp(c.created_date / 1000) as created,
           c.secret_data::json, c.credential_data::json
    from credential c
    join user_entity u on u.id = c.user_id
    join realm r on r.id = u.realm_id
    where r.name = :'realm' and u.username = :'username'
    order by c.priority""", realm=realm, username="joe")
print(creds)
otp = next(c for c in creds if c["type"] == "otp")
otp["credential_data"], otp["secret_data"]["value"]   # TOTP(value) in kc_driver
```

## Local user entity, attributes and links

For LDAP users, Keycloak keeps a local copy. Admin REST may show values read from LDAP instead (mappers with "always read value from LDAP"), so the two can differ.

```python
u = db.query("""
    select u.id, u.username, u.email, u.first_name, u.last_name, u.enabled, u.federation_link
    from user_entity u join realm r on r.id = u.realm_id
    where r.name = :'realm' and u.username = :'username'""", realm=realm, username="user1")
print(u)                             # federation_link: id of the user storage component
print(db.query("select name, value from user_attribute where user_id = :'id' order by name", id=u[0]["id"]))
# LDAP_ID, LDAP_ENTRY_DN, createTimestamp, ...
print(db.query("select identity_provider, federated_user_id, federated_username from federated_identity "
               "where user_id = :'id'", id=u[0]["id"]))   # brokered (IdP) links
```

## Components and their config

`component` has user federation providers, LDAP mappers (`parent_id` is the provider), key providers and so on. `component_config` has one row per config value, secrets in plain text included.

```python
print(db.query("""
    select c.name, c.provider_id, cc.name as key, cc.value
    from component c
    join realm r on r.id = c.realm_id
    left join component_config cc on cc.component_id = c.id
    where r.name = :'realm' and c.provider_type = 'org.keycloak.storage.UserStorageProvider'
    order by c.name, cc.name""", realm=realm))

# read.only of the mappers of the LDAP provider named "ldap"
print(db.query("""
    select m.name as mapper, cc.value as read_only
    from component p
    join realm r on r.id = p.realm_id
    join component m on m.parent_id = p.id
    join component_config cc on cc.component_id = m.id and cc.name = 'read.only'
    where r.name = :'realm' and p.name = :'provider'
    order by 1""", realm=realm, provider="ldap"))
```

## SQL that Keycloak runs during a flow

Turn on statement logging, run the flow, and read the PostgreSQL log. `ALTER SYSTEM` needs a superuser: the `keycloak` role is one in kind and compose. The compose file already starts PostgreSQL with `-c log_statement=all`. That command-line setting wins over `ALTER SYSTEM`, so the log there has every statement already.

```python
import re, subprocess, time
db.run("alter system set log_statement = 'all'; select pg_reload_conf();").expect()
start = time.time()
# ... run the flow, e.g. b.authorize(...) / b.submit(...) in the kc_driver session ...
since = f"{int(time.time() - start) + 2}s"
log = subprocess.run(["kubectl", "logs", "deploy/postgres", f"--since={since}"],
                     capture_output=True, text=True).stdout
# docker: ["docker", "logs", "--since", since, "keycloak-postgres-1"] with stderr=subprocess.STDOUT
stmts = re.findall(r"\[(\d+)\] LOG:  (?:statement|execute [^:]*): (.*)\n(?:.*DETAIL:  Parameters: (.*)\n)?", log)
for pid, sql, params in stmts:
    if pid != str(db.pid) and "credential" in sql.lower():
        print(pid, sql[:200], params)
db.run("alter system reset log_statement; select pg_reload_conf();").expect()
```

- Each line has the backend pid (`[361]`). Statements of this driver come from `db.pid`; Keycloak's come from its pooled connections.
- Keycloak sends prepared statements (`execute S_1/C_2: ...` with `$1` placeholders). Their values are on the next `DETAIL:  Parameters:` line.
- Data served from Keycloak's caches runs no SQL: a second run of the same flow shows fewer statements. Clear the caches (see above) to see the full set.
