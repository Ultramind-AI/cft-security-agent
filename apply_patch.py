from pathlib import Path
import shutil

PATCH_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path.cwd()

FILES = [
    "pyproject.toml",
    "schemas/state.py",
    "agent/prompts.py",
    "agent/nodes.py",
    "agent/graph.py",
    "executor/executor.py",
    "app/main.py",
    "tests/test_agent_graph.py",
    "tests/test_contracts.py",
]

if not (PROJECT_ROOT / "README.md").exists():
    raise SystemExit(
        "Запусти apply_patch.py из корня cft-security-agent, "
        "где лежит README.md"
    )

for rel in FILES:
    source = PATCH_ROOT / rel
    destination = PROJECT_ROOT / rel

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    shutil.copy2(source, destination)
    print(f"patched: {rel}")

print()
print("Патч применен.")
print('Дальше: python3 -m pip install -e ".[dev]"')
print("Потом:  python3 -m pytest -q")
print("Demo:   python3 -m app.main")
