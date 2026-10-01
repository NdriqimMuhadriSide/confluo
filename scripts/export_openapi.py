"""Write the API's OpenAPI schema to apps/web/openapi.json.

The dashboard's TypeScript client is generated from this file (`make api-client`),
and CI fails if either is out of date with the code.
"""

import json
from pathlib import Path

from confluo_api.main import create_app

OUT = Path(__file__).resolve().parents[1] / "apps" / "web" / "openapi.json"


def main() -> None:
    schema = create_app().openapi()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
