#!/bin/bash
# Double-click this file in Finder to launch the PLC converter on macOS.
# It checks for Python, installs required libraries, then starts the wizard.

# Change to the directory containing this script so relative paths work
cd "$(dirname "$0")"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Check that Python is installed
# ─────────────────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  ========================================================="
    echo "   Python is not installed."
    echo "  ========================================================="
    echo ""
    echo "  To use this tool, you need to install Python first."
    echo ""
    echo "  Steps:"
    echo "    1. Go to https://www.python.org/downloads/"
    echo "    2. Click the yellow 'Download Python' button."
    echo "    3. Run the installer pkg file."
    echo "    4. Once installation is finished, close this window"
    echo "       and double-click run.command again."
    echo ""
    open "https://www.python.org/downloads/"
    read -rp "  Press Enter to exit..."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Check Python version is 3.8 or higher
# ─────────────────────────────────────────────────────────────────────────────
if ! python3 -c "import sys; exit(0 if sys.version_info >= (3,8) else 1)" 2>/dev/null; then
    echo ""
    echo "  ========================================================="
    echo "   Python 3.8 or higher is required."
    echo "  ========================================================="
    echo ""
    echo "  Your current Python version is too old."
    echo "  Please download and install the latest version of Python."
    echo ""
    echo "    https://www.python.org/downloads/"
    echo ""
    open "https://www.python.org/downloads/"
    read -rp "  Press Enter to exit..."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Auto-install required libraries (fast no-op when already up to date)
# ─────────────────────────────────────────────────────────────────────────────
echo "Checking required libraries..."
if ! python3 -m pip install acd-tools rich --quiet --upgrade; then
    echo ""
    echo "  ========================================================="
    echo "   Could not install required libraries automatically."
    echo "  ========================================================="
    echo ""
    echo "  Please try running this command yourself:"
    echo ""
    echo "      pip3 install acd-tools rich"
    echo ""
    echo "  Open Terminal, paste the line above, and press Enter."
    echo "  Then close this window and double-click run.command again."
    echo ""
    read -rp "  Press Enter to exit..."
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Launch the wizard
# ─────────────────────────────────────────────────────────────────────────────
echo ""
python3 wizard.py
read -rp "Press Enter to close..."
