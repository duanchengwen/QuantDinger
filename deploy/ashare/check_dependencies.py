"""Preserve the upstream security lock exception, reject every other mismatch."""
import subprocess

result = subprocess.run(['python', '-m', 'pip', 'check'], text=True, capture_output=True, check=False)
known = 'litellm 1.106.3 has requirement filelock<4.0,>=3.16.1, but you have filelock 4.2.4.'
lines = result.stdout.strip().splitlines()
if result.returncode and lines != [known]:
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)
if result.returncode:
    print('Upstream documented dependency exception retained: LiteLLM/filelock security pin.')
else:
    print('Dependency check passed.')
