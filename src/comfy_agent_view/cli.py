from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import config_path, object_info_cache_path
from .core import (
    apply_workflow_patch,
    diagnose_load,
    fetch_object_info,
    inspect_workflow_dependencies,
    list_workflows,
    normalize_workflow,
    plan_workflow_patch,
    repair_broken_links,
    summarize_workflow,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="comfy-agent-view")
    parser.add_argument(
        "--comfyui-user-dir",
        help="ComfyUI user directory for this invocation. Overrides the user config file.",
    )
    parser.add_argument(
        "--print-config-path",
        action="store_true",
        help="Print the user config path and exit.",
    )
    parser.add_argument(
        "--print-object-info-path",
        action="store_true",
        help="Print the default object_info cache path and exit.",
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("root", nargs="?")
    list_parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    list_parser.add_argument("--limit", type=int, default=100)

    summarize_parser = subparsers.add_parser("summarize")
    summarize_parser.add_argument("path")
    summarize_parser.add_argument("--profile", choices=["safe", "private", "full", "debug"], default="safe")
    summarize_parser.add_argument("--detail", default="compact")

    normalize_parser = subparsers.add_parser("normalize")
    normalize_parser.add_argument("path")
    normalize_parser.add_argument("--profile", choices=["safe", "private", "full", "debug"], default="safe")
    normalize_parser.add_argument("--comfy-url")
    normalize_parser.add_argument("--use-object-info", choices=["auto", "never", "require"], default="auto")

    deps_parser = subparsers.add_parser("inspect-dependencies")
    deps_parser.add_argument("path")
    deps_parser.add_argument("--use-object-info", choices=["auto", "never", "require"], default="auto")

    repair_parser = subparsers.add_parser("repair-links")
    repair_parser.add_argument("path")
    repair_parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    repair_parser.add_argument("--output-path")

    diagnose_parser = subparsers.add_parser("diagnose-load")
    diagnose_parser.add_argument("path")
    diagnose_parser.add_argument("--error-report-text")

    plan_patch_parser = subparsers.add_parser("plan-workflow-patch")
    plan_patch_parser.add_argument("path")
    plan_patch_parser.add_argument("--output-path", required=True)
    plan_patch_parser.add_argument("--patch-path")

    patch_parser = subparsers.add_parser("apply-workflow-patch")
    patch_parser.add_argument("patch_path")
    patch_parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    patch_parser.add_argument("--overwrite", action="store_true")

    object_info_parser = subparsers.add_parser("fetch-object-info")
    object_info_parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")

    subparsers.add_parser("mcp")

    args = parser.parse_args(argv)
    if args.print_config_path:
        sys.stdout.write(f"{config_path()}\n")
        return
    if args.print_object_info_path:
        sys.stdout.write(f"{object_info_cache_path()}\n")
        return
    if args.command is None:
        parser.error("the following arguments are required: command")

    try:
        if args.command == "list":
            _print(
                list_workflows(
                    root=args.root,
                    recursive=args.recursive,
                    limit=args.limit,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "summarize":
            _print(
                summarize_workflow(
                    path=args.path,
                    profile=args.profile,
                    detail=args.detail,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "normalize":
            _print(
                normalize_workflow(
                    path=args.path,
                    profile=args.profile,
                    comfy_url=args.comfy_url,
                    use_object_info=args.use_object_info,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "inspect-dependencies":
            _print(
                inspect_workflow_dependencies(
                    path=args.path,
                    use_object_info=args.use_object_info,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "repair-links":
            _print(
                repair_broken_links(
                    path=args.path,
                    dry_run=args.dry_run,
                    output_path=args.output_path,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "diagnose-load":
            _print(
                diagnose_load(
                    path=args.path,
                    comfyui_user_dir=args.comfyui_user_dir,
                    error_report_text=args.error_report_text,
                ).model_dump(mode="json")
            )
        elif args.command == "plan-workflow-patch":
            _print(
                plan_workflow_patch(
                    path=args.path,
                    output_path=args.output_path,
                    patch_path=args.patch_path,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "apply-workflow-patch":
            _print(
                apply_workflow_patch(
                    patch_path=args.patch_path,
                    dry_run=args.dry_run,
                    overwrite=args.overwrite,
                    comfyui_user_dir=args.comfyui_user_dir,
                ).model_dump(mode="json")
            )
        elif args.command == "fetch-object-info":
            _print(fetch_object_info(comfy_url=args.comfy_url).model_dump(mode="json"))
        elif args.command == "mcp":
            from .mcp_server import run

            run()
        else:
            parser.error(f"Unknown command: {args.command}")
    except (FileNotFoundError, PermissionError, ValueError) as error:
        _print_error(error)
        raise SystemExit(1) from error


def _print(value: dict[str, Any]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _print_error(error: Exception) -> None:
    json.dump(
        {
            "format": "comfy_agent_view_error_v1",
            "ok": False,
            "error": {
                "code": type(error).__name__.upper(),
                "message": str(error),
            },
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
