# Learnings

## [LRN-20260519-001] correction

**Logged**: 2026-05-19T15:28:00+09:00
**Priority**: medium
**Status**: pending
**Area**: workflow-diagnostics

### Summary
Do not treat every dangling ComfyUI workflow link as a load-blocking error.

### Details
User corrected that deleting/unconnecting nodes can leave missing-node or unconnected-state artifacts without causing ComfyUI load errors. The load-blocking pattern seen earlier was specifically an existing origin node whose `origin_slot` does not map to an output, producing `node.outputs[link_info.origin_slot] is undefined`.

### Suggested Action
Keep diagnostics focused on link issues that reproduce frontend load failures. Downgrade or ignore dangling links whose target node is absent, and do not conflate intentionally unconnected nodes with broken graph state.

### Metadata
- Source: user_feedback
- Related Files: src/comfy_agent_view/core.py, tests/test_core.py
- Tags: comfyui, workflow, diagnostics, correction

---
