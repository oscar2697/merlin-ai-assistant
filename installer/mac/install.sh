# MERLIN macOS installer placeholder (FUTURE target - Apple Silicon assumed).
# This milestone produces no Mac build; this script documents the planned steps.

set -e
echo "MERLIN macOS install is not implemented yet (future milestone)."
echo "Planned steps:"
echo "  1) Install Ollama and download the model configured in .env"
echo "  2) Bundle with pyinstaller merlin.spec (windowed)"
echo "  3) Wrap dist/Merlin into Merlin.app and build a .dmg"
echo "  4) Register launchd agent from installer/mac/com.merlin.assistant.plist"
exit 1
