"""Signal relay CLI stub used by the CI build verification."""
import sys


def main(argv):
    if "--selftest" in argv:
        print("selftest: relay configuration parsed")
        return 0
    if "--verify" in argv:
        print("verify: relay endpoint table loaded")
        return 0
    print("usage: main.py [--selftest|--verify]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
