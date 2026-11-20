<!-- mcp-name: io.github.xiaobinger/curlgen-mcp -->

# cURL Generator MCP

An MCP (Model Context Protocol) server that turns the [cURL Generator / 星轨控制台](https://github.com/xiaobinger/cURL_remote) toolbox into agent-callable tools:

- **HTTP requests** — send requests locally with structured params, headers, auth and bodies.
- **Signed code generation** — generate `cURL`, Python, JavaScript, TypeScript, Node.js, Java, C#, Go, PHP and Rust code; optionally compute a Java-compatible MD5/SHA256 signature at runtime or embed the static value.
- **Signature placement** — body, header, query string, or automatic (GET-like → query, others → body), with custom field/header names.
- **GateShell bastion** — password login, list authorized machines, jump into a target, execute commands, run HTTP requests from inside, upload/download files.
- **Standard SSH jump host** — `direct-tcpip` jump execution and SFTP upload/download.

The server speaks **stdio** and reuses the desktop app's `~/.cURL` configuration, so environments, signing keys, SSH jump hosts and target machines are shared.

## Install

```bash
pip install curlgen-mcp
# or run without installing
uvx curlgen-mcp
```

Python 3.10+ is required. Dependencies: `mcp`, `paramiko`.

## MCP client configuration

```json
{
  "mcpServers": {
    "curlgen": {
      "command": "uvx",
      "args": ["curlgen-mcp"],
      "env": {
        "CURLGEN_CONFIG_DIR": "C:\\Users\\you\\.cURL"
      }
    }
  }
}
```

`CURLGEN_CONFIG_DIR` is optional; it defaults to `~/.cURL`. Existing desktop-app files are read directly:

| File | Used for |
| --- | --- |
| `env_configs.json` | Environments, signing keys/algorithm/field, custom variables, SSH target label |
| `ssh_config.json` | GateShell jump host and target machine list |
| `saved_requests.json` | Read-only; not used by the current tools |

Secrets are never returned by `list_environments` or `ssh_list_targets`; they are only used for the requested operation.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_environments` | List environment aliases, signing settings, variable **names** and SSH target labels |
| `http_request` | Send an HTTP request from the local machine |
| `generate_code` | Generate request code in one of ten languages |
| `ssh_list_targets` | List configured target machines and jump host (password masked) |
| `ssh_exec` | Execute a command through a standard SSH jump host |
| `remote_http_request` | Send an HTTP request from a remote target (`transport=gateshell/ssh`) |
| `bastion_login` | Log in to the GateShell menu |
| `bastion_list_hosts` | List all machines authorized on the bastion |
| `bastion_connect` | Enter a target machine's shell |
| `bastion_exec` | Execute a command on the connected target |
| `bastion_status` | Show login state and the selected target |
| `bastion_logout` | Close the bastion session |
| `bastion_upload` | Upload a local file to the target via base64 chunks |
| `bastion_download` | Download a remote file via base64 chunks |
| `ssh_upload` | Upload a local file via SFTP |
| `ssh_download` | Download a remote file via SFTP |

### `http_request` / `generate_code` arguments

Both accept the same request shape:

- `method`, `url`, `environment`
- `headers`, `params`
- `body`, `body_type` (`json` / `form` / `raw` / `none`)
- `auth_type` (`none` / `bearer` / `basic` / `api_key`), `auth_token`, `auth_username`, `auth_password`, `auth_header`
- `sign_enabled`, `sign_placement` (`auto` / `body` / `header` / `query` / `none`), `sign_field`, `sign_header`, `sign_algo`, `sign_key`
- `verify_ssl`, `timeout`, `proxy`, `follow_redirects`

`generate_code` additionally takes `lang` and `sign_mode` (`static` / `runtime`).

### Signing compatibility

The signature string follows the Java backend used by the desktop app:

```text
sorted(k1=v1&k2=v2&...) + "&" + "key=" + secret
```

- Keys are sorted case-insensitively by upper-cased name.
- Empty values, `None`, and the signature field itself are excluded.
- MD5 and SHA256 both return upper-case hex.
- Query params and JSON / form / raw body params are merged before signing.
- `sign_placement=auto` puts the signature in the query string for `GET` / `HEAD` / `OPTIONS`, otherwise in the body.
- `sign_header` defaults to `X-Sign`; `sign_field` defaults to the environment's `param_name` (usually `sign`).

Runtime signing code is generated for Python, JavaScript, TypeScript, Node.js and Java. Other languages receive the static signature value (already injected into the URL, headers or body). This keeps generated snippets runnable without adding signing logic to every ecosystem.

Runtime signing snippets read the key from the `CURLGEN_SIGN_KEY` environment
variable; the real key is never embedded in generated code. Set it before
running the snippet, for example:

```bash
export CURLGEN_SIGN_KEY="your-key"   # PowerShell: $env:CURLGEN_SIGN_KEY="your-key"
```

## GateShell notes

GateShell is a **menu-based** bastion. It does not support SSH `exec` and does not pass raw TCP through `direct-tcpip`; this server therefore drives its interactive menu:

```text
password login → invoke_shell menu → /<ip> search → Enter → target shell
→ sentinel-wrapped command → exit code + output
```

The session is reused across tool calls; call `bastion_logout` to close it.

## File transfer

- GateShell uploads: truncate the remote path, append base64 chunks, then verify size and SHA-256.
- GateShell downloads: check remote size, read one base64 stream, decode locally, then verify size and SHA-256.
- Standard SSH: SFTP with the same size and SHA-256 checks.
- Default maximum transfer size is 8 MiB (`CURLGEN_MCP_MAX_TRANSFER` bytes). GateShell commands are capped at 16 KiB of payload even if `CURLGEN_MCP_CHUNK_SIZE` is larger.
- Local paths are restricted to `$HOME` and the current working directory by default. Override with `CURLGEN_MCP_FILE_ROOTS` (OS path separator, `;` on Windows, `:` on POSIX). Symlinks are resolved before the check.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `CURLGEN_CONFIG_DIR` | `~/.cURL` | Configuration directory |
| `CURLGEN_ENV` | — | Default environment alias |
| `CURLGEN_SIGN_KEY` | — | Override the environment signing key |
| `CURLGEN_SIGN_ALGO` | — | Override `MD5` / `SHA256` |
| `CURLGEN_JUMP_HOST` / `CURLGEN_JUMP_PORT` / `CURLGEN_JUMP_USER` / `CURLGEN_JUMP_PASSWORD` | — | Override jump host settings |
| `CURLGEN_TARGET_HOST` / `CURLGEN_TARGET_PORT` / `CURLGEN_TARGET_USER` / `CURLGEN_TARGET_PASSWORD` / `CURLGEN_TARGET_KEY` | — | Override target machine settings |
| `CURLGEN_JUMP_KEY` | — | Jump-host private key path |
| `CURLGEN_MCP_FILE_ROOTS` | `$HOME` + CWD | Allowed local paths for transfer |
| `CURLGEN_MCP_MAX_TRANSFER` | `8388608` | Maximum upload/download bytes |
| `CURLGEN_MCP_CHUNK_SIZE` | `65536` | GateShell transfer chunk bytes (capped at 16384) |
| `CURLGEN_MCP_MAX_RESPONSE` | `2097152` | Maximum response bytes (local and remote, max 20 MiB) |
| `CURLGEN_MCP_LOG_LEVEL` | `INFO` | Server log level written to stderr |

## Security

This server can execute commands and transfer files on remote machines. Run it only in MCP clients you trust, protect `~/.cURL` (it can contain signing keys and the jump-host password), and prefer environment-variable injection for secrets in CI. Every command is explicitly requested by the agent; no background jobs are started. Request previews and generated code intentionally include the request's own credentials (for example a bearer token) so the snippet can run; signing keys and SSH passwords are not included. Disabling TLS verification (`verify_ssl=false`) or using a proxy exposes traffic to interception — use only when you understand the risk.

## License

MIT. Registry metadata marker: `mcp-name: io.github.xiaobinger/curlgen-mcp`