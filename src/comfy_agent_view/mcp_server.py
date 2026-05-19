from __future__ import annotations

from .core import fetch_object_info, list_workflows, normalize_workflow, repair_broken_links, summarize_workflow


def run() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("Install MCP support with: pip install 'comfy-agent-view[mcp]'") from exc

    mcp = FastMCP("comfy-agent-view")

    @mcp.tool()
    def comfy_workflow_list(root: str | None = None, recursive: bool = True, limit: int = 100) -> dict:
        """List ComfyUI workflow JSON files."""
        return list_workflows(root=root, recursive=recursive, limit=limit).model_dump(mode="json")

    @mcp.tool()
    def comfy_workflow_summarize(path: str, profile: str = "safe", detail: str = "compact") -> dict:
        """Return a compact structured summary of a ComfyUI workflow."""
        return summarize_workflow(path=path, profile=profile, detail=detail).model_dump(mode="json")

    @mcp.tool()
    def comfy_workflow_normalize(
        path: str,
        profile: str = "safe",
        comfy_url: str | None = None,
        use_object_info: str = "auto",
    ) -> dict:
        """Normalize a ComfyUI workflow JSON into an agent-readable graph."""
        return normalize_workflow(
            path=path,
            profile=profile,
            comfy_url=comfy_url,
            use_object_info=use_object_info,
        ).model_dump(mode="json")

    @mcp.tool()
    def comfy_workflow_repair_links(path: str, dry_run: bool = True, output_path: str | None = None) -> dict:
        """Detect and optionally repair broken ComfyUI workflow links."""
        return repair_broken_links(path=path, dry_run=dry_run, output_path=output_path).model_dump(mode="json")

    @mcp.tool()
    def comfy_object_info_fetch(comfy_url: str = "http://127.0.0.1:8188") -> dict:
        """Fetch ComfyUI /object_info and save it to the default local cache."""
        return fetch_object_info(comfy_url=comfy_url).model_dump(mode="json")

    mcp.run()
