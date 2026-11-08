from .core import app, APP_DIR
from . import routes_main as _routes_main
from . import routes_extra as _routes_extra
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory=APP_DIR / "static", html=True), name="static")
