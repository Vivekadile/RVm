"""WSGI entry point (PythonAnywhere, gunicorn, etc.): exposes `application`."""
from app import create_app

application = create_app()
