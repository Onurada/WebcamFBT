"""Placeholder bundled with WebcamFBT instead of the real matplotlib (~70 MB with its dependencies).

MediaPipe's vision package imports matplotlib.pyplot at import time, but only uses it in
drawing_utils.plot_landmarks (a 3D debug plot) that WebcamFBT never calls. Using any of it raises
ImportError with this explanation. Development installs (.venv) have the real matplotlib.
"""
