<!-- version: 1 -->
# ExecutePython - Run Python Code

## Params

| Param | Description |
|-------|-------------|
| code | Python code to execute |
| sandbox | true = isolated, false = HA runtime (default) |
| requirements | List of pip packages (sandbox only) |
| timeout | Execution timeout in seconds |

## Inline Mode (default)

Has access to:
- `hass` - Home Assistant instance
- `OUTPUT_DIR` - Output directory
- `TMP_DIR` - Temp directory
- `output_url(name)` - Get URL for output file
- `list_outputs()` - List output files
- `list_tmp()` - List temp files

```python
# Example: Create a file
with open(f"{OUTPUT_DIR}/report.txt", "w") as f:
    f.write("Hello")
print(output_url("report.txt"))
```

## Sandbox Mode

Isolated environment, no hass access. For:
- Heavy computation
- External packages
- Risky code

```json
{"code": "import pandas as pd; ...", "sandbox": true, "requirements": ["pandas"]}
```

## When to Use

Only when native HA tools cannot do the job:
- Complex data processing
- Custom calculations
- File generation
- External API calls (sandbox)

## Notes

- Destructive ops need user consent
- Prefer native tools first
- Inline has full HA access
