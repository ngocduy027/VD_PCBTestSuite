"""VD_PCBTestSuite: Windows CAN/RS485 test application for motor-driver PCBs.

``__version__`` is the single source of truth for the release number; the
package metadata, window title and PyInstaller executable name derive from it.
"""

__all__ = ["APP_ORGANIZATION", "APP_TITLE", "__version__"]

__version__ = "1.2.0"

APP_ORGANIZATION = "VinD"
APP_TITLE = "VD_PCBTestSuite_V" + ".".join(__version__.split(".")[:2])
