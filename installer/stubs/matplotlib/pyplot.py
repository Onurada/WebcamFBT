"""See matplotlib/__init__.py: placeholder, plotting is not available in the WebcamFBT package."""


def __getattr__(name):
    raise ImportError(f"matplotlib.pyplot.{name}: matplotlib is not bundled with WebcamFBT")
