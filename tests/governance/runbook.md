# Test Runbook

## Environment

Dependencies were installed into workspace-local `.codex_pydeps`.

Use this PowerShell setup before running tests:

```powershell
$env:PYTHONPATH='D:\Agents\Codex\janus\.codex_pydeps;D:\Agents\Codex\janus'
```

## Full Unit Suite

```powershell
& 'C:\Users\markereversey\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m pytest -q --basetemp .pytest_tmp
```

Expected current result:

```text
86 passed
```

## Architecture Guard

```powershell
rg -n -i "wti|eia|ovx" core adapters
```

Expected current result: no matches.

## Cleanup

After local runs:

```powershell
Remove-Item -LiteralPath .pytest_tmp -Recurse -Force
```

Do not delete `.codex_pydeps` unless you want to reinstall dependencies.
