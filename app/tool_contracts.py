import argparse
import json

from tools.contracts import describe_tool_contracts, get_tool_contract


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the CFT security-agent tool contracts as JSON schemas."
    )
    parser.add_argument(
        "--name",
        help="Print one contract by name instead of the whole catalog.",
    )
    args = parser.parse_args()

    if args.name:
        payload: object = get_tool_contract(args.name).describe()
    else:
        payload = describe_tool_contracts()

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
