"""``python -m {{GATE_MODULE}} [ROOT] [--no-clone]`` — run the gate without the build system."""
import sys

from .validate import main

sys.exit(main())
