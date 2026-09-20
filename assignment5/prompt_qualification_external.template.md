# External Prompt Qualification Record

Paste `prompt.py` and the rubric from `prompt_qualification_pack.md` into
ChatGPT, Claude, or Cursor. Save the returned JSON verdict here as
`prompt_qualification_external.md` before submission.

Required verdict shape:

```json
{
  "explicit_reasoning": true,
  "structured_output": true,
  "tool_separation": true,
  "conversation_loop": true,
  "instructional_framing": true,
  "internal_self_checks": true,
  "reasoning_type_awareness": true,
  "fallbacks": true,
  "overall_clarity": "..."
}
```

This template is not a substitute for an external evaluator response; it is a
submission checklist and storage location for that response.