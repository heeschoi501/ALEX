"""Install the official verified portable Ollama runtime."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jarvis.runtime import install
if __name__ == "__main__":
    install()
