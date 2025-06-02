import inspect
from typing import TYPE_CHECKING


if not TYPE_CHECKING:
    import fusionscript

else:
    from beatlapse import fusionscript


def get_resolve() -> "fusionscript.Resolve":
    resolve = fusionscript.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "Unable to connect to Davinci Resolve. Make sure the app is running."
        )
    return resolve


if __name__ == "__main__":
    fusion = get_resolve().Fusion()
    for name in dir(fusion):
        if not callable(attr := getattr(fusion, name)):
            continue

        sig = inspect.signature(attr)
        print(f"Fusion.{name}{sig}()")
