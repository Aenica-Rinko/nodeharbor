"""CLI and pipe-based network worker; a console bootloader preserves stdio."""
import sys


if __name__ == "__main__":
    if sys.argv[1:] in (["--network-worker"], ["--network-worker", "--persistent"]):
        from nodeharbor.network_worker import main
        main()
    else:
        from nodeharbor.cli import main
        raise SystemExit(main())
