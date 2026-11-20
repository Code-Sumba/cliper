"""Guard: .env.example is committed to a public repo - it must hold no secrets."""
import re
from pathlib import Path

SECRET = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD|CLIENT_ID|USER_ID|ENDPOINT_URL|PUBLIC_BASE|WEBHOOK_URL)")


def test_env_example_has_no_real_values():
    leaked = []
    for line in (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if SECRET.search(name) and value:
            leaked.append(name)
    assert not leaked, f"real values in .env.example (move them to .env): {leaked}"
