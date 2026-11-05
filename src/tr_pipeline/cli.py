import argparse
import os

from .catalog import build_catalog
from .common import ASSET_TYPES, root_dir
from .pipeline import conclusions, run_all, run_asset_type


def main():
    parser = argparse.ArgumentParser(description="Pipeline de Trade Republic por ISIN")
    parser.add_argument("command", choices=["catalog", "run", "conclusions", "notebooks"])
    parser.add_argument("--type", choices=ASSET_TYPES)
    parser.add_argument("--refresh", action="store_true", help="Descargar de nuevo, sin caché")
    parser.add_argument("--offline", action="store_true", help="Solo caché y CSV locales")
    parser.add_argument("--limit", type=int, help="Ensayo: reducir la selección por relevancia; no amplía el máximo configurado")
    parser.add_argument("--isin", action="append", help="Selección parcial por ISIN; puede repetirse")
    args = parser.parse_args()
    if args.refresh and args.offline:
        parser.error("--refresh y --offline son incompatibles")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit debe ser positivo")
    root = root_dir()
    if args.command == "catalog":
        _, manifest = build_catalog(root, refresh=args.refresh, offline=args.offline)
        print(manifest)
    elif args.command == "run":
        if args.type:
            catalog = build_catalog(root, refresh=args.refresh, offline=args.offline)
            result = run_asset_type(root, args.type, args.refresh, args.offline, args.limit,
                                    catalog=catalog, isins=args.isin)
            print(result["manifest"])
        else:
            run_all(root, args.refresh, args.offline, args.limit, isins=args.isin)
            print("Actualizado reports/conclusiones.md")
    elif args.command == "conclusions":
        conclusions(root, offline=args.offline)
        print("Actualizado reports/conclusiones.md")
    else:
        # Recalcula los artefactos primero y ejecuta los cuatro notebooks en orden.
        import nbformat
        from nbclient import NotebookClient
        if args.limit or args.isin:
            parser.error("notebooks no acepta --limit/--isin; utiliza run para un ensayo")
        run_all(root, args.refresh, args.offline)
        os.environ["TR_PIPELINE_OFFLINE"] = "1" if args.offline else "0"
        for path in sorted((root / "notebooks").glob("*.ipynb")):
            notebook = nbformat.read(path, as_version=4)
            NotebookClient(notebook, timeout=None, kernel_name="python3",
                           resources={"metadata": {"path": str(root)}}).execute()
            nbformat.write(notebook, path)
            print(f"Actualizado {path.name}")


if __name__ == "__main__":
    main()

