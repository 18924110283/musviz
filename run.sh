#!/usr/bin/env bash
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

if ! $PY -c "import PIL, numpy, tkinter" 2>/dev/null; then
    echo "缺依赖。先运行： ./install.sh"
    exit 1
fi

exec $PY app.py "$@"
