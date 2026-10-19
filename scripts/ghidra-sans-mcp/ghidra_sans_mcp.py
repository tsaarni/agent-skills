"""ghidra-sans-mcp: Python client for pyghidra-mcp that lets LLM agents drive Ghidra by writing Python code instead of using MCP tools.

A lightweight, zero-dependency Python client that talks to a running pyghidra-mcp
server over Streamable HTTP (JSON-RPC).

Backend Setup & Requirements:
1. JDK.
2. Ghidra install.
   On Apple Silicon (macOS arm64), compile native decompiler & sleigh first:
     cd <ghidra_dir>/support/gradle && ./gradlew buildNatives_mac_arm_64
3. uv (for running pyghidra-mcp via uvx).
4. runagent (background process manager): https://github.com/tsaarni/runagent

Start the server with runagent:
    runagent run -n ghidramcp \\
      --env GHIDRA_INSTALL_DIR="/path/to/ghidra_12.1.4_PUBLIC" \\
      -- uvx pyghidra-mcp --transport streamable-http \\
         --project-path "/path/to/ghidra_projects" \\
         --project-name re

Server management:
    runagent ps                     # check process status
    runagent logs ghidramcp --last 20  # check startup logs (ready at :8000/mcp)
    runagent kill ghidramcp         # stop server

Usage in Python:
    from ghidra_sans_mcp import GhidraSansMCP

    ghidra = GhidraSansMCP()  # defaults to http://127.0.0.1:8000/mcp

    # Import and analyze binary
    ghidra.import_binary("path/to/libtarget.so")

    # List binaries in project
    print(ghidra.list_binary_names())

    # Decompile function (by name or address)
    code = ghidra.decompile("libtarget.so", "FUN_001938a8")
    print(code)

    # Search symbols, strings, xrefs, call graph
    syms = ghidra.search_symbols("libtarget.so", ".*Location.*")
    strings = ghidra.search_strings("libtarget.so", "https://")
    xrefs = ghidra.xrefs("libtarget.so", "0x001938a8")
    graph = ghidra.callgraph("libtarget.so", "FUN_001938a8")
"""
import json
import os
import urllib.error
import urllib.request


class GhidraSansMCPError(RuntimeError):
    """Raised when communication with pyghidra-mcp fails or tool returns an error."""


