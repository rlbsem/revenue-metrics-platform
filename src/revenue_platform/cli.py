import argparse
from pathlib import Path

from revenue_platform.consumer import analyst_csv, review
from revenue_platform.runtime import demo


def main():
    parser = argparse.ArgumentParser(description="Synthetic commercial analytics")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("demo")
    build.add_argument("--workspace", type=Path, default=Path(".local/demo"))
    build.add_argument(
        "--profile", choices=["enterprise", "fixture", "stress"], default="enterprise"
    )
    generate = commands.add_parser("generate")
    generate.add_argument("--profile", choices=["enterprise", "stress"], default="enterprise")
    generate.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export")
    export.add_argument("--publication", type=Path, default=Path(".local/demo/published"))
    export.add_argument("--region", default="ALL")
    export.add_argument("--expected-release")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "demo":
        print("Validated release:", demo(args.workspace, args.profile))
    elif args.command == "generate":
        from revenue_platform.generator import generate

        manifest = generate(args.output, args.profile)
        print("Generated", manifest["config"]["profile"], manifest["row_counts"])
    else:
        result = review(args.publication, args.expected_release)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(analyst_csv(result, args.region), encoding="utf-8")
        print("Exported", args.output)


if __name__ == "__main__":
    main()