class GhidraSansMCP:
    def __init__(self, url="http://127.0.0.1:8000/mcp", timeout=60):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session_id = None
        self._init_session()

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    def _init_session(self):
        """Perform MCP initialize handshake and extract mcp-session-id."""
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ghidra-bridge-python", "version": "1.0"},
            },
        }
        try:
            req = urllib.request.Request(
                self.url,
                headers=self._headers(),
                data=json.dumps(init_payload).encode("utf-8"),
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.session_id = resp.headers.get("mcp-session-id")
        except urllib.error.URLError as exc:
            raise GhidraSansMCPError(
                f"Cannot connect to pyghidra-mcp at {self.url}. "
                "Ensure the server is running (e.g. via runagent or uvx pyghidra-mcp)."
            ) from exc

        # Send notifications/initialized
        init_notif = urllib.request.Request(
            self.url,
            headers=self._headers(),
            data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
        )
        try:
            urllib.request.urlopen(init_notif, timeout=self.timeout)
        except Exception:
            pass

    def call_tool(self, name, arguments=None):
        """Call an MCP tool by name and return its structured or parsed result."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        req = urllib.request.Request(
            self.url,
            headers=self._headers(),
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GhidraSansMCPError(f"HTTP {exc.code} calling {name}: {body}") from exc

        # Parse SSE stream response
        for line in raw.splitlines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "error" in data:
                    raise GhidraSansMCPError(f"RPC Error: {data['error']}")
                res = data.get("result", {})
                if res.get("isError"):
                    msg = res.get("content", [{}])[0].get("text", "Tool execution failed")
                    raise GhidraSansMCPError(f"Tool {name} error: {msg}")
                if "structuredContent" in res:
                    return res["structuredContent"]
                content = res.get("content", [])
                if content and content[0].get("type") == "text":
                    text = content[0].get("text")
                    try:
                        return json.loads(text)
                    except Exception:
                        return text
                return res

        raise GhidraSansMCPError(f"No SSE data payload received from server: {raw[:200]}")

    # --- Project and Binary Management ---

    def list_binaries(self):
        """List all programs imported into the active Ghidra project."""
        res = self.call_tool("list_project_binaries")
        if isinstance(res, dict) and "programs" in res:
            return res["programs"]
        return res

    def list_binary_names(self):
        """Return simple list of binary names in the project."""
        progs = self.list_binaries()
        if isinstance(progs, list):
            return [p.get("name") for p in progs if isinstance(p, dict)]
        return []

    def resolve_binary(self, name):
        """Fuzzy-match binary name (e.g. 'libiotmgr.so' -> '/libiotmgr.so-0dc302')."""
        names = self.list_binary_names()
        if name in names:
            return name
        for n in names:
            if name in n:
                return n
        raise ValueError(f"Binary {name!r} not found in project. Available: {names}")

    def import_binary(self, binary_path):
        """Import an ELF, .so, or executable binary into the Ghidra project."""
        abs_path = os.path.abspath(binary_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"Binary file not found: {abs_path}")
        return self.call_tool("import_binary", {"binary_path": abs_path})

    def binary_metadata(self, binary_name):
        """Get analysis metadata for an imported binary."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool("list_project_binary_metadata", {"binary_name": bin_name})

    def delete_binary(self, binary_name):
        """Delete a program from the Ghidra project."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool("delete_project_binary", {"binary_name": bin_name})

    def save(self):
        """Save the Ghidra project changes."""
        return self.call_tool("save")

    # --- Decompilation and Disassembly ---

    def decompile(self, binary_name, name_or_address, raw=False):
        """Decompile a function into pseudo-C.

        Args:
            binary_name: Name of binary (or substring)
            name_or_address: Function name (e.g. 'FUN_001938a8' or 'main') or hex address
            raw: If True, return full dict with callees, xrefs, signature. If False, return C code.
        """
        bin_name = self.resolve_binary(binary_name)
        res = self.call_tool(
            "decompile_function",
            {"binary_name": bin_name, "name_or_address": str(name_or_address)},
        )
        if isinstance(res, dict) and not raw:
            return res.get("code") or res
        return res

    def disassemble(self, binary_name, address, count=20, include_bytes=False):
        """Disassemble instructions starting at address."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "disassemble",
            {
                "binary_name": bin_name,
                "address": str(address),
                "count": count,
                "include_bytes": include_bytes,
            },
        )

    def read_bytes(self, binary_name, address, size=64):
        """Read raw hex bytes from memory at address."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "read_bytes",
            {"binary_name": bin_name, "address": str(address), "size": size},
        )

    # --- Search and Analysis ---

    def search_symbols(self, binary_name, query=".*", functions_only=False, limit=50):
        """Search symbol table using regex query."""
        bin_name = self.resolve_binary(binary_name)
        res = self.call_tool(
            "search_symbols_by_name",
            {
                "binary_name": bin_name,
                "query": query,
                "functions_only": functions_only,
                "limit": limit,
            },
        )
        if isinstance(res, dict) and "symbols" in res:
            return res["symbols"]
        return res

    def search_strings(self, binary_name, query, limit=50):
        """Search defined strings in binary."""
        bin_name = self.resolve_binary(binary_name)
        res = self.call_tool(
            "search_strings",
            {"binary_name": bin_name, "query": query, "limit": limit},
        )
        if isinstance(res, dict) and "strings" in res:
            return res["strings"]
        return res

    def search_code(self, binary_name, query, limit=10):
        """Semantic search across decompiled code using ChromaDB embeddings."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "search_code",
            {"binary_name": bin_name, "query": query, "limit": limit},
        )

    def xrefs(self, binary_name, name_or_address):
        """List cross-references / callers to a function or address."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "list_xrefs",
            {"binary_name": bin_name, "name_or_address": str(name_or_address)},
        )

    def exports(self, binary_name, query=".*", limit=100):
        """List exported symbols."""
        bin_name = self.resolve_binary(binary_name)
        res = self.call_tool(
            "list_exports",
            {"binary_name": bin_name, "query": query, "limit": limit},
        )
        if isinstance(res, dict) and "exports" in res:
            return res["exports"]
        return res

    def imports(self, binary_name, query=".*", limit=100):
        """List imported symbols."""
        bin_name = self.resolve_binary(binary_name)
        res = self.call_tool(
            "list_imports",
            {"binary_name": bin_name, "query": query, "limit": limit},
        )
        if isinstance(res, dict) and "imports" in res:
            return res["imports"]
        return res

    def callgraph(self, binary_name, function_name, direction="both"):
        """Generate a Mermaid call graph for a function."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "gen_callgraph",
            {
                "binary_name": bin_name,
                "function_name": function_name,
                "direction": direction,
            },
        )

    # --- Editing and Annotations ---

    def rename_function(self, binary_name, name_or_address, new_name):
        """Rename a function."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "rename_function",
            {
                "binary_name": bin_name,
                "name_or_address": str(name_or_address),
                "new_name": new_name,
            },
        )

    def set_comment(self, binary_name, target, comment, comment_type="plate"):
        """Set a comment on an address or function."""
        bin_name = self.resolve_binary(binary_name)
        return self.call_tool(
            "set_comment",
            {
                "binary_name": bin_name,
                "target": str(target),
                "comment": comment,
                "comment_type": comment_type,
            },
        )


# Aliases for convenience and backward compatibility
Ghidra = GhidraSansMCP
GhidraBridge = GhidraSansMCP
GhidraBridgeError = GhidraSansMCPError
